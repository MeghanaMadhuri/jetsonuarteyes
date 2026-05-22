"""Unit tests for AT42QT2120 touch debounce / re-arm helpers."""

from __future__ import annotations

from nina.sensors.touch_at42qt2120_monitor import (
    touch_baseline_ready_step,
    touch_debounce_step,
    touch_release_rearm_step,
    touch_rising_edge_debounce_step,
    touch_stuck_high_step,
)


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


def test_rising_edge_requires_fresh_edge_for_first_hit() -> None:
    fire, hits = touch_rising_edge_debounce_step(
        True, True, debounce_reads=3, consecutive_hits=0
    )
    assert not fire
    assert hits == 1


def test_rising_edge_fires_after_n_consecutive_touched_polls() -> None:
    hits = 0
    prev = False
    fired = False
    for _ in range(2):
        fire, hits = touch_rising_edge_debounce_step(
            True, prev, debounce_reads=3, consecutive_hits=hits
        )
        assert not fire
        prev = True
    fire, hits = touch_rising_edge_debounce_step(
        True, prev, debounce_reads=3, consecutive_hits=hits
    )
    assert fire
    fired = True
    assert hits == 0
    assert fired


def test_rising_edge_resets_when_released() -> None:
    _, hits = touch_rising_edge_debounce_step(
        True, False, debounce_reads=3, consecutive_hits=0
    )
    assert hits == 1
    fire, hits = touch_rising_edge_debounce_step(
        False, True, debounce_reads=3, consecutive_hits=hits
    )
    assert not fire
    assert hits == 0


def test_release_rearm_stays_disarmed_while_touch_held() -> None:
    armed, clear = touch_release_rearm_step(
        True, armed=False, release_reads=2, consecutive_clear=0
    )
    assert not armed
    assert clear == 0


def test_release_rearm_after_consecutive_clear_polls() -> None:
    armed, clear = touch_release_rearm_step(
        False, armed=False, release_reads=2, consecutive_clear=0
    )
    assert not armed
    assert clear == 1
    armed, clear = touch_release_rearm_step(
        False, armed=False, release_reads=2, consecutive_clear=clear
    )
    assert armed
    assert clear == 0


def test_stuck_high_suppresses_effective_touch() -> None:
    t0 = 100.0
    eff, latched, since, clear_hits, newly = touch_stuck_high_step(
        True,
        stuck_latched=False,
        touch_high_since_mono=t0,
        stuck_clear_hits=0,
        now=t0 + 2.1,
        stuck_after_sec=2.0,
        stuck_clear_reads=5,
    )
    assert not eff
    assert latched
    assert newly


def test_stuck_high_clears_after_release() -> None:
    _, latched, _, clear_hits, _ = touch_stuck_high_step(
        False,
        stuck_latched=True,
        touch_high_since_mono=None,
        stuck_clear_hits=3,
        now=0.0,
        stuck_after_sec=2.0,
        stuck_clear_reads=5,
    )
    assert latched
    assert clear_hits == 4
    eff, latched, _, _, _ = touch_stuck_high_step(
        False,
        stuck_latched=True,
        touch_high_since_mono=None,
        stuck_clear_hits=5,
        now=0.0,
        stuck_after_sec=2.0,
        stuck_clear_reads=5,
    )
    assert not eff
    assert not latched


def test_baseline_requires_clear_before_ready() -> None:
    ready, hits = touch_baseline_ready_step(
        True, baseline_ready=False, baseline_clear_reads=3, consecutive_clear=0
    )
    assert not ready
    assert hits == 0
    ready, hits = touch_baseline_ready_step(
        False, baseline_ready=False, baseline_clear_reads=3, consecutive_clear=2
    )
    assert ready
    assert hits == 0


def test_simulated_monitor_loop_one_fire_per_gesture() -> None:
    """Exercise the same state transitions the monitor thread uses."""
    armed = True
    prev = False
    hits = 0
    release_hits = 0
    fires = 0
    debounce_reads = 5
    release_reads = 5

    def poll(touched: bool) -> None:
        nonlocal armed, prev, hits, release_hits, fires
        if not armed:
            armed, release_hits = touch_release_rearm_step(
                touched,
                armed=False,
                release_reads=release_reads,
                consecutive_clear=release_hits,
            )
            hits = 0
            prev = touched
            return
        if not touched:
            hits = 0
            prev = False
            return
        fire, hits = touch_rising_edge_debounce_step(
            touched, prev, debounce_reads=debounce_reads, consecutive_hits=hits
        )
        prev = touched
        if fire:
            fires += 1
            armed = False
            release_hits = 0
            hits = 0

    # Gesture 1
    for _ in range(debounce_reads):
        poll(True)
    for _ in range(5):
        poll(True)  # held — must not fire again
    for _ in range(release_reads):
        poll(False)
    # Gesture 2
    for _ in range(debounce_reads):
        poll(True)

    assert fires == 2
