"""MPU-9250 I²C driver + yaw-drift tracker for straight-line study (gyro Z integration).

Reads gyroscope Z only (no magnetometer fusion). **Yaw drift** is the integral of
gyro Z minus a bias learned briefly at monitor startup while the node is assumed
stationary.

Environment
------------
* ``NINA_IMU_MPU9250_ENABLE`` — ``1`` / ``true`` / ``on`` to start the monitor at
  app init (default off so dev laptops without I²C do not error).
* ``NINA_IMU_I2C_BUS`` — default ``1`` (``/dev/i2c-1``).
* ``NINA_IMU_I2C_ADDR`` — default ``0x68``.
* ``NINA_IMU_POLL_HZ`` — sample rate (default ``100``).
* ``NINA_IMU_CALIB_SEC`` — seconds of gyro bias capture at startup (default ``0.4``).
* ``NINA_IMU_YAW_DISPLAY_SIGN`` — multiply integrated yaw for UI (default ``1``;
  set ``-1`` if left/right feels inverted vs your mount).
* ``NINA_IMU_DRIFT_DEADBAND_DEG`` — below this magnitude UI shows ``CENTER``
  (default ``0.5``).

I²C wiring matches MPU-9250 breakout: 3.3 V, GND, SDA, SCL, AD0→GND for 0x68.
"""

from __future__ import annotations

import logging
import os
import threading
import time
from dataclasses import dataclass
from typing import Optional, Tuple

log = logging.getLogger("nina.sensors.mpu9250")

# MPU-9250 (I2C primary) registers
_REG_CONFIG = 0x1A
_REG_GYRO_CONFIG = 0x1B
_REG_ACCEL_CONFIG = 0x1C
_REG_ACCEL_XOUT_H = 0x3B
_REG_GYRO_XOUT_H = 0x43
_REG_PWR_MGMT_1 = 0x6B
_REG_PWR_MGMT_2 = 0x6C
_REG_WHO_AM_I = 0x75

_WHO_AM_I_MPU9250 = 0x71

# Gyro ±250 °/s → 131 LSB/(°/s)
_GYRO_LSB_PER_DPS = 131.0


def _env_truthy(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name, "")
    if raw is None or raw.strip() == "":
        return default
    return raw.strip().lower() in ("1", "true", "yes", "y", "on")


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        return int(raw.strip(), 0)
    except ValueError:
        return default


def _env_float(name: str, default: float) -> float:
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        return float(raw.strip())
    except ValueError:
        return default


def is_imu_monitor_enabled() -> bool:
    """True when ``NINA_IMU_MPU9250_ENABLE`` requests the background IMU thread."""
    return _env_truthy("NINA_IMU_MPU9250_ENABLE", False)


def is_mpu9250_available(bus_num: Optional[int] = None) -> Tuple[bool, str]:
    if bus_num is None:
        bus_num = _env_int("NINA_IMU_I2C_BUS", 1)
    try:
        import smbus2  # noqa: F401
    except Exception as exc:
        return False, f"smbus2 not installed ({exc})"
    dev = f"/dev/i2c-{bus_num}"
    if not os.path.exists(dev):
        return False, f"{dev} not present"
    return True, ""


def _combine_i16(hi: int, lo: int) -> int:
    v = (hi << 8) | lo
    if v >= 0x8000:
        v -= 0x10000
    return v


@dataclass
class ImuDriftSnapshot:
    ok: bool
    """False during startup bias capture or I²C errors."""

    message: str
    yaw_drift_deg: float
    """Integrated yaw about IMU Z since straight baseline (display sign applied)."""

    yaw_rate_dps: float
    """Instantaneous gyro Z (°/s), bias subtracted, raw mount sign."""

    drift_side: str
    """``left`` | ``right`` | ``center`` | ``n/a``."""

    bias_z_dps: float
    """Estimated gyro Z bias (°/s) from startup calibration."""


class MPU9250:
    """Minimal MPU-9250 on I²C: init + burst gyro read."""

    def __init__(self, bus_num: int, address: int = 0x68) -> None:
        self._bus_num = int(bus_num)
        self._addr = int(address) & 0x7F
        self._bus = None

    def open(self) -> None:
        import smbus2  # type: ignore

        self._bus = smbus2.SMBus(self._bus_num)

    def close(self) -> None:
        if self._bus is not None:
            try:
                self._bus.close()
            except Exception:
                pass
            self._bus = None

    def verify_who_am_i(self) -> None:
        if self._bus is None:
            raise RuntimeError("MPU9250 not opened")
        v = self._bus.read_byte_data(self._addr, _REG_WHO_AM_I)
        if v != _WHO_AM_I_MPU9250:
            raise RuntimeError(
                f"WHO_AM_I = 0x{v:02X}, expected 0x{_WHO_AM_I_MPU9250:02X} (MPU-9250)"
            )

    def configure(self) -> None:
        if self._bus is None:
            raise RuntimeError("MPU9250 not opened")
        # Exit sleep, use best clock source
        self._bus.write_byte_data(self._addr, _REG_PWR_MGMT_1, 0x01)
        time.sleep(0.05)
        self._bus.write_byte_data(self._addr, _REG_PWR_MGMT_2, 0x00)
        # DLPF: 41 Hz gyro (approx), 11 Hz temp — smooth for integration
        self._bus.write_byte_data(self._addr, _REG_CONFIG, 0x03)
        # Gyro ±250 °/s
        self._bus.write_byte_data(self._addr, _REG_GYRO_CONFIG, 0x00)
        # Accel ±2 g (not used for drift UI; keeps device in known state)
        self._bus.write_byte_data(self._addr, _REG_ACCEL_CONFIG, 0x00)
        time.sleep(0.05)

    def read_gyro_z_dps(self) -> float:
        """Return ω_z in °/s, +Z up right-hand rule."""
        if self._bus is None:
            raise RuntimeError("MPU9250 not opened")
        data = self._bus.read_i2c_block_data(self._addr, _REG_GYRO_XOUT_H, 6)
        gz = _combine_i16(data[4], data[5])
        return float(gz) / _GYRO_LSB_PER_DPS


class Mpu9250DriftMonitor:
    """Background sampling + straight-test yaw drift integration."""

    def __init__(self) -> None:
        self._bus_num = _env_int("NINA_IMU_I2C_BUS", 1)
        self._addr = _env_int("NINA_IMU_I2C_ADDR", 0x68)
        self._poll_hz = max(20.0, min(250.0, _env_float("NINA_IMU_POLL_HZ", 100.0)))
        self._calib_sec = max(0.0, min(3.0, _env_float("NINA_IMU_CALIB_SEC", 0.4)))
        self._yaw_sign = _env_float("NINA_IMU_YAW_DISPLAY_SIGN", 1.0)
        self._deadband = max(0.0, _env_float("NINA_IMU_DRIFT_DEADBAND_DEG", 0.5))

        self._imu: Optional[MPU9250] = None
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._lock = threading.Lock()

        self._bias_z = 0.0
        self._calibrating = True
        self._yaw_accum = 0.0
        self._track_straight = False
        self._last_gz = 0.0
        self._last_err = ""

    def start(self) -> None:
        if self._thread is not None:
            return
        ok, msg = is_mpu9250_available(self._bus_num)
        if not ok:
            raise RuntimeError(msg)
        imu = MPU9250(self._bus_num, self._addr)
        imu.open()
        try:
            imu.verify_who_am_i()
            imu.configure()
        except Exception:
            imu.close()
            raise
        self._imu = imu
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._run, name="Mpu9250DriftMonitor", daemon=True
        )
        self._thread.start()
        log.info(
            "MPU-9250 drift monitor started (i2c-%s 0x%02X, %.0f Hz)",
            self._bus_num,
            self._addr,
            self._poll_hz,
        )

    def stop(self) -> None:
        self._stop.set()
        t = self._thread
        self._thread = None
        if t is not None:
            t.join(timeout=3.0)
            if t.is_alive():
                log.warning("MPU-9250 monitor thread did not exit in time")
        if self._imu is not None:
            try:
                self._imu.close()
            except Exception:
                pass
            self._imu = None
        with self._lock:
            self._calibrating = True
            self._track_straight = False
        log.info("MPU-9250 drift monitor stopped")

    def begin_straight_leg(self) -> None:
        """Reset integrated yaw; start accumulating until ``end_straight_leg``."""
        with self._lock:
            self._yaw_accum = 0.0
            self._track_straight = True

    def end_straight_leg(self) -> None:
        """Stop accumulating (snapshot retains last yaw until next begin)."""
        with self._lock:
            self._track_straight = False

    def snapshot(self) -> ImuDriftSnapshot:
        with self._lock:
            cal = self._calibrating
            err = self._last_err
            bias = self._bias_z
            gz = self._last_gz
            if cal:
                return ImuDriftSnapshot(
                    ok=False,
                    message=err or "calibrating gyro bias",
                    yaw_drift_deg=0.0,
                    yaw_rate_dps=gz,
                    drift_side="n/a",
                    bias_z_dps=bias,
                )
            yaw = self._yaw_accum * self._yaw_sign
            side = self._drift_side(yaw)
            if not self._track_straight:
                return ImuDriftSnapshot(
                    ok=True,
                    message=err or "idle",
                    yaw_drift_deg=yaw,
                    yaw_rate_dps=gz,
                    drift_side="n/a",
                    bias_z_dps=bias,
                )
            return ImuDriftSnapshot(
                ok=True,
                message=err or "",
                yaw_drift_deg=yaw,
                yaw_rate_dps=gz,
                drift_side=side,
                bias_z_dps=bias,
            )

    def _drift_side(self, yaw_display: float) -> str:
        if abs(yaw_display) <= self._deadband:
            return "center"
        return "left" if yaw_display < 0 else "right"

    def _run(self) -> None:
        imu = self._imu
        if imu is None:
            return
        period = 1.0 / self._poll_hz
        t_prev = time.monotonic()

        # Bias capture: average gyro Z at startup
        calib_deadline = time.monotonic() + self._calib_sec
        bias_sum = 0.0
        bias_n = 0

        while not self._stop.is_set():
            t = time.monotonic()
            dt = max(0.0, min(0.05, t - t_prev))
            t_prev = t
            try:
                gz_raw = imu.read_gyro_z_dps()
                self._last_err = ""
            except Exception as exc:
                self._last_err = str(exc)
                log.debug("MPU9250 read failed: %s", exc)
                if self._stop.wait(period):
                    return
                continue

            if t < calib_deadline:
                bias_sum += gz_raw
                bias_n += 1
                with self._lock:
                    self._calibrating = True
                    self._last_gz = gz_raw
                if self._stop.wait(period):
                    return
                continue

            if bias_n > 0:
                bz = bias_sum / float(bias_n)
            else:
                bz = 0.0
            with self._lock:
                self._bias_z = bz
                self._calibrating = False
                gz0 = gz_raw - bz
                self._last_gz = gz0
                if self._track_straight:
                    self._yaw_accum += gz0 * dt

            slip = time.monotonic() - t
            wait = max(0.0, period - slip)
            if self._stop.wait(wait):
                return
