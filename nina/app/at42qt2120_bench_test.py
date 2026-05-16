"""Bench test: AT42QT2120 touch on Jetson header pins **3** (SDA) + **5** (SCL).

Default ``/dev/i2c-7`` (``sudo i2cdetect -y -r 7`` → **1c**). Shares the bus with
ADS1115 (**48**) and MPU-9250 (**68**).

**Run on Jetson:**

    cd ~/BLDC_HARI/Nvidia-jetson-platform
    export PYTHONPATH=.
    python3 -m nina.app.at42qt2120_bench_test
    python3 -m nina.app.at42qt2120_bench_test --watch
"""

from __future__ import annotations

import argparse
import sys
import time

from nina.sensors.at42qt2120 import (
    AT42QT2120,
    DEFAULT_TOUCH_I2C_ADDR,
    DEFAULT_TOUCH_I2C_BUS,
    default_touch_i2c_bus,
    is_available,
)


def _format_mask(mask: int) -> str:
    if mask == 0:
        return "none"
    chans = [str(ch) for ch in range(12) if mask & (1 << ch)]
    return ",".join(chans) if chans else f"0x{mask:03X}"


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
        default=0.1,
        help="Poll interval for --watch (seconds)",
    )
    args = parser.parse_args(argv)

    bus = int(args.bus) if args.bus is not None else default_touch_i2c_bus()
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
        print(f"OK  /dev/i2c-{bus}  AT42QT2120 @ 0x{args.addr & 0x7F:02X}  CHIP_ID=0x{chip:02X}")

        if not args.watch:
            st = dev.read_status()
            mask = dev.read_key_mask()
            print(f"  STATUS=0x{st:02X}  keys_pressed={dev.keys_pressed()}  mask={_format_mask(mask)}")
            return 0

        print("Watching touch (Ctrl+C to stop)...")
        prev = False
        while True:
            cal = dev.is_calibrating()
            touched = dev.any_touch()
            mask = dev.read_key_mask()
            edge = touched and not prev
            prev = touched
            line = (
                f"touch={touched}  calibrating={cal}  mask={_format_mask(mask)}"
            )
            if edge:
                line += "  ** TOUCH **"
            print(line)
            time.sleep(max(0.02, float(args.interval)))
    except KeyboardInterrupt:
        print()
        return 0
    finally:
        dev.close()


if __name__ == "__main__":
    raise SystemExit(main())
