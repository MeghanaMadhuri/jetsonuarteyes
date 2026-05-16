"""Bundled sensor alert MP3 paths and phrase constants."""

from __future__ import annotations

from nina.sensors.ads1115 import LOW_BATTERY_TTS
from nina.sensors.at42qt2120 import DEFAULT_TOUCH_TTS
from nina.services.sensor_alert_audio import (
    LOW_BATTERY_ALERT_MP3,
    TOUCH_ALERT_MP3,
)


def test_alert_mp3_files_exist() -> None:
    assert LOW_BATTERY_ALERT_MP3.is_file(), f"missing {LOW_BATTERY_ALERT_MP3}"
    assert TOUCH_ALERT_MP3.is_file(), f"missing {TOUCH_ALERT_MP3}"
    assert LOW_BATTERY_ALERT_MP3.stat().st_size > 1000
    assert TOUCH_ALERT_MP3.stat().st_size > 1000


def test_alert_phrase_constants() -> None:
    assert "low on battery" in LOW_BATTERY_TTS.lower()
    assert "Please" in LOW_BATTERY_TTS
    assert DEFAULT_TOUCH_TTS == "Please dont touch me"
