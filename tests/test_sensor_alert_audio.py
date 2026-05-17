"""Bundled sensor alert MP3 paths and phrase constants."""

from __future__ import annotations

import threading

import pytest

import nina.services.sensor_alert_audio as sal
from nina.sensors.ads1115 import LOW_BATTERY_TTS
from nina.sensors.at42qt2120 import DEFAULT_TOUCH_TTS
from nina.services.sensor_alert_audio import (
    DEFAULT_LOW_BATTERY_COOLDOWN_SEC,
    DEFAULT_OBSTACLE_TTS,
    LOW_BATTERY_ALERT_MP3,
    OBSTACLE_ALERT_MP3,
    TOUCH_ALERT_MP3,
    _low_battery_cooldown_sec,
    _reset_low_battery_cooldown_for_tests,
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
    # Operator-locked low-battery phrase. Used only as the gTTS fallback
    # when the bundled MP3 is missing — in production the operator
    # always hears the bundled ``low_battery.mp3`` voice.
    assert LOW_BATTERY_TTS == "I'm low on battery"
    assert DEFAULT_TOUCH_TTS == "Please dont touch me"
    assert "obstacle" in DEFAULT_OBSTACLE_TTS.lower()


class _ImmediateThread(threading.Thread):
    """Drop-in replacement for ``threading.Thread`` that runs the target
    synchronously inside :meth:`start`. Mirrors the pattern in
    ``test_bldc_speech_alerts.py`` so tests don't have to race a
    background daemon to observe playback.
    """

    def start(self) -> None:  # type: ignore[override]
        self.run()


@pytest.fixture
def synchronous_speak(monkeypatch: pytest.MonkeyPatch):
    """Make ``maybe_speak_low_battery`` execute its playback synchronously
    AND return a list that records every ``play_low_battery_alert`` call.
    """
    _reset_low_battery_cooldown_for_tests()
    monkeypatch.setattr(threading, "Thread", _ImmediateThread)
    calls: list[dict] = []

    def fake_play(*, phrase: str | None = None) -> None:
        calls.append({"phrase": phrase})

    monkeypatch.setattr(sal, "play_low_battery_alert", fake_play)
    yield calls
    _reset_low_battery_cooldown_for_tests()


def test_maybe_speak_low_battery_plays_bundled_mp3_by_default(
    synchronous_speak: list[dict],
) -> None:
    """All low-battery TTS (initial latch, per-0.2 V repeats, and worker
    refusals) must drive the bundled MP3 playback path so the operator
    hears the same voice regardless of which subsystem fired the alert.
    """
    maybe_speak_low_battery()
    assert synchronous_speak == [{"phrase": None}], (
        f"expected one MP3 playback with phrase=None; saw {synchronous_speak}"
    )


def test_maybe_speak_low_battery_honours_explicit_phrase(
    synchronous_speak: list[dict],
) -> None:
    """Callers may pass a custom phrase (settings override). The phrase
    reaches ``play_low_battery_alert`` verbatim so the gTTS fallback path
    (used only when the bundled MP3 is missing) renders it.
    """
    maybe_speak_low_battery("Pack at 25 point two volts")
    assert synchronous_speak == [{"phrase": "Pack at 25 point two volts"}]


def test_maybe_speak_low_battery_swallows_playback_failures(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A broken playback path must not crash the monitor thread or the
    worker that's currently refusing a command — failures are logged and
    silently dropped.
    """
    _reset_low_battery_cooldown_for_tests()
    monkeypatch.setattr(threading, "Thread", _ImmediateThread)

    def boom(*, phrase: str | None = None) -> None:
        raise RuntimeError("mpg123 wedged")

    monkeypatch.setattr(sal, "play_low_battery_alert", boom)
    # Must not raise even though the playback path throws.
    maybe_speak_low_battery()
    _reset_low_battery_cooldown_for_tests()


def test_maybe_speak_low_battery_cooldown_dedups_rapid_calls(
    monkeypatch: pytest.MonkeyPatch, synchronous_speak: list[dict],
) -> None:
    """Three calls inside one cooldown window must produce exactly one
    playback — this is what prevents D-pad mashing or a steep voltage
    drop from spawning overlapping mpg123 processes on the same ALSA
    sink.
    """
    monkeypatch.setenv("NINA_BATTERY_ALERT_COOLDOWN_SEC", "60")
    maybe_speak_low_battery()
    maybe_speak_low_battery()
    maybe_speak_low_battery()
    assert len(synchronous_speak) == 1, (
        f"cooldown must collapse rapid calls; saw {len(synchronous_speak)} "
        f"actual playbacks: {synchronous_speak}"
    )


def test_maybe_speak_low_battery_cooldown_clears_after_window(
    monkeypatch: pytest.MonkeyPatch, synchronous_speak: list[dict],
) -> None:
    """After ``NINA_BATTERY_ALERT_COOLDOWN_SEC`` elapses (or the cooldown
    is explicitly reset), the next call must fire a fresh playback so
    the per-0.2 V repeat warning continues to work over long discharges.
    """
    monkeypatch.setenv("NINA_BATTERY_ALERT_COOLDOWN_SEC", "60")
    maybe_speak_low_battery()
    assert len(synchronous_speak) == 1
    # Simulate "the cooldown elapsed" by resetting the timestamp.
    _reset_low_battery_cooldown_for_tests()
    maybe_speak_low_battery()
    assert len(synchronous_speak) == 2, (
        "cooldown clear must let the next call play a fresh alert"
    )


def test_low_battery_cooldown_env_overrides_and_clamps(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``NINA_BATTERY_ALERT_COOLDOWN_SEC`` overrides the default; invalid
    or out-of-range values fall back to the safe default / clamp.
    """
    monkeypatch.setenv("NINA_BATTERY_ALERT_COOLDOWN_SEC", "3.5")
    assert _low_battery_cooldown_sec() == 3.5
    # Negative clamped to 0 (disables the cooldown entirely).
    monkeypatch.setenv("NINA_BATTERY_ALERT_COOLDOWN_SEC", "-5")
    assert _low_battery_cooldown_sec() == 0.0
    # Absurd upper bound clamped to 600 s.
    monkeypatch.setenv("NINA_BATTERY_ALERT_COOLDOWN_SEC", "9999")
    assert _low_battery_cooldown_sec() == 600.0
    # Invalid -> safe default.
    monkeypatch.setenv("NINA_BATTERY_ALERT_COOLDOWN_SEC", "garbage")
    assert _low_battery_cooldown_sec() == DEFAULT_LOW_BATTERY_COOLDOWN_SEC
