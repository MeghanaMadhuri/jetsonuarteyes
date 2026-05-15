"""Tune hoverboard lean FWD/REV Dynamixel goals (e.g. IDs 12+13) with live preview."""

from __future__ import annotations

from PyQt5.QtCore import Qt, pyqtSignal
from PyQt5.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSlider,
    QVBoxLayout,
    QWidget,
)

from sirena_ui.widgets.common import Breadcrumb, Card, CardTitle, MutedLabel
from sirena_ui.workers.nina_service import NinaService


def _make_slider() -> QSlider:
    s = QSlider(Qt.Horizontal)
    s.setMinimum(0)
    s.setMaximum(4095)
    s.setSingleStep(1)
    s.setPageStep(16)
    s.setMinimumHeight(28)
    s.setFocusPolicy(Qt.NoFocus)
    return s


class MotionCalibrationScreen(QWidget):
    """Sliders around merged FWD/REV calibration; writes ``hover_calibration.json``."""

    back_requested = pyqtSignal()

    def __init__(self, service: NinaService, parent=None) -> None:
        super().__init__(parent)
        self._service = service

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
                "Sliders set absolute Dynamixel goal ticks (0\u20134095). "
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
            "(does not change forward or backward slider values)."
        )
        self._neutral_btn.clicked.connect(self._on_neutral_clicked)
        neutral_row.addWidget(self._neutral_btn)
        neutral_row.addStretch(1)
        inner.addLayout(neutral_row)

        self._fwd_l = _make_slider()
        self._fwd_r = _make_slider()
        self._back_l = _make_slider()
        self._back_r = _make_slider()

        inner.addWidget(self._build_section_forward())
        inner.addWidget(self._build_section_backward())
        inner.addStretch(1)

    def _build_section_forward(self) -> Card:
        card = Card(padding=12, spacing=8)
        card.add(CardTitle("Forward motion"))
        row1, self._lbl_fwd_l = self._motor_row(
            "Motor", self._id_left, self._fwd_l, forward=True
        )
        row2, self._lbl_fwd_r = self._motor_row(
            "Motor", self._id_right, self._fwd_r, forward=True
        )
        card.add_layout(row1)
        card.add_layout(row2)
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
        row1, self._lbl_back_l = self._motor_row(
            "Motor", self._id_left, self._back_l, forward=False
        )
        row2, self._lbl_back_r = self._motor_row(
            "Motor", self._id_right, self._back_r, forward=False
        )
        card.add_layout(row1)
        card.add_layout(row2)
        save = QPushButton("Save backward")
        save.setObjectName("primaryButton")
        save.setCursor(Qt.PointingHandCursor)
        save.setFocusPolicy(Qt.NoFocus)
        save.clicked.connect(self._save_backward)
        card.add(save)
        return card

    def _motor_row(
        self,
        label: str,
        motor_id: int,
        slider: QSlider,
        *,
        forward: bool,
    ) -> tuple[QHBoxLayout, QLabel]:
        row = QHBoxLayout()
        row.setSpacing(8)
        cap = QLabel(f"{label} {motor_id}")
        cap.setStyleSheet(
            "color: #1c1c1e; font-size: 13px; font-weight: 600;"
            " background-color: transparent; min-width: 88px;"
        )
        row.addWidget(cap)
        row.addWidget(slider, stretch=1)
        val = QLabel("0")
        val.setStyleSheet(
            "color: #1c1c1e; font-size: 15px; font-weight: 700;"
            " background-color: transparent; min-width: 44px;"
        )
        val.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        row.addWidget(val)

        def on_change(v: int) -> None:
            val.setText(str(v))
            if forward:
                self._service.preview_hover_lean_positions(
                    self._fwd_l.value(), self._fwd_r.value()
                )
            else:
                self._service.preview_hover_lean_positions(
                    self._back_l.value(), self._back_r.value()
                )

        slider.valueChanged.connect(on_change)
        return row, val

    def _on_neutral_clicked(self) -> None:
        self._service.park_hoverboard_brake()

    def _sync_sliders_from_settings(self) -> None:
        ax = self._service.settings.hoverboard_axis
        for s, v in (
            (self._fwd_l, int(ax.forward_pos_left)),
            (self._fwd_r, int(ax.forward_pos_right)),
            (self._back_l, int(ax.backward_pos_left)),
            (self._back_r, int(ax.backward_pos_right)),
        ):
            s.blockSignals(True)
            s.setValue(v)
            s.blockSignals(False)

    def on_enter(self) -> None:
        try:
            self._service.ensure_bus()
        except Exception as exc:
            QMessageBox.warning(
                self,
                "Bus",
                f"Could not open the Dynamixel bus:\n{exc}\n\n"
                "Sliders will still adjust saved values after Save.",
            )
        self._sync_sliders_from_settings()
        self._refresh_value_labels()
        ax = self._service.settings.hoverboard_axis
        self._neutral_lbl.setText(
            f"Brake / neutral reference: motor {self._id_left}={ax.brake_pos_left}, "
            f"motor {self._id_right}={ax.brake_pos_right}."
        )
        self._service.park_hoverboard_brake()

    def _refresh_value_labels(self) -> None:
        self._lbl_fwd_l.setText(str(self._fwd_l.value()))
        self._lbl_fwd_r.setText(str(self._fwd_r.value()))
        self._lbl_back_l.setText(str(self._back_l.value()))
        self._lbl_back_r.setText(str(self._back_r.value()))

    def on_leave(self) -> None:
        self._service.park_hoverboard_brake()

    def _save_forward(self) -> None:
        self._service.persist_hover_lean_calibration(
            forward_pos_left=self._fwd_l.value(),
            forward_pos_right=self._fwd_r.value(),
        )
        self._service.park_hoverboard_brake()
        QMessageBox.information(
            self,
            "Saved",
            f"Forward goals: {self._id_left}={self._fwd_l.value()}, "
            f"{self._id_right}={self._fwd_r.value()}.\n"
            "Lean servos returned to brake.",
        )

    def _save_backward(self) -> None:
        self._service.persist_hover_lean_calibration(
            backward_pos_left=self._back_l.value(),
            backward_pos_right=self._back_r.value(),
        )
        self._service.park_hoverboard_brake()
        QMessageBox.information(
            self,
            "Saved",
            f"Backward goals: {self._id_left}={self._back_l.value()}, "
            f"{self._id_right}={self._back_r.value()}.\n"
            "Lean servos returned to brake.",
        )
