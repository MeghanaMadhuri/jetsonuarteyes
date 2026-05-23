"""Tests for interruptible smooth playback + neutral release ramp."""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from unittest.mock import MagicMock

from nina.controllers.dynamixel_manager import DynamixelManager


def _write_action(path: Path, frames: list) -> None:
    path.write_text(
        json.dumps({"frames": frames}, indent=2),
        encoding="utf-8",
    )


def test_play_smooth_ramps_to_neutral_when_stop_event_set(tmp_path: Path) -> None:
    action = tmp_path / "wave.json"
    neutral = tmp_path / "neutral.json"
    _write_action(
        action,
        [
            {
                "duration": 0.2,
                "servos": {"1": {"type": "absolute", "value": 1000}},
            },
            {
                "delay": 1.0,
                "duration": 0.2,
                "servos": {"1": {"type": "absolute", "value": 3000}},
            },
        ],
    )
    _write_action(
        neutral,
        [{"duration": 0.5, "servos": {"1": {"type": "absolute", "value": 2048}}}],
    )

    dxl = DynamixelManager("/dev/null", 222222, list(range(1, 14)))
    dxl._is_initialized = True  # type: ignore[attr-defined]
    dxl._serial = MagicMock()  # type: ignore[attr-defined]
    dxl.read_reg = MagicMock(return_value=2048)
    dxl.sync_write_goal_position = MagicMock()
    dxl.set_moving_speed_for_ids = MagicMock()

    stop = threading.Event()

    def _stop_soon() -> None:
        time.sleep(0.05)
        stop.set()

    threading.Thread(target=_stop_soon, daemon=True).start()

    completed = dxl.play_smooth(
        action,
        sub_hz=100.0,
        max_speed=500,
        speed=10.0,
        warmup_sec=0.0,
        stop_event=stop,
        release_neutral_path=neutral,
        release_ramp_sec=0.1,
        release_max_speed=200,
    )

    assert completed is False
    assert dxl.sync_write_goal_position.call_count >= 2
    last_call = dxl.sync_write_goal_position.call_args_list[-1][0][0]
    assert last_call[1] == 2048
