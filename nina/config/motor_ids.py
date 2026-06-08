"""Canonical Dynamixel IDs on Nina (arm + hoverboard lean axes)."""

from __future__ import annotations

import os
from typing import List

# Body DOF 1..11; hoverboard driver tilt: MX-28 at 12 (L) and 13 (R).
ARM_MOTOR_IDS: List[int] = list(range(1, 12))
HOVERBOARD_LEAN_IDS: List[int] = [12, 13]

# Recorded actions and playback: arm/neck only (not BLDC lean axes).
ACTION_MOTOR_IDS: List[int] = ARM_MOTOR_IDS

EXPECTED_DYNAMIXEL_IDS: List[int] = ARM_MOTOR_IDS + HOVERBOARD_LEAN_IDS


def dynamixel_expected_ids_from_env() -> List[int]:
    """IDs the bus manager pings at startup (default all 13; lean-only: ``12,13``)."""
    raw = (os.environ.get("NINA_DXL_EXPECTED_IDS") or "").strip()
    if not raw:
        return list(EXPECTED_DYNAMIXEL_IDS)
    ids: List[int] = []
    for part in raw.replace(" ", "").split(","):
        if not part:
            continue
        try:
            sid = int(part)
        except ValueError:
            continue
        if 0 < sid < 254:
            ids.append(sid)
    return sorted(set(ids)) if ids else list(EXPECTED_DYNAMIXEL_IDS)
