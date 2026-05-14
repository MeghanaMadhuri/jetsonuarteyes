"""Unit tests for hoverboard pack power relay helpers (no Jetson.GPIO)."""

from nina.controllers.hoverboard_power_relay import build_power_relay


def test_build_power_relay_disabled_when_none() -> None:
    assert build_power_relay(None, power_on_level=1) is None


def test_build_power_relay_disabled_when_zero() -> None:
    assert build_power_relay(0, power_on_level=1) is None


def test_build_power_relay_returns_helper_for_positive_bcm() -> None:
    from nina.controllers.hoverboard_power_relay import HoverboardPowerRelay

    r = build_power_relay(26, power_on_level=0)
    assert r is not None
    assert isinstance(r, HoverboardPowerRelay)
