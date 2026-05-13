"""Canonical Dynamixel IDs on Nina (arm + hoverboard lean axes)."""

from typing import List

# Body DOF 1..11; hoverboard module lean mounted on AX-18 at 12 (L) and 13 (R).
ARM_MOTOR_IDS: List[int] = list(range(1, 12))
HOVERBOARD_LEAN_IDS: List[int] = [12, 13]

EXPECTED_DYNAMIXEL_IDS: List[int] = ARM_MOTOR_IDS + HOVERBOARD_LEAN_IDS
