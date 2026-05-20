"""Action recording/playback must not include hoverboard lean axes (12, 13)."""

from nina.config.motor_ids import ACTION_MOTOR_IDS, HOVERBOARD_LEAN_IDS
from nina.controllers.dynamixel_manager import DynamixelManager


def test_action_motor_ids_exclude_hoverboard_lean():
    assert not set(HOVERBOARD_LEAN_IDS) & set(ACTION_MOTOR_IDS)
    assert 12 not in ACTION_MOTOR_IDS
    assert 13 not in ACTION_MOTOR_IDS


def test_frame_goals_ignores_lean_axes():
    dxl = DynamixelManager("/dev/null", 222222, list(range(1, 14)))
    frame = {
        "servos": {
            "12": {"type": "absolute", "value": 2048},
            "13": {"type": "absolute", "value": 2048},
            "1": {"type": "absolute", "value": 100},
        }
    }
    goals = dxl._frame_goals(frame)
    assert 12 not in goals and 13 not in goals
    assert goals[1] == 100
