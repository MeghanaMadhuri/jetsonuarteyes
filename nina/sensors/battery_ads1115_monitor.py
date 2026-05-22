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
from nina.sensors.battery_latch_store import (
    clear_battery_latch_state,
    load_battery_latch_state,
    snapshot_latched,
)
from nina.services.sensor_alert_audio import maybe_speak_low_battery

if TYPE_CHECKING:
    from sirena_ui.workers.nina_service import NinaService

log = logging.getLogger("nina.sensors.battery_ads1115")

DEFAULT_REPEAT_STEP_V = 0.2


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


def decide_low_battery_reminder(
    *,
    now_mono: float,
    last_reminder_mono: float,
    reminder_interval_sec: float,
) -> bool:
    """Time-based reminder while latched (covers slow drain / post-load voltage bounce)."""
    if reminder_interval_sec <= 0:
        return False
    if last_reminder_mono < 0:
        return False
    return (now_mono - last_reminder_mono) >= reminder_interval_sec


def update_pack_min_v(current_min: Optional[float], pack_v: float) -> float:
    """Track the lowest pack voltage seen since latch (for repeat warnings)."""
    if current_min is None:
        return float(pack_v)
    return min(float(current_min), float(pack_v))


class BatteryAds1115Monitor:
    """Poll ADS1115; when pack voltage is low, call ``NinaService.run_low_battery_reaction``."""

    def __init__(self, service: "NinaService") -> None:
        self._svc = service
        s = service.settings.battery_ads1115
        self._low_v = float(s.low_voltage_v)
        self._clear_v = float(s.clear_voltage_v)
        self._debounce_reads = int(s.debounce_reads)
        self._startup_debounce_reads = max(1, int(s.startup_debounce_reads))
        self._cooldown_sec = float(s.cooldown_sec)
        self._poll_sec = float(s.poll_interval_sec)
        self._divider = float(s.divider_ratio)
        self._cal_scale = float(s.cal_scale)
        self._cal_offset_v = float(s.cal_offset_v)
        self._channel = int(s.channel)
        self._repeat_step_v = float(s.repeat_step_v)
        self._reminder_interval_sec = float(s.reminder_interval_sec)
        self._adc = ADS1115(s.i2c_bus, s.i2c_address)
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._hits = 0
        self._last_fire_mono = -1e30
        self._latched_low = False
        self._last_announced_v: Optional[float] = None
        self._pack_min_v: Optional[float] = None
        self._last_reminder_mono = -1.0
        self._boot_latch_applied = False

    def is_running(self) -> bool:
        t = self._thread
        return t is not None and t.is_alive()

    def start(self) -> None:
        ok, msg = is_available(self._svc.settings.battery_ads1115.i2c_bus)
        if not ok:
            raise RuntimeError(msg)
        self._adc.open()
        self._stop.clear()
        self._hits = 0
        self._last_fire_mono = -1e30
        self._boot_latch_applied = False
        self._try_restore_or_probe_startup_latch()
        self._thread = threading.Thread(
            target=self._run, name="BatteryAds1115Monitor", daemon=True
        )
        self._thread.start()
        log.info(
            "Battery ADS1115 monitor started (i2c-%s 0x%02X AIN%s "
            "low<=%.2f V clear>=%.2f V repeat_step=%.2f V reminder=%.0fs "
            "startup_debounce=%d)",
            self._svc.settings.battery_ads1115.i2c_bus,
            self._svc.settings.battery_ads1115.i2c_address,
            self._channel,
            self._low_v,
            self._clear_v,
            self._repeat_step_v,
            self._reminder_interval_sec,
            self._startup_debounce_reads,
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

    def _read_pack_v(self) -> Tuple[Optional[float], Optional[float]]:
        try:
            v_pin = self._adc.read_single_ended_volts(self._channel)
            pack_v = pack_voltage_from_ain_volts(
                v_pin,
                divider_ratio=self._divider,
                cal_scale=self._cal_scale,
                cal_offset_v=self._cal_offset_v,
            )
            return pack_v, v_pin
        except Exception:
            log.debug("ADS1115 read failed", exc_info=True)
            return None, None

    def _try_restore_or_probe_startup_latch(self) -> None:
        """Before the poll thread runs: restore disk latch or fast-latch if still low."""
        pack_v, ain_v = self._read_pack_v()
        if pack_v is None:
            return

        publish_battery_reading(
            pack_v=pack_v,
            ain_v=float(ain_v or 0.0),
            ok=True,
            low_threshold_v=self._low_v,
        )

        persisted = load_battery_latch_state()
        if pack_v >= self._clear_v:
            if persisted is not None:
                clear_battery_latch_state()
            return

        if persisted is not None and persisted.latched:
            self._enter_latched_state(
                pack_v,
                last_announced_v=persisted.last_announced_v,
                pack_min_v=persisted.pack_min_v or persisted.pack_v_at_latch,
                run_full_reaction=False,
                speak_alert=_restart_speak_enabled(),
                reason="restored from disk",
            )
            self._boot_latch_applied = True
            return

        if pack_v <= self._low_v:
            fire, _ = battery_low_debounce_step(
                pack_v,
                low_v=self._low_v,
                debounce_reads=self._startup_debounce_reads,
                consecutive_low=self._startup_debounce_reads - 1,
            )
            if fire:
                self._enter_latched_state(
                    pack_v,
                    run_full_reaction=True,
                    speak_alert=True,
                    reason="startup pack still low",
                )
                self._boot_latch_applied = True

    def _enter_latched_state(
        self,
        pack_v: float,
        *,
        last_announced_v: Optional[float] = None,
        pack_min_v: Optional[float] = None,
        run_full_reaction: bool,
        speak_alert: bool,
        reason: str,
    ) -> None:
        self._latched_low = True
        self._hits = 0
        self._last_fire_mono = time.monotonic()
        self._last_announced_v = (
            float(last_announced_v) if last_announced_v is not None else float(pack_v)
        )
        self._pack_min_v = update_pack_min_v(pack_min_v, pack_v)
        self._last_reminder_mono = time.monotonic()
        set_battery_latched_low(True)
        snapshot_latched(
            pack_v=pack_v,
            last_announced_v=self._last_announced_v,
            pack_min_v=self._pack_min_v,
        )
        log.warning(
            "Battery ADS1115 monitor: low-battery latch engaged (%.2f V, %s)",
            pack_v,
            reason,
        )
        if run_full_reaction:
            try:
                self._svc.run_low_battery_reaction()
            except Exception:
                log.exception("Low battery reaction failed")
        elif speak_alert:
            try:
                maybe_speak_low_battery(
                    phrase=(self._svc.settings.battery_ads1115.tts_text or "").strip()
                )
            except Exception:
                log.exception("Low battery restore alert failed")

    def _clear_latched_state(self, pack_v: float) -> None:
        self._latched_low = False
        self._hits = 0
        self._last_announced_v = None
        self._pack_min_v = None
        self._last_reminder_mono = -1.0
        set_battery_latched_low(False)
        clear_battery_latch_state()
        log.info(
            "Battery ADS1115 monitor: pack recovered to %.2f V "
            "(>= clear %.2f V) — motion unlatched",
            pack_v,
            self._clear_v,
        )

    def _persist_latch_bookkeeping(self, pack_v: float) -> None:
        snapshot_latched(
            pack_v=pack_v,
            last_announced_v=self._last_announced_v,
            pack_min_v=self._pack_min_v,
        )

    def _run(self) -> None:
        while not self._stop.is_set():
            pack_v, ain_v = self._read_pack_v()
            if pack_v is None:
                publish_battery_reading(ok=False, low_threshold_v=self._low_v)
                if self._latched_low:
                    set_battery_latched_low(True)
                time.sleep(max(self._poll_sec, 0.5))
                continue

            publish_battery_reading(
                pack_v=pack_v,
                ain_v=float(ain_v or 0.0),
                ok=True,
                low_threshold_v=self._low_v,
            )

            if self._latched_low:
                self._pack_min_v = update_pack_min_v(self._pack_min_v, pack_v)
                if pack_v >= self._clear_v:
                    self._clear_latched_state(pack_v)
                else:
                    set_battery_latched_low(True)
                    self._persist_latch_bookkeeping(pack_v)
                    self._maybe_repeat_low_battery_warning()
                    self._maybe_reminder_warning()
                time.sleep(self._poll_sec)
                continue

            if not self._boot_latch_applied and pack_v <= self._low_v:
                fire, self._hits = battery_low_debounce_step(
                    pack_v,
                    low_v=self._low_v,
                    debounce_reads=self._startup_debounce_reads,
                    consecutive_low=self._hits,
                )
                if fire:
                    self._boot_latch_applied = True
                    self._enter_latched_state(
                        pack_v,
                        run_full_reaction=True,
                        speak_alert=True,
                        reason="startup debounce",
                    )
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
                self._boot_latch_applied = True
                self._enter_latched_state(
                    pack_v,
                    run_full_reaction=True,
                    speak_alert=True,
                    reason="pack at/below threshold",
                )
            time.sleep(self._poll_sec)

    def _maybe_repeat_low_battery_warning(self) -> None:
        """Re-speak every ``repeat_step_v`` sag using the pack minimum since latch."""
        if self._pack_min_v is None:
            return
        should_speak, new_anchor = decide_low_battery_repeat(
            self._pack_min_v,
            last_announced_v=self._last_announced_v,
            step_v=self._repeat_step_v,
        )
        self._last_announced_v = new_anchor
        if not should_speak:
            return
        log.warning(
            "Battery ADS1115 monitor: pack minimum %.2f V — "
            "repeating low-battery warning (step=%.2f V)",
            self._pack_min_v,
            self._repeat_step_v,
        )
        try:
            maybe_speak_low_battery()
        except Exception:
            log.exception("Low battery repeat warning failed")
        self._last_reminder_mono = time.monotonic()

    def _maybe_reminder_warning(self) -> None:
        """Periodic reminder while latched even if voltage bounces under load."""
        now = time.monotonic()
        if not decide_low_battery_reminder(
            now_mono=now,
            last_reminder_mono=self._last_reminder_mono,
            reminder_interval_sec=self._reminder_interval_sec,
        ):
            return
        self._last_reminder_mono = now
        snap = self._pack_min_v
        log.warning(
            "Battery ADS1115 monitor: timed low-battery reminder "
            "(pack_min=%.2f V, interval=%.0f s)",
            snap if snap is not None else -1.0,
            self._reminder_interval_sec,
        )
        try:
            maybe_speak_low_battery()
        except Exception:
            log.exception("Low battery reminder failed")


def _restart_speak_enabled() -> bool:
    raw = (os.environ.get("NINA_BATTERY_RESTART_SPEAK") or "1").strip().lower()
    return raw not in ("0", "false", "no", "off", "n")
