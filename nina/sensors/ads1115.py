"""Minimal TI ADS1115 16-bit I²C ADC (single-ended reads via ``smbus2``).

Used for scaled pack voltage: hardware must divide the battery down so the
AIN pin stays **below the ADS1115 VDD** (typically 3.3 V on the Jetson header).

Connections (typical Jetson 40-pin + ADS1115 module)
----------------------------------------------------
* **ADS1115 VDD** → **3.3 V** header (same logic rail as Jetson I/O).
* **ADS1115 GND** → **GND**.
* **ADS1115 SDA** → I²C **SDA** for the chosen bus (default ``/dev/i2c-1`` is
  usually **BCM 2** = physical pin **3** on the reference Orin NX / Orin Nano
  header layout used by ``Jetson.GPIO``).
* **ADS1115 SCL** → I²C **SCL** (default bus 1 is often **BCM 3** = physical pin **5**).
  Confirm with ``i2cdetect -y 1`` and your carrier pinout; bus index can differ.
* **ADDR** → **GND** for default address **0x48** (or VDD / SDA / SCL for 0x49–0x4B).
* **AIN0** (or AIN1–3 per ``NINA_BATTERY_ADS1115_CHANNEL``) → centre tap of a
  **resistor divider** from pack (+) to GND so **V_ain ≤ VDD** at max charge.
  Example: 24 V nominal → use e.g. **100 kΩ** from **BAT+** to **AIN0**, **10 kΩ**
  from **AIN0** to **GND** → ratio **11:1** → ``NINA_BATTERY_DIVIDER_RATIO=11``.

Register map matches TI datasheet. Default gain is **±4.096 V** full-scale
(PGA = 001); single-ended readings are interpreted as 0..+4.096 V at the pin.

See ``BatteryAds1115Monitor`` for ``divider_ratio`` (``V_pack = V_ain * ratio``).
"""

from __future__ import annotations

import logging
import os
import time
from typing import Optional, Tuple

log = logging.getLogger("nina.sensors.ads1115")

# TI register addresses
_REG_CONVERSION = 0x00
_REG_CONFIG = 0x01

# Single-shot, MUX = AINn vs GND, PGA ±4.096 V, 128 SPS, comparator off
# OS=1 start; MUX 100+ch; PGA 001; MODE=1; DR=100; COMP_* = 11
_BASE_CONFIG = 0x8000 | 0x0200 | 0x0100 | (4 << 5) | 0x0003


def is_available(bus_num: Optional[int] = None) -> Tuple[bool, str]:
    if bus_num is None:
        bus_num = int(os.environ.get("NINA_BATTERY_I2C_BUS", "1"))
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

    def read_single_ended_volts(self, channel: int) -> float:
        """Return voltage **at the AIN pin** (0..~4.096 V for default PGA)."""
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
        # ±4.096 V full-scale → LSB = 4.096 / 32768 V
        volts = (raw / 32768.0) * 4.096
        if volts < 0.0:
            volts = 0.0
        return float(volts)

    def _read_conversion_i16(self) -> int:
        assert self._bus is not None
        data = self._bus.read_i2c_block_data(self._addr, _REG_CONVERSION, 2)
        val = (data[0] << 8) | data[1]
        if val >= 0x8000:
            val -= 0x10000
        return int(val)
