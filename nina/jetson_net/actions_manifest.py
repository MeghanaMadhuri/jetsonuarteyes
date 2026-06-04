"""Parse ``manifest.json`` for HTTP listing — no Dynamixel access."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List


def list_recordings_on_disk(manifest_path: Path) -> List[Dict[str, Any]]:
    """``recordings/*.json`` next to ``manifest.json`` — no Dynamixel access."""
    rd = manifest_path.parent / "recordings"
    if not rd.is_dir():
        return []
    out: List[Dict[str, Any]] = []
    for p in sorted(rd.glob("*.json")):
        try:
            st = p.stat()
            out.append(
                {
                    "file": f"recordings/{p.name}",
                    "name": p.stem,
                    "size_bytes": st.st_size,
                    "mtime": st.st_mtime,
                }
            )
        except OSError:
            continue
    return out


def _motion_duration_frames(manifest_parent: Path, rel_file: Any) -> tuple[Optional[float], Optional[int]]:
    """Read ``recordings/*.json`` next to manifest for duration / frame count (Qt PlaybackPanel parity)."""
    if not rel_file or not isinstance(rel_file, str):
        return None, None
    p = manifest_parent / rel_file
    if not p.is_file():
        return None, None
    try:
        motion = json.loads(p.read_text(encoding="utf-8"))
        frames = motion.get("frames") or []
        count = len(frames)
        if not frames:
            return 0.0, 0
        total = sum(
            float(f.get("duration", 0.0)) + float(f.get("delay", 0.0)) for f in frames
        )
        return total, count
    except (OSError, json.JSONDecodeError, TypeError, ValueError):
        return None, None


def load_manifest_actions(path: Path) -> List[Dict[str, Any]]:
    if not path.exists():
        return []
    data = json.loads(path.read_text(encoding="utf-8"))
    raw = data.get("actions") or {}
    root = path.parent
    out: List[Dict[str, Any]] = []
    for name, entry in raw.items():
        if isinstance(entry, str):
            dur, nfrm = _motion_duration_frames(root, entry)
            out.append(
                {
                    "name": name,
                    "file": entry,
                    "audio": None,
                    "audio_offset": None,
                    "eye_expression": None,
                    "eye_offset": None,
                    "duration_sec": dur,
                    "frame_count": nfrm,
                }
            )
        elif isinstance(entry, dict):
            rel = entry.get("file")
            dur, nfrm = _motion_duration_frames(root, rel)
            out.append(
                {
                    "name": name,
                    "file": rel,
                    "audio": entry.get("audio"),
                    "audio_offset": entry.get("audio_offset"),
                    "eye_expression": entry.get("eye_expression"),
                    "eye_offset": entry.get("eye_offset"),
                    "duration_sec": dur,
                    "frame_count": nfrm,
                }
            )
    out.sort(key=lambda x: str(x.get("name", "")).lower())
    return out
