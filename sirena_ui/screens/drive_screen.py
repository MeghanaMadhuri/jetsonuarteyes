"""Drive screen: front camera placeholder + manual control cockpit.

The screen talks to locomotion through `NinaService.drive`, a Qt facade over
`HoverboardAxisDrive` (Dynamixel MX-28 lean axes, typically IDs 12+13) via
`DriveController`. Hardware init runs lazily on first visit to this screen.

On a dev host without a live Dynamixel bus, init may fail and the status pill
shows the error—buttons still update internal state but servos will not move.

Two input modes are supported:

* On-screen D-pad — press and hold one direction at a time (other arrows grey out).
  **Forward/back** always use IMU straight pulse on hoverboard; **left/right** fire
  closed-loop micro-steps (~``NINA_DRIVE_TURN_PIVOT_DEG``) on an interval while held
  (``NINA_DRIVE_HOLD_TURN_INTERVAL_SEC``).
* **Turn left / Turn right** — one micro-step per click (same geometry as held L/R).
* Keyboard — W/A/S/D forward / left / back / right while held,
  Space stops, Esc fires the EMERGENCY STOP. Auto-repeat events are
  ignored so a held key looks like one press + one release to the
  motor controller. WASD bubbles up through the focused widget on
  PyQt5, so it works when the screen body or a button has focus.
"""

from __future__ import annotations

import logging
import math
import os
import time
from typing import TYPE_CHECKING, List, Optional, Tuple

from nina.config.settings import HoverboardAxisSettings
from nina.controllers.hoverboard_axis_drive import (
    estimate_backward_pulse_series_duration_sec,
    estimate_forward_pulse_series_duration_sec,
)

if TYPE_CHECKING:
    from sirena_ui.workers.autonomy_controller import AutonomyController

from PyQt5.QtCore import Qt, QTimer
from PyQt5.QtGui import QImage, QPixmap
from PyQt5.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from sirena_ui.widgets.common import (
    Breadcrumb,
    Card,
    CardTitle,
    MutedLabel,
    Pill,
    SectionLabel,
)
from sirena_ui.widgets.dpad import DPad
from sirena_ui.workers.drive_controller import MAX_SPEED_PCT, MIN_SPEED_PCT
from sirena_ui.workers.nina_service import NinaService
from sirena_ui.workers.straight_bench_speed import straight_bench_speed_pct


log = logging.getLogger("sirena_ui.drive_screen")

# Keyboard map for held-while-pressed driving.
_KEY_TO_DIRECTION = {
    Qt.Key_W: "forward",
    Qt.Key_S: "back",
    Qt.Key_A: "left",
    Qt.Key_D: "right",
}

# Bench / field check: drive straight for NINA_STRAIGHT_TEST_MS, then stop. When unset and
# forward pulse is active, duration matches ``estimate_forward_pulse_series_duration_sec`` (+ slack);
# otherwise default 20 s.
# PWM: NINA_STRAIGHT_TEST_SPEED_PCT (8–100; use only where mechanically safe).
STRAIGHT_READY_POLL_MS = 50
STRAIGHT_READY_MAX_POLLS = 100

# Drive-only tuning (Jetson kiosk). Face inference is expensive — off unless enabled.
_DRIVE_CAMERA_LIVE = os.environ.get("NINA_DRIVE_CAMERA_LIVE", "1").strip().lower() not in (
    "0",
    "false",
    "no",
    "off",
)
_DRIVE_FACE_GREET = os.environ.get("NINA_DRIVE_FACE_GREET", "0").strip().lower() in (
    "1",
    "true",
    "yes",
    "on",
)
_DRIVE_CAM_MAX_W = max(160, int(os.environ.get("NINA_DRIVE_CAM_MAX_W", "480")))
_DRIVE_DEFER_HEAVY_MS = max(0, int(os.environ.get("NINA_DRIVE_DEFER_HEAVY_MS", "50")))
_STATE_RENDER_COALESCE_MS = max(16, int(os.environ.get("NINA_DRIVE_STATE_COALESCE_MS", "50")))


def _straight_test_speed_pct() -> int:
    return straight_bench_speed_pct()


def _straight_sequence_spec(
    axis: HoverboardAxisSettings, *, use_forward_pulse_bench: bool
) -> List[Tuple[str, int]]:
    """Single forward segment: (\"fwd\", duration_ms)."""
    raw = (os.environ.get("NINA_STRAIGHT_TEST_MS") or "").strip()
    if not raw:
        raw = (os.environ.get("NINA_STRAIGHT_SEQ_FWD1_MS") or "").strip()
    if raw:
        try:
            ms = int(raw)
        except ValueError:
            ms = 40_000
    elif use_forward_pulse_bench:
        est_sec = estimate_forward_pulse_series_duration_sec(axis)
        ms = min(120_000, max(100, int(math.ceil(est_sec * 1000.0)) + 200))
    else:
        ms = 40_000
    ms = max(100, min(120_000, ms))
    return [("fwd", ms)]


def _straight_back_sequence_spec(
    axis: HoverboardAxisSettings, *, use_backward_pulse_bench: bool
) -> List[Tuple[str, int]]:
    """Single backward segment duration (direction is set on the screen)."""
    raw = (os.environ.get("NINA_STRAIGHT_BACK_TEST_MS") or "").strip()
    if raw:
        try:
            ms = int(raw)
        except ValueError:
            ms = 40_000
    elif use_backward_pulse_bench:
        est_sec = estimate_backward_pulse_series_duration_sec(axis)
        ms = min(120_000, max(100, int(math.ceil(est_sec * 1000.0)) + 200))
    else:
        ms = 40_000
    ms = max(100, min(120_000, ms))
    return [("back", ms)]


class DriveScreen(QWidget):
    def __init__(self, service: NinaService, parent=None) -> None:
        super().__init__(parent)
        self._service = service
        self._drive = service.drive
        self._autonomy: Optional["AutonomyController"] = None
        self._drive_face_was_enabled = False
        self._pending_state: Optional[dict] = None
        self._state_coalesce_timer = QTimer(self)
        self._state_coalesce_timer.setSingleShot(True)
        self._state_coalesce_timer.setInterval(_STATE_RENDER_COALESCE_MS)
        self._state_coalesce_timer.timeout.connect(self._apply_pending_state)
        self._defer_heavy_timer = QTimer(self)
        self._defer_heavy_timer.setSingleShot(True)
        self._defer_heavy_timer.timeout.connect(self._deferred_on_enter_heavy)
        self._drive.state_changed.connect(self._queue_render_state)

        # Accept keyboard focus so WASD/Space/Esc reach keyPressEvent
        # even when the user hasn't clicked into a child widget.
        self.setFocusPolicy(Qt.StrongFocus)
        self._kb_active_key: Optional[int] = None
        self._cam_preview_last_ms: float = 0.0
        self._cam_preview_min_interval_ms = float(
            os.environ.get("NINA_DRIVE_CAM_PREVIEW_MS", "100")
        )
        self._cam_scaled_cache: Optional[QPixmap] = None
        self._cam_scaled_key: Optional[tuple[int, int, int]] = None

        self._straight_test_timer = QTimer(self)
        self._straight_test_timer.setSingleShot(True)
        self._straight_test_timer.timeout.connect(self._on_straight_sequence_timer)
        self._straight_pending = False
        self._straight_sequence_spec: List[Tuple[str, int]] = []
        self._straight_seq_index: int = -1
        self._straight_seq_fwd_dir: str = "forward"
        self._straight_run_backward = False
        self._imu_straight_started = False

        self._imu_poll_timer = QTimer(self)
        self._imu_poll_timer.setInterval(50)
        self._imu_poll_timer.timeout.connect(self._refresh_imu_hud)

        self._battery_hud_timer = QTimer(self)
        self._battery_hud_timer.setInterval(2000)
        self._battery_hud_timer.timeout.connect(self._refresh_battery_hud)

        # Live RGB feed wiring. The "Front camera" card on the left of
        # the Drive screen used to be a static placeholder; we now
        # subscribe to VisionWorker.frame_ready and stream pixmaps in.
        # The camera is acquired ONCE on first on_enter (via the
        # VisionWorker's refcount API) so navigating Drive -> Vision
        # -> Drive doesn't tear down the feed when the Vision screen's
        # on_leave releases its own reference.
        self._vision_acquired = False
        self._cam_feed_label: Optional[QLabel] = None
        self._cam_placeholder: Optional[QWidget] = None
        try:
            # frame_ready is connected only in on_enter so hidden screens
            # do not each duplicate 30 Hz QImage deliveries on the GUI thread.
            self._service.vision.status_changed.connect(self._on_camera_status)
        except Exception:
            # In headless / vision-disabled builds the worker may
            # still construct but never emit; not fatal for the Drive
            # screen, which can still drive without a camera feed.
            pass

        outer = QVBoxLayout(self)
        outer.setContentsMargins(6, 6, 6, 6)
        outer.setSpacing(6)

        top = QVBoxLayout()
        top.setSpacing(4)
        top_title = QHBoxLayout()
        top_title.setSpacing(6)
        top_title.addWidget(Breadcrumb("Nina", "Drive"))
        top_title.addStretch(1)
        top.addLayout(top_title)
        top_pills = QHBoxLayout()
        top_pills.setSpacing(6)
        top_pills.addStretch(1)
        self._auto_pill = Pill("Auto: OFF", Pill.KIND_NEUTRAL)
        top_pills.addWidget(self._auto_pill)
        self._conn_pill = Pill("BLDC …", Pill.KIND_NEUTRAL)
        top_pills.addWidget(self._conn_pill)
        top.addLayout(top_pills)
        outer.addLayout(top)

        body = QHBoxLayout()
        body.setSpacing(8)
        outer.addLayout(body, stretch=1)

        # Favor manual controls (~52%) so Turn/E-STOP are not clipped at 864 px.
        body.addWidget(self._build_camera_card(), stretch=48)
        _control = self._build_control_card()
        # Scroll so Manual controls (straight test + 90° turns) stay
        # reachable on 1024×600; the stack also caches this screen on
        # first open—restart the app after deploy to pick up UI changes.
        _ctrl_scroll = QScrollArea()
        _ctrl_scroll.setWidgetResizable(True)
        _ctrl_scroll.setFrameShape(QFrame.NoFrame)
        _ctrl_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        _ctrl_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        _control.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Minimum)
        _ctrl_scroll.setWidget(_control)
        _ctrl_scroll.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Expanding)
        body.addWidget(_ctrl_scroll, stretch=52)

        # Push initial state into the HUD / pills.
        self._queue_render_state(self._drive.state())

    def _autonomy_ctrl(self) -> "AutonomyController":
        """Lazy — do not construct AutonomyController/Slam until Drive needs it."""
        if self._autonomy is None:
            self._autonomy = self._service.autonomy
            self._autonomy.enabled_changed.connect(
                self._on_autonomy_enabled, type=Qt.UniqueConnection
            )
        return self._autonomy

    def _autonomy_is_enabled(self) -> bool:
        """Autonomous drive is disabled on kiosk until the pilot UI ships."""
        return False

    def _connect_vision_frame_preview(self) -> None:
        try:
            sig = self._service.vision.frame_ready
            try:
                sig.disconnect(self._on_camera_frame)
            except TypeError:
                pass
            sig.connect(self._on_camera_frame)
        except Exception:
            pass

    def _disconnect_vision_frame_preview(self) -> None:
        try:
            self._service.vision.frame_ready.disconnect(self._on_camera_frame)
        except TypeError:
            pass

    # ---------- camera card ----------

    def _build_camera_card(self) -> Card:
        card = Card(padding=10, spacing=6)

        header = QHBoxLayout()
        header.setSpacing(6)
        card.add_layout(header)
        header.addWidget(CardTitle("Front camera"))
        header.addStretch(1)
        self._cam_pill = Pill("Preview \u2014 camera not connected", Pill.KIND_NEUTRAL)
        header.addWidget(self._cam_pill)

        # The camera viewport now hosts the LIVE VisionWorker feed.
        # Layout pattern mirrors VisionScreen._build_camera_card so
        # the visual shape matches across screens: a placeholder
        # QWidget centered in the viewport (shown while no frame has
        # arrived) plus a hidden QLabel that gets pixmaps once
        # `frame_ready` fires.
        #
        # IMPORTANT: do NOT call setAlignment() on the viewport's
        # layout. With an alignment set, QBoxLayout hands children
        # their sizeHint instead of stretching them, and a QLabel's
        # sizeHint follows the pixmap. The pixmap is scaled to the
        # label size in _on_camera_frame, so an alignment-centered
        # label collapses to a "dot" on first paint.
        #
        # Min height: 200 px - we need to fit the HUD row underneath
        # in the 600-tall panel.
        viewport = QFrame()
        viewport.setObjectName("cardSubtle")
        viewport.setMinimumHeight(200)
        viewport.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        v = QVBoxLayout(viewport)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(0)

        placeholder = QWidget(viewport)
        placeholder.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        ph_layout = QVBoxLayout(placeholder)
        ph_layout.setContentsMargins(0, 0, 0, 0)
        ph_layout.setSpacing(8)
        ph_layout.addStretch(1)
        glyph = QLabel("\u25C9", placeholder)
        glyph.setStyleSheet(
            "color: #c4c4c8; font-size: 64px; background-color: transparent;"
        )
        glyph.setAlignment(Qt.AlignCenter)
        ph_layout.addWidget(glyph)
        msg = QLabel("USB camera not connected", placeholder)
        msg.setStyleSheet(
            "color: #8e8e93; font-size: 12px; background-color: transparent;"
        )
        msg.setAlignment(Qt.AlignCenter)
        ph_layout.addWidget(msg)
        ph_layout.addStretch(1)
        v.addWidget(placeholder, stretch=1)
        self._cam_placeholder = placeholder

        feed = QLabel(viewport)
        feed.setAlignment(Qt.AlignCenter)
        feed.setStyleSheet("background-color: transparent;")
        # No minimum width — a 320 px floor forced the camera column past the
        # 1024×600 panel and clipped the manual controls on the right.
        feed.setMinimumHeight(120)
        # Ignored size policy so the (potentially huge) pixmap can't
        # feed back into the layout and balloon or collapse the card.
        feed.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Ignored)
        feed.hide()
        v.addWidget(feed, stretch=1)
        self._cam_feed_label = feed

        card.add(viewport, stretch=1)

        # HUD row beneath the viewport with the live drive state.
        hud = QHBoxLayout()
        hud.setSpacing(8)
        card.add_layout(hud)
        self._hud_speed = self._make_hud("Speed", "0%")
        self._hud_heading = self._make_hud("Heading", "0\u00b0")
        self._hud_distance = self._make_hud("Distance", "0.0 m")
        self._hud_battery = self._make_hud("Battery", "n/a")
        self._hud_imu = self._make_hud("IMU drift", "\u2014")
        for w in (
            self._hud_speed,
            self._hud_heading,
            self._hud_distance,
            self._hud_battery,
            self._hud_imu,
        ):
            hud.addWidget(w, stretch=1)

        return card

    def _make_hud(self, label: str, value: str) -> Card:
        # Tight HUD tile - was padding=12, spacing=4. At 1024 x 600 we
        # need every px the camera viewport can borrow.
        box = Card(padding=6, spacing=2, subtle=True)
        box.add(SectionLabel(label))
        v = QLabel(value)
        v.setStyleSheet(
            "color: #1c1c1e; font-size: 14px; font-weight: 700;"
            " background-color: transparent;"
        )
        box.add(v)
        # Stash the value label on the card so we can update it later.
        box._value_label = v  # type: ignore[attr-defined]
        return box

    # ---------- control card ----------

    def _build_control_card(self) -> Card:
        # Tight stack for the 1024 x 600 panel: title row + autonomy +
        # D-pad + straight test + turn pivots + brake/reverse + ESTOP.
        card = Card(padding=10, spacing=6)

        # Title + autonomy toggle on the same row so the toggle isn't
        # full-width (it stretched and looked oversized on the panel).
        title_row = QHBoxLayout()
        title_row.setSpacing(6)
        card.add_layout(title_row)
        title_row.addWidget(CardTitle("Manual"))
        title_row.addStretch(1)
        self._autonomy_btn = QPushButton("Auto")
        self._autonomy_btn.setObjectName("primaryButton")
        self._autonomy_btn.setCursor(Qt.PointingHandCursor)
        self._autonomy_btn.setCheckable(False)
        self._autonomy_btn.setFocusPolicy(Qt.NoFocus)
        self._autonomy_btn.setMinimumHeight(34)
        self._autonomy_btn.setMaximumHeight(34)
        self._autonomy_btn.clicked.connect(self._on_autonomy_coming_soon)
        title_row.addWidget(self._autonomy_btn)

        # Banner removed - the "Auto: OFF/ON" pill above is enough
        # surface area to communicate the mode at this resolution. We
        # keep the attribute set to None so handlers that reach for it
        # don't need to be conditional everywhere.
        self._auto_banner = None  # type: ignore[assignment]

        self._manual_hint = QLabel("")
        self._manual_hint.setWordWrap(True)
        self._manual_hint.setStyleSheet(
            "color: #b45309; font-size: 12px; font-weight: 600;"
            " background-color: transparent; padding: 4px 0;"
        )
        self._manual_hint.hide()
        card.add(self._manual_hint)

        # D-pad sits in a horizontal row centered with stretches so it
        # doesn't pin to the left edge when the card is wider.
        dpad_row = QHBoxLayout()
        dpad_row.setContentsMargins(0, 0, 0, 0)
        dpad_row.addStretch(1)
        self._dpad = DPad()
        self._dpad.direction_pressed.connect(self._on_dpad_pressed)
        self._dpad.direction_released.connect(self._on_dpad_released)
        self._dpad.stop_clicked.connect(self._on_dpad_stop)
        dpad_row.addWidget(self._dpad)
        dpad_row.addStretch(1)
        card.add_layout(dpad_row)

        straight_row = QHBoxLayout()
        straight_row.setContentsMargins(0, 0, 0, 0)
        straight_row.setSpacing(6)
        self._straight_test_btn = QPushButton("Str. front")
        self._straight_test_btn.setObjectName("secondaryButton")
        self._straight_test_btn.setCursor(Qt.PointingHandCursor)
        self._straight_test_btn.setFocusPolicy(Qt.NoFocus)
        self._straight_test_btn.setMinimumHeight(32)
        self._straight_test_btn.setToolTip(
            "Straight front: runs for NINA_STRAIGHT_TEST_MS (legacy: NINA_STRAIGHT_SEQ_FWD1_MS), or if those "
            "are unset and hover forward pulse is on, for roughly one full pulse series plus margin. "
            "Otherwise default 40 s. Speed: NINA_STRAIGHT_TEST_SPEED_PCT. Respects Reverse. "
            "Space cancels; brake, E-STOP, autonomy, or leaving Drive stops the run. "
            "Turn off autonomous mode and release the brake first."
        )
        self._straight_test_btn.clicked.connect(self._on_straight_test_clicked)
        straight_row.addWidget(self._straight_test_btn, stretch=1)
        self._straight_back_test_btn = QPushButton("Str. back")
        self._straight_back_test_btn.setObjectName("secondaryButton")
        self._straight_back_test_btn.setCursor(Qt.PointingHandCursor)
        self._straight_back_test_btn.setFocusPolicy(Qt.NoFocus)
        self._straight_back_test_btn.setMinimumHeight(32)
        self._straight_back_test_btn.setToolTip(
            "Drives straight backward for NINA_STRAIGHT_BACK_TEST_MS (default 40 s) "
            "at NINA_STRAIGHT_TEST_SPEED_PCT, then stops. Ignores the Reverse toggle. "
            "Space cancels; brake, E-STOP, autonomy, or leaving Drive stops the run. "
            "Turn off autonomous mode and release the brake first."
        )
        self._straight_back_test_btn.clicked.connect(self._on_straight_back_clicked)
        straight_row.addWidget(self._straight_back_test_btn, stretch=1)
        card.add_layout(straight_row)

        # Row freed from per-wheel Flip L/R toggles: full width for timed pivots.
        turn_row = QHBoxLayout()
        turn_row.setContentsMargins(0, 0, 0, 0)
        turn_row.setSpacing(8)
        card.add_layout(turn_row)
        self._turn_90_left_btn = QPushButton("Turn left")
        self._turn_90_left_btn.setObjectName("secondaryButton")
        self._turn_90_left_btn.setCursor(Qt.PointingHandCursor)
        self._turn_90_left_btn.setFocusPolicy(Qt.NoFocus)
        self._turn_90_left_btn.setMinimumHeight(36)
        self._turn_90_left_btn.setToolTip(
            "Timed yaw (~0.45 s hold, ~15° lean: NINA_NAV_TURN_SEC / NINA_DRIVE_TURN_PIVOT_DEG): "
            "no straight-line prime—partial pivot blend from brake; "
            "left (e.g. ID 12) toward FWD, right (e.g. 13) toward REV. "
            "Held D-pad left repeats micro-steps (~15° / ~0.5 s each, 0.52 s pause) until release. "
            "NINA_HOVER_SWAP_TURN_LR / TURN_PUSH_TICKS still apply."
        )
        self._turn_90_left_btn.clicked.connect(lambda: self._on_turn_90_clicked("left"))
        turn_row.addWidget(self._turn_90_left_btn, stretch=1)
        self._turn_90_right_btn = QPushButton("Turn right")
        self._turn_90_right_btn.setObjectName("secondaryButton")
        self._turn_90_right_btn.setCursor(Qt.PointingHandCursor)
        self._turn_90_right_btn.setFocusPolicy(Qt.NoFocus)
        self._turn_90_right_btn.setMinimumHeight(36)
        self._turn_90_right_btn.setToolTip(
            "Timed yaw (~0.45 s hold, ~15° lean: NINA_NAV_TURN_SEC / NINA_DRIVE_TURN_PIVOT_DEG): "
            "no straight-line prime—partial pivot blend; right toward FWD, left toward REV. "
            "Held D-pad right repeats micro-steps (~15° / ~0.5 s each, 0.52 s pause) until release. "
            "NINA_HOVER_SWAP_TURN_LR / TURN_PUSH_TICKS still apply."
        )
        self._turn_90_right_btn.clicked.connect(lambda: self._on_turn_90_clicked("right"))
        turn_row.addWidget(self._turn_90_right_btn, stretch=1)

        # Brake / Reverse on the SAME row as ESTOP so the bottom of the
        # card collapses from three rows into one.
        bottom_row = QHBoxLayout()
        bottom_row.setSpacing(6)
        card.add_layout(bottom_row)
        self._brake_btn = QPushButton("Brake: ON")
        self._brake_btn.setObjectName("togglePill")
        self._brake_btn.setCheckable(True)
        self._brake_btn.setChecked(True)
        self._brake_btn.setFocusPolicy(Qt.NoFocus)
        self._brake_btn.setMinimumHeight(34)
        self._brake_btn.setMaximumHeight(34)
        self._brake_btn.setToolTip(
            "When ON, the D-pad is disabled and WASD does not drive. "
            "Tap to OFF when you are ready to move (Straight front/Turn show a reminder if still ON)."
        )
        self._brake_btn.clicked.connect(self._on_brake_toggle)
        bottom_row.addWidget(self._brake_btn)

        self._reverse_btn = QPushButton("Reverse: OFF")
        self._reverse_btn.setObjectName("togglePill")
        self._reverse_btn.setCheckable(True)
        self._reverse_btn.setFocusPolicy(Qt.NoFocus)
        self._reverse_btn.setMinimumHeight(34)
        self._reverse_btn.setMaximumHeight(34)
        self._reverse_btn.clicked.connect(self._on_reverse_toggle)
        bottom_row.addWidget(self._reverse_btn)
        bottom_row.addStretch(1)

        # Big red panic button - shrunk from the default `stopButton`
        # styling (16/24 padding, 18 px font) which was ~50 px tall and
        # pushed the kb hint off-screen on the 10.1" panel.
        self._estop_btn = QPushButton("\u26A0  E-STOP")
        self._estop_btn.setObjectName("stopButton")
        self._estop_btn.setCursor(Qt.PointingHandCursor)
        self._estop_btn.setFocusPolicy(Qt.NoFocus)
        self._estop_btn.setMinimumHeight(34)
        self._estop_btn.setMaximumHeight(34)
        self._estop_btn.setStyleSheet(
            "QPushButton#stopButton {"
            "  padding: 4px 12px; font-size: 14px; border-radius: 17px;"
            "}"
        )
        self._estop_btn.clicked.connect(self._on_emergency_stop)
        bottom_row.addWidget(self._estop_btn)

        # Keyboard hint as a single small line right above the bottom.
        kb_hint = QLabel(
            "WASD drive \u00b7 Space stop \u00b7 Esc E-STOP"
        )
        kb_hint.setStyleSheet(
            "color: #8e8e93; font-size: 11px; background-color: transparent;"
        )
        kb_hint.setAlignment(Qt.AlignCenter)
        card.add(kb_hint)

        return card

    # ---------- handlers ----------

    def _on_brake_toggle(self, checked: bool) -> None:
        if checked and self._straight_test_timer.isActive():
            self._finish_straight_test()
        self._brake_btn.setText(f"Brake: {'ON' if checked else 'OFF'}")
        self._drive.set_brake(checked)
        self.setFocus()

    def _on_reverse_toggle(self, checked: bool) -> None:
        self._reverse_btn.setText(f"Reverse: {'ON' if checked else 'OFF'}")
        self._drive.set_reverse(checked)
        self.setFocus()

    def _on_emergency_stop(self) -> None:
        self._drive.emergency_stop()
        # Sync the Brake toggle so the screen reflects the new state
        # immediately (the controller already engaged the brake on the
        # hardware; this just makes the UI agree).
        self._brake_btn.blockSignals(True)
        self._brake_btn.setChecked(True)
        self._brake_btn.setText("Brake: ON")
        self._brake_btn.blockSignals(False)
        self._restore_after_straight_test()
        # Return focus to the screen so a follow-up Esc / Space still
        # reaches our key handlers instead of the EMERGENCY STOP button.
        self.setFocus()

    def _begin_straight_bench_run(self, *, backward: bool) -> None:
        if self._straight_test_timer.isActive() or self._straight_pending:
            return
        if self._autonomy_ctrl().is_enabled():
            QMessageBox.warning(
                self,
                "Autonomous mode",
                "Turn off autonomous mode before running a manual straight test.",
            )
            return
        if self._brake_btn.isChecked():
            QMessageBox.information(
                self,
                "Brake engaged",
                "Release the brake (Brake: OFF) before running Straight front or Straight back.",
            )
            return
        self._drive.ensure_hardware()
        self._straight_run_backward = backward
        self._imu_straight_started = False
        self._straight_test_btn.setEnabled(False)
        self._straight_back_test_btn.setEnabled(False)
        self._dpad.set_enabled(False)
        self._turn_90_left_btn.setEnabled(False)
        self._turn_90_right_btn.setEnabled(False)
        self._straight_pending = True
        self._straight_ready_polls = 0
        self._render_state(self._drive.state())
        QTimer.singleShot(STRAIGHT_READY_POLL_MS, self._try_straight_when_ready)
        self.setFocus()

    def _on_straight_test_clicked(self) -> None:
        self._begin_straight_bench_run(backward=False)

    def _on_straight_back_clicked(self) -> None:
        self._begin_straight_bench_run(backward=True)

    def _try_straight_when_ready(self) -> None:
        """Start straight test only after BLDC init finishes (async worker)."""
        if not self._straight_pending:
            return
        self._straight_ready_polls += 1
        self._drive.ensure_hardware()
        if not self._drive.state().get("connected"):
            if self._straight_ready_polls >= STRAIGHT_READY_MAX_POLLS:
                self._straight_pending = False
                QMessageBox.warning(
                    self,
                    "Drive not ready",
                    "Drive hardware did not initialize in time. On the robot, "
                    "confirm the Dynamixel bus (USB cable, NINA_DXL_PORT / baud, "
                    "IDs 12 and 13), and wait for the status pill to show the "
                    "hoverboard driver connected. Then try Straight front or Straight back again.",
                )
                self._restore_after_straight_test()
                return
            QTimer.singleShot(STRAIGHT_READY_POLL_MS, self._try_straight_when_ready)
            return
        self._straight_pending = False
        if self._straight_run_backward:
            self._straight_seq_fwd_dir = "back"
            self._straight_sequence_spec = _straight_back_sequence_spec(
                self._service.settings.hoverboard_axis,
                use_backward_pulse_bench=self._drive.supports_forward_pulse(),
            )
        else:
            self._straight_seq_fwd_dir = (
                "back" if self._drive.state().get("reverse") else "forward"
            )
            bench_pulse = (
                self._straight_seq_fwd_dir == "forward"
                and self._drive.supports_forward_pulse()
            )
            self._straight_sequence_spec = _straight_sequence_spec(
                self._service.settings.hoverboard_axis,
                use_forward_pulse_bench=bench_pulse,
            )
        self._straight_seq_index = -1
        self._apply_straight_sequence_segment(0)
        self.setFocus()
        self._render_state(self._drive.state())

    def _apply_straight_sequence_segment(self, index: int) -> None:
        spec = self._straight_sequence_spec
        n = len(spec)
        while index < n and spec[index][1] <= 0:
            index += 1
        if index >= n:
            self._finish_straight_test()
            return
        _, ms = spec[index]
        pct_fwd = _straight_test_speed_pct()
        d = self._straight_seq_fwd_dir
        self._straight_seq_index = index
        if not self._imu_straight_started:
            self._service.imu_straight_begin()
            if not self._imu_poll_timer.isActive():
                self._imu_poll_timer.start()
            self._imu_straight_started = True
        self._straight_test_timer.start(ms)
        if d == "forward" and self._drive.supports_forward_pulse():
            self._drive.start_forward_pulse_bench(pct_fwd)
        elif d == "back" and self._drive.supports_forward_pulse():
            self._drive.start_backward_pulse_bench(pct_fwd)
        else:
            self._drive.drive_wheels(d, pct_fwd, d, pct_fwd)

    def _refresh_battery_hud(self) -> None:
        try:
            lbl = self._hud_battery._value_label  # type: ignore[attr-defined]
        except Exception:
            return
        text = self._service.battery_pack_voltage_display()
        lbl.setText(text if text and text != "\u2014" else "n/a")

    def _set_imu_hud_idle(self) -> None:
        try:
            self._hud_imu._value_label.setText("\u2014")  # type: ignore[attr-defined]
        except Exception:
            pass

    def _refresh_imu_hud(self) -> None:
        if not self._straight_test_timer.isActive():
            self._imu_poll_timer.stop()
            self._set_imu_hud_idle()
            return
        try:
            lbl = self._hud_imu._value_label  # type: ignore[attr-defined]
        except Exception:
            return
        mon = self._service.imu_monitor
        if mon is None:
            lbl.setText("IMU off")
            return
        s = mon.snapshot()
        if not s.ok:
            msg = (s.message or "\u2026").replace("\n", " ")
            if len(msg) > 22:
                msg = msg[:19] + "\u2026"
            lbl.setText(msg)
            return
        if s.drift_side == "n/a":
            lbl.setText("\u2014")
            return
        if s.drift_side == "left":
            sym = "\u2190"
        elif s.drift_side == "right":
            sym = "\u2192"
        else:
            sym = "\u00b7"
        lbl.setText(f"{s.yaw_drift_deg:+.1f}\u00b0 {sym}")

    def _on_straight_sequence_timer(self) -> None:
        self._straight_test_timer.stop()
        try:
            self._drive.stop(drain=True)
        except Exception:
            try:
                self._drive.stop()
            except Exception:
                pass
        next_i = self._straight_seq_index + 1
        if next_i >= len(self._straight_sequence_spec):
            self._restore_after_straight_test()
            self.setFocus()
            return
        self._apply_straight_sequence_segment(next_i)

    def _restore_after_straight_test(self) -> None:
        self._straight_test_timer.stop()
        self._imu_poll_timer.stop()
        self._service.imu_straight_end()
        self._imu_straight_started = False
        self._set_imu_hud_idle()
        self._straight_sequence_spec = []
        self._straight_seq_index = -1
        self._straight_pending = False
        self._straight_run_backward = False
        if not self._autonomy_ctrl().is_enabled():
            self._straight_test_btn.setEnabled(True)
            self._straight_back_test_btn.setEnabled(True)
            st = self._drive.state()
            self._dpad.set_enabled(not st["brake"])
            self._turn_90_left_btn.setEnabled(True)
            self._turn_90_right_btn.setEnabled(True)
        else:
            self._straight_test_btn.setEnabled(False)
            self._straight_back_test_btn.setEnabled(False)
            self._turn_90_left_btn.setEnabled(False)
            self._turn_90_right_btn.setEnabled(False)
        self._render_state(self._drive.state())

    def _finish_straight_test(self) -> None:
        try:
            self._drive.stop(drain=True)
        except Exception:
            try:
                self._drive.stop()
            except Exception:
                pass
        self._restore_after_straight_test()
        self.setFocus()

    def _on_dpad_pressed(self, direction: str) -> None:
        self._dpad.set_exclusive_direction(direction)
        self._drive.drive(direction)

    def _on_dpad_released(self, _direction: str) -> None:
        self._dpad.set_exclusive_direction(None)
        self._drive.stop()

    def _on_dpad_stop(self) -> None:
        self._dpad.set_exclusive_direction(None)
        self._drive.stop()

    def _on_turn_90_clicked(self, which: str) -> None:
        if self._autonomy_ctrl().is_enabled():
            QMessageBox.warning(
                self,
                "Autonomous mode",
                "Turn off autonomous mode before using manual 90° turns.",
            )
            return
        if self._brake_btn.isChecked():
            QMessageBox.information(
                self,
                "Brake engaged",
                "Release the brake (Brake: OFF) before running a 90° turn.",
            )
            return
        self._drive.ensure_hardware()
        self._drive.turn_90(which)
        self.setFocus()

    def _on_autonomy_coming_soon(self) -> None:
        QMessageBox.information(
            self,
            "Autonomous mode",
            "Autonomous mode is coming soon. Use manual drive and the D-pad for now.",
        )

    def _on_autonomy_enabled(self, on: bool) -> None:
        if on:
            try:
                self._autonomy_ctrl().set_enabled(False)
            except Exception:
                pass
            on = False
        if on and self._straight_test_timer.isActive():
            try:
                self._drive.stop(drain=True)
            except Exception:
                try:
                    self._drive.stop()
                except Exception:
                    pass
            self._restore_after_straight_test()
        self._autonomy_btn.setText("Auto")
        self._auto_pill.setText("Auto: soon")
        self._auto_pill.set_kind(Pill.KIND_NEUTRAL)

        st = self._drive.state()
        self._dpad.set_enabled(not st["brake"])
        self._brake_btn.setEnabled(True)
        self._reverse_btn.setEnabled(True)
        self._straight_test_btn.setEnabled(True)
        self._straight_back_test_btn.setEnabled(True)
        self._turn_90_left_btn.setEnabled(True)
        self._turn_90_right_btn.setEnabled(True)
        # _auto_banner was removed in the 1024 x 600 refit; nothing to
        # update here. The title-row pill conveys the same state.

    def on_enter(self) -> None:
        """Refresh drive handle and defer BLDC/camera work so the screen paints first."""
        self._drive = self._service.drive
        self._queue_render_state(self._drive.state())
        self._service.start_battery_ads1115_monitor()
        self._refresh_battery_hud()
        if not self._battery_hud_timer.isActive():
            self._battery_hud_timer.start()
        self.setFocus()
        if self._defer_heavy_timer.isActive():
            self._defer_heavy_timer.stop()
        self._defer_heavy_timer.start(_DRIVE_DEFER_HEAVY_MS)

    def _deferred_on_enter_heavy(self) -> None:
        if not self.isVisible():
            return
        self._drive.ensure_hardware()
        self._on_autonomy_enabled(self._autonomy_ctrl().is_enabled())
        try:
            self._service.start_battery_ads1115_monitor()
            self._service.start_mpu9250_imu_monitor()
        except Exception:
            pass
        if not _DRIVE_CAMERA_LIVE:
            return
        if not self._vision_acquired:
            try:
                self._service.vision.acquire()
                self._vision_acquired = True
            except Exception:
                pass
        vision = self._service.vision
        if _DRIVE_FACE_GREET:
            try:
                vision.set_face_enabled(True)
                self._drive_face_was_enabled = True
            except Exception:
                pass
            self._service.reset_face_greet_cooldown()
        else:
            try:
                vision.set_face_enabled(False)
            except Exception:
                pass
        self._connect_vision_frame_preview()

    def on_leave(self) -> None:
        if self._defer_heavy_timer.isActive():
            self._defer_heavy_timer.stop()
        self._battery_hud_timer.stop()
        self._imu_poll_timer.stop()
        self._service.imu_straight_end()
        self._state_coalesce_timer.stop()
        self._pending_state = None
        self._disconnect_vision_frame_preview()
        if self._drive_face_was_enabled:
            try:
                self._service.vision.set_face_enabled(False)
            except Exception:
                pass
            self._drive_face_was_enabled = False
        if self._straight_test_timer.isActive():
            self._finish_straight_test()
        elif self._straight_pending:
            self._restore_after_straight_test()
        if self._vision_acquired:
            try:
                self._service.vision.release()
            except Exception:
                pass
            self._vision_acquired = False

    def _on_camera_frame(self, image: QImage) -> None:
        """Render an incoming RGB frame into the Front-camera card."""
        now_ms = time.monotonic() * 1000.0
        if now_ms - self._cam_preview_last_ms < self._cam_preview_min_interval_ms:
            return
        self._cam_preview_last_ms = now_ms
        if not self.isVisible():
            return
        feed = self._cam_feed_label
        if feed is None:
            return
        if self._cam_placeholder is not None and self._cam_placeholder.isVisible():
            self._cam_placeholder.hide()
            feed.show()
        if image.width() > _DRIVE_CAM_MAX_W:
            image = image.scaledToWidth(_DRIVE_CAM_MAX_W, Qt.FastTransformation)
        target = feed.size()
        tw, th = target.width(), target.height()
        if tw <= 0 or th <= 0:
            feed.setPixmap(QPixmap.fromImage(image))
            return
        cache_key = (tw, th, image.width(), image.height(), image.cacheKey())
        if (
            self._cam_scaled_cache is not None
            and not self._cam_scaled_cache.isNull()
            and cache_key == self._cam_scaled_key
        ):
            feed.setPixmap(self._cam_scaled_cache)
            return
        pix = QPixmap.fromImage(image).scaled(
            target,
            Qt.KeepAspectRatio,
            Qt.FastTransformation,
        )
        self._cam_scaled_key = cache_key
        self._cam_scaled_cache = pix
        feed.setPixmap(pix)

    def _on_camera_status(self, status: dict) -> None:
        """Update the camera pill in the Front-camera card header.

        Mirrors VisionScreen's pill-state policy so the operator sees
        the same "USB camera connected / not connected / Detector
        unavailable" labels regardless of which screen they're on.
        """
        camera_open = bool(status.get("camera_open", False))
        message = str(status.get("message", "") or "")
        if camera_open:
            self._cam_pill.setText("Live")
            self._cam_pill.set_kind(Pill.KIND_OK)
            self._cam_pill.setToolTip("")
        else:
            label = message or "USB camera not connected"
            self._cam_pill.setText(label)
            self._cam_pill.set_kind(Pill.KIND_NEUTRAL)
            self._cam_pill.setToolTip("")
            # Drop back to the placeholder if the camera went away.
            if self._cam_feed_label is not None:
                self._cam_feed_label.clear()
                self._cam_feed_label.hide()
            if self._cam_placeholder is not None:
                self._cam_placeholder.show()

    def keyPressEvent(self, event) -> None:
        # Filter X11 auto-repeat: a held key would otherwise look like
        # press / release / press / release at ~30 Hz and thrash the
        # worker queue with duplicate drive commands.
        if event.isAutoRepeat():
            return
        if self._autonomy_is_enabled():
            super().keyPressEvent(event)
            return

        if self._straight_test_timer.isActive():
            key = event.key()
            if key == Qt.Key_Space:
                self._finish_straight_test()
                event.accept()
                return
            if key == Qt.Key_Escape:
                self._on_emergency_stop()
                event.accept()
                return
            if key in _KEY_TO_DIRECTION:
                event.accept()
                return
            super().keyPressEvent(event)
            return

        key = event.key()
        if key in _KEY_TO_DIRECTION:
            if self._brake_btn.isChecked():
                self._manual_hint.setText(
                    "Brake is ON — tap Brake: OFF on the right, then use WASD or the D-pad."
                )
                self._manual_hint.show()
                event.accept()
                return
            st = self._drive.state()
            if not st.get("connected"):
                self._manual_hint.setText(
                    "Motors not connected — wait for the green BLDC pill before WASD."
                )
                self._manual_hint.show()
                event.accept()
                return
            # Only allow one direction key at a time. Pressing a second
            # while one is already held is ignored - swapping mid-drive
            # is rough on a BLDC and rough on the operator's nerves.
            if self._kb_active_key is not None and self._kb_active_key != key:
                event.accept()
                return
            self._kb_active_key = key
            direction = _KEY_TO_DIRECTION[key]
            self._dpad.set_exclusive_direction(direction)
            self._drive.drive(direction)
            event.accept()
            return
        if key == Qt.Key_Space:
            self._dpad.set_exclusive_direction(None)
            self._drive.stop()
            event.accept()
            return
        if key == Qt.Key_Escape:
            self._on_emergency_stop()
            event.accept()
            return
        super().keyPressEvent(event)

    def keyReleaseEvent(self, event) -> None:
        if event.isAutoRepeat():
            return
        key = event.key()
        if key in _KEY_TO_DIRECTION and key == self._kb_active_key:
            self._kb_active_key = None
            self._dpad.set_exclusive_direction(None)
            self._drive.stop()
            event.accept()
            return
        super().keyReleaseEvent(event)

    def _queue_render_state(self, state: dict) -> None:
        self._pending_state = state
        if not self._state_coalesce_timer.isActive():
            self._state_coalesce_timer.start()

    def _apply_pending_state(self) -> None:
        state = self._pending_state
        if state is not None:
            self._render_state(state)

    def _render_state(self, state: dict) -> None:
        # Keep the brake pill aligned with DriveController (autonomy may toggle brake in software).
        br = bool(state.get("brake", True))
        self._brake_btn.blockSignals(True)
        self._brake_btn.setChecked(br)
        self._brake_btn.setText(f"Brake: {'ON' if br else 'OFF'}")
        self._brake_btn.blockSignals(False)

        self._hud_speed._value_label.setText(f"{state['speed_pct']}%")
        self._hud_heading._value_label.setText(f"{state['heading_deg']}\u00b0")
        self._hud_distance._value_label.setText(f"{state['distance_m']:.1f} m")

        dm = str(state.get("driver_message") or "").strip()
        if self._autonomy_is_enabled():
            self._manual_hint.hide()
        elif not state["connected"]:
            if self._straight_pending:
                self._manual_hint.setText(
                    "Connecting to motor drivers — keep this screen open "
                    "(Straight front / Straight back will start when ready or show an error)."
                )
            elif dm:
                self._manual_hint.setText(
                    "Motors did not come up — read the BLDC pill (hover for full text if truncated). "
                    "Typical fixes: jetson-io PWM on BCM 12+13, and matching "
                    "NINA_NAV_* pin env vars."
                )
            else:
                self._manual_hint.setText(
                    "Motors not ready — wait for the green BLDC pill. "
                    "Check Jetson-IO PWM and wiring."
                )
            self._manual_hint.show()
        elif state["brake"]:
            self._manual_hint.setText(
                "Brake is ON — tap Brake: OFF to use the D-pad. "
                "Straight front / Straight back / Turn will pop a reminder if you try while braked."
            )
            self._manual_hint.show()
        else:
            self._manual_hint.hide()

        # Autonomy lock takes priority over the brake-lock for D-pad
        # enablement: while autonomy is on, the D-pad stays disabled
        # regardless of the manual brake state. Brake ON also disables
        # the D-pad (release brake first); Straight front / Straight back / Turn stay enabled
        # so their handlers can show an explicit dialog instead of dead clicks.
        if not self._autonomy_is_enabled():
            if self._straight_test_timer.isActive() or self._straight_seq_index >= 0:
                self._dpad.set_enabled(False)
                self._turn_90_left_btn.setEnabled(False)
                self._turn_90_right_btn.setEnabled(False)
            else:
                st = self._drive.state()
                self._dpad.set_enabled(not st["brake"])
                self._turn_90_left_btn.setEnabled(True)
                self._turn_90_right_btn.setEnabled(True)

        message_raw = str(state.get("driver_message") or "").strip()
        display_msg = message_raw.replace("\n", " ")
        if len(display_msg) > 96:
            display_msg = display_msg[:93] + "..."

        def _pill_line(text: str, *, limit: int = 26) -> str:
            t = (text or "").replace("\n", " ").strip()
            if len(t) <= limit:
                return t or "BLDC"
            return t[: limit - 1] + "\u2026"

        conn_key = (bool(state["connected"]), message_raw)
        if conn_key != getattr(self, "_conn_pill_key", None):
            self._conn_pill_key = conn_key
            if state["connected"]:
                self._conn_pill.setToolTip(
                    "Software ready: navigation backend initialised (Jetson GPIO/PWM or bridge).\n"
                    "This does not prove the hubs spin — still need motor supply, EL/DIR/VR wiring, "
                    "and Brake OFF before D-pad / Straight front sends torque.\n\n"
                    "Same stack as the GUI, from the repo root:\n"
                    "  PYTHONPATH=. python3 -m nina.app.main nav-bridge-ping\n"
                    "  PYTHONPATH=. python3 -m nina.app.main nav-forward --speed 20 --hold 2\n"
                    "Local wiring twitch (multimeter / scope on PWM VR):\n"
                    "  PYTHONPATH=. python3 -m nina.app.main nav-test-pin --pin 12 --mode pwm "
                    "--duty 15 --hold 3"
                    "\n(use your configured left-PWM BCM from NINA_NAV_* if not 12)"
                )
            elif message_raw:
                self._conn_pill.setToolTip(message_raw)
            else:
                self._conn_pill.setToolTip("")
        if state["connected"]:
            self._conn_pill.setText(_pill_line(message_raw or "BLDC OK"))
            self._conn_pill.set_kind(Pill.KIND_OK)
        elif message_raw:
            self._conn_pill.setText(_pill_line(display_msg or "BLDC err"))
            self._conn_pill.set_kind(Pill.KIND_WARN)
        else:
            self._conn_pill.setText("BLDC off")
            self._conn_pill.set_kind(Pill.KIND_NEUTRAL)
