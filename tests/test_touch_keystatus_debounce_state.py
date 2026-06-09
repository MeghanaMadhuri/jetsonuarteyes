"""Keystatus debounce state shared by bench test and production monitor."""

from __future__ import annotations

from nina.sensors.touch_at42qt2120_monitor import (
    KeystatusDebounceState,
    touch_min_fire_reads,
)


def test_touch_min_fire_reads_hold_wins() -> None:
    assert touch_min_fire_reads(5, 0.4, 0.05) == 8


def test_keystatus_state_matches_bench_fire_sequence() -> None:
    st = KeystatusDebounceState(fire_reads=3, release_reads=2)
    for _ in range(2):
        fire, note = st.step(True)
        assert not fire
        assert "debounce" in note
    fire, note = st.step(True)
    assert fire
    assert note == "FIRE #1"
    assert not st.armed
    fire, note = st.step(True)
    assert not fire
    assert note == "re-arming"
    st.step(False)
    fire, note = st.step(False)
    assert not fire
    assert st.armed
    fire, _ = st.step(True)
    assert not fire
