"""Bench test: one HC-SR04 on Jetson GPIO (Trig + Echo).

Defaults match **physical pins 7 and 21** on the Orin Nano / Orin NX 40-pin
header (``Jetson.GPIO`` **BCM 4** = board pin **7** for Trig, **BCM 9** = board
pin **21** for Echo) — same as ``NINA_OBSTACLE_HCSR04_TRIG`` /
``NINA_OBSTACLE_HCSR04_ECHO`` in ``ObstacleStopSettings``.

**Wiring**

- **Vcc** → 5 V (module needs stable 5 V).
- **Gnd** → Jetson GND (common with the board).
- **Trig** → physical pin **7** (BCM **4** by default).
- **Echo** → physical pin **21** (BCM **9** by default) — HC-SR04 Echo is **5 V**;
  use a **level shifter** or **resistor divider** so the Jetson GPIO sees ≤ 3.3 V
  (see ``nina/sensors/obstacle_stop_monitor.py`` module doc).

**Run on the Jetson** (repo root on ``PYTHONPATH``):

    python3 -m nina.app.hcsr04_ping_test
    python3 -m nina.app.hcsr04_ping_test --samples 30 --interval 0.15
    python3 -m nina.app.hcsr04_ping_test --trig-bcm 4 --echo-bcm 9

Stop with Ctrl-C. Calls ``GPIO.cleanup()`` on exit so pins are released.

Non-Jetson hosts exit with a clear message (no hardware).
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from typing import Optional

from nina.controllers.navigation_manager import jetson_orin_nano_board_pin
from nina.sensors.hcsr04 import (
    PULSE_TIMEOUT_S,
    SOUND_SPEED_MM_PER_S,
    TRIGGER_PULSE_S,
    is_jetson_gpio_available,
)


def _ping_mm(gpio, trig: int, echo: int) -> Optional[int]:
    """Return one-way compensated range in mm, or ``None`` on timeout."""
    gpio.output(trig, gpio.LOW)
    time.sleep(2e-6)
    gpio.output(trig, gpio.HIGH)
    time.sleep(TRIGGER_PULSE_S)
    gpio.output(trig, gpio.LOW)

    t_start = time.monotonic()
    deadline = t_start + PULSE_TIMEOUT_S

    while gpio.input(echo) == 0:
        if time.monotonic() > deadline:
            return None
    t_rise = time.monotonic()

    while gpio.input(echo) == 1:
        if time.monotonic() > deadline:
            return None
    t_fall = time.monotonic()

    duration = t_fall - t_rise
    mm = int(duration * SOUND_SPEED_MM_PER_S / 2.0)
    if mm <= 0 or mm > 5000:
        return None
    return mm


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--trig-bcm",
        type=int,
        default=4,
        help="BCM number for HC-SR04 Trig (default 4 → physical pin 7).",
    )
    parser.add_argument(
        "--echo-bcm",
        type=int,
        default=9,
        help="BCM number for HC-SR04 Echo (default 9 → physical pin 21).",
    )
    parser.add_argument(
        "--samples",
        type=int,
        default=25,
        help="How many pings to print (default 25).",
    )
    parser.add_argument(
        "--interval",
        type=float,
        default=0.08,
        help="Seconds between pings (default 0.08; allow echo decay).",
    )
    parser.add_argument(
        "--model",
        type=str,
        default="",
        help="Optional Jetson.GPIO model name (default: env NINA_JETSON_MODEL or ORIN_NANO).",
    )
    args = parser.parse_args(argv)

    ok, msg = is_jetson_gpio_available()
    if not ok:
        print(f"HC-SR04 test: not available — {msg}", file=sys.stderr)
        return 1

    os.environ.setdefault(
        "JETSON_MODEL_NAME",
        (args.model.strip() or os.environ.get("NINA_JETSON_MODEL", "JETSON_ORIN_NANO")),
    )

    import Jetson.GPIO as GPIO  # type: ignore

    trig = int(args.trig_bcm)
    echo = int(args.echo_bcm)
    phys_t = jetson_orin_nano_board_pin(trig)
    phys_e = jetson_orin_nano_board_pin(echo)

    print(
        f"HC-SR04 ping test: Trig BCM {trig} (40-pin phys {phys_t if phys_t is not None else '?'}) | "
        f"Echo BCM {echo} (40-pin phys {phys_e if phys_e is not None else '?'})"
    )
    print(f"JETSON_MODEL_NAME={os.environ.get('JETSON_MODEL_NAME')}")
    print("Pinging… (Ctrl-C to stop)\n")

    GPIO.setmode(GPIO.BCM)
    GPIO.setwarnings(False)
    try:
        GPIO.setup(trig, GPIO.OUT, initial=GPIO.LOW)
        GPIO.setup(echo, GPIO.IN)

        timeouts = 0
        for i in range(max(1, int(args.samples))):
            mm = _ping_mm(GPIO, trig, echo)
            if mm is None:
                timeouts += 1
                print(f"  #{i + 1:3d}  timeout (no echo or out of range)")
            else:
                print(f"  #{i + 1:3d}  {mm:5d} mm")
            time.sleep(max(0.02, float(args.interval)))

        print()
        if timeouts == int(args.samples):
            print("FAIL: every ping timed out — check Trig/Echo swap, GND, 5 V, and Echo level shifting.")
            return 2
        if timeouts > 0:
            print(f"WARN: {timeouts} timeout(s); others OK.")
        else:
            print("OK: sensor responded on every sample.")
        return 0
    except KeyboardInterrupt:
        print("\nStopped by user.")
        return 0
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 3
    finally:
        try:
            GPIO.cleanup()
        except Exception:
            pass


if __name__ == "__main__":
    raise SystemExit(main())
