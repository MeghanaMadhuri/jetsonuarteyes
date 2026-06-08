"""Dynamixel expected ID list from environment."""

from __future__ import annotations

from nina.config.motor_ids import (
    EXPECTED_DYNAMIXEL_IDS,
    HOVERBOARD_LEAN_IDS,
    dynamixel_expected_ids_from_env,
)


def test_dynamixel_expected_ids_default(monkeypatch) -> None:
    monkeypatch.delenv("NINA_DXL_EXPECTED_IDS", raising=False)
    assert dynamixel_expected_ids_from_env() == EXPECTED_DYNAMIXEL_IDS


def test_dynamixel_expected_ids_lean_only(monkeypatch) -> None:
    monkeypatch.setenv("NINA_DXL_EXPECTED_IDS", "12,13")
    assert dynamixel_expected_ids_from_env() == HOVERBOARD_LEAN_IDS


def test_dynamixel_expected_ids_ignores_invalid_tokens(monkeypatch) -> None:
    monkeypatch.setenv("NINA_DXL_EXPECTED_IDS", "12, bad, 13")
    assert dynamixel_expected_ids_from_env() == HOVERBOARD_LEAN_IDS
