"""Unit tests for IR obstacle threshold and motion-gated sensor lifecycle."""

from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

from nina.config.settings import IrObstacleStopSettings
from nina.sensors.ir_obstacle_stop_monitor import (
    IrObstacleStopMonitor,
    obstacle_debounce_step,
)


class IrObstacleMotionGatingTests(unittest.TestCase):
    def test_debounce_at_100cm_threshold(self) -> None:
        fire, n = obstacle_debounce_step(
            950, threshold_mm=1000, debounce_reads=2, consecutive_hits=0
        )
        self.assertFalse(fire)
        self.assertEqual(n, 1)
        fire2, _ = obstacle_debounce_step(
            900, threshold_mm=1000, debounce_reads=2, consecutive_hits=n
        )
        self.assertTrue(fire2)

    @patch("nina.sensors.ir_obstacle_stop_monitor.GP2Y0E02B")
    @patch("nina.sensors.ir_obstacle_stop_monitor.is_available", return_value=(True, ""))
    def test_sensor_open_close_with_motion(self, _avail, mock_gp2y) -> None:
        svc = MagicMock()
        svc.settings.ir_obstacle_stop = IrObstacleStopSettings(
            enabled=True,
            i2c_bus=7,
            i2c_address=0x40,
            threshold_mm=1000,
            debounce_reads=2,
            cooldown_sec=15.0,
            poll_interval_sec=0.02,
            tts_text="There is an obstacle in my way",
        )
        sensor = mock_gp2y.return_value

        mon = IrObstacleStopMonitor(svc, in_motion_fn=lambda: False)
        mon._open_sensor()
        sensor.open.assert_called_once()

        mon._close_sensor()
        sensor.close.assert_called_once()
        self.assertFalse(mon._sensor_open)

        mon._open_sensor()
        self.assertEqual(sensor.open.call_count, 2)


if __name__ == "__main__":
    unittest.main()
