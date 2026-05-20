"""Touch-friendly virtual D-pad used on the Drive screen."""

from __future__ import annotations

from typing import Optional

from PyQt5.QtCore import Qt, pyqtSignal
from PyQt5.QtWidgets import (
    QGridLayout,
    QPushButton,
    QWidget,
)


class DPad(QWidget):
    """Five-button D-pad: forward, back, left, right, stop."""

    direction_pressed = pyqtSignal(str)   # 'forward' | 'back' | 'left' | 'right'
    direction_released = pyqtSignal(str)
    stop_clicked = pyqtSignal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        grid = QGridLayout(self)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setSpacing(6)

        # Glyph-only labels - the chevron alone makes the direction
        # obvious and saves the horizontal space the words "Forward"
        # / "Back" used to claim. Critical at 1024 wide.
        self._fwd = self._make_btn("\u25B2", "forward")
        self._back = self._make_btn("\u25BC", "back")
        self._left = self._make_btn("\u25C0", "left")
        self._right = self._make_btn("\u25B6", "right")

        self._stop = QPushButton("STOP")
        self._stop.setObjectName("dpadStop")
        self._stop.setCursor(Qt.PointingHandCursor)
        self._stop.setFocusPolicy(Qt.NoFocus)
        self._stop.clicked.connect(self.stop_clicked.emit)

        grid.addWidget(self._fwd,   0, 1)
        grid.addWidget(self._left,  1, 0)
        grid.addWidget(self._stop,  1, 1)
        grid.addWidget(self._right, 1, 2)
        grid.addWidget(self._back,  2, 1)

        self._pad_enabled = True
        self._exclusive_direction: Optional[str] = None

    def _make_btn(self, label: str, direction: str) -> QPushButton:
        btn = QPushButton(label)
        btn.setObjectName("dpadButton")
        btn.setCursor(Qt.PointingHandCursor)
        # Don't steal keyboard focus on click - the parent Drive screen
        # listens for WASD / Space / Esc and needs to keep focus while
        # the operator pokes at the on-screen D-pad with a mouse.
        btn.setFocusPolicy(Qt.NoFocus)
        btn.pressed.connect(lambda d=direction: self.direction_pressed.emit(d))
        btn.released.connect(lambda d=direction: self.direction_released.emit(d))
        return btn

    def _refresh_buttons(self) -> None:
        exclusive = self._exclusive_direction
        for dir_name, btn in (
            ("forward", self._fwd),
            ("back", self._back),
            ("left", self._left),
            ("right", self._right),
        ):
            btn.setEnabled(
                self._pad_enabled
                and (exclusive is None or exclusive == dir_name)
            )
        self._stop.setEnabled(self._pad_enabled)

    def set_enabled(self, enabled: bool) -> None:
        """Enable or disable the whole pad (e.g. brake / autonomy)."""
        self._pad_enabled = enabled
        if not enabled:
            self._exclusive_direction = None
        self._refresh_buttons()

    def set_exclusive_direction(self, direction: Optional[str]) -> None:
        """Grey out all direction keys except *direction* and STOP (one pad at a time)."""
        self._exclusive_direction = direction
        self._refresh_buttons()
