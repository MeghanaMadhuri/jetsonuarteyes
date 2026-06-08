"""Resolve / autodetect the Dynamixel USB serial device."""

from __future__ import annotations

import glob
import logging
import os
from typing import Iterable, Optional, Set

log = logging.getLogger("nina.config.dynamixel_port")

_DEFAULT_BAUD = 222222
_PROBE_ID = 12  # hoverboard lean — present on every Nina with a drive bus


def _candidate_ports(exclude: Set[str]) -> list[str]:
    ports: list[str] = []
    for pattern in ("/dev/ttyUSB*", "/dev/ttyACM*"):
        for path in sorted(glob.glob(pattern)):
            if path not in exclude:
                ports.append(path)
    return ports


def port_responds(port: str, baudrate: int, *, probe_id: int = _PROBE_ID) -> bool:
    """Return True when ``probe_id`` answers a Dynamixel ping on ``port``."""
    port = (port or "").strip()
    if not port:
        return False
    try:
        from nina.controllers.dynamixel_manager import DynamixelManager

        mgr = DynamixelManager(port, baudrate, [probe_id])
        try:
            mgr.initialize_bus()
            return bool(mgr.ping(probe_id))
        finally:
            mgr.close()
    except Exception as exc:
        log.debug("Dynamixel probe failed on %s: %s", port, exc)
        return False


def autodetect_dynamixel_port(
    baudrate: int,
    *,
    exclude: Optional[Iterable[str]] = None,
    probe_id: int = _PROBE_ID,
) -> Optional[str]:
    """Pick the first USB serial node where ``probe_id`` responds."""
    skip = {p.strip() for p in (exclude or ()) if p and str(p).strip()}
    eye = (os.environ.get("NINA_EYE_UART_PORT") or "").strip()
    if eye:
        skip.add(eye)
    for port in _candidate_ports(skip):
        if port_responds(port, baudrate, probe_id=probe_id):
            log.info("Dynamixel autodetect: using %s (id=%s responded)", port, probe_id)
            return port
    return None


def resolve_dynamixel_port(
    configured: str,
    baudrate: int,
    *,
    exclude: Optional[Iterable[str]] = None,
) -> str:
    """Use ``configured`` when it responds; otherwise autodetect or keep configured."""
    configured = (configured or "/dev/ttyUSB1").strip()
    if port_responds(configured, baudrate):
        return configured
    found = autodetect_dynamixel_port(baudrate, exclude=exclude)
    if found:
        log.warning(
            "Dynamixel configured port %s has no response — switching to %s",
            configured,
            found,
        )
        return found
    log.error(
        "Dynamixel: no motor response on %s or any other USB serial port "
        "(check U2D2 cable / 12 V servo power)",
        configured,
    )
    return configured


__all__ = [
    "autodetect_dynamixel_port",
    "port_responds",
    "resolve_dynamixel_port",
]
