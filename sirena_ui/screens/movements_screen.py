"""Saved drive sequences — list, author, and run custom forward/turn programs."""

from __future__ import annotations

from typing import List, Optional

from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import (
    QDoubleSpinBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from nina.movements.model import (
    STEP_BACKWARD,
    STEP_FORWARD,
    STEP_TURN_LEFT,
    STEP_TURN_RIGHT,
    STEP_UTURN,
    MovementStep,
    SavedMovement,
)
from sirena_ui.widgets.common import Breadcrumb, Card, CardTitle, MutedLabel
from sirena_ui.workers.nina_service import NinaService


class MovementsScreen(QWidget):
    """Top-level screen: saved movements list + sequence editor."""

    def __init__(self, service: NinaService, parent=None) -> None:
        super().__init__(parent)
        self._service = service
        self._editing: Optional[SavedMovement] = None
        self._running_id: Optional[str] = None

        outer = QVBoxLayout(self)
        outer.setContentsMargins(10, 10, 10, 10)
        outer.setSpacing(8)

        top = QHBoxLayout()
        self._breadcrumb = Breadcrumb("Nina", "Movements")
        top.addWidget(self._breadcrumb)
        top.addStretch(1)
        outer.addLayout(top)

        self._stack = QStackedWidget()
        outer.addWidget(self._stack, stretch=1)

        self._list_page = self._build_list_page()
        self._editor_page = self._build_editor_page()
        self._stack.addWidget(self._list_page)
        self._stack.addWidget(self._editor_page)
        self._stack.setCurrentWidget(self._list_page)

        drive = self._service.drive
        drive.saved_movement_finished.connect(self._on_run_finished)
        drive.saved_movement_failed.connect(self._on_run_failed)

    def on_enter(self) -> None:
        self._service.drive.ensure_hardware()
        self._refresh_list()

    # ---------- list page ----------

    def _build_list_page(self) -> QWidget:
        page = QWidget()
        v = QVBoxLayout(page)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(8)

        v.addWidget(CardTitle("Saved movements"))
        hint = MutedLabel(
            "Run a named sequence of forward segments and IMU-guided turns. "
            "Turns use the same micro-step closed-loop control as the Drive screen; "
            "after each turn the heading reference is reset for the next straight leg."
        )
        hint.setWordWrap(True)
        v.addWidget(hint)

        card = Card(spacing=10)
        v.addWidget(card, stretch=1)

        self._movement_list = QListWidget()
        self._movement_list.itemDoubleClicked.connect(self._on_edit_selected)
        card.add(self._movement_list, stretch=1)

        row = QHBoxLayout()
        self._run_btn = QPushButton("Run")
        self._run_btn.setObjectName("primaryButton")
        self._run_btn.setCursor(Qt.PointingHandCursor)
        self._run_btn.clicked.connect(self._on_run_selected)
        row.addWidget(self._run_btn)

        self._delete_btn = QPushButton("Delete")
        self._delete_btn.setObjectName("secondaryButton")
        self._delete_btn.setCursor(Qt.PointingHandCursor)
        self._delete_btn.clicked.connect(self._on_delete_selected)
        row.addWidget(self._delete_btn)

        row.addStretch(1)

        self._create_btn = QPushButton("Create new")
        self._create_btn.setObjectName("primaryButton")
        self._create_btn.setCursor(Qt.PointingHandCursor)
        self._create_btn.clicked.connect(self._on_create_new)
        row.addWidget(self._create_btn)
        card.add_layout(row)

        self._list_status = MutedLabel("")
        v.addWidget(self._list_status)
        return page

    def _refresh_list(self) -> None:
        self._movement_list.clear()
        for mv in self._service.movement_store.load_all():
            item = QListWidgetItem(f"{mv.name}  ({mv.summary()})")
            item.setData(Qt.UserRole, mv.movement_id)
            self._movement_list.addItem(item)
        n = self._movement_list.count()
        self._list_status.setText(
            f"{n} saved movement{'s' if n != 1 else ''}."
            if n
            else "No saved movements yet — tap Create new."
        )

    def _selected_movement_id(self) -> Optional[str]:
        item = self._movement_list.currentItem()
        if item is None:
            return None
        return str(item.data(Qt.UserRole) or "") or None

    def _on_run_selected(self) -> None:
        mid = self._selected_movement_id()
        if not mid:
            QMessageBox.information(self, "Select a movement", "Choose a saved movement to run.")
            return
        mv = self._service.movement_store.get(mid)
        if mv is None:
            QMessageBox.warning(self, "Not found", "That movement was removed.")
            self._refresh_list()
            return
        if self._service.drive.state().get("brake"):
            confirm = QMessageBox.question(
                self,
                "Release brake?",
                "Brake is engaged. Release it and run the sequence?",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.Yes,
            )
            if confirm != QMessageBox.Yes:
                return
        self._running_id = mid
        self._list_status.setText(f"Running “{mv.name}” …")
        self._set_list_busy(True)
        self._service.drive.run_saved_movement(mv)

    def _on_delete_selected(self) -> None:
        mid = self._selected_movement_id()
        if not mid:
            QMessageBox.information(self, "Select a movement", "Choose a movement to delete.")
            return
        mv = self._service.movement_store.get(mid)
        if mv is None:
            self._refresh_list()
            return
        confirm = QMessageBox.question(
            self,
            "Delete movement?",
            f"Delete “{mv.name}”?",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if confirm != QMessageBox.Yes:
            return
        self._service.movement_store.delete(mid)
        self._refresh_list()

    def _on_create_new(self) -> None:
        self._editing = SavedMovement(name="New movement", steps=[])
        self._show_editor()

    def _on_edit_selected(self) -> None:
        mid = self._selected_movement_id()
        if not mid:
            return
        mv = self._service.movement_store.get(mid)
        if mv is None:
            return
        self._editing = SavedMovement(
            name=mv.name,
            steps=list(mv.steps),
            movement_id=mv.movement_id,
        )
        self._show_editor()

    # ---------- editor page ----------

    def _build_editor_page(self) -> QWidget:
        page = QWidget()
        v = QVBoxLayout(page)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(8)

        v.addWidget(CardTitle("Create movement"))
        intro = MutedLabel(
            "Build a sequence: forward time in seconds, turn angles (IMU closed-loop), "
            "or a 180° U-turn. Steps run in order on the Drive worker thread."
        )
        intro.setWordWrap(True)
        v.addWidget(intro)

        card = Card(spacing=10)
        v.addWidget(card, stretch=1)

        from PyQt5.QtWidgets import QLineEdit

        form = QFormLayout()
        self._name_field = QLineEdit()
        self._name_field.setPlaceholderText("e.g. Patrol loop")
        form.addRow("Name:", self._name_field)
        card.add_layout(form)

        card.add(QLabel("Sequence (in order):"))
        self._steps_list = QListWidget()
        card.add(self._steps_list, stretch=1)

        add_row = QHBoxLayout()
        for label, slot in (
            ("+ Forward", self._add_forward),
            ("+ Back", self._add_backward),
            ("+ Turn left", self._add_turn_left),
            ("+ Turn right", self._add_turn_right),
            ("+ U-turn", self._add_uturn),
        ):
            btn = QPushButton(label)
            btn.setObjectName("secondaryButton")
            btn.setCursor(Qt.PointingHandCursor)
            btn.clicked.connect(slot)
            add_row.addWidget(btn)
        card.add_layout(add_row)

        rem_row = QHBoxLayout()
        rem_btn = QPushButton("Remove step")
        rem_btn.setObjectName("secondaryButton")
        rem_btn.setCursor(Qt.PointingHandCursor)
        rem_btn.clicked.connect(self._remove_step)
        rem_row.addWidget(rem_btn)
        rem_row.addStretch(1)
        card.add_layout(rem_row)

        nav = QHBoxLayout()
        self._cancel_edit_btn = QPushButton("Cancel")
        self._cancel_edit_btn.setObjectName("secondaryButton")
        self._cancel_edit_btn.setCursor(Qt.PointingHandCursor)
        self._cancel_edit_btn.clicked.connect(self._on_cancel_edit)
        nav.addWidget(self._cancel_edit_btn)
        nav.addStretch(1)
        self._save_btn = QPushButton("Save")
        self._save_btn.setObjectName("primaryButton")
        self._save_btn.setCursor(Qt.PointingHandCursor)
        self._save_btn.clicked.connect(self._on_save)
        nav.addWidget(self._save_btn)
        card.add_layout(nav)
        return page

    def _show_editor(self) -> None:
        if self._editing is None:
            return
        self._name_field.setText(self._editing.name)
        self._refresh_steps_list()
        self._stack.setCurrentWidget(self._editor_page)
        self._breadcrumb.set_parts("Nina", "Movements", "Edit")
        from PyQt5.QtCore import QTimer

        from sirena_ui.workers.osk import activate_text_input

        QTimer.singleShot(50, lambda: activate_text_input(self._name_field))

    def _refresh_steps_list(self) -> None:
        self._steps_list.clear()
        if self._editing is None:
            return
        for idx, step in enumerate(self._editing.steps, start=1):
            self._steps_list.addItem(f"{idx}. {step.summary()}")

    def _append_step(self, step: MovementStep) -> None:
        if self._editing is None:
            return
        self._editing.steps.append(step)
        self._refresh_steps_list()

    def _add_forward(self) -> None:
        sec = _prompt_seconds(self, "Forward duration", default=1.0, max_sec=120.0)
        if sec is None:
            return
        self._append_step(MovementStep(kind=STEP_FORWARD, seconds=sec))

    def _add_backward(self) -> None:
        sec = _prompt_seconds(self, "Backward duration", default=1.0, max_sec=120.0)
        if sec is None:
            return
        self._append_step(MovementStep(kind=STEP_BACKWARD, seconds=sec))

    def _add_turn_left(self) -> None:
        deg = _prompt_degrees(self, "Turn left angle", default=90.0)
        if deg is None:
            return
        self._append_step(MovementStep(kind=STEP_TURN_LEFT, degrees=deg))

    def _add_turn_right(self) -> None:
        deg = _prompt_degrees(self, "Turn right angle", default=90.0)
        if deg is None:
            return
        self._append_step(MovementStep(kind=STEP_TURN_RIGHT, degrees=deg))

    def _add_uturn(self) -> None:
        from PyQt5.QtWidgets import QInputDialog

        direction, ok = QInputDialog.getItem(
            self,
            "U-turn direction",
            "Rotate 180° (U-turn) which way?",
            ["Right", "Left"],
            0,
            False,
        )
        if not ok:
            return
        uturn_dir = "left" if direction == "Left" else "right"
        self._append_step(
            MovementStep(kind=STEP_UTURN, degrees=180.0, uturn_direction=uturn_dir)
        )

    def _remove_step(self) -> None:
        if self._editing is None:
            return
        row = self._steps_list.currentRow()
        if row < 0 or row >= len(self._editing.steps):
            QMessageBox.information(self, "Select a step", "Choose a step to remove.")
            return
        del self._editing.steps[row]
        self._refresh_steps_list()

    def _on_cancel_edit(self) -> None:
        self._editing = None
        self._stack.setCurrentWidget(self._list_page)
        self._breadcrumb.set_parts("Nina", "Movements")
        self._refresh_list()

    def _on_save(self) -> None:
        if self._editing is None:
            return
        name = self._name_field.text().strip()
        if not name:
            QMessageBox.warning(self, "Name required", "Enter a name for this movement.")
            return
        if not self._editing.steps:
            QMessageBox.warning(
                self,
                "Empty sequence",
                "Add at least one step before saving.",
            )
            return
        self._editing.name = name
        self._service.movement_store.upsert(self._editing)
        self._editing = None
        self._on_cancel_edit()

    def _on_run_finished(self, movement_id: str) -> None:
        if self._running_id and movement_id != self._running_id:
            return
        self._running_id = None
        self._set_list_busy(False)
        self._list_status.setText("Sequence finished.")
        self._refresh_list()

    def _on_run_failed(self, movement_id: str, message: str) -> None:
        if self._running_id and movement_id != self._running_id:
            return
        self._running_id = None
        self._set_list_busy(False)
        self._list_status.setText(f"Failed: {message}")
        QMessageBox.warning(self, "Movement failed", message)

    def _set_list_busy(self, busy: bool) -> None:
        for w in (
            self._movement_list,
            self._run_btn,
            self._delete_btn,
            self._create_btn,
        ):
            w.setEnabled(not busy)


def _prompt_seconds(parent: QWidget, title: str, *, default: float, max_sec: float) -> Optional[float]:
    from PyQt5.QtWidgets import QDialog, QDialogButtonBox

    dlg = QDialog(parent)
    dlg.setWindowTitle(title)
    lay = QVBoxLayout(dlg)
    spin = QDoubleSpinBox()
    spin.setRange(0.1, max_sec)
    spin.setDecimals(1)
    spin.setSuffix(" s")
    spin.setValue(default)
    lay.addWidget(spin)
    buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
    buttons.accepted.connect(dlg.accept)
    buttons.rejected.connect(dlg.reject)
    lay.addWidget(buttons)
    spin.setFocus()
    if dlg.exec_() != QDialog.Accepted:
        return None
    return float(spin.value())


def _prompt_degrees(parent: QWidget, title: str, *, default: float) -> Optional[float]:
    from PyQt5.QtWidgets import QDialog, QDialogButtonBox

    dlg = QDialog(parent)
    dlg.setWindowTitle(title)
    lay = QVBoxLayout(dlg)
    spin = QDoubleSpinBox()
    spin.setRange(1.0, 360.0)
    spin.setDecimals(0)
    spin.setSuffix(" °")
    spin.setValue(default)
    lay.addWidget(spin)
    buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
    buttons.accepted.connect(dlg.accept)
    buttons.rejected.connect(dlg.reject)
    lay.addWidget(buttons)
    spin.setFocus()
    if dlg.exec_() != QDialog.Accepted:
        return None
    return float(spin.value())
