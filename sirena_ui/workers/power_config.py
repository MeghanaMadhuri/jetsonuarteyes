"""Environment-driven config for ``PowerManager`` (see docs/NINA_POWER_MANAGEMENT.md)."""

from __future__ import annotations

import os
from dataclasses import dataclass


def _env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name, "").strip().lower()
    if raw in ("1", "true", "yes", "y", "on"):
        return True
    if raw in ("0", "false", "no", "off", "n"):
        return False
    return default


def _env_int(name: str, default: int, *, minimum: int = 0) -> int:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        return max(minimum, int(raw))
    except ValueError:
        return default


@dataclass(frozen=True)
class PowerConfig:
    enabled: bool = True
    idle_sec: int = 300
    sleep_sec: int = 900
    backlight_sysfs: str = ""
    wake_display_tap: bool = True
    wake_double_tap: bool = True
    double_tap_ms: int = 600
    wake_tablet: bool = True
    wake_voice: bool = False
    # ESP8266 TFT eye expression on L2 sleep / wake. Only sent when the
    # eye UART is enabled (NINA_EYE_UART_ENABLE=1). -1 disables either send.
    # Defaults: 35 = screen_blue on sleep, 0 = neutral on wake.
    sleep_eye_id: int = 35
    wake_eye_id: int = 0

    @classmethod
    def from_env(cls) -> PowerConfig:
        idle = _env_int("NINA_POWER_IDLE_SEC", 300, minimum=30)
        sleep = _env_int("NINA_POWER_SLEEP_SEC", 900, minimum=idle + 30)
        return cls(
            enabled=_env_bool("NINA_POWER_ENABLE", True),
            idle_sec=idle,
            sleep_sec=sleep,
            backlight_sysfs=(os.environ.get("NINA_BACKLIGHT_SYSFS") or "").strip(),
            wake_display_tap=_env_bool("NINA_POWER_WAKE_DISPLAY_TAP", True),
            wake_double_tap=_env_bool("NINA_POWER_WAKE_DOUBLE_TAP", True),
            double_tap_ms=_env_int("NINA_WAKE_DOUBLE_TAP_MS", 600, minimum=200),
            wake_tablet=_env_bool("NINA_POWER_WAKE_TABLET", True),
            wake_voice=_env_bool("NINA_POWER_WAKE_VOICE", False),
            sleep_eye_id=_env_int("NINA_POWER_SLEEP_EYE_ID", 35, minimum=-1),
            wake_eye_id=_env_int("NINA_POWER_WAKE_EYE_ID", 0, minimum=-1),
        )


__all__ = ["PowerConfig"]
