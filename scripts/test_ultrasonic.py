#!/usr/bin/env python3
"""Bench HC-SR04 on Jetson 40-pin header (BOARD numbering).

Default wiring (Orin Nano dev-kit pinout):

    Trig  -> physical pin 7   (BCM 4)
    Echo  -> physical pin 21  (BCM 9)
    Vcc   -> 5 V, Gnd -> common ground

Echo divider (5 V sensor → ~3 V at the Jetson so HIGH is recognized):

    HC-SR04 Echo ──[ 1.5 kΩ ]── pin 21 ──[ 2.2 kΩ ]── GND

    Wrong (Echo often reads LOW after Trig): 2.2 kΩ series + 1 kΩ to GND
    (~1.6 V HIGH — below Jetson Vih).

Do **not** use physical pin 12 for Trig (I2S / PCM).

Override pins / longer trigger pulse::

    TRIG=18 ECHO=21 TRIG_US=15 python3 scripts/test_ultrasonic.py

One-shot echo trace (after wiring Trig/Echo)::

    python3 scripts/test_ultrasonic.py --diag
"""

from __future__ import annotations

import argparse
import os
import time

import Jetson.GPIO as GPIO

TRIG = int(os.environ.get("TRIG", "7"))
ECHO = int(os.environ.get("ECHO", "21"))
TRIGGER_PULSE_S = float(os.environ.get("TRIG_US", "10")) * 1e-6

PULSE_TIMEOUT_S = 0.030
IDLE_WAIT_S = 0.050
MIN_GAP_S = 0.060
_BAD_TRIG = {12}


def measure() -> tuple[float | None, str]:
    deadline = time.monotonic() + IDLE_WAIT_S
    while GPIO.input(ECHO) == 1:
        if time.monotonic() > deadline:
            return None, "echo idle HIGH — fix Echo divider (see script docstring)"

    GPIO.output(TRIG, False)
    time.sleep(2e-6)
    GPIO.output(TRIG, True)
    time.sleep(TRIGGER_PULSE_S)
    GPIO.output(TRIG, False)

    deadline = time.monotonic() + PULSE_TIMEOUT_S
    while GPIO.input(ECHO) == 0:
        if time.monotonic() > deadline:
            return None, (
                "no echo pulse — Trig not firing, weak divider (~1.6 V), "
                "no 5 V, or Trig/Echo swapped on module"
            )

    t_rise = time.monotonic()
    while GPIO.input(ECHO) == 1:
        if time.monotonic() > deadline:
            return None, "echo pulse too long — divider marginal or noisy line"

    duration = time.monotonic() - t_rise
    return round((duration * 34300) / 2, 2), ""


def run_diag(samples: int = 400) -> None:
    """Fire one Trig and sample Echo every ~75 us; print the bit trace."""
    GPIO.output(TRIG, False)
    time.sleep(0.06)
    GPIO.output(TRIG, True)
    time.sleep(TRIGGER_PULSE_S)
    GPIO.output(TRIG, False)

    bits = []
    t0 = time.monotonic()
    for _ in range(samples):
        bits.append(str(int(GPIO.input(ECHO))))
        time.sleep(75e-6)
    elapsed_ms = (time.monotonic() - t0) * 1000
    joined = "".join(bits)
    highs = joined.count("1")
    print(f"Echo trace ({elapsed_ms:.0f} ms, {highs} HIGH samples):")
    # Wrap for readability
    width = 80
    for i in range(0, len(joined), width):
        print(joined[i : i + width])
    if highs == 0:
        print(
            "\nNo HIGH samples — Jetson never saw Echo rise. "
            "Check Trig wire, 5 V, and use 1.5k + 2.2k divider (not 2.2k + 1k)."
        )
    else:
        print(f"\nEcho did go HIGH ({highs} samples). Ranging should work with this script.")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--diag",
        action="store_true",
        help="One Trig pulse + Echo bit trace (use before the main loop).",
    )
    args = parser.parse_args()

    if TRIG in _BAD_TRIG:
        raise SystemExit(
            f"Trig on physical pin {TRIG} is unreliable on Orin Nano — use pin 7 or 18."
        )

    GPIO.setwarnings(False)
    GPIO.setmode(GPIO.BOARD)
    GPIO.setup(TRIG, GPIO.OUT, initial=GPIO.LOW)
    GPIO.setup(ECHO, GPIO.IN)

    print(f"HC-SR04 — Trig pin {TRIG}, Echo pin {ECHO}, trigger {TRIGGER_PULSE_S * 1e6:.0f} us")
    print(f"Echo idle: {GPIO.input(ECHO)}  (want 0)\n")

    try:
        if args.diag:
            run_diag()
            return

        print("Press Ctrl+C to stop\n")
        while True:
            dist, err = measure()
            if err:
                print(f"Skip — {err}")
            elif dist is None or dist < 2 or dist > 400:
                print(f"Out of range: {dist} cm")
            else:
                print(f"Distance: {dist} cm")
            time.sleep(MIN_GAP_S)
    except KeyboardInterrupt:
        print("\nStopped.")
    finally:
        GPIO.cleanup()


if __name__ == "__main__":
    main()
