"""Host shutdown / reboot helpers (HTTP gateway + in-process fallback)."""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Dict, Literal

from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import QMessageBox, QWidget

from sirena_ui.workers.nina_service import NinaService

PowerAction = Literal["poweroff", "reboot"]


def _link_base_url() -> str:
    return os.environ.get("NINA_LINK_URL", "http://127.0.0.1:8787").rstrip("/")


def request_host_power(action: PowerAction, *, timeout: float = 6.0) -> Dict[str, Any]:
    """POST ``/v1/system/poweroff|reboot`` or fall back to ``host_control``."""
    path = (
        "/v1/system/poweroff"
        if action == "poweroff"
        else "/v1/system/reboot"
    )
    url = _link_base_url() + path
    req = urllib.request.Request(
        url,
        data=b"{}",
        method="POST",
        headers={
            "Accept": "application/json",
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            payload = resp.read().decode("utf-8")
            return json.loads(payload) if payload else {}
    except (urllib.error.HTTPError, urllib.error.URLError, OSError):
        from nina.jetson_net import host_control

        if action == "poweroff":
            return host_control.queue_poweroff()
        return host_control.queue_reboot()


def confirm_power_action(parent: QWidget, title: str, body: str) -> bool:
    box = QMessageBox(parent)
    box.setIcon(QMessageBox.Question)
    box.setWindowTitle(title)
    box.setText(body)
    box.setStandardButtons(QMessageBox.Yes | QMessageBox.No)
    box.setDefaultButton(QMessageBox.No)
    box.setWindowFlags(box.windowFlags() | Qt.WindowStaysOnTopHint)
    return box.exec_() == QMessageBox.Yes


def perform_host_power(
    parent: QWidget,
    service: NinaService,
    action: PowerAction,
) -> None:
    """Shut down motors, then queue host poweroff/reboot."""
    try:
        service.shutdown()
    except Exception:
        pass

    result = request_host_power(action)
    ok = bool(result.get("ok", False))
    msg = str(result.get("message") or "").strip()
    hint = str(result.get("install_hint") or "").strip()
    if not msg:
        msg = f"{action} requested." if ok else f"{action} failed."

    if not ok:
        body = msg
        if hint:
            body = f"{body}\n\n{hint}"
        warn = QMessageBox(parent)
        warn.setIcon(QMessageBox.Warning)
        warn.setWindowTitle("Power")
        warn.setText(body)
        warn.setStandardButtons(QMessageBox.Ok)
        warn.setWindowFlags(warn.windowFlags() | Qt.WindowStaysOnTopHint)
        warn.exec_()
        return

    info = QMessageBox(parent)
    info.setIcon(QMessageBox.Information)
    info.setWindowTitle("Power")
    info.setText(msg)
    info.setStandardButtons(QMessageBox.Ok)
    info.setWindowFlags(info.windowFlags() | Qt.WindowStaysOnTopHint)
    info.exec_()
