"""Display blanking for L2 sleep (DPMS + sysfs backlight)."""

from __future__ import annotations

import glob
import logging
import os
import subprocess
from pathlib import Path
from typing import Optional, Tuple

log = logging.getLogger("sirena_ui.display_power")


def discover_backlight_brightness_path(explicit: str = "") -> Optional[Path]:
    if explicit:
        p = Path(explicit)
        return p if p.is_file() else None
    for pattern in (
        "/sys/class/backlight/*/brightness",
        "/sys/class/leds/*backlight*/brightness",
    ):
        matches = sorted(glob.glob(pattern))
        if matches:
            return Path(matches[0])
    return None


class DisplayPowerController:
    """Save/restore backlight and toggle DPMS for the kiosk X session."""

    def __init__(self, backlight_path: str = "") -> None:
        self._brightness_path = discover_backlight_brightness_path(backlight_path)
        self._saved_brightness: Optional[int] = None

    @property
    def backlight_available(self) -> bool:
        return self._brightness_path is not None

    def _read_brightness(self) -> Optional[int]:
        if self._brightness_path is None:
            return None
        try:
            return int(self._brightness_path.read_text().strip())
        except (OSError, ValueError):
            return None

    def _write_brightness(self, value: int) -> None:
        if self._brightness_path is None:
            return
        try:
            max_path = self._brightness_path.parent / "max_brightness"
            maximum = int(max_path.read_text().strip()) if max_path.is_file() else 255
            clipped = max(0, min(maximum, int(value)))
            self._brightness_path.write_text(str(clipped))
        except OSError as exc:
            log.debug("backlight write failed: %s", exc)

    def _xset(self, *args: str) -> None:
        env = os.environ.copy()
        env.setdefault("DISPLAY", os.environ.get("DISPLAY", ":0"))
        try:
            subprocess.run(
                ["xset", *args],
                env=env,
                check=False,
                timeout=3.0,
                capture_output=True,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            log.debug("xset %s failed: %s", " ".join(args), exc)

    def sleep_display(self) -> None:
        current = self._read_brightness()
        if current is not None:
            self._saved_brightness = current
            self._write_brightness(0)
        self._xset("dpms", "force", "off")

    def wake_display(self) -> None:
        self._xset("dpms", "force", "on")
        if self._saved_brightness is not None:
            self._write_brightness(self._saved_brightness)
            self._saved_brightness = None


__all__ = ["DisplayPowerController", "discover_backlight_brightness_path"]
