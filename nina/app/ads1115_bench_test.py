"""Bench test: ADS1115 pack voltage on Jetson header pins **3** (SDA) + **5** (SCL).

Default ``/dev/i2c-7`` (``sudo i2cdetect -y -r 7`` → **0x48**). Reads raw code,
V at AIN0, and pack V with **218 kΩ / 33 kΩ** divider (``V_pack = V_ain * 251/33``).

**Wiring (Orin NX — verified on this carrier)**

| ADS1115 | Jetson 40-pin |
|---------|----------------|
| VDD | 3.3 V (pin 1 or 17) |
| GND | GND |
| SDA | **Pin 3** |
| SCL | **Pin 5** |
| ADDR | GND → **0x48** |
| AIN0 | 218 kΩ from BAT+; 33 kΩ from AIN0 to GND |

MPU-9250 (0x68) can share pins 3/5 when reconnected.

**Run on Jetson:**

    cd ~/BLDC_HARI/Nvidia-jetson-platform
    export PYTHONPATH=.
    python3 -m nina.app.ads1115_bench_test
    python3 -m nina.app.ads1115_bench_test --samples 20 --avg 5
"""

from __future__ import annotations

import argparse
import os
import statistics
import sys
import time

from nina.sensors.ads1115 import (
    ADS1115,
    DEFAULT_BATTERY_CAL_SCALE,
    DEFAULT_BATTERY_I2C_ADDR,
    DEFAULT_BATTERY_I2C_BUS,
    DEFAULT_BATTERY_R1_OHM,
    DEFAULT_BATTERY_R2_OHM,
    divider_ratio_from_resistors,
    is_available,
    pack_voltage_from_ain_volts,
    resolve_battery_i2c_bus,
)


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
        description="ADS1115 battery bench (header pins 3 SDA, 5 SCL → i2c-7)."
    )
    parser.add_argument(
        "--bus",
        type=int,
        default=None,
        help=f"I2C bus /dev/i2c-N (default: env or {DEFAULT_BATTERY_I2C_BUS})",
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
        "--r1-ohm",
        type=float,
        default=float(os.environ.get("NINA_BATTERY_R1_OHM", str(DEFAULT_BATTERY_R1_OHM))),
        help="R1 ohms BAT+ to AIN0 (default 218000)",
    )
    parser.add_argument(
        "--r2-ohm",
        type=float,
        default=float(os.environ.get("NINA_BATTERY_R2_OHM", str(DEFAULT_BATTERY_R2_OHM))),
        help="R2 ohms AIN0 to GND (default 33000)",
    )
    parser.add_argument(
        "--divider-ratio",
        type=float,
        default=None,
        help="Override (R1+R2)/R2; default from --r1-ohm and --r2-ohm",
    )
    parser.add_argument(
        "--cal-scale",
        type=float,
        default=float(os.environ.get("NINA_BATTERY_CAL_SCALE", str(DEFAULT_BATTERY_CAL_SCALE))),
        help="DMM trim scale after divider (default ~1.082)",
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
            "Wire SDA→pin 3, SCL→pin 5, ADDR→GND. Then: sudo i2cdetect -y -r 7",
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
    print(f"  Header:  pin 3 = SDA, pin 5 = SCL  (Orin NX)")
    print(f"  Bus:     /dev/i2c-{bus}  (expect 0x48: sudo i2cdetect -y -r {bus})")
    print(f"  Addr:    0x{args.addr:02X}   AIN{ch}")
    print(
        f"  Divider: R1={args.r1_ohm/1000:g} kΩ (BAT+→AIN0)  "
        f"R2={args.r2_ohm/1000:g} kΩ (AIN0→GND)  ratio={ratio:.4f}"
    )
    print(
        f"  Formula: V_pack = V_ain * (R1+R2)/R2 * cal_scale + offset"
    )
    if args.cal_scale != 1.0 or args.cal_offset != 0.0:
        print(f"  Cal:     scale={args.cal_scale:.4f}  offset={args.cal_offset:g} V")
    print(f"  Average: {args.avg} sample(s), discard first {args.discard}")
    print("  Compare V_pack with a DMM; tune --cal-scale / --cal-offset.\n")

    n = 0
    try:
        while True:
            try:
                raw, v_pin = _read_averaged(
                    adc, ch, avg=args.avg, discard=args.discard
                )
                v_pack = pack_voltage_from_ain_volts(
                    v_pin,
                    divider_ratio=ratio,
                    cal_scale=args.cal_scale,
                    cal_offset_v=args.cal_offset,
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
