"""Microchip AT42QT2120 12-channel QTouch controller (I²C comms mode).

Shares the Nina header I²C bus with ADS1115 (**0x48**) and MPU-9250 (**0x68**):

* **SDA** → pin **3**, **SCL** → pin **5** → default ``/dev/i2c-7``
* AT42QT2120 fixed 7-bit address **0x1C** (not configurable per datasheet)
* Confirm: ``sudo i2cdetect -y -r 7`` → **1c** alongside **48** and **68**

Register map (comms mode) matches Microchip datasheet / community drivers:
CHIP_ID **0x00**, STATUS **0x02**, KEY_STATUS1 **0x03**, KEY_STATUS2 **0x04**.
"""

from __future__ import annotations

import logging
import os
import time
from typing import Optional, Tuple

# Orin NX: pins 3/5 → i2c-7 (same as battery + IMU on this bot).
DEFAULT_TOUCH_I2C_BUS = 7
DEFAULT_TOUCH_I2C_ADDR = 0x1C
_EXPECTED_CHIP_ID = 0x3E

REG_CHIP_ID = 0x00
REG_STATUS = 0x02
REG_KEY_STATUS1 = 0x03
REG_KEY_STATUS2 = 0x04

# STATUS bits
_STATUS_KEYS = 1 << 0
_STATUS_SLIDER = 1 << 1
_STATUS_CALIBRATING = 1 << 7

DEFAULT_TOUCH_TTS = "Please dont touch me"
DEFAULT_MOTOR_GOAL = 2048

log = logging.getLogger("nina.sensors.at42qt2120")


def default_touch_i2c_bus() -> int:
    raw = (os.environ.get("NINA_TOUCH_I2C_BUS") or "").strip()
    if raw:
        try:
            return max(0, int(raw, 0))
        except ValueError:
            pass
    return int(DEFAULT_TOUCH_I2C_BUS)


def is_available(
    bus_num: Optional[int] = None,
    address: int = DEFAULT_TOUCH_I2C_ADDR,
    *,
    probe_attempts: int = 1,
    probe_delay_sec: float = 0.0,
) -> Tuple[bool, str]:
    if bus_num is None:
        bus_num = default_touch_i2c_bus()
    try:
        import smbus2  # noqa: F401
    except Exception as exc:
        return False, f"smbus2 not installed ({exc})"
    dev = f"/dev/i2c-{int(bus_num)}"
    if not os.path.exists(dev):
        return False, f"{dev} not present"
    attempts = max(1, int(probe_attempts))
    delay = max(0.0, float(probe_delay_sec))
    last_msg = (
        f"AT42QT2120 not detected on {dev} @ 0x{int(address) & 0x7F:02X} "
        f"(expected CHIP_ID 0x{_EXPECTED_CHIP_ID:02X})"
    )
    for attempt in range(attempts):
        if probe_at42qt2120_on_bus(int(bus_num), int(address)):
            return True, ""
        if attempt + 1 < attempts and delay > 0:
            time.sleep(delay)
    return False, last_msg


def probe_at42qt2120_on_bus(bus_num: int, address: int = DEFAULT_TOUCH_I2C_ADDR) -> bool:
    """Return True if CHIP_ID matches AT42QT2120 on ``/dev/i2c-<bus_num>``."""
    dev = f"/dev/i2c-{int(bus_num)}"
    if not os.path.exists(dev):
        return False
    addr = int(address) & 0x7F
    try:
        import smbus2  # type: ignore

        bus = smbus2.SMBus(int(bus_num))
        try:
            chip_id = bus.read_byte_data(addr, REG_CHIP_ID)
            return int(chip_id) == _EXPECTED_CHIP_ID
        finally:
            bus.close()
    except Exception:
        return False


class AT42QT2120:
    """Minimal AT42QT2120 reader for touch-detect on ``/dev/i2c-<bus>``."""

    def __init__(self, bus_num: int, address: int = DEFAULT_TOUCH_I2C_ADDR) -> None:
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

    def read_register(self, register: int) -> int:
        if self._bus is None:
            raise RuntimeError("AT42QT2120 not opened")
        return int(self._bus.read_byte_data(self._addr, int(register) & 0xFF))

    def read_chip_id(self) -> int:
        return self.read_register(REG_CHIP_ID)

    def verify_chip_id(self) -> None:
        chip_id = self.read_chip_id()
        if chip_id != _EXPECTED_CHIP_ID:
            raise RuntimeError(
                f"AT42QT2120 CHIP_ID=0x{chip_id:02X}, expected 0x{_EXPECTED_CHIP_ID:02X}"
            )

    def read_status(self) -> int:
        return self.read_register(REG_STATUS)

    def is_calibrating(self) -> bool:
        return bool(self.read_status() & _STATUS_CALIBRATING)

    def keys_pressed(self) -> bool:
        """True when STATUS bit 0 reports any key channel active."""
        return bool(self.read_status() & _STATUS_KEYS)

    def slider_pressed(self) -> bool:
        return bool(self.read_status() & _STATUS_SLIDER)

    def read_key_mask(self) -> int:
        """12-bit mask: bit *n* set when key *n* is touched (keys 0..11)."""
        lo = self.read_register(REG_KEY_STATUS1)
        hi = self.read_register(REG_KEY_STATUS2)
        return ((hi & 0x0F) << 8) | (lo & 0xFF)

    def any_touch(self) -> bool:
        """True if STATUS or key mask reports activity (bench + safety)."""
        if self.keys_pressed():
            return True
        return self.read_key_mask() != 0

    def touch_snapshot(self) -> dict:
        """Raw register view for bench/debug (STATUS vs key mask)."""
        st = self.read_status()
        mask = self.read_key_mask()
        return {
            "status": st,
            "mask": mask,
            "keys_pressed": bool(st & _STATUS_KEYS),
            "slider_pressed": bool(st & _STATUS_SLIDER),
            "calibrating": bool(st & _STATUS_CALIBRATING),
            "any_touch": self.any_touch(),
        }

    def touched_channel_names(self) -> list[int]:
        mask = self.read_key_mask()
        return [ch for ch in range(12) if mask & (1 << ch)]
