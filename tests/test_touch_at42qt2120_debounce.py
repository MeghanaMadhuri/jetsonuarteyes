"""Unit tests for AT42QT2120 touch debounce helper."""

from __future__ import annotations

from nina.sensors.touch_at42qt2120_monitor import touch_debounce_step


def test_touch_debounce_fires_after_n_reads() -> None:
    hits = 0
    for _ in range(2):
        fire, hits = touch_debounce_step(True, debounce_reads=3, consecutive_hits=hits)
        assert not fire
    fire, hits = touch_debounce_step(True, debounce_reads=3, consecutive_hits=hits)
    assert fire
    assert hits == 0


def test_touch_debounce_resets_when_not_touched() -> None:
    fire, hits = touch_debounce_step(False, debounce_reads=2, consecutive_hits=5)
    assert not fire
    assert hits == 0
