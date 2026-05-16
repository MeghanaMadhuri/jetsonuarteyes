"""Minimal TI ADS1115 16-bit I²C ADC (single-ended reads via ``smbus2``).

Used for scaled pack voltage: hardware must divide the battery down so the
AIN pin stays **below the ADS1115 VDD** (typically 3.3 V on the Jetson header).

Connections (Nina / Orin NX — header pins **3** SDA + **5** SCL)
----------------------------------------------------------------
* **Default bus** ``/dev/i2c-7`` (``NINA_BATTERY_I2C_BUS=7``). Confirm with
  ``sudo i2cdetect -y -r 7`` → **0x48** for ADS1115 (ADDR→GND).
* **IMU (MPU-9250 @ 0x68)** and **ADS1115 (@ 0x48)** may share the same SDA/SCL
  on pins **3** and **5** (different I²C addresses).
* **ADS1115 VDD** → **3.3 V**; **GND** → GND; **ADDR** → GND (**0x48**).
* **AIN0** → divider centre: **218 kΩ** BAT+→AIN0, **33 kΩ** AIN0→GND
  (``V_pack = V_ain * 251/33``).

Alternate: pins **27**/**28** (jetson-io **i2c2**) — often ``/dev/i2c-1`` on paper,
but on this carrier **pins 3/5 → bus 7** is the working header I²C.

Register map matches TI datasheet. Default gain is **±4.096 V** full-scale
(PGA = 001); single-ended readings are interpreted as 0..+4.096 V at the pin.

See ``BatteryAds1115Monitor`` for ``divider_ratio`` (``V_pack = V_ain * ratio``).
"""

from __future__ import annotations

import logging
import os
import time
from typing import Optional, Sequence, Tuple

# Orin NX 40-pin: pins 3 (SDA) + 5 (SCL) → /dev/i2c-7 on this bot (verified).
DEFAULT_BATTERY_I2C_BUS = 7
DEFAULT_BATTERY_I2C_ADDR = 0x48

# Divider: BAT+ ── R1 ── AIN0 ── R2 ── GND  →  V_ain = V_pack * R2/(R1+R2)
DEFAULT_BATTERY_R1_OHM = 218_000.0
DEFAULT_BATTERY_R2_OHM = 33_000.0
# One-point DMM trim (26.3 V vs ~24.24 V uncorrected on bench); override with env.
DEFAULT_BATTERY_CAL_SCALE = 26.3 / (3.197 * (251.0 / 33.0))
# Never probe bus 5 on Orin NX (can reboot). Prefer 7 before legacy 1/2 guesses.
_BATTERY_PROBE_BUSES: Tuple[int, ...] = (7, 1, 2, 8, 0)

log = logging.getLogger("nina.sensors.ads1115")

# TI register addresses
_REG_CONVERSION = 0x00
_REG_CONFIG = 0x01

# Single-shot, MUX = AINn vs GND, PGA ±4.096 V, 128 SPS, comparator off
# OS=1 start; MUX 100+ch; PGA 001; MODE=1; DR=100; COMP_* = 11
_BASE_CONFIG = 0x8000 | 0x0200 | 0x0100 | (4 << 5) | 0x0003


def divider_ratio_from_resistors(r1_ohm: float, r2_ohm: float) -> float:
    """``V_pack = V_ain * (R1+R2)/R2`` for BAT+→R1→AIN→R2→GND."""
    r2 = float(r2_ohm)
    if r2 <= 0.0:
        return 1.0
    return (float(r1_ohm) + r2) / r2


def pack_voltage_from_ain_volts(
    v_ain: float,
    *,
    divider_ratio: float,
    cal_scale: float = 1.0,
    cal_offset_v: float = 0.0,
) -> float:
    """Pack voltage from AIN pin reading and divider + optional linear trim."""
    return (
        float(v_ain) * float(divider_ratio) * float(cal_scale) + float(cal_offset_v)
    )


def default_battery_i2c_bus() -> int:
    """Return configured battery I²C bus (``NINA_BATTERY_I2C_BUS`` or default)."""
    raw = (os.environ.get("NINA_BATTERY_I2C_BUS") or "").strip()
    if raw:
        try:
            return max(0, int(raw, 0))
        except ValueError:
            pass
    return int(DEFAULT_BATTERY_I2C_BUS)


def probe_ads1115_on_bus(bus_num: int, address: int = DEFAULT_BATTERY_I2C_ADDR) -> bool:
    """Return True if an ADS1115 answers on ``/dev/i2c-<bus_num>``."""
    dev = f"/dev/i2c-{int(bus_num)}"
    if not os.path.exists(dev):
        return False
    try:
        import smbus2  # type: ignore

        bus = smbus2.SMBus(int(bus_num))
        try:
            data = bus.read_i2c_block_data(int(address) & 0x7F, _REG_CONVERSION, 2)
            return len(data) == 2
        finally:
            bus.close()
    except Exception:
        return False


def discover_ads1115_bus(
    address: int = DEFAULT_BATTERY_I2C_ADDR,
    *,
    prefer: Optional[int] = None,
    candidates: Optional[Sequence[int]] = None,
) -> Optional[int]:
    """Find ``/dev/i2c-N`` with ADS1115 at ``address`` (prefers ``prefer`` first)."""
    if prefer is not None and probe_ads1115_on_bus(prefer, address):
        return int(prefer)
    for bus_num in candidates or _BATTERY_PROBE_BUSES:
        if prefer is not None and int(bus_num) == int(prefer):
            continue
        if probe_ads1115_on_bus(int(bus_num), address):
            return int(bus_num)
    return None


def resolve_battery_i2c_bus(
    explicit: Optional[int] = None,
    *,
    auto_discover: bool = False,
) -> int:
    """Bus for pack ADC: explicit arg → env → optional probe → ``DEFAULT_BATTERY_I2C_BUS``."""
    if explicit is not None:
        return int(explicit)
    env = (os.environ.get("NINA_BATTERY_I2C_BUS") or "").strip()
    if env:
        try:
            return max(0, int(env, 0))
        except ValueError:
            pass
    if auto_discover or _env_bool_auto_discover():
        found = discover_ads1115_bus(prefer=DEFAULT_BATTERY_I2C_BUS)
        if found is not None:
            return found
    return int(DEFAULT_BATTERY_I2C_BUS)


def _env_bool_auto_discover() -> bool:
    raw = (os.environ.get("NINA_BATTERY_I2C_AUTO") or "1").strip().lower()
    return raw in ("1", "true", "yes", "on", "y")


def is_available(bus_num: Optional[int] = None) -> Tuple[bool, str]:
    if bus_num is None:
        bus_num = default_battery_i2c_bus()
    try:
        import smbus2  # noqa: F401
    except Exception as exc:
        return False, f"smbus2 not installed ({exc})"
    dev = f"/dev/i2c-{bus_num}"
    if not os.path.exists(dev):
        return False, f"{dev} not present"
    return True, ""


class ADS1115:
    """One ADS1115 on ``/dev/i2c-<bus>`` at ``address`` (default 0x48)."""

    def __init__(self, bus_num: int, address: int = 0x48) -> None:
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

    def read_single_ended_sample(self, channel: int) -> tuple[int, float]:
        """Return ``(signed_raw_code, pin_volts)`` for single-ended *channel* 0..3."""
        if self._bus is None:
            raise RuntimeError("ADS1115 not opened")
        if channel < 0 or channel > 3:
            raise ValueError("channel must be 0..3")
        mux = (4 + channel) << 12
        cfg = _BASE_CONFIG | mux
        hi = (cfg >> 8) & 0xFF
        lo = cfg & 0xFF
        try:
            self._bus.write_i2c_block_data(self._addr, _REG_CONFIG, [hi, lo])
        except OSError as exc:
            log.debug("ADS1115 config write failed: %s", exc)
            raise
        # 128 SPS → ~8.9 ms conversion; margin for clock tolerance
        time.sleep(0.012)
        raw = self._read_conversion_i16()
        volts = (raw / 32768.0) * 4.096
        if volts < 0.0:
            volts = 0.0
        return raw, float(volts)

    def read_single_ended_volts(self, channel: int) -> float:
        """Return voltage **at the AIN pin** (0..~4.096 V for default PGA)."""
        return self.read_single_ended_sample(channel)[1]

    def _read_conversion_i16(self) -> int:
        assert self._bus is not None
        data = self._bus.read_i2c_block_data(self._addr, _REG_CONVERSION, 2)
        val = (data[0] << 8) | data[1]
        if val >= 0x8000:
            val -= 0x10000
        return int(val)
