"""PWM % used by Drive \"Straight\" bench and matching visual servoing.

Avoid importing ``drive_controller`` (PyQt) so headless tests and ArUco
logic can load this module alone. Values match ``MIN_SPEED_PCT`` /
``MAX_SPEED_PCT`` in ``drive_controller``.
"""

from __future__ import annotations

import os

# Mirrors sirena_ui.workers.drive_controller MIN/MAX for the GUI envelope.
_MIN_STRAIGHT_PCT = 8
_DEFAULT_STRAIGHT_PCT = 14


def straight_bench_speed_pct() -> int:
    try:
        raw = int(
            os.environ.get("NINA_STRAIGHT_TEST_SPEED_PCT", str(_DEFAULT_STRAIGHT_PCT))
        )
    except ValueError:
        raw = _DEFAULT_STRAIGHT_PCT
    return max(_MIN_STRAIGHT_PCT, min(100, raw))
