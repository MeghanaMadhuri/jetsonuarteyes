"""Tests for shared action playback tempo."""

from nina.controllers.action_runner import action_playback_speed


def test_action_playback_speed_default_is_half_tempo() -> None:
    assert action_playback_speed() == 0.5


def test_action_playback_speed_env_override(monkeypatch) -> None:
    monkeypatch.setenv("NINA_ACTION_PLAYBACK_SPEED", "1.0")
    assert action_playback_speed() == 1.0
