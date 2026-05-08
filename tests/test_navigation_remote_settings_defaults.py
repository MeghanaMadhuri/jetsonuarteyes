"""Navigation defaults when ``NINA_NAV_MODE=remote`` (Pi serial bridge).

Values align with Sirena_Humanoid-2 / UBOT_app field reference (see
``nina.config.settings.load_settings`` doc block).
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from nina.config.settings import load_settings


REPO_ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture
def clean_nav_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in list(os.environ):
        if key.startswith("NINA_NAV_"):
            monkeypatch.delenv(key, raising=False)


def test_remote_mode_ignored_without_legacy_flag(clean_nav_env, monkeypatch) -> None:
    """Stale remote+ttyTHS1 footgun: without opt-in, we stay on Jetson GPIO."""
    monkeypatch.setenv("NINA_NAV_MODE", "remote")
    monkeypatch.setenv("NINA_NAV_REMOTE_PORT", "/dev/ttyTHS1")
    s = load_settings(REPO_ROOT)
    assert s.navigation.mode == "local"


def test_local_navigation_defaults_unchanged(clean_nav_env, monkeypatch) -> None:
    monkeypatch.setenv("NINA_NAV_MODE", "local")
    s = load_settings(REPO_ROOT)
    assert s.navigation.mode == "local"
    assert s.navigation.default_speed_percent == 8
    assert s.navigation.dir_pwm_gap_sec == pytest.approx(0.03)
    assert s.navigation.settle_delay_sec == pytest.approx(0.1)


def test_remote_navigation_defaults_match_rpi_tcp_ref(clean_nav_env, monkeypatch) -> None:
    monkeypatch.setenv("NINA_NAV_LEGACY_PI_BRIDGE", "1")
    monkeypatch.setenv("NINA_NAV_MODE", "remote")
    s = load_settings(REPO_ROOT)
    assert s.navigation.mode == "remote"
    assert s.navigation.default_speed_percent == 13
    assert s.navigation.dir_pwm_gap_sec == pytest.approx(0.1)
    assert s.navigation.settle_delay_sec == pytest.approx(0.25)


def test_remote_defaults_can_be_overridden_by_env(clean_nav_env, monkeypatch) -> None:
    monkeypatch.setenv("NINA_NAV_LEGACY_PI_BRIDGE", "1")
    monkeypatch.setenv("NINA_NAV_MODE", "remote")
    monkeypatch.setenv("NINA_NAV_SPEED", "9")
    monkeypatch.setenv("NINA_NAV_SETTLE_SEC", "0.15")
    monkeypatch.setenv("NINA_NAV_DIR_SETTLE_SEC", "0.05")
    s = load_settings(REPO_ROOT)
    assert s.navigation.default_speed_percent == 9
    assert s.navigation.settle_delay_sec == pytest.approx(0.15)
    assert s.navigation.dir_pwm_gap_sec == pytest.approx(0.05)
