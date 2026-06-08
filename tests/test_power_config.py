"""Power manager configuration from environment."""

from __future__ import annotations

import os

from sirena_ui.workers.power_config import PowerConfig


def test_power_config_defaults(monkeypatch) -> None:
    for key in (
        "NINA_POWER_ENABLE",
        "NINA_POWER_IDLE_SEC",
        "NINA_POWER_SLEEP_SEC",
    ):
        monkeypatch.delenv(key, raising=False)
    cfg = PowerConfig.from_env()
    assert cfg.enabled is True
    assert cfg.idle_sec == 300
    assert cfg.sleep_sec == 900


def test_power_config_sleep_at_least_idle_plus_margin(monkeypatch) -> None:
    monkeypatch.setenv("NINA_POWER_IDLE_SEC", "600")
    monkeypatch.setenv("NINA_POWER_SLEEP_SEC", "100")
    cfg = PowerConfig.from_env()
    assert cfg.sleep_sec >= cfg.idle_sec + 30
