#!/usr/bin/env python3
"""HC-SR04 obstacle test: print ``blocked`` / ``open`` vs 100 cm threshold.

Uses the same timing and Jetson.GPIO setup as ``nina.sensors.hcsr04.HCSR04Array``.
Run on the Jetson from the repo root::

    PYTHONPATH=. python3 scripts/test_hcsr04_obstacle_100cm.py

Default pins match ``hcsr04`` front-left (BCM trig=4 / phys 7, echo=9 / phys 21). Override:

    NINA_HCSR04_TEST_TRIG=11 NINA_HCSR04_TEST_ECHO=4 PYTHONPATH=. \\
        python3 scripts/test_hcsr04_obstacle_100cm.py

If ``NINA_HCSR04_DISABLE=1`` is set for the rest of the stack, this script
still runs (it only checks that Jetson.GPIO imports).
"""

from __future__ import annotations

import argparse
import os
import signal
import sys
import time


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    try:
        return int(raw) if raw is not None else default
    except Exception:
        return default


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--threshold-cm",
        type=float,
        default=100.0,
        help="Obstacle closer than this (cm) => blocked (default: 100).",
    )
    parser.add_argument(
        "--hz",
        type=float,
        default=4.0,
        help="Max print rate when state unchanged (default: 4).",
    )
    args = parser.parse_args()
    threshold_mm = int(args.threshold_cm * 10.0)

    from nina.sensors.hcsr04 import (
        HCSR04Array,
        HCSR04Channel,
        is_jetson_gpio_available,
    )

    ok, msg = is_jetson_gpio_available()
    if not ok:
        print(f"skip: {msg}", file=sys.stderr)
        return 1

    # Same defaults as front_left in hcsr04.py unless overridden.
    trig = _env_int("NINA_HCSR04_TEST_TRIG", _env_int("NINA_HCSR04_FL_TRIG", 4))
    echo = _env_int("NINA_HCSR04_TEST_ECHO", _env_int("NINA_HCSR04_FL_ECHO", 9))
    channel = HCSR04Channel(position="sensor", trig=trig, echo=echo)

    arr = HCSR04Array(channels=[channel])
    stop = False

    def _handle_sig(_sig, _frame):
        nonlocal stop
        stop = True

    signal.signal(signal.SIGINT, _handle_sig)
    signal.signal(signal.SIGTERM, _handle_sig)

    try:
        arr.open()
    except RuntimeError as exc:
        print(f"open failed: {exc}", file=sys.stderr)
        return 1

    last_state: str | None = None
    min_interval = 1.0 / max(args.hz, 0.1)
    last_print = 0.0

    try:
        while not stop:
            r = arr.read("sensor")
            if r is None or r.distance_mm is None:
                state = "open"
            elif r.distance_mm <= threshold_mm:
                state = "blocked"
            else:
                state = "open"

            now = time.monotonic()
            if state != last_state or (now - last_print) >= min_interval:
                print(state, flush=True)
                last_state = state
                last_print = now
            time.sleep(0.02)
    finally:
        arr.close()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
