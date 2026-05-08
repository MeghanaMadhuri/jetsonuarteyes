"""Tests for :mod:`sirena_ui.workers.straight_bench_speed` (no PyQt)."""

from __future__ import annotations

import os

from sirena_ui.workers.straight_bench_speed import straight_bench_speed_pct


def test_straight_bench_speed_clamped(monkeypatch) -> None:
    monkeypatch.delenv("NINA_STRAIGHT_TEST_SPEED_PCT", raising=False)
    v = straight_bench_speed_pct()
    assert 8 <= v <= 100

    monkeypatch.setenv("NINA_STRAIGHT_TEST_SPEED_PCT", "5")
    assert straight_bench_speed_pct() == 8

    monkeypatch.setenv("NINA_STRAIGHT_TEST_SPEED_PCT", "99")
    assert straight_bench_speed_pct() == 99
