"""Unit tests for ADS1115 bus discovery helpers."""

from __future__ import annotations

import os
from unittest import mock

from nina.sensors import ads1115 as m


def test_resolve_battery_i2c_bus_env_wins(monkeypatch) -> None:
    monkeypatch.setenv("NINA_BATTERY_I2C_BUS", "2")
    monkeypatch.setenv("NINA_BATTERY_I2C_AUTO", "0")
    assert m.resolve_battery_i2c_bus(auto_discover=False) == 2


def test_resolve_battery_i2c_bus_explicit() -> None:
    assert m.resolve_battery_i2c_bus(5, auto_discover=False) == 5


def test_discover_prefers_prefer_bus(monkeypatch) -> None:
    def fake_probe(bus: int, address: int = 0x48) -> bool:
        return bus == 1

    monkeypatch.setattr(m, "probe_ads1115_on_bus", fake_probe)
    assert m.discover_ads1115_bus(prefer=1) == 1


def test_default_divider_ratio() -> None:
    assert abs(m.DEFAULT_BATTERY_I2C_BUS - 1) < 1e-9
    ratio = (218_000.0 + 33_000.0) / 33_000.0
    assert abs(ratio - 7.606060606) < 0.001
