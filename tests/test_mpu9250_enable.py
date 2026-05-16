"""MPU-9250 monitor env gate (no hardware)."""

from __future__ import annotations

import os

from nina.sensors.mpu9250 import is_imu_monitor_enabled


def test_imu_monitor_disabled_when_unset() -> None:
    os.environ.pop("NINA_IMU_MPU9250_ENABLE", None)
    assert is_imu_monitor_enabled() is False


def test_imu_monitor_enabled_when_truthy() -> None:
    os.environ["NINA_IMU_MPU9250_ENABLE"] = "1"
    try:
        assert is_imu_monitor_enabled() is True
    finally:
        os.environ.pop("NINA_IMU_MPU9250_ENABLE", None)
