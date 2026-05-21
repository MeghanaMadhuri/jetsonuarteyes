"""Backward-lean bench calibration screen.

Live preview only — nothing is persisted to disk, no calibration file,
no settings mutation. The operator drags two sliders (or taps explicit
``−`` / ``+`` buttons) until the chassis goes straight backward on the
bench, then copies the discovered tick values into
``/etc/nina-link/navigation.env`` as
``NINA_HOVER_FWD_POS_LEFT`` / ``NINA_HOVER_FWD_POS_RIGHT`` (loaded as
backward lean after the fleet hall FWD/REV swap in ``settings.py``).

Mechanism:

* Every value change is pushed to the MX-28 lean servos immediately via
  :meth:`NinaService.preview_hover_lean_positions`, which writes the
  raw goal positions on the Dynamixel bus while holding ``bus_lock``.
* On screen entry the chassis is parked to the calibrated brake pose
  so the lean starts from a known-safe neutral.
* On screen exit (``on_leave``) the chassis is parked back to brake so
  the operator never walks away from a tilted lean stack.

Safety knobs:

* Tick range is clamped on the UI side to ``brake ± 300`` (i.e. roughly
  ``1748 .. 2348`` with the stock 2048 brake). The underlying bus also
  clamps to ``[0, 4095]``, but the UI clamp keeps a stray slider
  release from sending the lean stack into a saturated corner. The
  bench operator can widen this constant if a chassis needs a wilder
  range — there is no production code path that depends on it.
* Slider drags do NOT apply mid-drag; the lean is only re-sent on
  slider release (and on every ``+`` / ``−`` tap / spinbox commit /
  explicit APPLY click). That keeps the bus from being flooded while
  the operator is still aiming.
"""

from __future__ import annotations

import logging
from typing import Optional

from PyQt5.QtCore import Qt, QTimer, pyqtSignal
from PyQt5.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QSlider,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from nina.config.navigation_env import (
    DEFAULT_ENV_PATH as _NAV_ENV_PATH,
    NavigationEnvWriteError,
    update_backward_lean as _save_backward_lean_to_env,
)
from sirena_ui.widgets.common import Card, CardTitle, MutedLabel
from sirena_ui.workers.nina_service import NinaService

log = logging.getLogger(__name__)


_TICK_HALF_RANGE = 300


class _MotorRow(QFrame):
    """One motor's calibration row: label, −, slider, +, value spinbox.

    Emits :pyattr:`apply_requested` (``int``) whenever the user's
    intended tick value changes via an action that should push to the
    bus immediately (``+`` / ``−`` tap, spinbox edit committed, slider
    released). Mid-drag slider movement does NOT emit.
    """

    apply_requested = pyqtSignal(int)

    def __init__(
        self,
        title: str,
        motor_id: int,
        initial_ticks: int,
        tick_min: int,
        tick_max: int,
        brake: int,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self._motor_id = motor_id
        self._tick_min = int(tick_min)
        self._tick_max = int(tick_max)
        self._brake = int(brake)

        h = QHBoxLayout(self)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(10)

        name = QLabel(
            f"<b>{title}</b><br>"
            f"<span style='font-size:11px;color:#8e8e93'>motor {motor_id}</span>"
        )
        name.setMinimumWidth(110)
        h.addWidget(name)

        self._btn_minus = QPushButton("\u2212")  # minus sign
        self._btn_minus.setFixedSize(48, 48)
        self._btn_minus.setStyleSheet(
            "font-size: 22px; font-weight: 700;"
        )
        h.addWidget(self._btn_minus)

        self._slider = QSlider(Qt.Horizontal)
        self._slider.setRange(self._tick_min, self._tick_max)
        self._slider.setValue(self._clamp(initial_ticks))
        self._slider.setTickPosition(QSlider.TicksBelow)
        self._slider.setTickInterval(50)
        self._slider.setSingleStep(1)
        self._slider.setPageStep(10)
        self._slider.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        h.addWidget(self._slider, stretch=1)

        self._btn_plus = QPushButton("+")
        self._btn_plus.setFixedSize(48, 48)
        self._btn_plus.setStyleSheet(
            "font-size: 22px; font-weight: 700;"
        )
        h.addWidget(self._btn_plus)

        self._spin = QSpinBox()
        self._spin.setRange(self._tick_min, self._tick_max)
        self._spin.setValue(self._clamp(initial_ticks))
        self._spin.setFixedWidth(96)
        self._spin.setAlignment(Qt.AlignCenter)
        self._spin.setStyleSheet("font-size: 16px; font-weight: 600;")
        self._spin.setButtonSymbols(QSpinBox.NoButtons)
        h.addWidget(self._spin)

        self._delta = QLabel(self._delta_text(self._spin.value()))
        self._delta.setMinimumWidth(86)
        self._delta.setStyleSheet(
            "color: #8e8e93; font-family: monospace; font-size: 12px;"
        )
        h.addWidget(self._delta)

        # Internal sync between slider and spinbox (no apply emitted —
        # the apply is gated to actual operator commits below).
        self._slider.valueChanged.connect(self._on_slider_visual_changed)
        self._spin.valueChanged.connect(self._on_spin_visual_changed)

        # Apply triggers: slider released, +/- tap, spinbox commit.
        self._slider.sliderReleased.connect(
            lambda: self._emit_apply(self._slider.value())
        )
        self._btn_minus.clicked.connect(lambda: self._bump(-1))
        self._btn_plus.clicked.connect(lambda: self._bump(+1))
        self._spin.editingFinished.connect(
            lambda: self._emit_apply(self._spin.value())
        )

    # --- public API ---

    def ticks(self) -> int:
        return int(self._spin.value())

    def set_ticks(self, v: int, *, emit_apply: bool = False) -> None:
        v = self._clamp(int(v))
        # Keep slider + spinbox in sync without re-emitting visual signals.
        self._slider.blockSignals(True)
        self._spin.blockSignals(True)
        self._slider.setValue(v)
        self._spin.setValue(v)
        self._slider.blockSignals(False)
        self._spin.blockSignals(False)
        self._delta.setText(self._delta_text(v))
        if emit_apply:
            self.apply_requested.emit(v)

    # --- internals ---

    def _clamp(self, v: int) -> int:
        return max(self._tick_min, min(self._tick_max, int(v)))

    def _delta_text(self, v: int) -> str:
        d = int(v) - self._brake
        return f"\u0394 brake: {d:+d}"

    def _bump(self, delta: int) -> None:
        new_val = self._clamp(self._spin.value() + int(delta))
        # set_ticks updates both widgets; emit so the bus gets the new value.
        self.set_ticks(new_val, emit_apply=True)

    def _emit_apply(self, v: int) -> None:
        v = self._clamp(int(v))
        # Keep slider + spinbox in sync (the emitting widget already
        # has the new value; this just propagates).
        if self._slider.value() != v:
            self._slider.blockSignals(True)
            self._slider.setValue(v)
            self._slider.blockSignals(False)
        if self._spin.value() != v:
            self._spin.blockSignals(True)
            self._spin.setValue(v)
            self._spin.blockSignals(False)
        self._delta.setText(self._delta_text(v))
        self.apply_requested.emit(v)

    def _on_slider_visual_changed(self, v: int) -> None:
        # During slider drag, just keep the spinbox + delta in sync;
        # do NOT push to the bus. The apply lands on sliderReleased.
        if self._spin.value() != v:
            self._spin.blockSignals(True)
            self._spin.setValue(v)
            self._spin.blockSignals(False)
        self._delta.setText(self._delta_text(v))

    def _on_spin_visual_changed(self, v: int) -> None:
        if self._slider.value() != v:
            self._slider.blockSignals(True)
            self._slider.setValue(v)
            self._slider.blockSignals(False)
        self._delta.setText(self._delta_text(v))


class LeanCalScreen(QWidget):
    """Bench-only screen for finding the MX-28 lean tick values that
    make the chassis go straight backward. No persistence — values are
    not saved to disk. Operator copies the discovered numbers into
    ``/etc/nina-link/navigation.env`` manually when satisfied.
    """

    def __init__(self, service: NinaService, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._service = service
        axis = service.settings.hoverboard_axis
        self._left_id = int(axis.id_left)
        self._right_id = int(axis.id_right)
        self._brake_left = int(axis.brake_pos_left)
        self._brake_right = int(axis.brake_pos_right)

        # Range = brake ± _TICK_HALF_RANGE on each side. Plenty of room
        # for either polarity (backward lean is mirror of forward).
        l_min = max(0, self._brake_left - _TICK_HALF_RANGE)
        l_max = min(4095, self._brake_left + _TICK_HALF_RANGE)
        r_min = max(0, self._brake_right - _TICK_HALF_RANGE)
        r_max = min(4095, self._brake_right + _TICK_HALF_RANGE)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(16, 12, 16, 12)
        outer.setSpacing(12)

        card = Card()
        card.add(CardTitle("Backward lean \u00b7 bench calibration"))

        info = MutedLabel(
            "Live preview only \u2014 values are NOT saved. Tap "
            "\u2212 / + or drag the slider; the lean updates "
            "immediately. When the chassis goes straight backward, "
            "copy the L / R numbers into "
            "<code>/etc/nina-link/navigation.env</code> as "
            "<code>NINA_HOVER_FWD_POS_LEFT</code> / "
            "<code>NINA_HOVER_FWD_POS_RIGHT</code> and restart the "
            "service."
        )
        info.setTextFormat(Qt.RichText)
        card.add(info)

        ref = QLabel(
            f"<span style='color:#8e8e93;font-family:monospace;font-size:12px'>"
            f"Brake reference: L(id{self._left_id})={self._brake_left} "
            f"R(id{self._right_id})={self._brake_right}"
            f"</span>"
        )
        ref.setTextFormat(Qt.RichText)
        card.add(ref)

        self._left_row = _MotorRow(
            "Left",
            self._left_id,
            initial_ticks=int(axis.backward_pos_left),
            tick_min=l_min,
            tick_max=l_max,
            brake=self._brake_left,
        )
        self._right_row = _MotorRow(
            "Right",
            self._right_id,
            initial_ticks=int(axis.backward_pos_right),
            tick_min=r_min,
            tick_max=r_max,
            brake=self._brake_right,
        )
        card.add(self._left_row)
        card.add(self._right_row)

        # Every per-row apply re-sends BOTH sides to the bus (the
        # MX-28 sync write takes a pair of goals — there's no
        # per-motor write).
        self._left_row.apply_requested.connect(lambda _v: self._push_to_bus())
        self._right_row.apply_requested.connect(lambda _v: self._push_to_bus())

        # Action row: APPLY (re-send current sliders) + BRAKE + RESET.
        actions = QHBoxLayout()
        actions.setSpacing(8)

        self._btn_apply = QPushButton("APPLY (lean now)")
        self._btn_apply.setMinimumHeight(44)
        self._btn_apply.setStyleSheet(
            "background-color: #2266aa; color: white; font-weight: 700;"
            " font-size: 14px; padding: 6px 16px;"
        )
        self._btn_apply.clicked.connect(self._push_to_bus)
        actions.addWidget(self._btn_apply)

        # SAVE persists the current L/R into /etc/nina-link/navigation.env
        # so the calibrated lean survives a service restart. The actual
        # in-place update + permission handling lives in
        # nina.config.navigation_env; we just call it and surface the
        # outcome on the status label.
        self._btn_save = QPushButton("SAVE to env")
        self._btn_save.setMinimumHeight(44)
        self._btn_save.setStyleSheet(
            "background-color: #2a8a4a; color: white; font-weight: 700;"
            " font-size: 14px; padding: 6px 16px;"
        )
        self._btn_save.setToolTip(
            "Persist the current L / R values into\n"
            "/etc/nina-link/navigation.env so they survive a service restart.\n"
            "May prompt for the operator's password if /etc/ requires root."
        )
        self._btn_save.clicked.connect(self._save_to_env)
        actions.addWidget(self._btn_save)

        self._btn_brake = QPushButton("BRAKE / NEUTRAL")
        self._btn_brake.setMinimumHeight(44)
        self._btn_brake.setStyleSheet(
            "background-color: #aa3333; color: white; font-weight: 700;"
            " font-size: 14px; padding: 6px 16px;"
        )
        self._btn_brake.clicked.connect(self._brake_now)
        actions.addWidget(self._btn_brake)

        self._btn_reset = QPushButton("Reset to configured")
        self._btn_reset.setMinimumHeight(44)
        self._btn_reset.clicked.connect(self._reset_to_configured)
        actions.addWidget(self._btn_reset)

        actions.addStretch(1)
        card.add_layout(actions)

        self._status = QLabel("Awaiting hardware \u2026")
        self._status.setStyleSheet(
            "color: #8e8e93; font-family: monospace; font-size: 12px;"
        )
        self._status.setWordWrap(True)
        card.add(self._status)

        outer.addWidget(card)
        outer.addStretch(1)

    # --- screen lifecycle ---

    def on_enter(self) -> None:
        """Cheap, non-blocking entry: do NOT re-init the bus here.

        The app-level ``_initialize_bus`` (kicked off from
        ``MainWindow`` at startup) brings the Dynamixel bus up on a
        background ``_BusInitThread`` — by the time the operator
        navigates to this screen the bus is normally ready. Calling
        :meth:`NinaService.ensure_bus` AGAIN from ``on_enter`` would
        block the GUI thread for ~15-20 s on every entry because
        ``ensure_bus`` re-runs the health check + joint-mode setup +
        torque enable on every call (not just the first), and it
        holds ``bus_lock`` the whole time.

        Instead we just check ``bus_ready`` and defer the (fast)
        brake park via a ``QTimer.singleShot(0, ...)`` so the screen
        paints first. If the bus isn't ready yet the operator gets a
        friendly status message; they can navigate away and back, or
        just wait — the first slider commit will retry the bus call
        via :meth:`_push_to_bus`, which is cheap.
        """
        self._status.setText("Loading \u2026")
        # Defer the actual work so the screen paints immediately.
        QTimer.singleShot(0, self._on_enter_deferred)

    def _on_enter_deferred(self) -> None:
        if not self._service.bus_ready:
            self._status.setText(
                "Dynamixel bus is still initializing \u2014 give it a few "
                "seconds and try again, or check the Health screen. "
                "Sliders work either way; they'll push to the motors "
                "as soon as the bus is up."
            )
            return
        try:
            self._service.park_hoverboard_brake()
        except Exception as exc:
            log.exception("lean-cal: initial brake park failed")
            self._status.setText(f"initial brake park failed: {exc}")
            return
        self._status.setText(
            f"Ready. Brake L={self._brake_left} R={self._brake_right}. "
            f"Adjust to push backward lean (positive L \u0394, negative R \u0394)."
        )

    def on_leave(self) -> None:
        """Always park back to brake when the operator leaves the
        screen — don't strand the lean stack tilted. Skipped when the
        bus isn't ready (nothing to park to)."""
        if not self._service.bus_ready:
            return
        try:
            self._service.park_hoverboard_brake()
        except Exception:
            log.exception("lean-cal: park-on-leave failed")

    # --- bus actions ---

    def _push_to_bus(self) -> None:
        l = self._left_row.ticks()
        r = self._right_row.ticks()
        if not self._service.bus_ready:
            self._status.setText("bus not ready \u2014 cannot apply lean")
            return
        try:
            self._service.preview_hover_lean_positions(l, r)
        except Exception as exc:
            log.exception("lean-cal: preview failed")
            self._status.setText(f"apply failed: {exc}")
            return
        dl = l - self._brake_left
        dr = r - self._brake_right
        self._status.setText(
            f"Applied: L(id{self._left_id})={l}  R(id{self._right_id})={r}  "
            f"(\u0394 L={dl:+d}, R={dr:+d})"
        )
        log.info(
            "lean-cal: applied L(id%d)=%d R(id%d)=%d (dL=%+d dR=%+d)",
            self._left_id, l, self._right_id, r, dl, dr,
        )

    def _brake_now(self) -> None:
        if not self._service.bus_ready:
            self._status.setText("bus not ready \u2014 cannot brake")
            return
        try:
            self._service.park_hoverboard_brake()
        except Exception as exc:
            log.exception("lean-cal: brake failed")
            self._status.setText(f"brake failed: {exc}")
            return
        # Reset visible sliders to brake too so the next +/- starts from
        # the displayed neutral, not the prior lean.
        self._left_row.set_ticks(self._brake_left, emit_apply=False)
        self._right_row.set_ticks(self._brake_right, emit_apply=False)
        self._status.setText(
            f"Braked. L(id{self._left_id})={self._brake_left}  "
            f"R(id{self._right_id})={self._brake_right}"
        )
        log.info("lean-cal: braked")

    def _reset_to_configured(self) -> None:
        axis = self._service.settings.hoverboard_axis
        self._left_row.set_ticks(int(axis.backward_pos_left), emit_apply=False)
        self._right_row.set_ticks(int(axis.backward_pos_right), emit_apply=False)
        # One bus write with both new values.
        self._push_to_bus()

    def _save_to_env(self) -> None:
        """Persist the current L / R slider values into the navigation
        env file so they survive a service restart. The systemd unit
        reads ``EnvironmentFile=-/etc/nina-link/navigation.env`` BEFORE
        the Python code starts, so once this file is updated, the next
        ``systemctl restart nina-ui-kiosk.service`` will pick up the
        new lean calibration.

        We don't restart the service from the UI — that would kill the
        process we're running in. The operator does that explicitly
        when they're ready.
        """
        l = self._left_row.ticks()
        r = self._right_row.ticks()
        try:
            written = _save_backward_lean_to_env(l, r)
        except NavigationEnvWriteError as exc:
            log.warning(
                "lean-cal: save to %s failed: %s (%s)",
                _NAV_ENV_PATH, exc, exc.detail,
            )
            # Surface a clear hint so the operator can copy/paste
            # manually if polkit / sudo isn't an option here.
            self._status.setText(
                f"SAVE failed: {exc}. "
                f"Copy manually into {_NAV_ENV_PATH}: "
                f"NINA_HOVER_FWD_POS_LEFT={l}  "
                f"NINA_HOVER_FWD_POS_RIGHT={r}"
            )
            return
        except ValueError as exc:
            log.error("lean-cal: save validation failed: %s", exc)
            self._status.setText(f"SAVE rejected: {exc}")
            return
        except Exception as exc:
            log.exception("lean-cal: unexpected save failure")
            self._status.setText(f"SAVE failed: {exc!r}")
            return
        log.info(
            "lean-cal: saved L=%d R=%d to %s (restart service to apply)",
            l, r, written,
        )
        self._status.setText(
            f"SAVED to {written}: L(id{self._left_id})={l}  "
            f"R(id{self._right_id})={r}. "
            f"Restart the service to load: "
            f"systemctl --user restart nina-ui-kiosk.service"
        )
