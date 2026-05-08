"""Tests for BLDC spoken alerts (cooldown + disable env)."""

from __future__ import annotations

import threading

import pytest

import nina.services.bldc_speech_alerts as alerts


def test_bldc_alert_respects_disable_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NINA_BLDC_ALERT_SPEECH", "0")
    called: list[str] = []

    def fake_thread(*_a, **_k):
        called.append("would-thread")

    monkeypatch.setattr(threading, "Thread", fake_thread)
    alerts.maybe_speak_bldc_alert("hello")
    assert called == []


def test_bldc_alert_cooldown_same_message(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("NINA_BLDC_ALERT_SPEECH", raising=False)
    monkeypatch.setenv("NINA_BLDC_ALERT_COOLDOWN_SEC", "30")
    runs = {"n": 0}

    monkeypatch.setattr(alerts.shutil, "which", lambda _p: "/usr/bin/espeak-ng")

    def fake_run(*_a, **_k):
        runs["n"] += 1

        class R:
            returncode = 0
            stderr = b""

        return R()

    monkeypatch.setattr(alerts.subprocess, "run", fake_run)
    monkeypatch.setattr(alerts, "play_silence_preroll_blocking", lambda: None)

    class _ImmediateThread(threading.Thread):
        def start(self) -> None:
            self.run()

    monkeypatch.setattr(threading, "Thread", _ImmediateThread)

    alerts._last_key = None  # type: ignore[attr-defined]
    alerts._last_at = 0.0  # type: ignore[attr-defined]

    alerts.maybe_speak_bldc_alert("same error")
    alerts.maybe_speak_bldc_alert("same error")
    assert runs["n"] == 1
