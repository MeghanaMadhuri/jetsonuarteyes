"""Catalog of TFT eye expression IDs (must match ESP firmware Serial.parseInt 0–36)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple


@dataclass(frozen=True)
class EyeExpression:
    id: int
    name: str
    mood: str  # "+", "-", "~" — matches firmware menu hints

    @property
    def label(self) -> str:
        return f"{self.id} {self.name}"

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "mood": self.mood,
            "label": self.label,
        }


# Order and names aligned with the Arduino ``printExpressionMenu()`` / switch cases.
EYE_EXPRESSIONS: Tuple[EyeExpression, ...] = (
    EyeExpression(0, "neutral", "~"),
    EyeExpression(1, "happy", "+"),
    EyeExpression(2, "excited", "+"),
    EyeExpression(3, "sad", "-"),
    EyeExpression(4, "sleepy", "~"),
    EyeExpression(5, "angry", "-"),
    EyeExpression(6, "shocked", "~"),
    EyeExpression(7, "wink_right", "~"),
    EyeExpression(8, "focus", "+"),
    EyeExpression(9, "loading", "~"),
    EyeExpression(10, "scan", "~"),
    EyeExpression(11, "pulse", "~"),
    EyeExpression(12, "glitch", "~"),
    EyeExpression(13, "look_down", "~"),
    EyeExpression(14, "look_up", "~"),
    EyeExpression(15, "love", "+"),
    EyeExpression(16, "look_left", "~"),
    EyeExpression(17, "look_right", "~"),
    EyeExpression(18, "double_blink", "~"),
    EyeExpression(19, "squint", "-"),
    EyeExpression(20, "confused", "~"),
    EyeExpression(21, "sleep", "-"),
    EyeExpression(22, "curious", "+"),
    EyeExpression(23, "side_eye", "~"),
    EyeExpression(24, "listening", "+"),
    EyeExpression(25, "thinking", "+"),
    EyeExpression(26, "acknowledging", "+"),
    EyeExpression(27, "unsure", "~"),
    EyeExpression(28, "relief", "+"),
    EyeExpression(29, "tracking", "~"),
    EyeExpression(30, "shy", "+"),
    EyeExpression(31, "concerned", "-"),
    EyeExpression(32, "wink_left", "~"),
    EyeExpression(33, "wink_both", "~"),
    EyeExpression(34, "screen_red", "~"),
    EyeExpression(35, "screen_blue", "~"),
    EyeExpression(36, "screen_green", "~"),
)

_BY_ID: Dict[int, EyeExpression] = {e.id: e for e in EYE_EXPRESSIONS}


def expression_by_id(expr_id: int) -> Optional[EyeExpression]:
    return _BY_ID.get(int(expr_id))


def expressions_as_dicts() -> List[dict]:
    return [e.to_dict() for e in EYE_EXPRESSIONS]
