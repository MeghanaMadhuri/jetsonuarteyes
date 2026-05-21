"""Data model for operator-authored drive sequences."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional
import uuid


STEP_FORWARD = "forward"
STEP_BACKWARD = "backward"
STEP_TURN_LEFT = "turn_left"
STEP_TURN_RIGHT = "turn_right"
STEP_UTURN = "uturn"

VALID_STEP_KINDS = frozenset(
    {
        STEP_FORWARD,
        STEP_BACKWARD,
        STEP_TURN_LEFT,
        STEP_TURN_RIGHT,
        STEP_UTURN,
    }
)


@dataclass
class MovementStep:
    """One leg of a saved movement."""

    kind: str
    seconds: float = 0.0
    degrees: float = 0.0
    uturn_direction: str = "right"  # "left" or "right" when kind == uturn

    def summary(self) -> str:
        k = self.kind
        if k == STEP_FORWARD:
            return f"Forward {self.seconds:.1f} s"
        if k == STEP_BACKWARD:
            return f"Backward {self.seconds:.1f} s"
        if k == STEP_TURN_LEFT:
            return f"Turn left {self.degrees:.0f}°"
        if k == STEP_TURN_RIGHT:
            return f"Turn right {self.degrees:.0f}°"
        if k == STEP_UTURN:
            d = "left" if self.uturn_direction == "left" else "right"
            return f"U-turn 360° ({d})"
        return k


@dataclass
class SavedMovement:
    """Named sequence of :class:`MovementStep` items."""

    name: str
    steps: List[MovementStep] = field(default_factory=list)
    movement_id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])

    def summary(self) -> str:
        n = len(self.steps)
        return f"{n} step{'s' if n != 1 else ''}"


def step_to_dict(step: MovementStep) -> Dict[str, Any]:
    return {
        "kind": step.kind,
        "seconds": float(step.seconds),
        "degrees": float(step.degrees),
        "uturn_direction": step.uturn_direction,
    }


def step_from_dict(raw: Dict[str, Any]) -> MovementStep:
    kind = str(raw.get("kind") or "").strip()
    if kind not in VALID_STEP_KINDS:
        raise ValueError(f"Unknown step kind: {kind!r}")
    uturn_dir = str(raw.get("uturn_direction") or "right").strip().lower()
    if uturn_dir not in ("left", "right"):
        uturn_dir = "right"
    return MovementStep(
        kind=kind,
        seconds=float(raw.get("seconds") or 0.0),
        degrees=float(raw.get("degrees") or 0.0),
        uturn_direction=uturn_dir,
    )


def movement_from_dict(raw: Dict[str, Any]) -> SavedMovement:
    name = str(raw.get("name") or "").strip()
    if not name:
        raise ValueError("Movement name is required")
    mid = str(raw.get("id") or raw.get("movement_id") or "").strip() or uuid.uuid4().hex[:12]
    steps_raw = raw.get("steps") or []
    if not isinstance(steps_raw, list):
        raise ValueError("steps must be a list")
    steps = [step_from_dict(s) for s in steps_raw if isinstance(s, dict)]
    return SavedMovement(name=name, steps=steps, movement_id=mid)


def movement_to_dict(mv: SavedMovement) -> Dict[str, Any]:
    return {
        "id": mv.movement_id,
        "name": mv.name,
        "steps": [step_to_dict(s) for s in mv.steps],
    }
