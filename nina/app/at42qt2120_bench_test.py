"""Bench test: AT42QT2120 touch on Jetson header pins **3** (SDA) + **5** (SCL).

Default ``/dev/i2c-7`` (``sudo i2cdetect -y -r 7`` → **1c**). Shares the bus with
ADS1115 (**48**) and MPU-9250 (**68**).

Production default is **DMR keystatus** (``NINA_TOUCH_DETECT=keystatus``): read
KEY_STATUS for channel 0, ``0xFF`` = idle, bit set = pressed.

**Run on Jetson:**

    cd ~/Nvidia-jetson-platform
    export PYTHONPATH=.
    python3 -m nina.app.at42qt2120_bench_test
    python3 -m nina.app.at42qt2120_bench_test --watch
    python3 -m nina.app.at42qt2120_bench_test --watch --dmr-init --detect keystatus
"""

from __future__ import annotations

import argparse
import math
import os
import sys
import time

from nina.sensors.at42qt2120 import (
    AT42QT2120,
    DEFAULT_TOUCH_I2C_ADDR,
    DEFAULT_TOUCH_I2C_BUS,
    _KEYSTATUS_IDLE_BYTE,
    default_touch_i2c_bus,
    is_available,
)
from nina.sensors.touch_at42qt2120_monitor import (
    touch_debounce_step,
    touch_grace_debounce_step,
    touch_inverted_idle,
    touch_inverted_press,
    touch_mask_active,
    touch_release_rearm_step,
    touch_rising_edge_debounce_step,
    touch_stuck_high_step,
)


def _format_mask(mask: int) -> str:
    if mask == 0:
        return "none"
    chans = [str(ch) for ch in range(12) if mask & (1 << ch)]
    return ",".join(chans) if chans else f"0x{mask:03X}"


def _env_detect_mode() -> str:
    raw = (os.environ.get("NINA_TOUCH_DETECT") or "").strip().lower()
    if raw in ("keystatus", "key_mask", "status"):
        return raw
    if (os.environ.get("NINA_TOUCH_USE_KEY_MASK") or "").strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    ):
        return "key_mask"
    return "keystatus"


def _env_int(name: str, default: int) -> int:
    raw = (os.environ.get(name) or "").strip()
    if not raw:
        return default
    try:
        return int(raw, 0)
    except ValueError:
        return default


def _env_float(name: str, default: float) -> float:
    raw = (os.environ.get(name) or "").strip()
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError:
        return default


def _min_fire_reads(debounce: int, hold_sec: float, poll_sec: float) -> int:
    hold_reads = 1
    if hold_sec > 0 and poll_sec > 0:
        hold_reads = max(1, int(math.ceil(hold_sec / poll_sec)))
    return max(debounce, hold_reads)


class _BenchState:
    """Mirrors production monitor debounce / re-arm for bench output."""

    def __init__(
        self,
        *,
        detect: str,
        channel: int,
        debounce_reads: int,
        release_reads: int,
        hold_sec: float,
        poll_sec: float,
        channel_mask: int,
        stuck_after_sec: float,
        stuck_clear_reads: int,
    ) -> None:
        self.detect = detect
        self.channel = channel
        self.debounce_reads = debounce_reads
        self.release_reads = release_reads
        self.fire_reads = _min_fire_reads(debounce_reads, hold_sec, poll_sec)
        self.poll_sec = poll_sec
        self.channel_mask = channel_mask & 0xFFF
        self.stuck_after_sec = stuck_after_sec
        self.stuck_clear_reads = stuck_clear_reads

        self.armed = True
        self.hits = 0
        self.release_hits = 0
        self.prev_touched = False
        self.prev_masked = 0
        self.idle_mask = 0
        self.baseline_ready = detect != "key_mask"
        self.baseline_clear_hits = 0
        self.stuck_latched = False
        self.stuck_clear_hits = 0
        self.touch_high_since: float | None = None
        self.last_signal_mono = -1e30
        self.fire_count = 0

    def step_keystatus(self, pressed: bool, now: float) -> tuple[bool, str]:
        if not self.armed:
            self.armed, self.release_hits = touch_release_rearm_step(
                pressed,
                armed=False,
                release_reads=self.release_reads,
                consecutive_clear=self.release_hits,
            )
            self.hits = 0
            return False, "re-arming"

        fire, self.hits = touch_debounce_step(
            pressed,
            debounce_reads=self.fire_reads,
            consecutive_hits=self.hits,
        )
        if fire:
            self.armed = False
            self.release_hits = 0
            self.hits = 0
            self.fire_count += 1
            return True, f"FIRE #{self.fire_count}"
        return False, f"debounce {self.hits}/{self.fire_reads}"

    def step_key_mask(
        self, dev: AT42QT2120, now: float
    ) -> tuple[bool, str, dict]:
        snap = dev.touch_snapshot(channel=self.channel)
        masked = int(snap["mask"]) & self.channel_mask
        status_stuck = dev.status_stuck_idle()

        _, self.stuck_latched, self.touch_high_since, self.stuck_clear_hits, _ = (
            touch_stuck_high_step(
                status_stuck,
                stuck_latched=self.stuck_latched,
                touch_high_since_mono=self.touch_high_since,
                stuck_clear_hits=self.stuck_clear_hits,
                now=now,
                stuck_after_sec=self.stuck_after_sec,
                stuck_clear_reads=self.stuck_clear_reads,
            )
        )

        if self.stuck_latched:
            touched = self.baseline_ready and touch_mask_active(
                masked, self.idle_mask, prev_masked=self.prev_masked
            )
        elif touch_inverted_idle(self.idle_mask):
            touched = self.baseline_ready and touch_inverted_press(
                masked, self.idle_mask
            )
        else:
            touched = self.baseline_ready and touch_mask_active(
                masked, self.idle_mask, prev_masked=self.prev_masked
            )

        if not self.baseline_ready:
            if self.idle_mask == 0:
                self.idle_mask = masked
                self.baseline_clear_hits = 1
            elif masked == self.idle_mask:
                self.baseline_clear_hits += 1
                if self.baseline_clear_hits >= 10:
                    self.baseline_ready = True
            else:
                self.idle_mask = masked
                self.baseline_clear_hits = 1
            self.prev_masked = masked
            return False, "learning idle", snap

        if not self.armed:
            self.armed, self.release_hits = touch_release_rearm_step(
                touched,
                armed=False,
                release_reads=self.release_reads,
                consecutive_clear=self.release_hits,
            )
            self.hits = 0
            self.prev_touched = touched
            self.prev_masked = masked
            return False, "re-arming", snap

        fire = False
        note = ""
        if touch_inverted_idle(self.idle_mask):
            fire, self.hits, self.last_signal_mono = touch_grace_debounce_step(
                touched,
                consecutive_hits=self.hits,
                last_signal_mono=self.last_signal_mono,
                now=now,
                debounce_reads=self.debounce_reads,
            )
            note = f"grace {self.hits}/{self.debounce_reads}"
        else:
            fire, self.hits = touch_rising_edge_debounce_step(
                touched,
                self.prev_touched,
                debounce_reads=self.debounce_reads,
                consecutive_hits=self.hits,
            )
            self.prev_touched = touched
            note = f"edge {self.hits}/{self.debounce_reads}"

        self.prev_masked = masked
        if fire:
            self.armed = False
            self.release_hits = 0
            self.hits = 0
            self.fire_count += 1
            return True, f"FIRE #{self.fire_count}", snap
        return False, note, snap


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="AT42QT2120 touch bench (header pins 3 SDA, 5 SCL → i2c-7)."
    )
    parser.add_argument(
        "--bus",
        type=int,
        default=None,
        help=f"I2C bus /dev/i2c-N (default: env or {DEFAULT_TOUCH_I2C_BUS})",
    )
    parser.add_argument(
        "--addr",
        type=lambda x: int(x, 0),
        default=DEFAULT_TOUCH_I2C_ADDR,
        help=f"7-bit I2C address (default 0x{DEFAULT_TOUCH_I2C_ADDR:02X})",
    )
    parser.add_argument(
        "--watch",
        action="store_true",
        help="Print touch state continuously until Ctrl+C",
    )
    parser.add_argument(
        "--interval",
        type=float,
        default=None,
        help="Poll interval for --watch (default: NINA_TOUCH_POLL_SEC or 0.05)",
    )
    parser.add_argument(
        "--detect",
        choices=("keystatus", "key_mask", "status"),
        default=None,
        help="Detection mode (default: NINA_TOUCH_DETECT or keystatus)",
    )
    parser.add_argument(
        "--channel",
        type=int,
        default=None,
        help="Touch channel 0..11 (default: NINA_TOUCH_CHANNEL or 0)",
    )
    parser.add_argument(
        "--dmr-init",
        action="store_true",
        help="Run DMR reset+calibrate+threshold before watch (NINA_TOUCH_CHIP_INIT)",
    )
    parser.add_argument(
        "--threshold",
        type=int,
        default=None,
        help="Detect threshold for --dmr-init (default: NINA_TOUCH_THRESHOLD or 25)",
    )
    parser.add_argument(
        "--debounce",
        type=int,
        default=None,
        help="Consecutive reads to fire (default: NINA_TOUCH_DEBOUNCE or 5)",
    )
    parser.add_argument(
        "--release-reads",
        type=int,
        default=None,
        help="Clear polls before re-arm (default: NINA_TOUCH_RELEASE_READS or 3)",
    )
    args = parser.parse_args(argv)

    bus = int(args.bus) if args.bus is not None else default_touch_i2c_bus()
    detect = args.detect or _env_detect_mode()
    channel = (
        max(0, min(11, args.channel))
        if args.channel is not None
        else max(0, min(11, _env_int("NINA_TOUCH_CHANNEL", 0)))
    )
    poll_sec = max(
        0.02,
        float(args.interval)
        if args.interval is not None
        else _env_float("NINA_TOUCH_POLL_SEC", 0.05),
    )
    debounce = max(
        2,
        args.debounce
        if args.debounce is not None
        else _env_int("NINA_TOUCH_DEBOUNCE", 5),
    )
    release_reads = max(
        1,
        args.release_reads
        if args.release_reads is not None
        else _env_int("NINA_TOUCH_RELEASE_READS", 3),
    )
    hold_sec = _env_float("NINA_TOUCH_HOLD_SEC", 0.4)
    chip_init = args.dmr_init or (
        (os.environ.get("NINA_TOUCH_CHIP_INIT") or "").strip().lower()
        in ("1", "true", "yes", "on")
    )
    threshold = (
        max(1, min(255, args.threshold))
        if args.threshold is not None
        else max(1, min(255, _env_int("NINA_TOUCH_THRESHOLD", 25)))
    )

    ok, msg = is_available(bus, args.addr)
    if not ok:
        print(f"ERROR: {msg}", file=sys.stderr)
        print(
            "Tip: sudo i2cdetect -y -r 7  (expect 1c; do not scan bus 5 on Orin NX)",
            file=sys.stderr,
        )
        return 1

    dev = AT42QT2120(bus, args.addr)
    dev.open()
    try:
        dev.verify_chip_id()
        chip = dev.read_chip_id()
        print(
            f"OK  /dev/i2c-{bus}  AT42QT2120 @ 0x{args.addr & 0x7F:02X}  "
            f"CHIP_ID=0x{chip:02X}  detect={detect}  ch={channel}"
        )

        if chip_init:
            print(f"DMR init: reset → calibrate → threshold={threshold} on ch={channel}")
            dev.dmr_bootstrap(
                detect_threshold=threshold,
                touch_channel=channel,
            )

        if not args.watch:
            snap = dev.touch_snapshot(channel=channel)
            ks = int(snap["key_status_byte"])
            print(
                f"  STATUS=0x{snap['status']:02X}  "
                f"KEY_STATUS[ch{channel}]=0x{ks:02X}  "
                f"dmr_pressed={snap['key_pressed_dmr']}  "
                f"(0x{_KEYSTATUS_IDLE_BYTE:02X}=idle)  "
                f"mask={_format_mask(int(snap['mask']))}"
            )
            return 0

        fire_reads = _min_fire_reads(debounce, hold_sec, poll_sec)
        print(
            f"Watching ({detect}) poll={poll_sec:.3f}s debounce={debounce} "
            f"fire_reads={fire_reads} release={release_reads}  Ctrl+C to stop"
        )
        print(
            "  keystatus: KEY_STATUS 0xFF=idle; production fires after sustained press + release."
        )

        bench = _BenchState(
            detect=detect,
            channel=channel,
            debounce_reads=debounce,
            release_reads=release_reads,
            hold_sec=hold_sec,
            poll_sec=poll_sec,
            channel_mask=_env_int("NINA_TOUCH_CHANNEL_MASK", 0xFFF),
            stuck_after_sec=_env_float("NINA_TOUCH_STUCK_SEC", 2.0),
            stuck_clear_reads=_env_int("NINA_TOUCH_STUCK_CLEAR_READS", 15),
        )

        while True:
            now = time.monotonic()
            if dev.is_calibrating():
                print("  calibrating…")
                time.sleep(poll_sec)
                continue

            if detect == "keystatus":
                pressed = dev.is_key_pressed(channel)
                ks = dev.read_key_status_byte(channel)
                fired, note = bench.step_keystatus(pressed, now)
                line = (
                    f"dmr ch{channel} KEY=0x{ks:02X} pressed={pressed} "
                    f"armed={bench.armed} {note}"
                )
            elif detect == "status":
                pressed = dev.keys_pressed()
                fired, note = bench.step_keystatus(pressed, now)
                st = dev.read_status()
                line = (
                    f"STATUS=0x{st:02X} keys={pressed} armed={bench.armed} {note}"
                )
            else:
                fired, note, snap = bench.step_key_mask(dev, now)
                line = (
                    f"mask={_format_mask(int(snap['mask']))} "
                    f"idle=0x{bench.idle_mask:03X} armed={bench.armed} {note}"
                )

            if fired:
                line += "  ** TOUCH (would react) **"
            print(line)
            time.sleep(poll_sec)
    except KeyboardInterrupt:
        print()
        return 0
    finally:
        dev.close()


if __name__ == "__main__":
    raise SystemExit(main())
