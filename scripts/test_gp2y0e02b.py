#!/usr/bin/env python3
"""Bench Sharp GP2Y0E02B IR distance sensor (I²C).

Uses ``nina.sensors.gp2y0e02b.GP2Y0E02B`` — same registers and scaling as
production cliff detection. On the Jetson::

    python3 scripts/test_gp2y0e02b.py          # repo root
    python3 test_gp2y0e02b.py                  # from scripts/
    python3 test_gp2y0e02b.py --diag           # same, one-shot registers

Wiring (Orin Nano J12 — pins 3 / 5, same bus as MPU IMU):

    VCC  -> 3.3 V  physical pin 17  (not pin 2 / 4 — those are 5 V)
    GND  -> GND    physical pin 6 or 9 (nearby)
    SDA  -> I²C    physical pin 3   (→ ``/dev/i2c-7``)
    SCL  -> I²C    physical pin 5

    i2cdetect -y 7   # expect 0x68 (IMU) and 0x40 (IR)

Useful range is about 4–50 cm (40–500 mm). Override bus / address::

    NINA_IR_I2C_BUS=7 NINA_IR_I2C_ADDR=0x40 python3 scripts/test_gp2y0e02b.py
"""

from __future__ import annotations

import argparse
import os
import signal
import sys
import time

_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    try:
        return int(raw, 0) if raw is not None else default
    except Exception:
        return default


def _diag(bus_num: int, addr: int) -> int:
    try:
        import smbus2
    except Exception as exc:
        print(f"smbus2 not installed: {exc}", file=sys.stderr)
        return 1

    dev = f"/dev/i2c-{bus_num}"
    if not os.path.exists(dev):
        print(f"{dev} not present — check wiring and bus number", file=sys.stderr)
        return 1

    bus = smbus2.SMBus(bus_num)
    try:
        shift_raw = bus.read_byte_data(addr, 0x35) & 0x07
        shift = max(1, 1 << shift_raw)
        high = bus.read_byte_data(addr, 0x5E)
        low = bus.read_byte_data(addr, 0x5F) & 0x0F
        cm = ((high << 4) | low) / 16.0 / float(shift)
        mm = int(cm * 10.0)
        print(f"bus={bus_num} addr=0x{addr:02X}")
        print(f"  reg 0x35 (shift idx) = 0x{shift_raw:02X}  -> factor {shift}")
        print(f"  reg 0x5E = 0x{high:02X}")
        print(f"  reg 0x5F = 0x{low:02X} (low nibble)")
        print(f"  distance = {cm:.2f} cm  ({mm} mm)")
        if mm <= 0 or mm > 1500:
            print("  (out of driver range — check target distance 4–50 cm)")
    except Exception as exc:
        print(f"I²C read failed: {exc}", file=sys.stderr)
        print(
            f"  Confirm sensor: i2cget -y {bus_num} 0x40 0x35",
            file=sys.stderr,
        )
        return 1
    finally:
        bus.close()
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--cliff-mm",
        type=int,
        default=_env_int("NINA_AUTO_CLIFF_MIN_MM", 60),
        help="Print CLIFF when distance_mm < this (default: NINA_AUTO_CLIFF_MIN_MM or 60).",
    )
    parser.add_argument(
        "--hz",
        type=float,
        default=5.0,
        help="Max print rate (default: 5).",
    )
    parser.add_argument(
        "--once",
        action="store_true",
        help="Print one sample and exit.",
    )
    parser.add_argument(
        "--diag",
        action="store_true",
        help="Dump shift/distance registers once and exit.",
    )
    args = parser.parse_args()

    bus_num = _env_int("NINA_IR_I2C_BUS", 7)
    addr = _env_int("NINA_IR_I2C_ADDR", 0x40)

    if args.diag:
        return _diag(bus_num, addr)

    from nina.sensors.gp2y0e02b import GP2Y0E02B, is_available

    ok, msg = is_available()
    if not ok:
        print(f"skip: {msg}", file=sys.stderr)
        return 1

    sensor = GP2Y0E02B(bus=bus_num, address=addr, position="test")
    stop = False

    def _handle_sig(_sig, _frame):
        nonlocal stop
        stop = True

    signal.signal(signal.SIGINT, _handle_sig)
    signal.signal(signal.SIGTERM, _handle_sig)

    try:
        sensor.open()
    except RuntimeError as exc:
        print(f"open failed: {exc}", file=sys.stderr)
        print(f"  try: i2cget -y {bus_num} 0x{addr:02x} 0x35", file=sys.stderr)
        return 1

    connected, status = sensor.status()
    print(f"GP2Y0E02B — {status}  (cliff threshold {args.cliff_mm} mm)")
    print("Press Ctrl+C to stop\n")

    min_interval = 1.0 / max(args.hz, 0.1)
    last_print = 0.0

    try:
        while not stop:
            r = sensor.read()
            now = time.monotonic()
            if r is None or (now - last_print) < min_interval:
                if args.once:
                    time.sleep(0.15)
                    r = sensor.read()
                else:
                    time.sleep(0.02)
                    continue

            last_print = now
            if r.distance_mm is None:
                line = "no reading (out of range or I²C glitch)"
            else:
                cm = r.distance_mm / 10.0
                cliff = r.distance_mm < args.cliff_mm
                tag = " CLIFF" if cliff else ""
                line = f"{r.distance_mm:4d} mm  ({cm:5.1f} cm){tag}"
            print(line, flush=True)

            if args.once:
                break
    finally:
        sensor.close()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
