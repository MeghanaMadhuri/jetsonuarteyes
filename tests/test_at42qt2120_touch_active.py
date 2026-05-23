"""Tests for AT42QT2120 touch detection."""

from __future__ import annotations

from unittest.mock import MagicMock

from nina.sensors.at42qt2120 import AT42QT2120


def test_touch_active_uses_mask_by_default() -> None:
    dev = AT42QT2120(7)
    dev.keys_pressed = MagicMock(return_value=False)  # type: ignore[method-assign]
    dev.read_key_mask = MagicMock(return_value=0x004)  # type: ignore[method-assign]
    assert dev.touch_active()
    dev.read_key_mask.assert_called_once()


def test_touch_active_respects_channel_mask() -> None:
    dev = AT42QT2120(7)
    dev.keys_pressed = MagicMock(return_value=False)  # type: ignore[method-assign]
    dev.read_key_mask = MagicMock(return_value=0x008)  # channel 3
    assert not dev.touch_active(channel_mask=0x004)
    assert dev.touch_active(channel_mask=0x008)


def test_touch_active_status_only_when_mask_disabled() -> None:
    dev = AT42QT2120(7)
    dev.keys_pressed = MagicMock(return_value=False)  # type: ignore[method-assign]
    dev.read_key_mask = MagicMock(return_value=0x004)  # type: ignore[method-assign]
    assert not dev.touch_active(use_key_mask=False)
    dev.read_key_mask.assert_not_called()


def test_status_stuck_idle_when_status_high_and_mask_clear() -> None:
    dev = AT42QT2120(7)
    dev.keys_pressed = MagicMock(return_value=True)  # type: ignore[method-assign]
    dev.read_key_mask = MagicMock(return_value=0)  # type: ignore[method-assign]
    assert dev.status_stuck_idle()


def test_status_stuck_idle_false_when_mask_shows_touch() -> None:
    dev = AT42QT2120(7)
    dev.keys_pressed = MagicMock(return_value=True)  # type: ignore[method-assign]
    dev.read_key_mask = MagicMock(return_value=0x004)  # type: ignore[method-assign]
    assert not dev.status_stuck_idle()
