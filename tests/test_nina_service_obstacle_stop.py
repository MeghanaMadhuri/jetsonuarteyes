from __future__ import annotations

import threading
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

pytest.importorskip("PyQt5.QtCore")

from sirena_ui.workers import nina_service as ns


def test_obstacle_stop_speaks_before_neutral_pose(monkeypatch) -> None:
    service = ns.NinaService.__new__(ns.NinaService)
    events: list[str] = []

    drive = MagicMock()
    drive.stop.side_effect = lambda *, drain: events.append("drive-stop")
    action_runner = MagicMock()
    action_runner.run_named_action.side_effect = lambda name: events.append(
        f"neutral:{name}"
    )
    dxl = MagicMock()
    dxl._require_initialized.side_effect = lambda: events.append("dxl-ready")

    service._face_follow = None
    service._drive = drive
    service._bus_ready = True
    service.bus_lock = threading.RLock()
    service.dxl = dxl
    service.action_runner = action_runner
    service.settings = SimpleNamespace(
        neutral_action_name="neutral",
        hoverboard_axis=SimpleNamespace(),
        ir_obstacle_stop=SimpleNamespace(
            tts_text="There is an obstacle in my way"
        ),
    )

    monkeypatch.setattr(
        ns,
        "maybe_speak_obstacle_alert",
        lambda *, phrase: events.append(f"speak:{phrase}"),
    )
    monkeypatch.setattr(
        ns,
        "apply_hoverboard_brake_positions",
        lambda dxl, axis: events.append("brake"),
    )

    service.run_obstacle_stop_reaction()

    assert events == [
        "drive-stop",
        "speak:There is an obstacle in my way",
        "dxl-ready",
        "brake",
        "neutral:neutral",
        "brake",
    ]
