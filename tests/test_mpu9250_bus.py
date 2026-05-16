"""IMU I2C bus defaults (Orin Nano header pins 3+5 → bus 7)."""

from __future__ import annotations

import os

from nina.sensors.mpu9250 import (
    DEFAULT_IMU_I2C_BUS,
    MPU9250,
    _ACCEPTED_WHO_AM_I,
    _WHO_AM_I_MPU6050,
    _WHO_AM_I_MPU9250,
    is_mpu9250_available,
)


def test_default_imu_i2c_bus_is_seven() -> None:
    assert DEFAULT_IMU_I2C_BUS == 7


def test_is_available_uses_bus_env(monkeypatch) -> None:
    monkeypatch.delenv("NINA_IMU_I2C_BUS", raising=False)
    # Default path uses bus 7 — only assert env override is honored when set.
    monkeypatch.setenv("NINA_IMU_I2C_BUS", "99")
    ok, msg = is_mpu9250_available()
    assert not ok
    assert "i2c-99" in msg or "99" in msg


def test_who_am_i_accepts_mpu6050_and_mpu9250() -> None:
    assert _WHO_AM_I_MPU6050 in _ACCEPTED_WHO_AM_I
    assert _WHO_AM_I_MPU9250 in _ACCEPTED_WHO_AM_I


def test_verify_who_am_i_sets_chip_label() -> None:
    class FakeBus:
        def __init__(self, who: int) -> None:
            self._who = who

        def read_byte_data(self, _addr: int, reg: int) -> int:
            assert reg == 0x75
            return self._who

    for who, expected in ((0x68, "MPU-6050"), (0x71, "MPU-9250")):
        imu = MPU9250(7, 0x68)
        imu._bus = FakeBus(who)  # type: ignore[assignment]
        imu.verify_who_am_i()
        assert imu._chip_label == expected
