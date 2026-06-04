"""Persistent dark-charcoal sidebar with Sirena nav rows."""

from __future__ import annotations

import os
from typing import Dict, List, Tuple

from PyQt5.QtCore import Qt, pyqtSignal
from PyQt5.QtWidgets import (
    QButtonGroup,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
)

from sirena_ui.widgets.dev_quit import DevQuitBrandBar


# (key, label, icon-glyph) — the glyphs are simple Unicode symbols so we
# don't need an icon-font. They render fine on the Jetson's default
# Noto/DejaVu fonts.
NAV_ITEMS: List[Tuple[str, str, str]] = [
    ("home", "Home", "\u2302"),                # house
    ("drive", "Drive", "\u2B95"),              # right arrow (substitute for car)
    ("vision", "Vision", "\u25CE"),            # bullseye
    ("voice", "Voice", "\u1F3A4"),             # microphone
    ("eye_exprs", "Eyes", "\u25C9"),           # eye expressions (ESP8266 TFT)
    ("perception", "Perception", "\u2299"),    # circled dot - sensor fusion view
    ("map", "Map", "\u25A6"),                  # square with grid
    ("actions", "Actions", "\u2630"),          # trigram (lines)
    ("movements", "Movements", "\u21BB"),      # clockwise arrows — saved drive sequences
    ("lean_cal", "Lean Cal", "\u2696"),        # balance scale - bench backward-lean calibration
    ("settings", "Settings", "\u2699"),        # gear
    ("health", "Health", "\u2665"),            # heart
]


class Sidebar(QFrame):
    nav_changed = pyqtSignal(str)
    dev_quit_requested = pyqtSignal()
    close_app_requested = pyqtSignal()

    def __init__(self, version_label: str = "v0.4", host_label: str = "", parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("sidebar")
        # 160 px (was 200) — nav rows fit at 14 px font; override via
        # ``NINA_UI_SIDEBAR_WIDTH`` (e.g. 136) only if Drive needs more room.
        raw = (os.environ.get("NINA_UI_SIDEBAR_WIDTH") or "160").strip()
        try:
            width = max(120, min(200, int(raw)))
        except ValueError:
            width = 160
        self.setFixedWidth(width)
        self._buttons: Dict[str, QPushButton] = {}

        v = QVBoxLayout(self)
        v.setContentsMargins(0, 8, 0, 8)
        v.setSpacing(2)

        v.addWidget(self._build_brand())

        v.addSpacing(6)

        self._group = QButtonGroup(self)
        self._group.setExclusive(True)
        for key, label, glyph in NAV_ITEMS:
            btn = QPushButton(f"  {glyph}   {label}")
            btn.setObjectName("navRow")
            btn.setCheckable(True)
            btn.setCursor(Qt.PointingHandCursor)
            btn.clicked.connect(lambda _checked=False, k=key: self._on_clicked(k))
            self._group.addButton(btn)
            v.addWidget(btn)
            self._buttons[key] = btn

        v.addStretch(1)

        self._close_btn = QPushButton("  \u2715   Close app")
        self._close_btn.setObjectName("navRow")
        self._close_btn.setCursor(Qt.PointingHandCursor)
        self._close_btn.clicked.connect(self.close_app_requested.emit)
        v.addWidget(self._close_btn)

        footer_text = version_label
        if host_label:
            footer_text = f"{version_label} \u00b7 {host_label}"
        footer = QLabel(footer_text)
        footer.setObjectName("sidebarFooter")
        footer.setAlignment(Qt.AlignCenter)
        footer.setStyleSheet("color: #9a9a9f; font-size: 11px; padding: 8px;")
        v.addWidget(footer)

    def _build_brand(self) -> QFrame:
        bar = DevQuitBrandBar()
        bar.setStyleSheet("background-color: transparent;")
        bar.dev_quit_requested.connect(self.dev_quit_requested.emit)
        return bar

    def _on_clicked(self, key: str) -> None:
        self.nav_changed.emit(key)

    def select(self, key: str) -> None:
        btn = self._buttons.get(key)
        if btn is not None:
            btn.setChecked(True)
