"""Tests for ``robot_set_brake`` (tablet gateway parity with kiosk ``DriveController.set_brake``)."""

from __future__ import annotations

import unittest
from unittest.mock import MagicMock

from sirena_ui.android_gateway.drive_http import robot_set_brake


class TestRobotSetBrake(unittest.TestCase):
    def test_blocks_release_when_autonomy_active(self) -> None:
        svc = MagicMock()
        svc.autonomy.is_enabled.return_value = True
        out = robot_set_brake(svc, on=False)
        self.assertFalse(out["ok"])
        self.assertIn("autonomy", out["error"])
        svc.drive.set_brake.assert_not_called()

    def test_calls_set_brake_when_release_allowed(self) -> None:
        svc = MagicMock()
        svc.autonomy.is_enabled.return_value = False
        dc = MagicMock()
        dc.state.return_value = {"brake": False}
        svc.drive = dc
        out = robot_set_brake(svc, on=False)
        self.assertTrue(out["ok"])
        self.assertFalse(out["brake"])
        dc.ensure_hardware.assert_called_once()
        dc.set_brake.assert_called_once_with(False)

    def test_engage_allowed_under_autonomy(self) -> None:
        svc = MagicMock()
        svc.autonomy.is_enabled.return_value = True
        dc = MagicMock()
        dc.state.return_value = {"brake": True}
        svc.drive = dc
        out = robot_set_brake(svc, on=True)
        self.assertTrue(out["ok"])
        dc.set_brake.assert_called_once_with(True)


if __name__ == "__main__":
    unittest.main()
