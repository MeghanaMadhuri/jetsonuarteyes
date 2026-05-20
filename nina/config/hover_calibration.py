"""Read persisted hover lean ticks from ``/etc/nina-link/navigation.env``."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Dict, Optional

from nina.config.navigation_env import DEFAULT_ENV_PATH, _KEY_LINE_RE

_HOVER_POS_PREFIX = "NINA_HOVER_"


def _env_path() -> Path:
    custom = (os.environ.get("NINA_NAVIGATION_ENV_PATH") or "").strip()
    if custom:
        return Path(custom)
    return DEFAULT_ENV_PATH


def read_hover_calibration_ints(
    env_path: Optional[Path] = None,
) -> Dict[str, int]:
    """Return ``NINA_HOVER_*_POS_*`` integer overrides present in the env file.

    Used by the tablet motion-calibration snapshot. Missing file or unreadable
    path yields an empty dict (runtime defaults from ``settings.py`` apply).
    """
    path = env_path or _env_path()
    if not path.exists():
        return {}
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return {}

    out: Dict[str, int] = {}
    for line in text.splitlines():
        m = _KEY_LINE_RE.match(line)
        if m is None:
            continue
        key = m.group("key")
        if not key.startswith(_HOVER_POS_PREFIX) or "_POS_" not in key:
            continue
        raw = m.group("value").strip().strip("\"'")
        try:
            out[key] = int(raw)
        except ValueError:
            continue
    return out
