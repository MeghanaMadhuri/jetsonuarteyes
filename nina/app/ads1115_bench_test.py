"""Bench test: ADS1115 pack voltage on Jetson I2C2 (header pins 27/28).

Reads raw ADC code, voltage at AIN0, and pack voltage using the **218 kΩ / 33 kΩ**
divider (``V_pack = V_ain * 251/33``).

**Wiring (Orin NX — separate from IMU on pins 3+5)**

| ADS1115 | Jetson 40-pin |
|---------|----------------|
| VDD | 3.3 V (pin 1 or 17) |
| GND | GND |
| SDA | **Pin 27** (I2C2 — enable in jetson-io) |
| SCL | **Pin 28** (I2C2) |
| ADDR | GND → **0x48** |
| AIN0 | 218 kΩ from BAT+; 33 kΩ from AIN0 to GND |

**One-time (persists after reboot):**

    sudo bash scripts/jetson-enable-battery-i2c2.sh
    # then jetson-io → Save and reboot

**Run on Jetson:**

    python3 -m nina.app.ads1115_bench_test
    python3 -m nina.app.ads1115_bench_test --samples 20 --avg 5
    python3 scripts/test_ads1115.py --cal-scale 1.02   # after DMM trim

Default bus: ``/dev/i2c-1`` for I2C2 @ pins 27/28 (auto-probes if unset).
"""

from __future__ import annotations

import argparse
import os
import statistics
import sys
import time

from nina.sensors.ads1115 import (
    ADS1115,
    DEFAULT_BATTERY_I2C_ADDR,
    is_available,
    resolve_battery_i2c_bus,
)

DIVIDER_R_TOP_OHM = 218_000.0
DIVIDER_R_BOT_OHM = 33_000.0
DEFAULT_DIVIDER_RATIO = (DIVIDER_R_TOP_OHM + DIVIDER_R_BOT_OHM) / DIVIDER_R_BOT_OHM


def _pack_volts_nina(v_pin: float, ratio: float, cal_scale: float, cal_offset: float) -> float:
    return v_pin * ratio * cal_scale + cal_offset


def _read_averaged(
    adc: ADS1115, channel: int, *, avg: int, discard: int
) -> tuple[int, float]:
    """Return mean (raw, V_ain) over *avg* samples after *discard* throwaways."""
    avg = max(1, int(avg))
    discard = max(0, int(discard))
    raws: list[int] = []
    pins: list[float] = []
    for i in range(avg + discard):
        raw, v_pin = adc.read_single_ended_sample(channel)
        if i >= discard:
            raws.append(raw)
            pins.append(v_pin)
        if i + 1 < avg + discard:
            time.sleep(0.004)
    return int(round(statistics.mean(raws))), float(statistics.mean(pins))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="ADS1115 battery voltage bench (I2C2 pins 27/28 on Orin NX)."
    )
    parser.add_argument(
        "--bus",
        type=int,
        default=None,
        help="I2C bus /dev/i2c-N (default: env, else auto-probe, else 1)",
    )
    parser.add_argument(
        "--no-auto",
        action="store_true",
        help="Do not scan buses; use --bus or NINA_BATTERY_I2C_BUS only",
    )
    parser.add_argument(
        "--addr",
        type=lambda x: int(x, 0),
        default=int(os.environ.get("NINA_BATTERY_I2C_ADDR", hex(DEFAULT_BATTERY_I2C_ADDR)), 0),
        help="ADS1115 address (default 0x48)",
    )
    parser.add_argument(
        "--channel",
        type=int,
        default=int(os.environ.get("NINA_BATTERY_ADS1115_CHANNEL", "0")),
        help="AIN channel 0..3 (default 0)",
    )
    parser.add_argument(
        "--divider-ratio",
        type=float,
        default=float(os.environ.get("NINA_BATTERY_DIVIDER_RATIO", str(DEFAULT_DIVIDER_RATIO))),
        help=f"V_pack = V_ain * ratio (default {DEFAULT_DIVIDER_RATIO:g} = 218k+33k)",
    )
    parser.add_argument(
        "--cal-scale",
        type=float,
        default=float(os.environ.get("NINA_BATTERY_CAL_SCALE", "1")),
        help="Multiply pack voltage after divider (DMM trim)",
    )
    parser.add_argument(
        "--cal-offset",
        type=float,
        default=float(os.environ.get("NINA_BATTERY_CAL_OFFSET_V", "0")),
        help="Add to pack voltage after scale (volts)",
    )
    parser.add_argument(
        "--avg",
        type=int,
        default=int(os.environ.get("NINA_BATTERY_BENCH_AVG", "3")),
        help="Average this many conversions per line (default 3)",
    )
    parser.add_argument(
        "--discard",
        type=int,
        default=1,
        help="Throw away this many reads before averaging (default 1)",
    )
    parser.add_argument(
        "--samples",
        type=int,
        default=0,
        help="Stop after N lines (0 = until Ctrl-C)",
    )
    parser.add_argument(
        "--interval",
        type=float,
        default=0.5,
        help="Seconds between lines (default 0.5)",
    )
    args = parser.parse_args(argv)

    bus = resolve_battery_i2c_bus(
        args.bus,
        auto_discover=not args.no_auto,
    )
    ok, msg = is_available(bus)
    if not ok:
        print(f"ADS1115 not available on bus {bus}: {msg}", file=sys.stderr)
        print(
            "Enable I2C2 on pins 27/28: sudo bash scripts/jetson-enable-battery-i2c2.sh\n"
            "Then jetson-io → Save and reboot. Verify: sudo i2cdetect -y 1",
            file=sys.stderr,
        )
        return 1

    adc = ADS1115(bus, args.addr)
    try:
        adc.open()
    except Exception as exc:
        print(f"Failed to open /dev/i2c-{bus} 0x{args.addr:02X}: {exc}", file=sys.stderr)
        return 1

    ch = max(0, min(3, int(args.channel)))
    print("=== ADS1115 battery bench ===")
    print(f"  Bus:     /dev/i2c-{bus}  (I2C2 → header pins 27 SDA, 28 SCL)")
    print(f"  Addr:    0x{args.addr:02X}   AIN{ch}")
    print(
        f"  Divider: {DIVIDER_R_TOP_OHM/1000:g} kΩ (BAT+→AIN) + "
        f"{DIVIDER_R_BOT_OHM/1000:g} kΩ (AIN→GND)  ratio={args.divider_ratio:.4f}"
    )
    if args.cal_scale != 1.0 or args.cal_offset != 0.0:
        print(f"  Cal:     V_pack = V_ain*ratio*{args.cal_scale:g} + ({args.cal_offset:g})")
    print(f"  Average: {args.avg} sample(s), discard first {args.discard}")
    print("  Compare V_pack with a DMM; tune --cal-scale / --cal-offset or env.\n")

    n = 0
    try:
        while True:
            try:
                raw, v_pin = _read_averaged(
                    adc, ch, avg=args.avg, discard=args.discard
                )
                v_pack = _pack_volts_nina(
                    v_pin,
                    args.divider_ratio,
                    args.cal_scale,
                    args.cal_offset,
                )
            except Exception as exc:
                print(f"read error: {exc}")
                time.sleep(max(0.1, args.interval))
                continue

            print(
                f"[{n:04d}] raw={raw:+6d}  "
                f"V_ain={v_pin:7.4f} V  "
                f"V_pack={v_pack:7.3f} V",
                flush=True,
            )
            n += 1
            if args.samples > 0 and n >= args.samples:
                break
            time.sleep(max(0.05, args.interval))
    except KeyboardInterrupt:
        print("\nStopped.")
    finally:
        adc.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
