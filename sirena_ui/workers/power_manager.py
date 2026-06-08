"""Global idle / deep-sleep orchestration for the Sirena kiosk."""

from __future__ import annotations

import logging
import threading
import time
from typing import Any, Callable, Dict, Optional, Tuple

from PyQt5.QtCore import QObject, QTimer, pyqtSignal

from sirena_ui.workers.display_power import DisplayPowerController
from sirena_ui.workers.nina_service import NinaService
from sirena_ui.workers.power_config import PowerConfig

log = logging.getLogger("sirena_ui.power_manager")

STATE_ACTIVE = "active"
STATE_IDLE = "idle"
STATE_SLEEP = "sleep"


class PowerManager(QObject):
    """L0 active → L1 idle → L2 sleep; unified wake pipeline."""

    state_changed = pyqtSignal(str)

    def __init__(
        self,
        service: NinaService,
        *,
        config: Optional[PowerConfig] = None,
        navigate_home: Optional[Callable[[], None]] = None,
        parent: Optional[QObject] = None,
    ) -> None:
        super().__init__(parent)
        self._service = service
        self._config = config or PowerConfig.from_env()
        self._navigate_home = navigate_home
        self._display = DisplayPowerController(self._config.backlight_sysfs)
        self._state = STATE_ACTIVE
        self._last_activity = time.monotonic()
        self._first_touch_press_at: float = 0.0
        self._sleep_overlay_show: Optional[Callable[[], None]] = None
        self._sleep_overlay_hide: Optional[Callable[[], None]] = None
        self._current_screen_key: Callable[[], str] = lambda: ""
        self._suppress_activity_notes = False

        self._tick = QTimer(self)
        self._tick.setInterval(1000)
        self._tick.timeout.connect(self._on_tick)
        if self._config.enabled:
            self._tick.start()

    @property
    def config(self) -> PowerConfig:
        return self._config

    @property
    def state(self) -> str:
        return self._state

    def set_navigate_home(self, fn: Callable[[], None]) -> None:
        self._navigate_home = fn

    def set_screen_key_provider(self, fn: Callable[[], str]) -> None:
        self._current_screen_key = fn

    def set_sleep_overlay_hooks(
        self,
        show: Callable[[], None],
        hide: Callable[[], None],
    ) -> None:
        self._sleep_overlay_show = show
        self._sleep_overlay_hide = hide

    def reload_config(self, config: Optional[PowerConfig] = None) -> None:
        self._config = config or PowerConfig.from_env()
        if self._config.enabled and not self._tick.isActive():
            self._tick.start()
        elif not self._config.enabled:
            self._tick.stop()
            if self._state != STATE_ACTIVE:
                self.wake("config_disabled")

    def note_activity(self, source: str = "ui") -> None:
        if not self._config.enabled or self._suppress_activity_notes:
            return
        self._last_activity = time.monotonic()
        self._first_touch_press_at = 0.0
        if self._state == STATE_SLEEP:
            self.wake(source)
        elif self._state == STATE_IDLE:
            self._set_state(STATE_ACTIVE)
            self._service.restore_background()
            log.info("power idle cleared (activity source=%s)", source)

    def can_sleep(self) -> Tuple[bool, str]:
        if self._service.is_hoverboard_in_motion():
            return False, "wheels_in_motion"
        screen = (self._current_screen_key() or "").partition(":")[0]
        if screen == "lean_cal":
            return False, "lean_cal_open"
        autonomy = getattr(self._service, "_autonomy", None)
        if autonomy is not None:
            enabled = bool(getattr(autonomy, "is_enabled", lambda: False)())
            navigating = bool(getattr(autonomy, "is_navigating", lambda: False)())
            if enabled and navigating:
                return False, "autonomy_navigating"
        return True, ""

    def status_dict(self) -> Dict[str, Any]:
        now = time.monotonic()
        elapsed = now - self._last_activity
        idle_rem = max(0, int(self._config.idle_sec - elapsed))
        sleep_rem = max(0, int(self._config.sleep_sec - elapsed))
        return {
            "state": self._state,
            "enabled": self._config.enabled,
            "idle_sec_remaining": idle_rem if self._state == STATE_ACTIVE else 0,
            "sleep_sec_remaining": sleep_rem if self._state != STATE_SLEEP else 0,
            "hint": (
                "idle — tap screen to stay awake"
                if self._state == STATE_IDLE
                else None
            ),
            "wake_sources_enabled": {
                "display_touch": self._config.wake_display_tap,
                "double_tap": self._config.wake_double_tap,
                "tablet": self._config.wake_tablet,
                "voice": self._config.wake_voice,
            },
        }

    def enter_idle(self) -> None:
        if self._state == STATE_IDLE:
            return
        ok, reason = self.can_sleep()
        if not ok:
            log.debug("power idle blocked: %s", reason)
            self._last_activity = time.monotonic()
            return
        self._suppress_activity_notes = True
        try:
            if self._navigate_home is not None:
                try:
                    self._navigate_home()
                except Exception:
                    log.exception("power navigate_home failed")
            keep_touch = self._config.wake_double_tap
            self._service.stand_down_background(keep_touch_monitor=keep_touch)
            self._set_state(STATE_IDLE)
            log.info("power enter idle")
        finally:
            self._suppress_activity_notes = False

    def enter_sleep(self) -> None:
        if self._state == STATE_SLEEP:
            return
        ok, reason = self.can_sleep()
        if not ok:
            log.debug("power sleep blocked: %s", reason)
            self._last_activity = time.monotonic()
            return
        if self._state == STATE_ACTIVE:
            self.enter_idle()
        keep_touch = self._config.wake_double_tap
        if not keep_touch:
            self._service.stop_touch_monitor_for_power()
        try:
            self._display.sleep_display()
        except Exception:
            log.exception("display sleep failed")
        if self._sleep_overlay_show is not None:
            try:
                self._sleep_overlay_show()
            except Exception:
                log.exception("sleep overlay show failed")
        self._send_eye_expression(self._config.sleep_eye_id)
        self._set_state(STATE_SLEEP)
        log.info("power enter sleep")

    def wake(self, source: str = "unknown") -> None:
        if not self._config.enabled and source != "config_disabled":
            return
        was_sleep = self._state == STATE_SLEEP
        was_idle = self._state == STATE_IDLE
        if self._state == STATE_ACTIVE and source not in ("tablet", "display_touch"):
            self._last_activity = time.monotonic()
            return
        self._last_activity = time.monotonic()
        self._first_touch_press_at = 0.0
        if was_sleep:
            if self._sleep_overlay_hide is not None:
                try:
                    self._sleep_overlay_hide()
                except Exception:
                    log.exception("sleep overlay hide failed")
            try:
                self._display.wake_display()
            except Exception:
                log.exception("display wake failed")
            self._send_eye_expression(self._config.wake_eye_id)
        if was_sleep or was_idle:
            self._service.restore_background()
        self._set_state(STATE_ACTIVE)
        log.info("power wake source=%s", source)

    def handle_touch_press(self) -> bool:
        """Return True when touch was consumed (wake or sleep suppress safety)."""
        if self._state != STATE_SLEEP:
            return False
        if not self._config.wake_double_tap:
            return True
        now = time.monotonic()
        if self._first_touch_press_at > 0.0:
            gap_ms = (now - self._first_touch_press_at) * 1000.0
            if gap_ms <= self._config.double_tap_ms:
                self.wake("double_tap")
                return True
        self._first_touch_press_at = now
        return True

    def _send_eye_expression(self, expr_id: int) -> None:
        """Push a TFT eye expression (sleep/wake) without blocking the GUI.

        No-op unless the eye UART is enabled; never raises into the caller so a
        missing/unplugged eye board can't disrupt the power state machine.
        """
        if expr_id is None or int(expr_id) < 0:
            return
        try:
            settings = getattr(self._service, "settings", None)
            eye_cfg = getattr(settings, "eye_uart", None)
            if not bool(getattr(eye_cfg, "enabled", False)):
                return
        except Exception:
            return

        eid = int(expr_id)

        def _run() -> None:
            try:
                self._service.send_eye_expression(eid)
            except Exception:
                log.debug("power eye expression %s failed", eid, exc_info=True)

        try:
            threading.Thread(target=_run, name="power-eye", daemon=True).start()
        except Exception:
            log.debug("power eye thread spawn failed", exc_info=True)

    def _on_tick(self) -> None:
        if not self._config.enabled:
            return
        elapsed = time.monotonic() - self._last_activity
        if elapsed >= self._config.sleep_sec:
            if self._state != STATE_SLEEP:
                self.enter_sleep()
            return
        if elapsed >= self._config.idle_sec and self._state == STATE_ACTIVE:
            self.enter_idle()

    def _set_state(self, state: str) -> None:
        if self._state == state:
            return
        self._state = state
        self.state_changed.emit(state)


__all__ = ["PowerManager", "STATE_ACTIVE", "STATE_IDLE", "STATE_SLEEP"]
