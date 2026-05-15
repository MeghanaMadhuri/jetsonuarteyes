"""Motion calibration save must not orphan DriveController worker threads."""

from __future__ import annotations

import tempfile
import unittest
from dataclasses import dataclass
from pathlib import Path
from unittest.mock import MagicMock, patch

from nina.config.settings import HoverboardAxisSettings
from nina.controllers.hoverboard_axis_drive import HoverboardAxisDrive
from sirena_ui.workers.nina_service import NinaService


def _minimal_axis() -> HoverboardAxisSettings:
    return HoverboardAxisSettings(
        id_left=12,
        id_right=13,
        brake_pos_left=2051,
        brake_pos_right=2056,
        forward_pos_left=2061,
        forward_pos_right=2056,
        backward_pos_left=2036,
        backward_pos_right=2066,
        swap_turn_lr=True,
        turn_push_ticks=20,
        tilt_deg=5.0,
        moving_speed=400,
        sign_left=1,
        sign_right=1,
        pulse_forward_enabled=False,
        pulse_forward_on_sec=1.0,
        pulse_forward_brake_sec=1.0,
        pulse_forward_return_ramp_sec=0.0,
        pulse_forward_coast_blend=0.0,
        pulse_forward_sync_present=False,
        pulse_forward_present_tol_ticks=4,
        pulse_forward_present_step_timeout_sec=0.25,
        pulse_ramp_profile="smootherstep",
        pulse_ramp_trap_edge=0.18,
        pulse_ramp_moving_speed=None,
        pulse_waveform="cosine",
        pulse_series_max=5,
        pulse_series_fwd_sec=1.3,
        pulse_series_coast_initial_sec=0.30,
        pulse_series_coast_increment_sec=0.20,
        pulse_series_min_transition_sec=0.18,
        pulse_series_coast_initial_pct=10.0,
        pulse_series_coast_step_pct=10.0,
    )


class _FakeSettings:
    hoverboard_axis: HoverboardAxisSettings


class TestHoverCalibrationPersist(unittest.TestCase):
    def test_persist_keeps_drive_instance(self) -> None:
        with patch.object(NinaService, "__init__", lambda self, s=None: None):
            svc = NinaService.__new__(NinaService)
        svc.settings = _FakeSettings(hoverboard_axis=_minimal_axis())
        old_drive = MagicMock()
        svc._drive = old_drive

        with tempfile.TemporaryDirectory() as tmp:
            with patch.dict("os.environ", {"XDG_CONFIG_HOME": tmp}, clear=False):
                svc.persist_hover_lean_calibration(
                    forward_pos_left=2100,
                    forward_pos_right=2101,
                )

        self.assertIs(svc._drive, old_drive)
        old_drive.update_hoverboard_axis.assert_called_once()
        self.assertEqual(svc.settings.hoverboard_axis.forward_pos_left, 2100)
        self.assertEqual(svc.settings.hoverboard_axis.forward_pos_right, 2101)

    def test_hoverboard_axis_drive_update_axis_config(self) -> None:
        axis = _minimal_axis()
        nav = HoverboardAxisDrive(
            MagicMock(),
            __import__("threading").RLock(),
            axis,
            MagicMock(),
        )
        updated = HoverboardAxisSettings(
            id_left=12,
            id_right=13,
            brake_pos_left=2051,
            brake_pos_right=2056,
            forward_pos_left=3000,
            forward_pos_right=3001,
            backward_pos_left=2036,
            backward_pos_right=2066,
            swap_turn_lr=True,
            turn_push_ticks=20,
            tilt_deg=5.0,
            moving_speed=400,
            sign_left=1,
            sign_right=1,
            pulse_forward_enabled=False,
            pulse_forward_on_sec=1.0,
            pulse_forward_brake_sec=1.0,
            pulse_forward_return_ramp_sec=0.0,
            pulse_forward_coast_blend=0.0,
            pulse_forward_sync_present=False,
            pulse_forward_present_tol_ticks=4,
            pulse_forward_present_step_timeout_sec=0.25,
            pulse_ramp_profile="smootherstep",
            pulse_ramp_trap_edge=0.18,
            pulse_ramp_moving_speed=None,
            pulse_waveform="cosine",
            pulse_series_max=5,
            pulse_series_fwd_sec=1.3,
            pulse_series_coast_initial_sec=0.30,
            pulse_series_coast_increment_sec=0.20,
            pulse_series_min_transition_sec=0.18,
            pulse_series_coast_initial_pct=10.0,
            pulse_series_coast_step_pct=10.0,
        )
        nav.update_axis_config(updated)
        self.assertEqual(nav._axis.forward_pos_left, 3000)


if __name__ == "__main__":
    unittest.main()
