"""Full-area "coming soon" placeholder (Android SirenaComingSoonGate parity)."""

from __future__ import annotations

from typing import List, Optional

from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import QHBoxLayout, QLabel, QVBoxLayout, QWidget

from sirena_ui.widgets.common import Breadcrumb, Card, MutedLabel, Pill


class ComingSoonGate(QWidget):
    """Centered message over a muted panel; optional breadcrumb + status pills."""

    def __init__(
        self,
        *,
        title: str,
        message: str,
        breadcrumbs: Optional[List[str]] = None,
        pill_labels: Optional[List[str]] = None,
        parent=None,
    ) -> None:
        super().__init__(parent)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(8)

        crumbs = breadcrumbs or []
        if crumbs:
            outer.addWidget(Breadcrumb(*crumbs))

        if pill_labels:
            row = QHBoxLayout()
            row.setSpacing(8)
            for label in pill_labels:
                row.addWidget(Pill(label, Pill.KIND_NEUTRAL))
            row.addStretch(1)
            outer.addLayout(row)

        card = Card(padding=24, spacing=12)
        outer.addWidget(card, stretch=1)

        title_lbl = QLabel(title)
        title_lbl.setAlignment(Qt.AlignCenter)
        title_lbl.setStyleSheet(
            "color: #1c1c1e; font-size: 18px; font-weight: 700;"
            " background-color: transparent;"
        )
        card.add(title_lbl)

        body = MutedLabel(message)
        body.setAlignment(Qt.AlignCenter)
        body.setWordWrap(True)
        card.add(body)

        soon = Pill("Coming soon", Pill.KIND_WARN)
        chip_row = QHBoxLayout()
        chip_row.addStretch(1)
        chip_row.addWidget(soon)
        chip_row.addStretch(1)
        card.add_layout(chip_row)
        card.add_stretch()
