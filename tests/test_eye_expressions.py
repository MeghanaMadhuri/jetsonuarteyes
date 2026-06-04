"""Tests for eye expression catalog and UART command formatting."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from nina.controllers.eye_expression_uart import (
    EyeExpressionUartClient,
    EyeUartConfig,
)
from nina.eye.expressions import EYE_EXPRESSIONS, expression_by_id


def test_catalog_has_37_expressions_0_to_36() -> None:
    assert len(EYE_EXPRESSIONS) == 37
    assert EYE_EXPRESSIONS[0].id == 0
    assert EYE_EXPRESSIONS[-1].id == 36
    assert expression_by_id(34).name == "screen_red"
    assert expression_by_id(15) is not None
    assert expression_by_id(15).name == "love"


def test_uart_send_writes_id_newline() -> None:
    cfg = EyeUartConfig(
        enabled=True,
        port="/dev/ttyTEST",
        baudrate=115200,
        command_delay_sec=0.0,
    )
    client = EyeExpressionUartClient(cfg)
    ser = MagicMock()
    ser.is_open = True
    client._serial = ser

    with patch.object(client, "_ensure_open"):
        out = client.send_expression(7)

    ser.write.assert_called_once_with(b"7\n")
    ser.flush.assert_called_once()
    assert out["ok"] is True
    assert out["id"] == 7
    assert out["name"] == "wink_right"
