"""
Watch a Jetson GPIO input for ESP32 trigger debugging.

Use this while the Nina kiosk is **stopped** so nothing else holds the line:

    systemctl --user stop nina-ui-kiosk.service
    python3 -m nina.app.esp32_gpio_probe --pin 17

You should see ``LOW -> HIGH`` when the ESP32 pulls the line up and
``HIGH -> LOW`` when it releases. If the line never toggles, check wiring
(physical pin 11 = BCM 17 on Orin Nano) and ESP32 firmware polarity.

Stop with Ctrl-C.
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
import time

from nina.controllers.navigation_manager import jetson_orin_nano_board_pin

log = logging.getLogger("nina.esp32_gpio_probe")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--pin",
        type=int,
        default=int(os.environ.get("NINA_ESP32_TRIGGER_GPIO", "17")),
        help="BCM GPIO to watch (default 17 = physical pin 11).",
    )
    parser.add_argument(
        "--interval",
        type=float,
        default=0.05,
        help="Poll interval in seconds (default 0.05).",
    )
    parser.add_argument(
        "--active-low",
        action="store_true",
        help="Treat a raw LOW as logical HIGH (matches NINA_ESP32_TRIGGER_ACTIVE_LOW=1).",
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
    )

    pin = int(args.pin)
    board_n = jetson_orin_nano_board_pin(pin)
    physical = board_n if board_n is not None else "?"
    print(
        "\n--------------------------------------------------\n"
        f"  ESP32 GPIO probe: BCM {pin}  (physical pin {physical})\n"
        f"  Poll interval: {args.interval:.3f}s\n"
        f"  Active-low mode: {args.active_low}\n"
        "--------------------------------------------------\n"
        "Stop nina-ui-kiosk first if it is running.\n"
    )

    os.environ.setdefault(
        "JETSON_MODEL_NAME",
        os.environ.get("NINA_JETSON_MODEL", "JETSON_ORIN_NANO"),
    )
    try:
        import Jetson.GPIO as GPIO  # type: ignore
    except Exception as exc:
        print(f"[FATAL] Jetson.GPIO import failed: {exc}", file=sys.stderr)
        return 2

    try:
        GPIO.setmode(GPIO.BCM)
        GPIO.setwarnings(False)
        GPIO.setup(pin, GPIO.IN, pull_up_down=GPIO.PUD_DOWN)
    except Exception as exc:
        print(f"[FATAL] GPIO setup failed for BCM {pin}: {exc}", file=sys.stderr)
        return 2

    prev_logical: bool | None = None
    try:
        while True:
            raw = bool(GPIO.input(pin))
            logical = (not raw) if args.active_low else raw
            if prev_logical is None:
                print(f"[init] raw={'HIGH' if raw else 'LOW'} logical={'HIGH' if logical else 'LOW'}")
            elif logical != prev_logical:
                print(
                    f"[edge] logical {('LOW' if prev_logical else 'HIGH')} -> "
                    f"{'HIGH' if logical else 'LOW'}  "
                    f"(raw={'HIGH' if raw else 'LOW'})"
                )
            prev_logical = logical
            time.sleep(max(0.01, float(args.interval)))
    except KeyboardInterrupt:
        print("\n[INTERRUPT] done")
    finally:
        try:
            GPIO.cleanup(pin)
        except Exception:
            pass

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
