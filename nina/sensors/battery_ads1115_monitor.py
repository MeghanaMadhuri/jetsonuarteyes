"""ADS1115 pack-voltage monitor: low-V alert, neutral arms, lean servos at 2048, gTTS."""

from __future__ import annotations

import logging
import threading
import time
from typing import TYPE_CHECKING, Optional

from nina.sensors.ads1115 import ADS1115, is_available

if TYPE_CHECKING:
    from sirena_ui.workers.nina_service import NinaService

log = logging.getLogger("nina.sensors.battery_ads1115")


def battery_low_debounce_step(
    pack_volts: float,
    *,
    low_v: float,
    debounce_reads: int,
    consecutive_low: int,
) -> tuple[bool, int]:
    """Return ``(should_fire, new_consecutive_low)`` when pack is at/below ``low_v``."""
    if pack_volts <= low_v:
        n = consecutive_low + 1
        if n >= debounce_reads:
            return True, 0
        return False, n
    return False, 0


class BatteryAds1115Monitor:
    """Poll ADS1115; when pack voltage is low, call ``NinaService.run_low_battery_reaction``."""

    def __init__(self, service: "NinaService") -> None:
        self._svc = service
        s = service.settings.battery_ads1115
        self._low_v = float(s.low_voltage_v)
        self._clear_v = float(s.clear_voltage_v)
        self._debounce_reads = int(s.debounce_reads)
        self._cooldown_sec = float(s.cooldown_sec)
        self._poll_sec = float(s.poll_interval_sec)
        self._divider = float(s.divider_ratio)
        self._channel = int(s.channel)
        self._adc = ADS1115(s.i2c_bus, s.i2c_address)
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._hits = 0
        self._last_fire_mono = -1e30
        self._latched_low = False

    def start(self) -> None:
        ok, msg = is_available(self._svc.settings.battery_ads1115.i2c_bus)
        if not ok:
            raise RuntimeError(msg)
        self._adc.open()
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._run, name="BatteryAds1115Monitor", daemon=True
        )
        self._thread.start()
        log.info(
            "Battery ADS1115 monitor started (i2c-%s 0x%02X AIN%s low<=%.2f V clear>=%.2f V)",
            self._svc.settings.battery_ads1115.i2c_bus,
            self._svc.settings.battery_ads1115.i2c_address,
            self._channel,
            self._low_v,
            self._clear_v,
        )

    def stop(self) -> None:
        self._stop.set()
        t = self._thread
        self._thread = None
        if t is not None:
            t.join(timeout=5.0)
            if t.is_alive():
                log.warning("Battery ADS1115 monitor thread did not exit in time")
        try:
            self._adc.close()
        except Exception:
            log.debug("ADS1115 close failed", exc_info=True)
        log.info("Battery ADS1115 monitor stopped")

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                v_pin = self._adc.read_single_ended_volts(self._channel)
                pack_v = v_pin * self._divider
            except Exception:
                log.debug("ADS1115 read failed", exc_info=True)
                time.sleep(max(self._poll_sec, 0.5))
                continue

            if self._latched_low:
                if pack_v >= self._clear_v:
                    self._latched_low = False
                    self._hits = 0
                time.sleep(self._poll_sec)
                continue

            fire, self._hits = battery_low_debounce_step(
                pack_v,
                low_v=self._low_v,
                debounce_reads=self._debounce_reads,
                consecutive_low=self._hits,
            )
            now = time.monotonic()
            if fire and (now - self._last_fire_mono) >= self._cooldown_sec:
                self._last_fire_mono = now
                self._latched_low = True
                self._hits = 0
                try:
                    self._svc.run_low_battery_reaction()
                except Exception:
                    log.exception("Low battery reaction failed")
            time.sleep(self._poll_sec)
