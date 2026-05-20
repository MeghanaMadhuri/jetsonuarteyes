"""Tests for ``robot_set_brake`` (tablet gateway parity with kiosk ``DriveController.set_brake``)."""

from __future__ import annotations

import unittest
from unittest.mock import MagicMock

import json
import math

from sirena_ui.android_gateway.drive_http import (
    _json_safe_float,
    _sanitize_drive_status_body,
    bootstrap_tablet_drive,
    drive_status_payload,
    navigation_hw_status,
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
        svc.bus_ready = True
        dc = MagicMock()
        dc._nav = object()
        dc.state.return_value = {"brake": False, "connected": True}
        svc.drive = dc
        svc._drive = dc
        out = robot_set_brake(svc, on=False)
        self.assertTrue(out["ok"])
        self.assertFalse(out["brake"])
        dc.ensure_hardware.assert_not_called()
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

    def test_release_kicks_init_without_waiting_when_bus_ready(self) -> None:
        svc = MagicMock()
        svc.autonomy.is_enabled.return_value = False
        svc.bus_ready = True
        dc = MagicMock()
        dc._nav = None
        dc.state.return_value = {"driver_message": "", "brake": True}
        svc.drive = dc
        svc._drive = None

        out = robot_set_brake(svc, on=False)

        self.assertFalse(out["ok"])
        self.assertTrue(out["hardware_initializing"])
        dc.ensure_hardware.assert_called_once()
        dc.set_brake.assert_not_called()

    def test_release_does_not_touch_drive_when_bus_still_initializing(self) -> None:
        svc = MagicMock()
        svc.autonomy.is_enabled.return_value = False
        svc.bus_ready = False
        svc._drive = None

        out = robot_set_brake(svc, on=False)

        self.assertFalse(out["ok"])
        self.assertTrue(out["hardware_initializing"])
        svc.ensure_bus.assert_not_called()
        svc.drive.set_brake.assert_not_called()


class TestDriveHttpStatusIsReadOnly(unittest.TestCase):
    def test_status_does_not_prime_bus_or_drive(self) -> None:
        svc = MagicMock()
        svc.bus_ready = False
        svc._drive = None

        out = navigation_hw_status(svc)

        self.assertFalse(out["connected"])
        self.assertEqual(out["message"], "Drive controller not started")
        svc.ensure_bus.assert_not_called()
        svc.start_mpu9250_imu_monitor.assert_not_called()
        svc.drive.ensure_hardware.assert_not_called()

    def test_extended_status_handles_missing_drive_without_creating_it(self) -> None:
        svc = MagicMock()
        svc.bus_ready = True
        svc._drive = None

        out = drive_status_payload(svc)

        self.assertFalse(out["connected"])
        self.assertEqual(out["direction"], "idle")
        self.assertFalse(out["straight_pulse_active"])
        svc.ensure_bus.assert_not_called()
        svc.drive.ensure_hardware.assert_not_called()

    def test_bootstrap_waits_for_existing_bus_before_touching_drive(self) -> None:
        svc = MagicMock()
        svc.bus_ready = False
        svc._drive = None

        bootstrap_tablet_drive(svc)

        svc.ensure_bus.assert_not_called()
        svc.drive.ensure_hardware.assert_not_called()


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
