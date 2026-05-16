"""Tune hoverboard lean FWD/REV / pivot Dynamixel goals (e.g. IDs 12+13) with live preview."""

from __future__ import annotations

from typing import Dict, Literal

from PyQt5.QtCore import Qt, pyqtSignal
from PyQt5.QtWidgets import (
    QDoubleSpinBox,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from nina.controllers.hoverboard_axis_drive import hover_computed_turn_pivot_goals
from sirena_ui.widgets.common import Breadcrumb, Card, CardTitle, MutedLabel
from sirena_ui.workers.nina_service import NinaService

_TickKey = Literal[
    "fwd_l", "fwd_r", "back_l", "back_r", "tl_l", "tl_r", "tr_l", "tr_r"
]
_PreviewMode = Literal["fwd", "back", "tl", "tr"]

_POS_MIN = 0
_POS_MAX = 4095
_TURN_DUR_MIN = 0.01
_TURN_DUR_MAX = 1.0


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
            "tl_l": 2048,
            "tl_r": 2048,
            "tr_l": 2048,
            "tr_r": 2048,
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
                "use brake on Drive and avoid autonomy while tuning. "
                "Turn rows default from forward/backward calibration + push/offset until you save custom pivot goals."
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
            "(does not change saved goal values)."
        )
        self._neutral_btn.clicked.connect(self._on_neutral_clicked)
        neutral_row.addWidget(self._neutral_btn)
        neutral_row.addStretch(1)
        inner.addLayout(neutral_row)

        inner.addWidget(self._build_section_forward())
        inner.addWidget(self._build_section_backward())
        inner.addWidget(self._build_section_turn_left())
        inner.addWidget(self._build_section_turn_right())
        inner.addWidget(self._build_section_turn_duration())
        inner.addStretch(1)

    def _apply_preview(self, mode: _PreviewMode) -> None:
        if mode == "fwd":
            self._service.preview_hover_lean_positions(
                self._ticks["fwd_l"], self._ticks["fwd_r"]
            )
        elif mode == "back":
            self._service.preview_hover_lean_positions(
                self._ticks["back_l"], self._ticks["back_r"]
            )
        elif mode == "tl":
            self._service.preview_hover_lean_positions(
                self._ticks["tl_l"], self._ticks["tl_r"]
            )
        else:
            self._service.preview_hover_lean_positions(
                self._ticks["tr_l"], self._ticks["tr_r"]
            )

    def _step_tick(self, key: _TickKey, delta: int, preview: _PreviewMode) -> None:
        self._ticks[key] = max(_POS_MIN, min(_POS_MAX, self._ticks[key] + delta))
        lbl = self._tick_labels.get(key)
        if lbl is not None:
            lbl.setText(str(self._ticks[key]))
        self._apply_preview(preview)

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
        preview: _PreviewMode,
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
        minus.clicked.connect(lambda: self._step_tick(tick_key, -1, preview))
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
        plus.clicked.connect(lambda: self._step_tick(tick_key, 1, preview))
        row.addWidget(plus)

        row.addStretch(1)
        return row

    def _build_section_forward(self) -> Card:
        card = Card(padding=12, spacing=8)
        card.add(CardTitle("Forward motion"))
        card.add_layout(
            self._motor_row("Motor", self._id_left, "fwd_l", preview="fwd")
        )
        card.add_layout(
            self._motor_row("Motor", self._id_right, "fwd_r", preview="fwd")
        )
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
        card.add_layout(
            self._motor_row("Motor", self._id_left, "back_l", preview="back")
        )
        card.add_layout(
            self._motor_row("Motor", self._id_right, "back_r", preview="back")
        )
        save = QPushButton("Save backward")
        save.setObjectName("primaryButton")
        save.setCursor(Qt.PointingHandCursor)
        save.setFocusPolicy(Qt.NoFocus)
        save.clicked.connect(self._save_backward)
        card.add(save)
        return card

    def _build_section_turn_left(self) -> Card:
        card = Card(padding=12, spacing=8)
        card.add(CardTitle("Turn left (pivot lean)"))
        card.add_layout(
            self._motor_row("Motor", self._id_left, "tl_l", preview="tl")
        )
        card.add_layout(
            self._motor_row("Motor", self._id_right, "tl_r", preview="tl")
        )
        save = QPushButton("Save turn left")
        save.setObjectName("primaryButton")
        save.setCursor(Qt.PointingHandCursor)
        save.setFocusPolicy(Qt.NoFocus)
        save.clicked.connect(self._save_turn_left)
        card.add(save)
        return card

    def _build_section_turn_right(self) -> Card:
        card = Card(padding=12, spacing=8)
        card.add(CardTitle("Turn right (pivot lean)"))
        card.add_layout(
            self._motor_row("Motor", self._id_left, "tr_l", preview="tr")
        )
        card.add_layout(
            self._motor_row("Motor", self._id_right, "tr_r", preview="tr")
        )
        save = QPushButton("Save turn right")
        save.setObjectName("primaryButton")
        save.setCursor(Qt.PointingHandCursor)
        save.setFocusPolicy(Qt.NoFocus)
        save.clicked.connect(self._save_turn_right)
        card.add(save)
        return card

    def _build_section_turn_duration(self) -> Card:
        card = Card(padding=12, spacing=8)
        card.add(CardTitle("Timed turn duration"))
        card.add(
            MutedLabel(
                "Hold time for Drive \u201cTurn left / right\u201d and timed pivots "
                f"({_TURN_DUR_MIN:.1f}\u2013{_TURN_DUR_MAX:.1f} s). Saved to the same JSON as lean goals."
            )
        )
        row = QHBoxLayout()
        row.setSpacing(8)
        row.addWidget(QLabel("Seconds"))
        self._dur_spin = QDoubleSpinBox()
        self._dur_spin.setDecimals(1)
        self._dur_spin.setRange(_TURN_DUR_MIN, _TURN_DUR_MAX)
        self._dur_spin.setSingleStep(0.1)
        self._dur_spin.setKeyboardTracking(False)
        row.addWidget(self._dur_spin)
        row.addStretch(1)
        card.add_layout(row)
        save = QPushButton("Save turn duration")
        save.setObjectName("primaryButton")
        save.setCursor(Qt.PointingHandCursor)
        save.setFocusPolicy(Qt.NoFocus)
        save.clicked.connect(self._save_turn_duration)
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
        tl0, tl1 = hover_computed_turn_pivot_goals(ax, turn_left=True)
        tr0, tr1 = hover_computed_turn_pivot_goals(ax, turn_left=False)
        self._ticks["tl_l"] = int(ax.turn_left_pos_left if ax.turn_left_pos_left is not None else tl0)
        self._ticks["tl_r"] = int(
            ax.turn_left_pos_right if ax.turn_left_pos_right is not None else tl1
        )
        self._ticks["tr_l"] = int(
            ax.turn_right_pos_left if ax.turn_right_pos_left is not None else tr0
        )
        self._ticks["tr_r"] = int(
            ax.turn_right_pos_right if ax.turn_right_pos_right is not None else tr1
        )
        for k, lbl in self._tick_labels.items():
            lbl.setText(str(self._ticks[k]))
        td = float(self._service.settings.navigation.turn_duration_sec)
        self._dur_spin.blockSignals(True)
        self._dur_spin.setValue(max(_TURN_DUR_MIN, min(_TURN_DUR_MAX, td)))
        self._dur_spin.blockSignals(False)

    def _refresh_computed_turn_rows_if_unset(self) -> None:
        """After FWD/REV save, update turn spinboxes if still derived (not custom)."""
        ax = self._service.settings.hoverboard_axis
        if ax.turn_left_pos_left is None:
            tl0, tl1 = hover_computed_turn_pivot_goals(ax, turn_left=True)
            self._ticks["tl_l"] = tl0
            self._ticks["tl_r"] = tl1
        if ax.turn_right_pos_left is None:
            tr0, tr1 = hover_computed_turn_pivot_goals(ax, turn_left=False)
            self._ticks["tr_l"] = tr0
            self._ticks["tr_r"] = tr1
        for k in ("tl_l", "tl_r", "tr_l", "tr_r"):
            lbl = self._tick_labels.get(k)
            if lbl is not None:
                lbl.setText(str(self._ticks[k]))

    def on_enter(self) -> None:
        if not self._service.bus_ready:
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
        self._refresh_computed_turn_rows_if_unset()
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
        self._refresh_computed_turn_rows_if_unset()
        self._service.park_hoverboard_brake()
        QMessageBox.information(
            self,
            "Saved",
            f"Backward goals: {self._id_left}={bl}, {self._id_right}={br}.\n"
            "Lean servos returned to brake.",
        )

    def _save_turn_left(self) -> None:
        ll = self._ticks["tl_l"]
        lr = self._ticks["tl_r"]
        self._service.persist_hover_lean_calibration(
            turn_left_pos_left=ll,
            turn_left_pos_right=lr,
        )
        self._service.park_hoverboard_brake()
        QMessageBox.information(
            self,
            "Saved",
            f"Turn left goals: {self._id_left}={ll}, {self._id_right}={lr}.\n"
            "Lean servos returned to brake.",
        )

    def _save_turn_right(self) -> None:
        rl = self._ticks["tr_l"]
        rr = self._ticks["tr_r"]
        self._service.persist_hover_lean_calibration(
            turn_right_pos_left=rl,
            turn_right_pos_right=rr,
        )
        self._service.park_hoverboard_brake()
        QMessageBox.information(
            self,
            "Saved",
            f"Turn right goals: {self._id_left}={rl}, {self._id_right}={rr}.\n"
            "Lean servos returned to brake.",
        )

    def _save_turn_duration(self) -> None:
        sec = float(self._dur_spin.value())
        self._service.persist_hover_lean_calibration(turn_duration_sec=sec)
        self._service.park_hoverboard_brake()
        QMessageBox.information(
            self,
            "Saved",
            f"Timed turn duration: {sec:.1f} s.\nLean servos returned to brake.",
        )
