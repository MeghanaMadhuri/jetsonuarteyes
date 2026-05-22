"""Tests for persisted low-battery latch state."""

import json
from pathlib import Path

from nina.sensors.battery_latch_store import (
    BatteryLatchPersisted,
    clear_battery_latch_state,
    load_battery_latch_state,
    save_battery_latch_state,
    snapshot_latched,
)


def test_round_trip_latch_state(tmp_path: Path, monkeypatch) -> None:
    path = tmp_path / "battery_latch.json"
    monkeypatch.setenv("NINA_BATTERY_LATCH_STATE_PATH", str(path))
    monkeypatch.setenv("NINA_BATTERY_LATCH_PERSIST", "1")

    snapshot_latched(pack_v=25.4, last_announced_v=25.5, pack_min_v=25.3)
    loaded = load_battery_latch_state(path)
    assert loaded is not None
    assert loaded.latched is True
    assert loaded.pack_v_at_latch == 25.4
    assert loaded.last_announced_v == 25.5
    assert loaded.pack_min_v == 25.3

    clear_battery_latch_state(path=path)
    assert load_battery_latch_state(path) is None


def test_corrupt_file_returns_none(tmp_path: Path, monkeypatch) -> None:
    path = tmp_path / "battery_latch.json"
    monkeypatch.setenv("NINA_BATTERY_LATCH_STATE_PATH", str(path))
    path.write_text("not json", encoding="utf-8")
    assert load_battery_latch_state(path) is None


def test_unlatched_json_ignored(tmp_path: Path, monkeypatch) -> None:
    path = tmp_path / "battery_latch.json"
    monkeypatch.setenv("NINA_BATTERY_LATCH_STATE_PATH", str(path))
    save_battery_latch_state(BatteryLatchPersisted(latched=False))
    assert load_battery_latch_state(path) is None
