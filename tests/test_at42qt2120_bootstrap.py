"""Tests for DMR-parity AT42QT2120 bootstrap (reset / calibrate / threshold)."""

from __future__ import annotations

from unittest.mock import MagicMock, call

from nina.sensors.at42qt2120 import (
    AT42QT2120,
    REG_CALIBRATION,
    REG_DETECT_THRESHOLD,
    REG_RESET,
    REG_STATUS,
    _STATUS_CALIBRATING,
)


def test_dmr_bootstrap_reset_calibrate_threshold() -> None:
    dev = AT42QT2120(7)
    dev._bus = MagicMock()
    statuses = [_STATUS_CALIBRATING, _STATUS_CALIBRATING, 0]

    def read_side_effect(addr: int, reg: int) -> int:
        if reg == REG_STATUS:
            return statuses.pop(0) if statuses else 0
        return 0

    dev._bus.read_byte_data.side_effect = read_side_effect

    dev.dmr_bootstrap(detect_threshold=25, reset_sleep_sec=0.0)

    writes = dev._bus.write_byte_data.call_args_list
    assert call(0x1C, REG_RESET, 0x07) in writes
    assert call(0x1C, REG_CALIBRATION, 0x07) in writes
    assert call(0x1C, REG_DETECT_THRESHOLD, 25) in writes


def test_recalibrate_writes_calibration_only() -> None:
    dev = AT42QT2120(7)
    dev._bus = MagicMock()
    dev._bus.read_byte_data.return_value = 0

    dev.recalibrate()

    dev._bus.write_byte_data.assert_called_once_with(0x1C, REG_CALIBRATION, 0x07)
