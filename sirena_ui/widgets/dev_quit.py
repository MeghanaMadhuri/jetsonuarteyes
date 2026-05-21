"""Hidden developer quit: 8 quick logo taps → password → exit app."""

from __future__ import annotations

import logging
import os
import time
from pathlib import Path
from typing import Optional

from PyQt5.QtCore import Qt, pyqtSignal
from PyQt5.QtGui import QPixmap
from PyQt5.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QVBoxLayout,
    QWidget,
)

from sirena_ui.styles import asset_path

log = logging.getLogger("sirena_ui.dev_quit")

# Android-style: consecutive taps must land within this gap (ms).
_DEV_TAP_COUNT = 8
_DEV_TAP_MAX_GAP_MS = 3000

_PASSWORD_CACHE: Optional[str] = None


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
    """Password from env or ``~/.config/nina/dev_quit_password`` (kiosk-safe)."""
    global _PASSWORD_CACHE
    if _PASSWORD_CACHE is not None:
        return _PASSWORD_CACHE

    pw = (os.environ.get("NINA_UI_DEV_QUIT_PASSWORD") or "").strip()
    if not pw:
        for path in (
            Path.home() / ".config" / "nina" / "dev_quit_password",
            Path("/etc/nina/dev_quit_password"),
        ):
            try:
                if path.is_file():
                    pw = path.read_text(encoding="utf-8").strip()
            except OSError as exc:
                log.debug("dev_quit: cannot read %s: %s", path, exc)
            if pw:
                log.info("dev_quit: password loaded from %s", path)
                break

    _PASSWORD_CACHE = pw
    if not pw:
        log.debug(
            "dev_quit: disabled — set NINA_UI_DEV_QUIT_PASSWORD or "
            "~/.config/nina/dev_quit_password"
        )
    return pw


def _register_brand_tap(tracker: _DevTapTracker) -> bool:
    if not dev_quit_password():
        return False
    if tracker.register_tap():
        log.info("dev_quit: tap threshold reached — opening password dialog")
        return True
    return False


class DevQuitBrandBar(QFrame):
    """Whole Sirena brand row in the sidebar — large tap target (touch + mouse)."""

    dev_quit_requested = pyqtSignal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._tracker = _DevTapTracker()
        self.setObjectName("devQuitBrandBar")
        self.setMinimumHeight(48)
        self.setCursor(Qt.PointingHandCursor)
        self.setAttribute(Qt.WA_AcceptTouchEvents, True)

        h = QHBoxLayout(self)
        h.setContentsMargins(8, 6, 8, 6)
        h.setSpacing(8)

        logo = QLabel()
        logo.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        pix = QPixmap(asset_path("sirena_logo.png"))
        if not pix.isNull():
            logo.setPixmap(pix.scaledToHeight(22, Qt.SmoothTransformation))
        h.addWidget(logo)

        word = QLabel("Sirena")
        word.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        word.setStyleSheet(
            "color: white; font-size: 15px; font-weight: 700;"
            " background-color: transparent;"
        )
        h.addWidget(word)
        h.addStretch(1)

    def _handle_tap(self) -> None:
        if _register_brand_tap(self._tracker):
            self.dev_quit_requested.emit()

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.LeftButton:
            self._handle_tap()
        super().mousePressEvent(event)

    def touchEvent(self, event) -> None:
        for point in event.touchPoints():
            if point.state() == Qt.TouchPointReleased:
                self._handle_tap()
                break
        event.accept()


def prompt_dev_quit_password(parent: QWidget) -> bool:
    """Modal password entry; returns True when password matches (caller quits app)."""
    expected = dev_quit_password()
    if not expected:
        return False

    dlg = QDialog(parent)
    dlg.setWindowTitle(" ")
    dlg.setModal(True)
    dlg.setWindowFlags(dlg.windowFlags() | Qt.WindowStaysOnTopHint)
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
    field.setMinimumWidth(280)
    layout.addWidget(field)

    buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
    buttons.accepted.connect(dlg.accept)
    buttons.rejected.connect(dlg.reject)
    layout.addWidget(buttons)

    field.returnPressed.connect(dlg.accept)
    field.setFocus()

    if dlg.exec_() != QDialog.Accepted:
        return False
    return field.text() == expected
