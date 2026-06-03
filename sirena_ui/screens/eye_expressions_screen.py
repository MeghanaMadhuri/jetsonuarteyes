"""ST7735 eye expressions — grid of 0..33, UART to ESP8266 NodeMCU."""

from __future__ import annotations

from PyQt5.QtCore import Qt, QThread, pyqtSignal
from PyQt5.QtWidgets import (
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from nina.eye.expressions import EYE_EXPRESSIONS
from sirena_ui.widgets.common import Breadcrumb, Card, CardTitle, MutedLabel, Pill
from sirena_ui.workers.nina_service import NinaService


class _SendEyeWorker(QThread):
    finished_ok = pyqtSignal(dict)
    finished_err = pyqtSignal(str)

    def __init__(self, service: NinaService, expr_id: int, parent=None) -> None:
        super().__init__(parent)
        self._service = service
        self._expr_id = int(expr_id)

    def run(self) -> None:
        try:
            out = self._service.send_eye_expression(self._expr_id)
            self.finished_ok.emit(out)
        except Exception as exc:
            self.finished_err.emit(str(exc))


class EyeExpressionsScreen(QWidget):
    def __init__(self, service: NinaService, parent=None) -> None:
        super().__init__(parent)
        self._service = service
        self._send_worker: _SendEyeWorker | None = None
        self._busy = False

        outer = QVBoxLayout(self)
        outer.setContentsMargins(10, 10, 10, 10)
        outer.setSpacing(8)

        top = QHBoxLayout()
        top.addWidget(Breadcrumb("Nina", "Eye expressions"))
        top.addStretch(1)
        self._status_pill = Pill("Ready", Pill.KIND_NEUTRAL)
        top.addWidget(self._status_pill)
        outer.addLayout(top)

        card = Card()
        card_l = QVBoxLayout(card)
        card_l.setSpacing(6)
        card_l.addWidget(CardTitle("ESP8266 face display"))
        eu = service.settings.eye_uart
        card_l.addWidget(
            MutedLabel(
                f"UART {eu.port} @ {eu.baudrate} — tap an expression. "
                "Wiring: Jetson TX→ESP RX, Jetson RX→ESP TX, GND."
            )
        )
        outer.addWidget(card)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.NoFrame)
        grid_host = QWidget()
        grid = QGridLayout(grid_host)
        grid.setSpacing(6)
        grid.setContentsMargins(0, 0, 0, 0)

        cols = 4
        for idx, expr in enumerate(EYE_EXPRESSIONS):
            btn = QPushButton(expr.label.replace("_", " "))
            btn.setObjectName("eyeExprButton")
            btn.setCursor(Qt.PointingHandCursor)
            btn.setMinimumHeight(44)
            mood = expr.mood
            tip = f"ID {expr.id} · {expr.name} · mood {mood}"
            btn.setToolTip(tip)
            eid = expr.id
            btn.clicked.connect(lambda _checked=False, i=eid: self._on_pick(i))
            row, col = divmod(idx, cols)
            grid.addWidget(btn, row, col)
        scroll.setWidget(grid_host)
        outer.addWidget(scroll, stretch=1)

    def on_enter(self) -> None:
        self._refresh_status_pill()

    def on_leave(self) -> None:
        w = self._send_worker
        if w is not None and w.isRunning():
            w.wait(2000)

    def _refresh_status_pill(self) -> None:
        eu = self._service.settings.eye_uart
        if not eu.enabled:
            self._status_pill.setText("UART disabled")
            self._status_pill.set_kind(Pill.KIND_WARN)
            return
        try:
            st = self._service.eye_uart_status()
            if st.get("open"):
                self._status_pill.setText(f"{eu.port} open")
                self._status_pill.set_kind(Pill.KIND_OK)
            else:
                self._status_pill.setText(f"{eu.port} (send to open)")
                self._status_pill.set_kind(Pill.KIND_NEUTRAL)
        except Exception as exc:
            self._status_pill.setText("Status error")
            self._status_pill.set_kind(Pill.KIND_WARN)

    def _on_pick(self, expr_id: int) -> None:
        if self._busy:
            return
        if not self._service.settings.eye_uart.enabled:
            QMessageBox.warning(
                self,
                "Eye UART",
                "Eye UART is disabled. Set NINA_EYE_UART_ENABLE=1 in navigation.env.",
            )
            return
        self._busy = True
        self._status_pill.setText(f"Sending {expr_id}…")
        self._status_pill.set_kind(Pill.KIND_WARN)
        w = _SendEyeWorker(self._service, expr_id, self)
        self._send_worker = w
        w.finished_ok.connect(self._on_sent_ok)
        w.finished_err.connect(self._on_sent_err)
        w.finished.connect(self._on_send_done)
        w.start()

    def _on_sent_ok(self, payload: dict) -> None:
        name = payload.get("name", "")
        self._status_pill.setText(f"→ {payload.get('id')} {name}")
        self._status_pill.set_kind(Pill.KIND_OK)

    def _on_sent_err(self, msg: str) -> None:
        self._status_pill.setText("Send failed")
        self._status_pill.set_kind(Pill.KIND_ERROR)
        QMessageBox.warning(
            self,
            "Eye UART",
            msg
            + "\n\nCheck USB-UART or header wiring and device path "
            f"({self._service.settings.eye_uart.port}).",
        )

    def _on_send_done(self) -> None:
        self._busy = False
        self._send_worker = None
