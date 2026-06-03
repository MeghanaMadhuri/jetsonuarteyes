"""Per-action eye expression binding (manifest eye_expression + eye_offset)."""

from __future__ import annotations

from PyQt5.QtCore import Qt, pyqtSignal
from PyQt5.QtWidgets import (
    QComboBox,
    QDoubleSpinBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from nina.eye.expressions import EYE_EXPRESSIONS
from sirena_ui.widgets.common import Card, CardTitle, MutedLabel
from sirena_ui.workers.nina_service import NinaService


class EyePanel(QWidget):
    eye_changed = pyqtSignal(str)

    def __init__(self, service: NinaService, parent=None) -> None:
        super().__init__(parent)
        self._service = service

        outer = QVBoxLayout(self)
        outer.setContentsMargins(8, 8, 8, 8)
        outer.setSpacing(14)

        outer.addWidget(CardTitle("Action eye expression"))
        intro = MutedLabel(
            "Bind a face expression (0–33) to each action. On playback, Nina sends "
            "the id over UART after the eye offset (same idea as audio offset)."
        )
        intro.setWordWrap(True)
        outer.addWidget(intro)

        card = Card(spacing=12)
        outer.addWidget(card, stretch=1)

        form = QFormLayout()
        form.setLabelAlignment(Qt.AlignRight)
        form.setSpacing(10)
        card.add_layout(form)

        self._action_combo = QComboBox()
        self._action_combo.currentIndexChanged.connect(self._on_action_changed)
        form.addRow("Action:", self._action_combo)

        self._expr_combo = QComboBox()
        self._expr_combo.addItem("(none)", None)
        for expr in EYE_EXPRESSIONS:
            self._expr_combo.addItem(expr.label.replace("_", " "), expr.id)
        form.addRow("Expression:", self._expr_combo)

        self._offset_spin = QDoubleSpinBox()
        self._offset_spin.setRange(0.0, 120.0)
        self._offset_spin.setSingleStep(0.1)
        self._offset_spin.setDecimals(2)
        self._offset_spin.setSuffix(" s")
        self._offset_spin.setToolTip(
            "Seconds after motion starts before the expression is sent to the ESP."
        )
        form.addRow("Eye offset:", self._offset_spin)

        self._status_label = QLabel("")
        self._status_label.setWordWrap(True)
        self._status_label.setStyleSheet("color:#6e6e73; font-size:12px;")
        card.add(self._status_label)

        row = QHBoxLayout()
        row.setSpacing(8)
        card.add_layout(row)

        self._preview_btn = QPushButton("Preview now")
        self._preview_btn.setObjectName("secondaryButton")
        self._preview_btn.setCursor(Qt.PointingHandCursor)
        self._preview_btn.clicked.connect(self._on_preview)
        row.addWidget(self._preview_btn)

        self._clear_btn = QPushButton("Clear binding")
        self._clear_btn.setObjectName("secondaryButton")
        self._clear_btn.setCursor(Qt.PointingHandCursor)
        self._clear_btn.clicked.connect(self._on_clear)
        row.addWidget(self._clear_btn)

        self._save_btn = QPushButton("Save")
        self._save_btn.setObjectName("primaryButton")
        self._save_btn.setCursor(Qt.PointingHandCursor)
        self._save_btn.clicked.connect(self._on_save)
        row.addWidget(self._save_btn)

        row.addStretch(1)

    def select_action(self, name: str) -> None:
        idx = self._action_combo.findText(name)
        if idx >= 0:
            self._action_combo.setCurrentIndex(idx)
        self._on_action_changed()

    def refresh(self) -> None:
        current = self._action_combo.currentText()
        self._action_combo.blockSignals(True)
        self._action_combo.clear()
        try:
            actions = sorted(self._service.list_actions().keys())
        except Exception as exc:
            self._status_label.setText(str(exc))
            self._action_combo.blockSignals(False)
            return
        for name in actions:
            self._action_combo.addItem(name)
        if current:
            idx = self._action_combo.findText(current)
            if idx >= 0:
                self._action_combo.setCurrentIndex(idx)
        self._action_combo.blockSignals(False)
        self._on_action_changed()

    def _on_action_changed(self) -> None:
        name = self._action_combo.currentText().strip()
        if not name:
            return
        try:
            info = self._service.get_action_eye_info(name)
        except Exception as exc:
            self._status_label.setText(str(exc))
            return
        eid = info.get("eye_expression")
        idx = 0
        if eid is not None:
            found = self._expr_combo.findData(int(eid))
            if found >= 0:
                idx = found
        self._expr_combo.setCurrentIndex(idx)
        self._offset_spin.setValue(float(info.get("eye_offset") or 0.0))
        if eid is None:
            self._status_label.setText("No expression bound for this action.")
        else:
            self._status_label.setText(
                f"Bound: {eid} {info.get('eye_expression_name', '')} "
                f"(+{float(info.get('eye_offset') or 0):.2f}s on play)"
            )

    def _on_preview(self) -> None:
        data = self._expr_combo.currentData()
        if data is None:
            QMessageBox.information(self, "Eye", "Select an expression first.")
            return
        try:
            self._service.send_eye_expression(int(data))
        except Exception as exc:
            QMessageBox.warning(self, "Eye UART", str(exc))

    def _on_clear(self) -> None:
        name = self._action_combo.currentText().strip()
        if not name:
            return
        try:
            self._service.set_action_eye(name, None)
            self.eye_changed.emit(name)
            self._on_action_changed()
        except Exception as exc:
            QMessageBox.warning(self, "Eye", str(exc))

    def _on_save(self) -> None:
        name = self._action_combo.currentText().strip()
        if not name:
            return
        data = self._expr_combo.currentData()
        try:
            self._service.set_action_eye(
                name,
                int(data) if data is not None else None,
                eye_offset=float(self._offset_spin.value()),
            )
            self.eye_changed.emit(name)
            self._on_action_changed()
        except Exception as exc:
            QMessageBox.warning(self, "Eye", str(exc))
