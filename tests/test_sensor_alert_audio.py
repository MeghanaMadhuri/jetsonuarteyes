"""Bundled sensor alert MP3 paths and phrase constants."""

from __future__ import annotations

from unittest.mock import patch

from nina.sensors.ads1115 import LOW_BATTERY_TTS
from nina.sensors.at42qt2120 import DEFAULT_TOUCH_TTS
from nina.services.sensor_alert_audio import (
    DEFAULT_OBSTACLE_TTS,
    LOW_BATTERY_ALERT_MP3,
    OBSTACLE_ALERT_MP3,
    TOUCH_ALERT_MP3,
    maybe_speak_low_battery,
)


def test_alert_mp3_files_exist() -> None:
    assert LOW_BATTERY_ALERT_MP3.is_file(), f"missing {LOW_BATTERY_ALERT_MP3}"
    assert TOUCH_ALERT_MP3.is_file(), f"missing {TOUCH_ALERT_MP3}"
    assert LOW_BATTERY_ALERT_MP3.stat().st_size > 1000
    assert TOUCH_ALERT_MP3.stat().st_size > 1000
    if not OBSTACLE_ALERT_MP3.is_file():
        raise AssertionError(
            f"missing {OBSTACLE_ALERT_MP3} — run: python3 scripts/generate-sensor-alert-audio.py"
        )
    assert OBSTACLE_ALERT_MP3.stat().st_size > 1000


def test_alert_phrase_constants() -> None:
    # Operator-locked low-battery phrase: short ``I'm low on battery``
    # (replaces the long ``I am low on battery , Please put me on charge``
    # so it fits inside one bldc speech-alert cooldown window).
    assert LOW_BATTERY_TTS == "I'm low on battery"
    assert DEFAULT_TOUCH_TTS == "Please dont touch me"
    assert "obstacle" in DEFAULT_OBSTACLE_TTS.lower()


def test_maybe_speak_low_battery_routes_through_bldc_espeak() -> None:
    """All low-battery TTS (initial latch, per-0.2 V repeats, and worker
    refusals) must go through the same bldc-speech-alerts entry-point so
    a single voice + a single per-message cooldown owns the chatter.
    """
    with patch(
        "nina.services.sensor_alert_audio.maybe_speak_bldc_alert"
    ) as bldc_speak:
        maybe_speak_low_battery()
    bldc_speak.assert_called_once_with(LOW_BATTERY_TTS)


def test_maybe_speak_low_battery_honours_explicit_phrase() -> None:
    """Callers may pass a custom phrase (e.g. settings override). When
    provided, that phrase reaches the speak path verbatim instead of
    the canonical constant.
    """
    with patch(
        "nina.services.sensor_alert_audio.maybe_speak_bldc_alert"
    ) as bldc_speak:
        maybe_speak_low_battery("Pack at 25 point two volts")
    bldc_speak.assert_called_once_with("Pack at 25 point two volts")


def test_maybe_speak_low_battery_swallows_speak_failures() -> None:
    """A broken speak path must not crash the monitor thread or the worker
    that's currently refusing a command.
    """
    with patch(
        "nina.services.sensor_alert_audio.maybe_speak_bldc_alert",
        side_effect=RuntimeError("espeak wedged"),
    ):
        # Should not raise.
        maybe_speak_low_battery()
