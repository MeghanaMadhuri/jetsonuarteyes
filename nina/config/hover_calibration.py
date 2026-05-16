"""Persist hoverboard lean goal positions (FWD/REV, optional pivots) for Dynamixel 12/13.

Values merge over env-loaded defaults and are read at ``load_settings()`` time.
Writes go to ``$XDG_CONFIG_HOME/sirena/hover_calibration.json`` (fallback:
``~/.config/sirena/``).

Optional ``turn_duration_sec`` (0.1–1.0) merges into ``NavigationSettings`` for
timed :meth:`turn_left` / :meth:`turn_right` and Drive screen pivots.
"""

from __future__ import annotations

import json
import os
from dataclasses import replace
from pathlib import Path
from typing import Any, Final, Mapping, Union

from nina.config.settings import HoverboardAxisSettings, NavigationSettings

CALIB_FILENAME: Final = "hover_calibration.json"

_FORWARD_POS_LEFT: Final = "forward_pos_left"
_FORWARD_POS_RIGHT: Final = "forward_pos_right"
_BACKWARD_POS_LEFT: Final = "backward_pos_left"
_BACKWARD_POS_RIGHT: Final = "backward_pos_right"
_TURN_LEFT_POS_LEFT: Final = "turn_left_pos_left"
_TURN_LEFT_POS_RIGHT: Final = "turn_left_pos_right"
_TURN_RIGHT_POS_LEFT: Final = "turn_right_pos_left"
_TURN_RIGHT_POS_RIGHT: Final = "turn_right_pos_right"
_TURN_DURATION_SEC: Final = "turn_duration_sec"

_POSITION_INT_KEYS: Final = frozenset(
    {
        _FORWARD_POS_LEFT,
        _FORWARD_POS_RIGHT,
        _BACKWARD_POS_LEFT,
        _BACKWARD_POS_RIGHT,
        _TURN_LEFT_POS_LEFT,
        _TURN_LEFT_POS_RIGHT,
        _TURN_RIGHT_POS_LEFT,
        _TURN_RIGHT_POS_RIGHT,
    }
)


def hover_calibration_config_dir(*, create: bool = False) -> Path:
    raw = os.environ.get("XDG_CONFIG_HOME", "").strip()
    base = Path(raw) if raw else Path.home() / ".config"
    d = base / "sirena"
    if create:
        d.mkdir(parents=True, exist_ok=True)
    return d


def hover_calibration_path(*, create: bool = False) -> Path:
    return hover_calibration_config_dir(create=create) / CALIB_FILENAME


def _read_file_dict() -> dict[str, Any]:
    path = hover_calibration_path(create=False)
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(data, dict):
        return {}
    return data


def read_hover_calibration_ints() -> dict[str, int]:
    raw = _read_file_dict()
    out: dict[str, int] = {}
    for k in _POSITION_INT_KEYS:
        if k not in raw:
            continue
        try:
            v = int(raw[k])
        except (TypeError, ValueError):
            continue
        out[k] = max(0, min(4095, v))
    return out


def read_hover_calibration_turn_duration_sec() -> float | None:
    raw = _read_file_dict()
    if _TURN_DURATION_SEC not in raw:
        return None
    try:
        v = float(raw[_TURN_DURATION_SEC])
    except (TypeError, ValueError):
        return None
    return max(1.0, min(5.0, v))


def merge_hover_calibration_into_axis(axis: HoverboardAxisSettings) -> HoverboardAxisSettings:
    cal = read_hover_calibration_ints()
    if not cal:
        return axis
    kwargs = {k: cal[k] for k in cal if k in _POSITION_INT_KEYS}
    if not kwargs:
        return axis
    return replace(axis, **kwargs)


def merge_hover_calibration_into_navigation(
    navigation: NavigationSettings,
) -> NavigationSettings:
    td = read_hover_calibration_turn_duration_sec()
    if td is None:
        return navigation
    return replace(navigation, turn_duration_sec=float(td))


def save_hover_calibration_partial(
    updates: Mapping[str, Union[int, float]],
) -> None:
    path = hover_calibration_path(create=True)
    data = _read_file_dict()
    for k, v in updates.items():
        if k == _TURN_DURATION_SEC:
            data[k] = max(0.1, min(1.0, float(v)))
        elif k in _POSITION_INT_KEYS:
            data[k] = max(0, min(4095, int(v)))
    path.write_text(
        json.dumps(data, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
