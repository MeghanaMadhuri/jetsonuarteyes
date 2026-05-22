"""Unit tests for DriveController.is_in_motion()."""

from __future__ import annotations

import unittest
from unittest.mock import MagicMock

from sirena_ui.workers.drive_controller import DriveController


class DriveControllerInMotionTests(unittest.TestCase):
    def test_idle_brake_not_in_motion(self) -> None:
        dc = DriveController.__new__(DriveController)
        dc._lock = __import__("threading").RLock()
        dc._state = {
            "brake": True,
            "direction": "idle",
        }
        dc._active_drive = None
        dc._nav = None
        dc._injected_nav = None
        self.assertFalse(dc.is_in_motion())

    def test_forward_active_drive_in_motion(self) -> None:
        dc = DriveController.__new__(DriveController)
        dc._lock = __import__("threading").RLock()
        dc._state = {
            "brake": False,
            "direction": "forward",
        }
        dc._active_drive = ("forward", 50, "forward", 50)
        dc._nav = None
        dc._injected_nav = None
        self.assertTrue(dc.is_in_motion())

    def test_pulse_series_counts_as_motion(self) -> None:
        dc = DriveController.__new__(DriveController)
        dc._lock = __import__("threading").RLock()
        dc._state = {
            "brake": False,
            "direction": "forward",
        }
        dc._active_drive = None
        nav = MagicMock()
        nav.is_straight_pulse_series_active.return_value = True
        dc._nav = nav
        dc._injected_nav = None
        self.assertTrue(dc.is_in_motion())

    def test_saved_sequence_counts_as_motion_while_direction_idle(self) -> None:
        dc = DriveController.__new__(DriveController)
        dc._lock = __import__("threading").RLock()
        dc._state = {
            "brake": False,
            "direction": "idle",
        }
        dc._active_drive = None
        nav = MagicMock()
        nav.is_straight_pulse_series_active.return_value = False
        nav.is_saved_sequence_active.return_value = True
        dc._nav = nav
        dc._injected_nav = None
        self.assertTrue(dc.is_in_motion())


if __name__ == "__main__":
    unittest.main()
