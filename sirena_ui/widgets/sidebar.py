"""Persistent dark-charcoal sidebar with grouped Sirena nav rows.

The nav list is grouped into a few labelled sections (Operate / Sense /
System) and lives inside a vertical scroll area so it stays tidy and
never clips as more screens are added. Brand bar (top), Close app, and
the version footer stay pinned outside the scroll region so they're
always reachable.
"""

from __future__ import annotations

import os
from typing import Dict, List, Tuple

from PyQt5.QtCore import Qt, pyqtSignal
from PyQt5.QtWidgets import (
    QButtonGroup,
    QFrame,
    QLabel,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from sirena_ui.widgets.dev_quit import DevQuitBrandBar


# Grouped nav layout: (section title, [(key, label, icon-glyph), ...]).
# An empty section title renders no header (used for the lone Home row).
# The glyphs are simple Unicode symbols so we don't need an icon-font;
# they render fine on the Jetson's default Noto/DejaVu fonts.
NAV_SECTIONS: List[Tuple[str, List[Tuple[str, str, str]]]] = [
    ("", [
        ("home", "Home", "\u2302"),                # house
    ]),
    ("Operate", [
        ("drive", "Drive", "\u2B95"),              # right arrow (drive)
        ("map", "Map", "\u25A6"),                  # square with grid
        ("movements", "Movements", "\u21BB"),      # clockwise arrows
        ("actions", "Actions", "\u2630"),          # trigram (lines)
    ]),
    ("Sense", [
        ("vision", "Vision", "\u25CE"),            # bullseye
        ("perception", "Perception", "\u2299"),    # circled dot
        ("voice", "Voice", "\u260E"),              # handset (talk)
        ("eye_exprs", "Eyes", "\u25C9"),           # fisheye (eye TFT)
    ]),
    ("System", [
        ("lean_cal", "Lean Cal", "\u2696"),        # balance scale
        ("settings", "Settings", "\u2699"),        # gear
        ("health", "Health", "\u2665"),            # heart
    ]),
]


# Flat (key, label, glyph) list kept for backwards-compatible callers
# (main_window re-export, Android nav-parity mirror).
NAV_ITEMS: List[Tuple[str, str, str]] = [
    item for _title, items in NAV_SECTIONS for item in items
]


class Sidebar(QFrame):
    nav_changed = pyqtSignal(str)
    dev_quit_requested = pyqtSignal()
    close_app_requested = pyqtSignal()

    def __init__(self, version_label: str = "v0.4", host_label: str = "", parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("sidebar")
        # 160 px (was 200) — nav rows fit at 13 px font; override via
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
        v.setSpacing(0)

        v.addWidget(self._build_brand())
        v.addSpacing(4)

        # Grouped, scrollable nav list. The scroll area absorbs any
        # future growth without clipping rows or pushing the footer
        # off the 1024×600 panel.
        self._group = QButtonGroup(self)
        self._group.setExclusive(True)
        v.addWidget(self._build_nav_scroll(), stretch=1)

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
        footer.setStyleSheet("color: #9a9a9f; font-size: 11px; padding: 6px;")
        v.addWidget(footer)

    def _build_nav_scroll(self) -> QScrollArea:
        scroll = QScrollArea()
        scroll.setObjectName("navScroll")
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)

        inner = QWidget()
        inner.setObjectName("navScrollInner")
        nav = QVBoxLayout(inner)
        nav.setContentsMargins(0, 0, 0, 0)
        nav.setSpacing(1)

        for title, items in NAV_SECTIONS:
            if title:
                label = QLabel(title.upper())
                label.setObjectName("navSection")
                nav.addWidget(label)
            for key, glyph_label, glyph in items:
                btn = QPushButton(f"  {glyph}   {glyph_label}")
                btn.setObjectName("navRow")
                btn.setCheckable(True)
                btn.setCursor(Qt.PointingHandCursor)
                btn.clicked.connect(
                    lambda _checked=False, k=key: self._on_clicked(k)
                )
                self._group.addButton(btn)
                nav.addWidget(btn)
                self._buttons[key] = btn

        nav.addStretch(1)
        scroll.setWidget(inner)
        return scroll

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
