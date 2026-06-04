"""Tests for manifest eye_expression / eye_offset helpers."""

from __future__ import annotations

import json
from pathlib import Path

from nina.jetson_net.manifest_eye import (
    action_eye_info,
    get_action_eye_info,
    set_action_eye,
    set_action_eye_offset_only,
)


def _write_manifest(path: Path, actions: dict) -> None:
    path.write_text(
        json.dumps({"actions": actions}, indent=2),
        encoding="utf-8",
    )


def test_set_and_get_eye_binding(tmp_path: Path) -> None:
    manifest = tmp_path / "manifest.json"
    _write_manifest(manifest, {"wave": {"file": "recordings/wave.json"}})

    set_action_eye(manifest, "wave", 7, eye_offset=1.25)
    info = get_action_eye_info(manifest, "wave")
    assert info["eye_expression"] == 7
    assert info["eye_expression_name"] == "wink_right"
    assert info["eye_offset"] == 1.25

    data = json.loads(manifest.read_text(encoding="utf-8"))
    entry = data["actions"]["wave"]
    assert entry["eye_expression"] == 7
    assert entry["eye_offset"] == 1.25


def test_clear_eye_binding(tmp_path: Path) -> None:
    manifest = tmp_path / "manifest.json"
    _write_manifest(
        manifest,
        {"wave": {"file": "recordings/wave.json", "eye_expression": 3, "eye_offset": 0.5}},
    )

    set_action_eye(manifest, "wave", None)
    info = action_eye_info(manifest, "wave")
    assert info["eye_expression"] is None
    assert info["eye_offset"] == 0.0

    data = json.loads(manifest.read_text(encoding="utf-8"))
    entry = data["actions"]["wave"]
    assert entry == {"file": "recordings/wave.json"} or entry == "recordings/wave.json"


def test_offset_only_requires_expression(tmp_path: Path) -> None:
    manifest = tmp_path / "manifest.json"
    _write_manifest(manifest, {"wave": "recordings/wave.json"})

    try:
        set_action_eye_offset_only(manifest, "wave", 2.0)
    except ValueError as exc:
        assert "no eye_expression" in str(exc)
    else:
        raise AssertionError("expected ValueError")
