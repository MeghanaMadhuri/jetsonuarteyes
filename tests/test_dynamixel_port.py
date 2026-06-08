"""Dynamixel port autodetect and bus discovery."""

from __future__ import annotations

from nina.config.dynamixel_port import autodetect_dynamixel_port, port_responds
from nina.config.motor_ids import EXPECTED_DYNAMIXEL_IDS, HOVERBOARD_LEAN_IDS
from nina.controllers.dynamixel_manager import DynamixelManager


def test_port_responds_on_ttyusb1_when_present(monkeypatch) -> None:
    """Skip when hardware absent; integration check on bench Jetson."""
    monkeypatch.delenv("NINA_EYE_UART_PORT", raising=False)
    if not port_responds("/dev/ttyUSB1", 222222):
        return
    assert autodetect_dynamixel_port(222222, exclude={"/dev/ttyUSB0"}) == "/dev/ttyUSB1"


def test_health_check_discovers_lean_only(monkeypatch) -> None:
    if not port_responds("/dev/ttyUSB1", 222222):
        return
    mgr = DynamixelManager("/dev/ttyUSB1", 222222, list(EXPECTED_DYNAMIXEL_IDS))
    try:
        mgr.initialize_bus()
        health = mgr.run_health_check(list(EXPECTED_DYNAMIXEL_IDS))
        assert health.detected_motors >= 2
        assert set(HOVERBOARD_LEAN_IDS).issubset(set(mgr.expected_motor_ids))
        assert health.connected is True
    finally:
        mgr.close()
