"""ADS1115 pack-voltage monitor: low-V alert, neutral arms, lean servos at 2048, gTTS."""

from __future__ import annotations

import logging
import os
import threading
import time
from typing import TYPE_CHECKING, Optional, Tuple

from nina.sensors.ads1115 import (
    ADS1115,
    is_available,
    pack_voltage_from_ain_volts,
    publish_battery_reading,
    set_battery_latched_low,
)
from nina.services.sensor_alert_audio import maybe_speak_low_battery

if TYPE_CHECKING:
    from sirena_ui.workers.nina_service import NinaService

log = logging.getLogger("nina.sensors.battery_ads1115")

# How far the pack voltage must sag *after* the first low-battery latch
# before the monitor re-speaks the warning. ``NINA_BATTERY_REPEAT_STEP_V``
# overrides; ``0`` disables repeats entirely. Default 0.2 V (operator
# preference — small enough to keep the user informed as the pack drops,
# wide enough that voltage sag under load doesn't ping-pong the alert).
DEFAULT_REPEAT_STEP_V = 0.2


def _env_repeat_step_v() -> float:
    raw = (os.environ.get("NINA_BATTERY_REPEAT_STEP_V") or "").strip()
    if not raw:
        return DEFAULT_REPEAT_STEP_V
    try:
        return max(0.0, min(5.0, float(raw)))
    except ValueError:
        return DEFAULT_REPEAT_STEP_V


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


def decide_low_battery_repeat(
    pack_v: float,
    *,
    last_announced_v: Optional[float],
    step_v: float,
) -> Tuple[bool, Optional[float]]:
    """Decide whether the repeat low-battery warning should fire.

    Returns ``(should_speak, new_last_announced_v)``. Pass
    ``new_last_announced_v`` back into the next call. ``None`` for
    ``last_announced_v`` means "no baseline yet" — the function returns
    ``(False, pack_v)`` to anchor without speaking. After the anchor
    exists, the warning fires (and the baseline re-anchors at the current
    ``pack_v``) once ``pack_v <= last_announced_v - step_v``.

    ``step_v <= 0`` disables the repeat entirely (returns the existing
    baseline untouched).
    """
    if step_v <= 0:
        return False, last_announced_v
    if last_announced_v is None:
        return False, float(pack_v)
    if float(pack_v) <= float(last_announced_v) - float(step_v):
        return True, float(pack_v)
    return False, last_announced_v


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
        self._cal_scale = float(s.cal_scale)
        self._cal_offset_v = float(s.cal_offset_v)
        self._channel = int(s.channel)
        self._adc = ADS1115(s.i2c_bus, s.i2c_address)
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._hits = 0
        self._last_fire_mono = -1e30
        self._latched_low = False
        # Per-0.2 V repeat warning baseline: anchored at the pack voltage
        # that triggered the latch, re-anchored each time the warning
        # re-fires, and cleared back to ``None`` when the pack recovers
        # to ``clear_voltage_v`` so the next low cycle starts fresh.
        self._repeat_step_v = _env_repeat_step_v()
        self._last_announced_v: Optional[float] = None

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
            "Battery ADS1115 monitor started (i2c-%s 0x%02X AIN%s "
            "low<=%.2f V clear>=%.2f V repeat_step=%.2f V)",
            self._svc.settings.battery_ads1115.i2c_bus,
            self._svc.settings.battery_ads1115.i2c_address,
            self._channel,
            self._low_v,
            self._clear_v,
            self._repeat_step_v,
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
                pack_v = pack_voltage_from_ain_volts(
                    v_pin,
                    divider_ratio=self._divider,
                    cal_scale=self._cal_scale,
                    cal_offset_v=self._cal_offset_v,
                )
            except Exception:
                log.debug("ADS1115 read failed", exc_info=True)
                publish_battery_reading(ok=False, low_threshold_v=self._low_v)
                time.sleep(max(self._poll_sec, 0.5))
                continue

            publish_battery_reading(
                pack_v=pack_v,
                ain_v=v_pin,
                ok=True,
                low_threshold_v=self._low_v,
            )

            if self._latched_low:
                if pack_v >= self._clear_v:
                    self._latched_low = False
                    self._hits = 0
                    set_battery_latched_low(False)
                    # Clear the repeat-warning baseline so the next low
                    # cycle starts fresh — the first sub-threshold sample
                    # will re-anchor in ``run_low_battery_reaction``.
                    self._last_announced_v = None
                    log.info(
                        "Battery ADS1115 monitor: pack recovered to %.2f V "
                        "(>= clear %.2f V) — motion unlatched",
                        pack_v,
                        self._clear_v,
                    )
                else:
                    set_battery_latched_low(True)
                    self._maybe_repeat_low_battery_warning(pack_v)
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
                set_battery_latched_low(True)
                # Anchor the repeat-warning baseline at the voltage that
                # tripped the latch. Set this BEFORE the reaction (which
                # speaks the initial alert) so a subsequent poll that
                # observes a small sag does not immediately re-fire.
                self._last_announced_v = float(pack_v)
                try:
                    self._svc.run_low_battery_reaction()
                except Exception:
                    log.exception("Low battery reaction failed")
            time.sleep(self._poll_sec)

    def _maybe_repeat_low_battery_warning(self, pack_v: float) -> None:
        """While latched, re-speak the low-battery alert every ``step_v`` sag.

        Delegates the should-speak decision to :func:`decide_low_battery_repeat`
        so the rule is exercised by pure unit tests. The TTS call goes
        through :func:`maybe_speak_low_battery`, which routes via the
        bldc espeak path; its built-in per-message cooldown coalesces
        bursts (e.g. a steep drop crossing several steps in one poll).
        """
        should_speak, new_anchor = decide_low_battery_repeat(
            pack_v,
            last_announced_v=self._last_announced_v,
            step_v=self._repeat_step_v,
        )
        self._last_announced_v = new_anchor
        if not should_speak:
            return
        log.warning(
            "Battery ADS1115 monitor: pack sagged to %.2f V — "
            "repeating low-battery warning (step=%.2f V)",
            pack_v,
            self._repeat_step_v,
        )
        try:
            maybe_speak_low_battery()
        except Exception:
            log.exception("Low battery repeat warning failed")
