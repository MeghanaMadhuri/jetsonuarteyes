from __future__ import annotations

import json
from pathlib import Path

from nina.movements.model import (
    STEP_BRAKE,
    STEP_FORWARD,
    STEP_TURN_RIGHT,
    MovementStep,
    SavedMovement,
    step_from_dict,
    step_to_dict,
)
from nina.movements.store import MovementStore


def test_movement_store_roundtrip(tmp_path: Path) -> None:
    path = tmp_path / "saved_movements.json"
    store = MovementStore(path)
    mv = SavedMovement(
        name="Test patrol",
        steps=[
            MovementStep(kind=STEP_FORWARD, seconds=2.5),
            MovementStep(kind=STEP_TURN_RIGHT, degrees=45.0),
        ],
    )
    store.upsert(mv)
    loaded = store.load_all()
    assert len(loaded) == 1
    assert loaded[0].name == "Test patrol"
    assert len(loaded[0].steps) == 2
    assert loaded[0].steps[0].seconds == 2.5
    assert loaded[0].steps[1].degrees == 45.0

    raw = json.loads(path.read_text(encoding="utf-8"))
    assert "movements" in raw
    assert store.delete(mv.movement_id)
    assert store.load_all() == []


def test_brake_step_roundtrip() -> None:
    step = MovementStep(kind=STEP_BRAKE)
    assert step.summary() == "Brake (hold pose)"
    raw = step_to_dict(step)
    loaded = step_from_dict(raw)
    assert loaded.kind == STEP_BRAKE
