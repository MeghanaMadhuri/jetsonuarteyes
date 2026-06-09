"""Background poll of AT42QT2120; fires ``NinaService.run_touch_reaction`` on touch."""

from __future__ import annotations

import logging
import math
import threading
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Callable, Optional, Tuple

from nina.sensors.at42qt2120 import AT42QT2120, is_available

if TYPE_CHECKING:
    from sirena_ui.workers.nina_service import NinaService

log = logging.getLogger("nina.sensors.touch_at42qt2120")

# Grace window for non-inverted grace debounce (mask-active path).
_PRESS_GRACE_SEC = 0.18


def touch_inverted_press(masked: int, idle_mask: int) -> bool:
    """True while an inverted electrode reads below its idle fingerprint."""
    idle = int(idle_mask) & 0xFFF
    if idle == 0:
        return False
    return (int(masked) & 0xFFF) < idle


def touch_press_edge(prev_masked: int, masked: int, idle_mask: int) -> bool:
    """True on the poll where a press begins (inverted drop or normal rise)."""
    prev = int(prev_masked) & 0xFFF
    m = int(masked) & 0xFFF
    idle = int(idle_mask) & 0xFFF
    if idle != 0 and prev >= idle and m < idle:
        return True
    return (m & ~idle) != 0 and (prev & ~idle) == 0


def touch_grace_debounce_step(
    signal: bool,
    *,
    consecutive_hits: int,
    last_signal_mono: float,
    now: float,
    debounce_reads: int,
    grace_sec: float = _PRESS_GRACE_SEC,
) -> Tuple[bool, int, float]:
    """Debounce noisy inverted presses that bounce back to idle mid-gesture."""
    if signal:
        n = consecutive_hits + 1
        if n >= debounce_reads:
            return True, 0, now
        return False, n, now
    if consecutive_hits > 0 and (now - last_signal_mono) <= grace_sec:
        return False, consecutive_hits, last_signal_mono
    return False, 0, last_signal_mono


def touch_inverted_idle(idle_mask: int) -> bool:
    """True when idle leakage sets mask bits (channel-0 inverted wiring)."""
    return (int(idle_mask) & 0xFFF) != 0


def touch_mask_active(
    masked: int,
    idle_mask: int,
    *,
    prev_masked: int = 0,
) -> bool:
    """True when *masked* differs from the learned idle fingerprint.

    Nina's AT42QT2120 wiring reads **inverted** on channel 0: idle leakage holds
    ``mask=0x001`` and a physical press **clears** it to ``0x000``. Also accept
    the normal (non-inverted) case where new bits appear above idle.
    """
    masked &= 0xFFF
    idle_mask &= 0xFFF
    prev = int(prev_masked) & 0xFFF

    if idle_mask != 0 and masked < idle_mask:
        return True
    if (masked & ~idle_mask) != 0:
        return True
    if masked > idle_mask:
        return True
    if prev < idle_mask and masked > idle_mask:
        return True
    return False


def touch_release_rearm_step(
    touched: bool,
    *,
    armed: bool,
    release_reads: int,
    consecutive_clear: int,
) -> tuple[bool, int]:
    """Return ``(armed, new_consecutive_clear)`` after a reaction.

    While disarmed, require *release_reads* consecutive polls with no touch
    before the monitor may fire again.
    """
    if armed:
        return True, 0
    if not touched:
        n = consecutive_clear + 1
        if n >= release_reads:
            return True, 0
        return False, n
    return False, 0


def touch_rising_edge_debounce_step(
    touched: bool,
    prev_touched: bool,
    *,
    debounce_reads: int,
    consecutive_hits: int,
) -> tuple[bool, int]:
    """Return ``(should_fire, new_consecutive_hits)`` for one poll.

    Counts consecutive *touched* samples starting at a rising edge
    (``touched and not prev_touched``). Resets when *touched* is false.
    """
    if not touched:
        return False, 0
    if not prev_touched:
        n = 1
    else:
        n = consecutive_hits + 1
    if n >= debounce_reads:
        return True, 0
    return False, n


def touch_debounce_step(
    touched: bool,
    *,
    debounce_reads: int,
    consecutive_hits: int,
) -> tuple[bool, int]:
    """Level-trigger debounce: fire after *debounce_reads* consecutive pressed polls."""
    if touched:
        n = consecutive_hits + 1
        if n >= debounce_reads:
            return True, 0
        return False, n
    return False, 0


def touch_stuck_high_step(
    touched_raw: bool,
    *,
    stuck_latched: bool,
    touch_high_since_mono: Optional[float],
    stuck_clear_hits: int,
    now: float,
    stuck_after_sec: float,
    stuck_clear_reads: int,
) -> Tuple[bool, bool, Optional[float], int, bool]:
    """Suppress a latched STATUS-high line (common when the electrode back touches metal).

    Returns:
        ``(effective_touched, new_stuck_latched, new_touch_high_since_mono,
        new_stuck_clear_hits, newly_entered_stuck)``.
    """
    if not touched_raw:
        touch_high_since_mono = None
        if stuck_latched:
            hits = stuck_clear_hits + 1
            if hits >= stuck_clear_reads:
                return False, False, None, 0, False
            return False, True, None, hits, False
        return False, False, None, 0, False

    if stuck_latched:
        return False, True, touch_high_since_mono, 0, False

    if touch_high_since_mono is None:
        touch_high_since_mono = now
    if (now - touch_high_since_mono) >= stuck_after_sec:
        return False, True, touch_high_since_mono, 0, True
    return True, False, touch_high_since_mono, 0, False


def touch_baseline_idle_step(
    masked: int,
    *,
    baseline_ready: bool,
    baseline_clear_reads: int,
    consecutive_clear: int,
    idle_mask: int,
) -> Tuple[bool, int, int]:
    """Learn a stable idle key-mask and require it for *baseline_clear_reads* polls."""
    if baseline_ready:
        return True, consecutive_clear, idle_mask
    candidate = int(masked) & 0xFFF
    if idle_mask == 0:
        return False, 1, candidate
    if candidate != idle_mask:
        return False, 1, candidate
    n = consecutive_clear + 1
    if n >= baseline_clear_reads:
        return True, 0, idle_mask
    return False, n, idle_mask


def touch_min_fire_reads(
    debounce_reads: int,
    hold_sec: float,
    poll_sec: float,
) -> int:
    """Consecutive pressed polls before fire (bench + production share this)."""
    hold_reads = 1
    if hold_sec > 0 and poll_sec > 0:
        hold_reads = max(1, int(math.ceil(hold_sec / poll_sec)))
    return max(int(debounce_reads), hold_reads)


@dataclass
class KeystatusDebounceState:
    """DMR keystatus debounce — same state machine as ``at42qt2120_bench_test``."""

    fire_reads: int
    release_reads: int
    armed: bool = True
    hits: int = 0
    release_hits: int = 0
    fire_count: int = 0

    def step(self, pressed: bool) -> tuple[bool, str]:
        """Return ``(should_fire, status_note)`` for one poll."""
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


def touch_baseline_ready_step(
    effective_touched: bool,
    *,
    baseline_ready: bool,
    baseline_clear_reads: int,
    consecutive_clear: int,
) -> Tuple[bool, int]:
    """Require a clean idle window after boot before touch reactions may fire."""
    if baseline_ready:
        return True, 0
    if effective_touched:
        return False, 0
    n = consecutive_clear + 1
    if n >= baseline_clear_reads:
        return True, 0
    return False, n


class TouchAt42qt2120Monitor:
    """Poll AT42QT2120; on touch, stop drive, speak, park motors."""

    def __init__(self, service: "NinaService") -> None:
        self._svc = service
        s = service.settings.touch_at42qt2120
        self._detect_mode = str(getattr(s, "detect_mode", "keystatus"))
        self._touch_channel = int(getattr(s, "touch_channel", 0))
        self._chip_init = bool(getattr(s, "chip_init", False))
        self._detect_threshold = int(getattr(s, "detect_threshold", 25))
        self._debounce_reads = int(s.debounce_reads)
        self._hold_sec = float(getattr(s, "hold_sec", 0.4))
        self._release_reads = int(s.release_reads)
        self._baseline_clear_reads = int(s.baseline_clear_reads)
        self._post_baseline_arm_reads = int(
            getattr(s, "post_baseline_arm_reads", 15)
        )
        self._stuck_after_sec = float(s.stuck_high_sec)
        self._stuck_clear_reads = int(s.stuck_clear_reads)
        self._cooldown_sec = float(s.cooldown_sec)
        self._blind_sec = float(s.blind_after_reaction_sec)
        self._use_key_mask = bool(getattr(s, "use_key_mask", False))
        self._channel_mask = int(getattr(s, "channel_mask", 0xFFF)) & 0xFFF
        self._poll_sec = float(s.poll_interval_sec)
        self._touch = AT42QT2120(s.i2c_bus, s.i2c_address)
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._armed = True
        self._baseline_ready = self._detect_mode != "key_mask"
        self._baseline_clear_hits = 0
        self._touch_armed = self._detect_mode != "key_mask"
        self._post_baseline_idle_hits = 0
        self._stuck_latched = False
        self._stuck_clear_hits = 0
        self._touch_high_since_mono: Optional[float] = None
        self._idle_mask: int = 0
        self._prev_masked: int = 0
        self._prev_touched = False
        self._hits = 0
        self._release_hits = 0
        self._last_signal_mono: float = -1e30
        self._last_fire_mono = -1e30
        self._quiet_until_mono = -1e30

    def start(self) -> None:
        s = self._svc.settings.touch_at42qt2120
        ok, msg = is_available(
            s.i2c_bus,
            s.i2c_address,
            probe_attempts=max(1, int(s.startup_probe_attempts)),
            probe_delay_sec=max(0.0, float(s.startup_probe_delay_sec)),
        )
        if not ok:
            raise RuntimeError(msg)
        self._touch.open()
        try:
            self._touch.verify_chip_id()
            if self._chip_init:
                self._touch.dmr_bootstrap(
                    detect_threshold=self._detect_threshold,
                    touch_channel=self._touch_channel,
                )
        except Exception:
            self._touch.close()
            raise
        self._armed = True
        self._baseline_ready = self._detect_mode != "key_mask"
        self._baseline_clear_hits = 0
        self._touch_armed = self._detect_mode != "key_mask"
        self._post_baseline_idle_hits = 0
        self._stuck_latched = False
        self._stuck_clear_hits = 0
        self._touch_high_since_mono = None
        self._idle_mask = 0
        self._prev_masked = 0
        self._prev_touched = False
        self._hits = 0
        self._release_hits = 0
        self._last_signal_mono = -1e30
        self._last_fire_mono = -1e30
        self._quiet_until_mono = -1e30
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._run, name="TouchAt42qt2120Monitor", daemon=True
        )
        self._thread.start()
        fire_reads = self._min_fire_reads()
        log.info(
            "AT42QT2120 touch monitor started (i2c-%s 0x%02X detect=%s "
            "ch=%d chip_init=%s threshold=%d poll=%.3fs debounce=%d "
            "fire_reads=%d release=%d hold=%.2fs cooldown=%.1fs blind=%.1fs)",
            self._svc.settings.touch_at42qt2120.i2c_bus,
            self._svc.settings.touch_at42qt2120.i2c_address,
            self._detect_mode,
            self._touch_channel,
            self._chip_init,
            self._detect_threshold,
            self._poll_sec,
            self._debounce_reads,
            fire_reads,
            self._release_reads,
            self._hold_sec,
            self._cooldown_sec,
            self._blind_sec,
        )

    def stop(self) -> None:
        self._stop.set()
        t = self._thread
        self._thread = None
        if t is not None:
            t.join(timeout=5.0)
            if t.is_alive():
                log.warning("AT42QT2120 touch monitor thread did not exit in time")
        try:
            self._touch.close()
        except Exception:
            log.debug("AT42QT2120 close failed", exc_info=True)
        log.info("AT42QT2120 touch monitor stopped")

    def is_running(self) -> bool:
        """True when the poll thread is alive."""
        t = self._thread
        return t is not None and t.is_alive()

    def _run(self) -> None:
        if self._detect_mode == "key_mask":
            self._run_key_mask()
        else:
            read_pressed: Callable[[], bool]
            if self._detect_mode == "status":
                read_pressed = self._touch.keys_pressed
            else:
                ch = self._touch_channel
                read_pressed = lambda ch=ch: self._touch.is_key_pressed(ch)
            self._run_keystatus(read_pressed)

    def _min_fire_reads(self) -> int:
        return touch_min_fire_reads(
            self._debounce_reads, self._hold_sec, self._poll_sec
        )

    def _run_keystatus(self, read_pressed: Callable[[], bool]) -> None:
        """DMR keystatus loop — same debounce path as ``at42qt2120_bench_test``."""
        debounce = KeystatusDebounceState(
            fire_reads=self._min_fire_reads(),
            release_reads=self._release_reads,
        )
        while not self._stop.is_set():
            try:
                if self._touch.is_calibrating():
                    debounce.hits = 0
                    time.sleep(self._poll_sec)
                    continue
                pressed = read_pressed()
            except Exception:
                log.debug("AT42QT2120 read failed", exc_info=True)
                debounce.hits = 0
                time.sleep(max(self._poll_sec, 0.1))
                continue

            now = time.monotonic()
            if now < self._quiet_until_mono:
                debounce.armed = False
                debounce.hits = 0
                debounce.release_hits = 0
                time.sleep(self._poll_sec)
                continue

            fire, _note = debounce.step(pressed)
            if fire:
                log.debug(
                    "AT42QT2120 keystatus %s pressed=%s",
                    _note,
                    pressed,
                )
                self._complete_reaction(pressed=pressed, masked=None)

            time.sleep(self._poll_sec)

    def _run_key_mask(self) -> None:
        """Legacy inverted 12-bit mask path with baseline learning."""
        while not self._stop.is_set():
            try:
                if self._touch.is_calibrating():
                    self._hits = 0
                    self._prev_touched = False
                    time.sleep(self._poll_sec)
                    continue
                mask = self._touch.read_key_mask()
                masked = mask & self._channel_mask
                status_stuck = self._touch.status_stuck_idle()
            except Exception:
                log.debug("AT42QT2120 read failed", exc_info=True)
                self._hits = 0
                self._prev_touched = False
                self._touch_high_since_mono = None
                time.sleep(max(self._poll_sec, 0.1))
                continue

            now = time.monotonic()
            _, self._stuck_latched, self._touch_high_since_mono, self._stuck_clear_hits, newly_stuck = (
                touch_stuck_high_step(
                    status_stuck,
                    stuck_latched=self._stuck_latched,
                    touch_high_since_mono=self._touch_high_since_mono,
                    stuck_clear_hits=self._stuck_clear_hits,
                    now=now,
                    stuck_after_sec=self._stuck_after_sec,
                    stuck_clear_reads=self._stuck_clear_reads,
                )
            )
            if self._stuck_latched:
                touched = self._baseline_ready and touch_mask_active(
                    masked, self._idle_mask, prev_masked=self._prev_masked
                )
            elif touch_inverted_idle(self._idle_mask):
                touched = self._baseline_ready and touch_inverted_press(
                    masked, self._idle_mask
                )
            else:
                touched = self._baseline_ready and touch_mask_active(
                    masked, self._idle_mask, prev_masked=self._prev_masked
                )

            if newly_stuck:
                snap = self._touch.touch_snapshot()
                log.warning(
                    "AT42QT2120 STATUS stuck HIGH with mask=0 (STATUS=0x%02X) — "
                    "touch reactions suppressed until STATUS clears. "
                    "Insulate the electrode back from metal/chassis.",
                    int(snap["status"]),
                )

            was_ready = self._baseline_ready
            self._baseline_ready, self._baseline_clear_hits, self._idle_mask = (
                touch_baseline_idle_step(
                    masked,
                    baseline_ready=self._baseline_ready,
                    baseline_clear_reads=self._baseline_clear_reads,
                    consecutive_clear=self._baseline_clear_hits,
                    idle_mask=self._idle_mask,
                )
            )
            if was_ready != self._baseline_ready and self._baseline_ready:
                self._prev_masked = self._idle_mask
                self._post_baseline_idle_hits = 0
                self._touch_armed = False
                log.info(
                    "AT42QT2120 idle baseline learned mask=0x%03X "
                    "(arming after %d stable idle polls)",
                    self._idle_mask,
                    self._post_baseline_arm_reads,
                )

            if not self._baseline_ready:
                self._hits = 0
                self._prev_touched = False
                self._prev_masked = masked
                time.sleep(self._poll_sec)
                continue

            if not self._touch_armed:
                if masked == self._idle_mask:
                    self._post_baseline_idle_hits += 1
                else:
                    self._post_baseline_idle_hits = 0
                    self._prev_masked = masked
                if self._post_baseline_idle_hits >= self._post_baseline_arm_reads:
                    self._touch_armed = True
                    self._prev_masked = self._idle_mask
                    log.info(
                        "AT42QT2120 touch reactions armed (idle=0x%03X)",
                        self._idle_mask,
                    )
                else:
                    self._hits = 0
                    time.sleep(self._poll_sec)
                    continue

            if now < self._quiet_until_mono:
                self._armed = False
                self._hits = 0
                self._release_hits = 0
                self._prev_touched = touched
                self._prev_masked = masked
                time.sleep(self._poll_sec)
                continue

            if not self._armed:
                self._armed, self._release_hits = touch_release_rearm_step(
                    touched,
                    armed=False,
                    release_reads=self._release_reads,
                    consecutive_clear=self._release_hits,
                )
                self._hits = 0
                self._last_signal_mono = -1e30
                self._prev_touched = touched
                self._prev_masked = masked
                time.sleep(self._poll_sec)
                continue

            fire = False
            if touch_inverted_idle(self._idle_mask):
                fire, self._hits, self._last_signal_mono = touch_grace_debounce_step(
                    touched,
                    consecutive_hits=self._hits,
                    last_signal_mono=self._last_signal_mono,
                    now=now,
                    debounce_reads=self._debounce_reads,
                )
            else:
                fire, self._hits = touch_rising_edge_debounce_step(
                    touched,
                    self._prev_touched,
                    debounce_reads=self._debounce_reads,
                    consecutive_hits=self._hits,
                )
                self._prev_touched = touched

            if fire:
                self._complete_reaction(pressed=touched, masked=masked)

            self._prev_masked = masked
            time.sleep(self._poll_sec)

    def _complete_reaction(
        self, *, pressed: bool, masked: Optional[int]
    ) -> None:
        self._armed = False
        self._release_hits = 0
        self._hits = 0
        try:
            self._svc.run_touch_reaction()
        except Exception:
            log.exception("Touch reaction failed")
        self._last_fire_mono = time.monotonic()
        quiet_sec = max(self._blind_sec, self._cooldown_sec)
        if quiet_sec > 0:
            self._quiet_until_mono = self._last_fire_mono + quiet_sec
        if masked is None:
            log.info(
                "AT42QT2120 touch reaction complete pressed=%s — quiet for %.1fs",
                pressed,
                quiet_sec,
            )
        else:
            log.info(
                "AT42QT2120 touch reaction complete mask=0x%03X idle=0x%03X — "
                "quiet for %.1fs",
                masked,
                self._idle_mask,
                quiet_sec,
            )
