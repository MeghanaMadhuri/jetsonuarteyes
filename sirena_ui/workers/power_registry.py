"""Process-wide handle to the kiosk ``PowerManager`` (HTTP gateway + touch)."""

from __future__ import annotations

from typing import Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from sirena_ui.workers.power_manager import PowerManager

_power_manager: Optional[PowerManager] = None


def set_power_manager(manager: Optional[PowerManager]) -> None:
    global _power_manager
    _power_manager = manager


def get_power_manager() -> Optional[PowerManager]:
    return _power_manager


def sensor_hardware_allowed() -> bool:
    """True when Vision/Drive/Perception may open camera, lidar, or depth."""
    pm = get_power_manager()
    if pm is None or not pm.config.enabled:
        return True
    return pm.state == "active"


POWER_SAVE_SENSOR_HINT = "Robot in power save — wake to use sensors"


__all__ = [
    "POWER_SAVE_SENSOR_HINT",
    "get_power_manager",
    "sensor_hardware_allowed",
    "set_power_manager",
]
