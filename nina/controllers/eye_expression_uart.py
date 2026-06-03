"""UART client for NodeMCU ESP8266 eye firmware (115200, expression id + newline)."""

from __future__ import annotations

import logging
import os
import threading
import time
from dataclasses import dataclass
from typing import Any, Dict, Optional

from nina.eye.expressions import EYE_EXPRESSIONS, expression_by_id

log = logging.getLogger("nina.controllers.eye_expression_uart")

DEFAULT_EYE_UART_PORT = "/dev/ttyTHS1"
DEFAULT_EYE_UART_BAUD = 115200
EXPR_ID_MIN = 0
EXPR_ID_MAX = 33


@dataclass(frozen=True)
class EyeUartConfig:
    enabled: bool
    port: str
    baudrate: int
    command_delay_sec: float


def eye_uart_config_from_env() -> EyeUartConfig:
    port = (os.environ.get("NINA_EYE_UART_PORT") or "").strip() or DEFAULT_EYE_UART_PORT
    try:
        baud = int(os.environ.get("NINA_EYE_UART_BAUD", str(DEFAULT_EYE_UART_BAUD)))
    except ValueError:
        baud = DEFAULT_EYE_UART_BAUD
    try:
        delay = float(os.environ.get("NINA_EYE_UART_CMD_DELAY_SEC", "0.02"))
    except ValueError:
        delay = 0.02
    enabled_raw = (os.environ.get("NINA_EYE_UART_ENABLE") or "1").strip().lower()
    enabled = enabled_raw not in ("0", "false", "no", "off")
    return EyeUartConfig(
        enabled=enabled,
        port=port,
        baudrate=max(9600, min(921600, baud)),
        command_delay_sec=max(0.0, min(0.5, delay)),
    )


class EyeExpressionUartClient:
    """Send ``<id>\\n`` over serial (Arduino ``Serial.parseInt()``)."""

    def __init__(self, cfg: EyeUartConfig) -> None:
        self._cfg = cfg
        self._lock = threading.RLock()
        self._serial: Any = None
        self._last_error: Optional[str] = None
        self._last_sent_id: Optional[int] = None
        self._last_sent_mono: float = 0.0

    @property
    def config(self) -> EyeUartConfig:
        return self._cfg

    def list_expressions(self) -> list[dict]:
        return [e.to_dict() for e in EYE_EXPRESSIONS]

    def status(self) -> Dict[str, Any]:
        with self._lock:
            open_ok = (
                self._serial is not None
                and getattr(self._serial, "is_open", False)
            )
        return {
            "enabled": self._cfg.enabled,
            "port": self._cfg.port,
            "baudrate": self._cfg.baudrate,
            "open": open_ok,
            "last_error": self._last_error,
            "last_expression_id": self._last_sent_id,
        }

    def close(self) -> None:
        with self._lock:
            if self._serial is not None:
                try:
                    if getattr(self._serial, "is_open", False):
                        self._serial.close()
                except Exception:
                    pass
                self._serial = None

    def _ensure_open(self) -> None:
        if self._serial is not None and getattr(self._serial, "is_open", False):
            return
        import serial  # type: ignore

        port = self._cfg.port
        if not os.path.exists(port):
            raise FileNotFoundError(f"Eye UART device not found: {port}")
        self._serial = serial.Serial(
            port=port,
            baudrate=self._cfg.baudrate,
            timeout=0.15,
            write_timeout=0.5,
        )
        self._serial.reset_input_buffer()
        self._serial.reset_output_buffer()
        log.info(
            "Eye UART opened %s @ %d baud",
            port,
            self._cfg.baudrate,
        )

    def send_expression(self, expr_id: int) -> Dict[str, Any]:
        if not self._cfg.enabled:
            raise RuntimeError(
                "Eye UART disabled (set NINA_EYE_UART_ENABLE=1)"
            )
        eid = int(expr_id)
        if eid < EXPR_ID_MIN or eid > EXPR_ID_MAX:
            raise ValueError(f"expression id must be {EXPR_ID_MIN}..{EXPR_ID_MAX}")
        meta = expression_by_id(eid)
        if meta is None:
            raise ValueError(f"unknown expression id {eid}")

        payload = f"{eid}\n".encode("ascii")
        with self._lock:
            try:
                self._ensure_open()
                assert self._serial is not None
                self._serial.write(payload)
                self._serial.flush()
                if self._cfg.command_delay_sec > 0:
                    time.sleep(self._cfg.command_delay_sec)
                self._last_error = None
                self._last_sent_id = eid
                self._last_sent_mono = time.monotonic()
            except Exception as exc:
                self._last_error = str(exc)
                self.close()
                raise RuntimeError(f"Eye UART send failed: {exc}") from exc

        return {
            "ok": True,
            "id": eid,
            "name": meta.name,
            "mood": meta.mood,
        }
