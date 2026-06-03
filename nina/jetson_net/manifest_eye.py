"""Manifest eye-expression helpers (UART expression id + offset)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Optional

from nina.eye.expressions import expression_by_id


def _load_manifest(manifest_path: Path) -> Dict[str, Any]:
    if not manifest_path.exists():
        raise FileNotFoundError(f"Manifest not found: {manifest_path}")
    return json.loads(manifest_path.read_text(encoding="utf-8"))


def _save_manifest(manifest_path: Path, data: Dict[str, Any]) -> None:
    manifest_path.write_text(json.dumps(data, indent=2), encoding="utf-8")


def _parse_eye_id(raw: Any) -> Optional[int]:
    if raw is None:
        return None
    if isinstance(raw, bool):
        return None
    try:
        eid = int(raw)
    except (TypeError, ValueError):
        return None
    if 0 <= eid <= 33:
        return eid
    return None


def action_eye_info(manifest_path: Path, action_name: str) -> Dict[str, Any]:
    """HTTP / tablet entry point."""
    return get_action_eye_info(manifest_path, action_name)


def get_action_eye_info(
    manifest_path: Path, action_name: str
) -> Dict[str, Any]:
    manifest = _load_manifest(manifest_path)
    actions = manifest.get("actions") or {}
    if action_name not in actions:
        raise ValueError(f"Action '{action_name}' is not in the manifest.")
    entry = actions[action_name]
    expr_id: Optional[int] = None
    offset = 0.0
    if isinstance(entry, dict):
        expr_id = _parse_eye_id(entry.get("eye_expression"))
        off = entry.get("eye_offset")
        if isinstance(off, (int, float)):
            offset = max(0.0, float(off))
    meta = expression_by_id(expr_id) if expr_id is not None else None
    return {
        "action": action_name,
        "eye_expression": expr_id,
        "eye_expression_name": meta.name if meta else None,
        "eye_offset": offset,
    }


def set_action_eye(
    manifest_path: Path,
    action_name: str,
    expression_id: Optional[int],
    *,
    eye_offset: Optional[float] = None,
) -> None:
    """Bind an expression id (0–33) to an action; ``None`` clears eye fields."""
    manifest = _load_manifest(manifest_path)
    actions = manifest.setdefault("actions", {})
    existing = actions.get(action_name)
    if existing is None:
        raise ValueError(f"Action '{action_name}' is not in the manifest.")

    if isinstance(existing, dict):
        file_name = str(existing.get("file", ""))
        entry: Dict[str, Any] = dict(existing)
    else:
        file_name = str(existing)
        entry = {"file": file_name}

    entry["file"] = file_name
    if expression_id is None:
        entry.pop("eye_expression", None)
        entry.pop("eye_offset", None)
        if len(entry) == 1 and "file" in entry:
            actions[action_name] = file_name
        else:
            actions[action_name] = entry
    else:
        eid = int(expression_id)
        if eid < 0 or eid > 33:
            raise ValueError("eye_expression must be 0..33")
        entry["eye_expression"] = eid
        if eye_offset is not None:
            if eye_offset > 0:
                entry["eye_offset"] = float(eye_offset)
            else:
                entry.pop("eye_offset", None)
        elif "eye_offset" not in entry:
            pass
        actions[action_name] = entry

    _save_manifest(manifest_path, manifest)


def set_action_eye_offset_only(
    manifest_path: Path, action_name: str, eye_offset: float
) -> None:
    info = get_action_eye_info(manifest_path, action_name)
    eid = info.get("eye_expression")
    if eid is None:
        raise ValueError(
            f"Action '{action_name}' has no eye_expression; set expression id first."
        )
    set_action_eye(manifest_path, action_name, int(eid), eye_offset=eye_offset)
