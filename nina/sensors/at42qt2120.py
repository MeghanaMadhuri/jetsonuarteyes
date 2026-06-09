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
REG_CALIBRATION = 0x06
REG_RESET = 0x07
REG_DETECT_THRESHOLD = 0x10  # KEY 0; keys 1..11 at 0x11..0x1B

# DMR firmware treats a keystatus byte of 0xFF as no touch on that register.
_KEYSTATUS_IDLE_BYTE = 0xFF

# Soft reset: watchdog ~125 ms then full reset; chip NACKs ~200 ms (datasheet).
_DMR_RESET_QUIET_SEC = 0.35

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

    def write_register(self, register: int, value: int) -> None:
        if self._bus is None:
            raise RuntimeError("AT42QT2120 not opened")
        self._bus.write_byte_data(
            self._addr, int(register) & 0xFF, int(value) & 0xFF
        )

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

    def wait_for_calibration(self, *, timeout_sec: float = 2.0) -> None:
        """Block until the CALIBRATE status bit clears."""
        deadline = time.monotonic() + max(0.1, float(timeout_sec))
        while time.monotonic() < deadline:
            if not self.is_calibrating():
                return
            time.sleep(0.02)
        raise RuntimeError("AT42QT2120 calibration timed out")

    def recalibrate(self, *, timeout_sec: float = 2.0) -> None:
        """DMR-style recalibration command (register 0x06)."""
        self.write_register(REG_CALIBRATION, 0x07)
        self.wait_for_calibration(timeout_sec=timeout_sec)

    def dmr_bootstrap(
        self,
        *,
        detect_threshold: int = 25,
        touch_channel: int = 0,
        reset_sleep_sec: float = _DMR_RESET_QUIET_SEC,
        cal_timeout_sec: float = 2.0,
    ) -> None:
        """Reset, calibrate, and set per-key detect threshold (DMR parity).

        Matches fleet bench: soft reset → calibrate → DTHR for ``touch_channel``.
        Raise ``detect_threshold`` (default 25) if the pad is too sensitive.
        """
        self.write_register(REG_RESET, 0x07)
        quiet = float(reset_sleep_sec)
        if quiet < 0:
            quiet = _DMR_RESET_QUIET_SEC
        if quiet > 0:
            time.sleep(quiet)
        self.write_register(REG_CALIBRATION, 0x07)
        self.wait_for_calibration(timeout_sec=cal_timeout_sec)
        ch = max(0, min(11, int(touch_channel)))
        thr = max(1, min(255, int(detect_threshold)))
        self.write_register(REG_DETECT_THRESHOLD + ch, thr)
        log.info(
            "AT42QT2120 DMR bootstrap done (ch=%d threshold=%d)",
            ch,
            thr,
        )

    def read_key_status_byte(self, channel: int = 0) -> int:
        """Raw KEY_STATUS byte for ``channel`` (DMR: 0xFF = idle)."""
        ch = int(channel)
        if ch < 0 or ch > 11:
            raise ValueError("channel must be 0..11")
        if ch > 7:
            return self.read_register(REG_KEY_STATUS2)
        return self.read_register(REG_KEY_STATUS1)

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

    def is_key_pressed(self, channel: int = 0) -> bool:
        """DMR-style touch read: KEY_STATUS byte idle ``0xFF`` or ``0x00``, bit set = pressed."""
        ch = int(channel)
        if ch < 0 or ch > 11:
            return False
        if ch > 7:
            data = int(self.read_register(REG_KEY_STATUS2))
            ch -= 8
        else:
            data = int(self.read_register(REG_KEY_STATUS1))
        if data == _KEYSTATUS_IDLE_BYTE or data == 0x00:
            return False
        return bool(data & (1 << ch))

    def any_touch(self) -> bool:
        """True if STATUS or (optionally) key mask reports activity."""
        if self.keys_pressed():
            return True
        return self.read_key_mask() != 0

    def status_stuck_idle(self) -> bool:
        """True when STATUS keys bit is set but the 12-bit mask is zero.

        This pattern means the electrode back is coupled to metal/chassis —
        not a real capacitive touch on a wired channel.
        """
        return self.keys_pressed() and self.read_key_mask() == 0

    def touch_active(
        self,
        *,
        use_key_mask: bool = True,
        channel_mask: int = 0xFFF,
    ) -> bool:
        """Detect touch via STATUS and/or filtered key-mask bits."""
        if self.keys_pressed():
            return True
        if use_key_mask:
            return (self.read_key_mask() & (int(channel_mask) & 0xFFF)) != 0
        return False

    def touch_snapshot(self, *, channel: int = 0) -> dict:
        """Raw register view for bench/debug (STATUS vs key mask vs DMR keystatus)."""
        st = self.read_status()
        mask = self.read_key_mask()
        ks = self.read_key_status_byte(channel)
        ch = max(0, min(11, int(channel)))
        return {
            "status": st,
            "mask": mask,
            "key_status_byte": ks,
            "key_pressed_dmr": self.is_key_pressed(ch),
            "keys_pressed": bool(st & _STATUS_KEYS),
            "slider_pressed": bool(st & _STATUS_SLIDER),
            "calibrating": bool(st & _STATUS_CALIBRATING),
            "any_touch": self.any_touch(),
        }

    def touched_channel_names(self) -> list[int]:
        mask = self.read_key_mask()
        return [ch for ch in range(12) if mask & (1 << ch)]
