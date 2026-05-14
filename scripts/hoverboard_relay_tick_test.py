#!/usr/bin/env python3
"""Toggle the hoverboard pack relay **IN** GPIO on Jetson to hear the coil click.

This script does **not** import Nina — only ``Jetson.GPIO``. Use it to prove the
relay module and wiring before debugging Sirena / brake integration.

**Important:** stop anything else that owns the same BCM (usually the kiosk)::

    systemctl --user stop nina-ui-kiosk.service

Then run from the repo (or any cwd)::

    python3 scripts/hoverboard_relay_tick_test.py
    python3 scripts/hoverboard_relay_tick_test.py --bcm 26 --cycles 8 --dwell 0.4

Stock Nina wiring: relay **IN** on 40-pin **header pin 37** → **BCM 26** on
Jetson Orin Nano (same mapping as ``NINA_HOVER_RELAY_HEADER_PIN=37``).

If you hear nothing, many 5 V modules click very quietly; try ``--dwell 0.8``
or confirm **VCC/GND** on the relay board and **IN** on the expected BCM with a
meter (see ``docs/HOVERBOARD_POWER_RELAY.md``).
"""

from __future__ import annotations

import argparse
import sys
import time


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument(
        "--bcm",
        type=int,
        default=26,
        help="Jetson BCM index wired to relay IN (default: 26 = header pin 37)",
    )
    p.add_argument(
        "--cycles",
        type=int,
        default=6,
        help="Number of LOW→HIGH→… transitions (default: 6)",
    )
    p.add_argument(
        "--dwell",
        type=float,
        default=0.35,
        help="Seconds to hold each level (default: 0.35)",
    )
    p.add_argument(
        "--start-high",
        action="store_true",
        help="First sample is HIGH instead of LOW",
    )
    args = p.parse_args()
    bcm = int(args.bcm)
    if bcm < 0:
        print("error: --bcm must be non-negative", file=sys.stderr)
        return 2

    try:
        import Jetson.GPIO as GPIO  # type: ignore[import-not-found]
    except ImportError as exc:
        print(
            "error: Jetson.GPIO is not available (run this on the Jetson, in the "
            f"system Python or a venv that has Jetson.GPIO installed): {exc}",
            file=sys.stderr,
        )
        return 1

    print(
        f"Relay tick test: BCM {bcm}, {args.cycles} half-cycles, "
        f"dwell {args.dwell}s. Ctrl+C to abort.\n"
        "Ensure nina-ui-kiosk (or anything else using this pin) is stopped."
    )
    time.sleep(0.5)

    GPIO.setwarnings(False)
    try:
        GPIO.setmode(GPIO.BCM)
    except RuntimeError:
        pass

    try:
        GPIO.setup(bcm, GPIO.OUT, initial=GPIO.HIGH if args.start_high else GPIO.LOW)
        level = bool(args.start_high)
        for i in range(int(args.cycles)):
            GPIO.output(bcm, GPIO.HIGH if level else GPIO.LOW)
            print(f"  step {i + 1}/{args.cycles}: GPIO {'HIGH' if level else 'LOW'}")
            time.sleep(max(0.05, float(args.dwell)))
            level = not level
        # Leave line LOW (common idle before app owns polarity semantics).
        GPIO.output(bcm, GPIO.LOW)
        print("done. Pin left LOW. Exiting.")
    except KeyboardInterrupt:
        print("\ninterrupted.")
        try:
            GPIO.output(bcm, GPIO.LOW)
        except Exception:
            pass
    except Exception as exc:
        print(f"error: GPIO failed: {exc}", file=sys.stderr)
        return 1
    finally:
        try:
            GPIO.cleanup(bcm)
        except Exception:
            try:
                GPIO.cleanup()
            except Exception:
                pass

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
