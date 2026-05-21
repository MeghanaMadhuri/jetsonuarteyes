"""Hidden developer quit: 8 quick logo taps → password → exit app."""

from __future__ import annotations

import os
import time

from PyQt5.QtCore import Qt, pyqtSignal
from PyQt5.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QLabel,
    QLineEdit,
    QVBoxLayout,
    QWidget,
)

# Android-style: consecutive taps must land within this gap (ms).
_DEV_TAP_COUNT = 8
_DEV_TAP_MAX_GAP_MS = 2000


class _DevTapTracker:
    def __init__(self) -> None:
        self._count = 0
        self._last_ms = 0.0

    def register_tap(self) -> bool:
        now = time.monotonic() * 1000.0
        if self._last_ms and (now - self._last_ms) > _DEV_TAP_MAX_GAP_MS:
            self._count = 0
        self._last_ms = now
        self._count += 1
        if self._count >= _DEV_TAP_COUNT:
            self._count = 0
            self._last_ms = 0.0
            return True
        return False


def dev_quit_password() -> str:
    """Expected password; empty disables the gate (taps are ignored)."""
    return (os.environ.get("NINA_UI_DEV_QUIT_PASSWORD") or "").strip()


class DevQuitLogoLabel(QLabel):
    """Sirena sidebar logo — counts rapid taps, emits when threshold reached."""

    dev_quit_requested = pyqtSignal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._tracker = _DevTapTracker()
        self.setCursor(Qt.PointingHandCursor)

    def mouseReleaseEvent(self, event) -> None:
        if (
            event.button() == Qt.LeftButton
            and dev_quit_password()
            and self._tracker.register_tap()
        ):
            self.dev_quit_requested.emit()
        super().mouseReleaseEvent(event)


def prompt_dev_quit_password(parent: QWidget) -> bool:
    """Modal password entry; returns True when password matches (caller quits app)."""
    expected = dev_quit_password()
    if not expected:
        return False

    dlg = QDialog(parent)
    dlg.setWindowTitle(" ")
    dlg.setModal(True)
    dlg.setWindowFlags(
        dlg.windowFlags() | Qt.WindowStaysOnTopHint
    )
    layout = QVBoxLayout(dlg)
    layout.setContentsMargins(20, 20, 20, 16)
    layout.setSpacing(12)

    prompt = QLabel("Password")
    prompt.setStyleSheet(
        "color: #1c1c1e; font-size: 15px; font-weight: 600;"
        " background: transparent;"
    )
    layout.addWidget(prompt)

    field = QLineEdit()
    field.setEchoMode(QLineEdit.Password)
    field.setPlaceholderText("")
    field.setMinimumWidth(280)
    layout.addWidget(field)

    buttons = QDialogButtonBox(
        QDialogButtonBox.Ok | QDialogButtonBox.Cancel
    )
    buttons.accepted.connect(dlg.accept)
    buttons.rejected.connect(dlg.reject)
    layout.addWidget(buttons)

    field.returnPressed.connect(dlg.accept)
    field.setFocus()

    if dlg.exec_() != QDialog.Accepted:
        return False
    return field.text() == expected
