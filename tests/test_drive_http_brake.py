"""Tests for ``robot_set_brake`` (tablet gateway parity with kiosk ``DriveController.set_brake``)."""

from __future__ import annotations

import unittest
from unittest.mock import MagicMock

import json
import math

from sirena_ui.android_gateway.drive_http import (
    _json_safe_float,
    _sanitize_drive_status_body,
    robot_set_brake,
)


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


class TestDriveStatusJson(unittest.TestCase):
    def test_json_safe_float_rejects_nan(self) -> None:
        self.assertIsNone(_json_safe_float(float("nan")))
        self.assertEqual(_json_safe_float(3.5), 3.5)

    def test_sanitize_body_is_json_encodable(self) -> None:
        body = _sanitize_drive_status_body(
            {
                "heading_deg": float("nan"),
                "distance_m": float("inf"),
                "imu_drift_deg": 1.0,
            }
        )
        json.dumps(body)
        self.assertIsNone(body["heading_deg"])
        self.assertIsNone(body["distance_m"])
        self.assertEqual(body["imu_drift_deg"], 1.0)


if __name__ == "__main__":
    unittest.main()
