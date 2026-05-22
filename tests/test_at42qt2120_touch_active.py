"""Tests for conservative AT42QT2120 touch detection."""

from __future__ import annotations

from unittest.mock import MagicMock

from nina.sensors.at42qt2120 import AT42QT2120


def test_touch_active_ignores_mask_by_default() -> None:
    dev = AT42QT2120(7)
    dev.keys_pressed = MagicMock(return_value=False)  # type: ignore[method-assign]
    dev.read_key_mask = MagicMock(return_value=0x004)  # type: ignore[method-assign]
    assert not dev.touch_active()
    dev.read_key_mask.assert_not_called()


def test_touch_active_can_opt_into_mask() -> None:
    dev = AT42QT2120(7)
    dev.keys_pressed = MagicMock(return_value=False)  # type: ignore[method-assign]
    dev.read_key_mask = MagicMock(return_value=0x004)  # type: ignore[method-assign]
    assert dev.touch_active(use_key_mask=True)
    dev.read_key_mask.assert_called_once()
