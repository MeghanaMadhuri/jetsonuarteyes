"""Tune hoverboard lean FWD/REV Dynamixel goals (e.g. IDs 12+13) with live preview."""

from __future__ import annotations

from typing import Dict, Literal

from PyQt5.QtCore import Qt, pyqtSignal
from PyQt5.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from sirena_ui.widgets.common import Breadcrumb, Card, CardTitle, MutedLabel
from sirena_ui.workers.nina_service import NinaService

_TickKey = Literal["fwd_l", "fwd_r", "back_l", "back_r"]

_POS_MIN = 0
_POS_MAX = 4095


class MotionCalibrationScreen(QWidget):
    """Adjust motor goal ticks with - / + buttons; persist via ``hover_calibration.json``."""

    back_requested = pyqtSignal()

    def __init__(self, service: NinaService, parent=None) -> None:
        super().__init__(parent)
        self._service = service
        self._ticks: Dict[_TickKey, int] = {
            "fwd_l": 2048,
            "fwd_r": 2048,
            "back_l": 2048,
            "back_r": 2048,
        }
        self._tick_labels: Dict[_TickKey, QLabel] = {}

        outer = QVBoxLayout(self)
        outer.setContentsMargins(10, 10, 10, 10)
        outer.setSpacing(10)

        top = QHBoxLayout()
        top.setSpacing(8)
        back = QPushButton("\u2190 Back to Drive")
        back.setObjectName("secondaryButton")
        back.setCursor(Qt.PointingHandCursor)
        back.setFocusPolicy(Qt.NoFocus)
        back.clicked.connect(self.back_requested.emit)
        top.addWidget(back)
        top.addStretch(1)
        outer.addLayout(top)

        outer.addWidget(Breadcrumb("Nina", "Motion calibration"))

        outer.addWidget(
            MutedLabel(
                "Use - and + to change each goal by one tick (range 0\u20134095). "
                "Brake/neutral is shown for reference. Live preview moves the servos; "
                "use brake on Drive and avoid autonomy while tuning."
            )
        )

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        host = QWidget()
        scroll.setWidget(host)
        outer.addWidget(scroll, stretch=1)
        inner = QVBoxLayout(host)
        inner.setContentsMargins(0, 0, 0, 0)
        inner.setSpacing(10)

        axis = self._service.settings.hoverboard_axis
        self._id_left = int(axis.id_left)
        self._id_right = int(axis.id_right)

        self._neutral_lbl = MutedLabel(
            f"Brake / neutral reference: motor {self._id_left}={axis.brake_pos_left}, "
            f"motor {self._id_right}={axis.brake_pos_right}."
        )
        inner.addWidget(self._neutral_lbl)

        neutral_row = QHBoxLayout()
        neutral_row.setSpacing(8)
        self._neutral_btn = QPushButton("Neutral")
        self._neutral_btn.setObjectName("secondaryButton")
        self._neutral_btn.setCursor(Qt.PointingHandCursor)
        self._neutral_btn.setFocusPolicy(Qt.NoFocus)
        self._neutral_btn.setMinimumHeight(34)
        self._neutral_btn.setToolTip(
            "Command both lean servos to the configured brake / neutral pose "
            "(does not change forward or backward goal values)."
        )
        self._neutral_btn.clicked.connect(self._on_neutral_clicked)
        neutral_row.addWidget(self._neutral_btn)
        neutral_row.addStretch(1)
        inner.addLayout(neutral_row)

        inner.addWidget(self._build_section_forward())
        inner.addWidget(self._build_section_backward())
        inner.addStretch(1)

    def _step_tick(self, key: _TickKey, delta: int, forward: bool) -> None:
        self._ticks[key] = max(_POS_MIN, min(_POS_MAX, self._ticks[key] + delta))
        lbl = self._tick_labels.get(key)
        if lbl is not None:
            lbl.setText(str(self._ticks[key]))
        if forward:
            self._service.preview_hover_lean_positions(
                self._ticks["fwd_l"], self._ticks["fwd_r"]
            )
        else:
            self._service.preview_hover_lean_positions(
                self._ticks["back_l"], self._ticks["back_r"]
            )

    def _make_step_button(self, text: str) -> QPushButton:
        b = QPushButton(text)
        b.setObjectName("secondaryButton")
        b.setCursor(Qt.PointingHandCursor)
        b.setFocusPolicy(Qt.NoFocus)
        b.setFixedWidth(44)
        b.setMinimumHeight(36)
        return b

    def _motor_row(
        self,
        label: str,
        motor_id: int,
        tick_key: _TickKey,
        *,
        forward: bool,
    ) -> QHBoxLayout:
        row = QHBoxLayout()
        row.setSpacing(8)
        cap = QLabel(f"{label} {motor_id}")
        cap.setStyleSheet(
            "color: #1c1c1e; font-size: 13px; font-weight: 600;"
            " background-color: transparent; min-width: 88px;"
        )
        row.addWidget(cap)

        minus = self._make_step_button("-")
        minus.clicked.connect(lambda: self._step_tick(tick_key, -1, forward))
        row.addWidget(minus)

        val = QLabel(str(self._ticks[tick_key]))
        val.setStyleSheet(
            "color: #1c1c1e; font-size: 15px; font-weight: 700;"
            " background-color: transparent; min-width: 52px;"
        )
        val.setAlignment(Qt.AlignCenter)
        row.addWidget(val)
        self._tick_labels[tick_key] = val

        plus = self._make_step_button("+")
        plus.clicked.connect(lambda: self._step_tick(tick_key, 1, forward))
        row.addWidget(plus)

        row.addStretch(1)
        return row

    def _build_section_forward(self) -> Card:
        card = Card(padding=12, spacing=8)
        card.add(CardTitle("Forward motion"))
        card.add_layout(self._motor_row("Motor", self._id_left, "fwd_l", forward=True))
        card.add_layout(self._motor_row("Motor", self._id_right, "fwd_r", forward=True))
        save = QPushButton("Save forward")
        save.setObjectName("primaryButton")
        save.setCursor(Qt.PointingHandCursor)
        save.setFocusPolicy(Qt.NoFocus)
        save.clicked.connect(self._save_forward)
        card.add(save)
        return card

    def _build_section_backward(self) -> Card:
        card = Card(padding=12, spacing=8)
        card.add(CardTitle("Backward motion"))
        card.add_layout(self._motor_row("Motor", self._id_left, "back_l", forward=False))
        card.add_layout(
            self._motor_row("Motor", self._id_right, "back_r", forward=False)
        )
        save = QPushButton("Save backward")
        save.setObjectName("primaryButton")
        save.setCursor(Qt.PointingHandCursor)
        save.setFocusPolicy(Qt.NoFocus)
        save.clicked.connect(self._save_backward)
        card.add(save)
        return card

    def _on_neutral_clicked(self) -> None:
        self._service.park_hoverboard_brake()

    def _sync_values_from_settings(self) -> None:
        ax = self._service.settings.hoverboard_axis
        self._ticks["fwd_l"] = int(ax.forward_pos_left)
        self._ticks["fwd_r"] = int(ax.forward_pos_right)
        self._ticks["back_l"] = int(ax.backward_pos_left)
        self._ticks["back_r"] = int(ax.backward_pos_right)
        for k, lbl in self._tick_labels.items():
            lbl.setText(str(self._ticks[k]))

    def on_enter(self) -> None:
        try:
            self._service.ensure_bus()
        except Exception as exc:
            QMessageBox.warning(
                self,
                "Bus",
                f"Could not open the Dynamixel bus:\n{exc}\n\n"
                "You can still adjust saved values with Save.",
            )
        self._sync_values_from_settings()
        ax = self._service.settings.hoverboard_axis
        self._neutral_lbl.setText(
            f"Brake / neutral reference: motor {self._id_left}={ax.brake_pos_left}, "
            f"motor {self._id_right}={ax.brake_pos_right}."
        )
        self._service.park_hoverboard_brake()

    def on_leave(self) -> None:
        self._service.park_hoverboard_brake()

    def _save_forward(self) -> None:
        fl = self._ticks["fwd_l"]
        fr = self._ticks["fwd_r"]
        self._service.persist_hover_lean_calibration(
            forward_pos_left=fl,
            forward_pos_right=fr,
        )
        self._service.park_hoverboard_brake()
        QMessageBox.information(
            self,
            "Saved",
            f"Forward goals: {self._id_left}={fl}, {self._id_right}={fr}.\n"
            "Lean servos returned to brake.",
        )

    def _save_backward(self) -> None:
        bl = self._ticks["back_l"]
        br = self._ticks["back_r"]
        self._service.persist_hover_lean_calibration(
            backward_pos_left=bl,
            backward_pos_right=br,
        )
        self._service.park_hoverboard_brake()
        QMessageBox.information(
            self,
            "Saved",
            f"Backward goals: {self._id_left}={bl}, {self._id_right}={br}.\n"
            "Lean servos returned to brake.",
        )
