from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
VISION_SCREEN = ROOT / "sirena_ui" / "screens" / "vision_screen.py"


def test_vision_detectors_default_to_opt_in() -> None:
    text = VISION_SCREEN.read_text(encoding="utf-8")

    assert 'return _env_bool("NINA_VISION_FACE_AUTO", False)' in text
    assert 'return _env_bool("NINA_VISION_OBJECT_AUTO", False)' in text
    assert '_ToggleRow("Object detection", on=True)' not in text
