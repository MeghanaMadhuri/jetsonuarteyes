"""Tests for AT42QT2120 touch detection."""

from __future__ import annotations

from unittest.mock import MagicMock

from nina.sensors.at42qt2120 import AT42QT2120, REG_KEY_STATUS2


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


def test_is_key_pressed_uses_ff_idle_sentinel() -> None:
    dev = AT42QT2120(7)
    dev.read_register = MagicMock(return_value=0xFF)  # type: ignore[method-assign]
    assert not dev.is_key_pressed(0)
    dev.read_register = MagicMock(return_value=0x01)  # type: ignore[method-assign]
    assert dev.is_key_pressed(0)
    assert not dev.is_key_pressed(1)


def test_is_key_pressed_treats_zero_byte_as_idle() -> None:
    dev = AT42QT2120(7)
    dev.read_register = MagicMock(return_value=0x00)  # type: ignore[method-assign]
    assert not dev.is_key_pressed(0)


def test_is_key_pressed_reads_second_register_for_high_channels() -> None:
    dev = AT42QT2120(7)
    dev.read_register = MagicMock(return_value=0x04)  # type: ignore[method-assign]
    assert dev.is_key_pressed(10)
    dev.read_register.assert_called_once_with(REG_KEY_STATUS2)
