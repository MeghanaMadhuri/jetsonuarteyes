"""Map / SLAM screen — coming-soon gate (companion parity).

Full SLAM / tap-to-goal / autonomous routing UI is disabled on the kiosk
until the operator workflow is ready. Navigating here shows a static
placeholder; tapping the preview panel opens the same message dialog.
"""

from __future__ import annotations

from PyQt5.QtCore import QEvent, Qt
from PyQt5.QtWidgets import (
    QMessageBox,
    QVBoxLayout,
    QWidget,
)

from sirena_ui.widgets.coming_soon_gate import ComingSoonGate
from sirena_ui.widgets.common import Breadcrumb
from sirena_ui.workers.nina_service import NinaService


_MAP_MESSAGE = (
    "Occupancy grid, tap-to-goal, and autonomous routing are coming soon "
    "on the kiosk. Use Drive for manual control for now."
)


class MapScreen(QWidget):
    def __init__(self, service: NinaService, parent=None) -> None:
        super().__init__(parent)
        self._service = service

        outer = QVBoxLayout(self)
        outer.setContentsMargins(10, 10, 10, 10)
        outer.setSpacing(8)
        outer.addWidget(Breadcrumb("Nina", "Map"))

        self._gate = ComingSoonGate(
            title="Map & navigation",
            message=_MAP_MESSAGE,
            pill_labels=["SLAM", "Goals", "Auto"],
        )
        self._gate.setCursor(Qt.PointingHandCursor)
        self._gate.installEventFilter(self)
        outer.addWidget(self._gate, stretch=1)

    def eventFilter(self, obj, event) -> bool:
        if obj is self._gate and event.type() == QEvent.MouseButtonRelease:
            if event.button() == Qt.LeftButton:
                QMessageBox.information(
                    self,
                    "Map & navigation",
                    _MAP_MESSAGE,
                )
                return True
        return super().eventFilter(obj, event)

    def on_enter(self) -> None:
        pass

    def on_leave(self) -> None:
        pass
