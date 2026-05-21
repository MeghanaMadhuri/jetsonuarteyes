"""Home / Dashboard screen.

Shows a hero card with Nina's photo, a quick-action tile grid that
deep-links into the major sub-screens, and a small "live status"
card. Designed to be the first thing a user sees when the app
launches on the 10.1" Jetson display.
"""

from __future__ import annotations

import sys
from typing import Dict, List, Optional, Tuple

from PyQt5.QtCore import QProcess, Qt, QThread, QTimer, pyqtSignal
from PyQt5.QtGui import QPixmap
from PyQt5.QtWidgets import (
    QApplication,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from sirena_ui.styles import asset_path
from sirena_ui.widgets.common import Breadcrumb, Card, CardTitle, MutedLabel, Pill, SectionLabel
from nina.sensors.ads1115 import overview_pill_for_battery
from sirena_ui.workers.health_collector import collect
from sirena_ui.workers.nina_service import NinaService


# (key, label, glyph, blurb)
# Keys that contain a ":" are deep links of the form "screen:subtab".
# They are routed by `MainWindow.navigate` to the right screen and
# then to the right inner tab via that screen's `set_subtab(name)`.
QUICK_ACTIONS: List[Tuple[str, str, str, str]] = [
    ("actions:playback", "Play action", "\u25B6", "Run a saved motion"),
    ("actions:record", "Record", "\u25CF", "Capture a new pose"),
    ("actions:audio", "Audio", "\u266B", "Voice clips"),
    ("drive", "Drive", "\u2B95", "Manual control"),
    ("vision", "Vision", "\u25CE", "Camera & faces"),
    ("map", "Map", "\u25A6", "SLAM & dock"),
    ("health", "Health", "\u2665", "System checks"),
    ("settings", "Settings", "\u2699", "Configure"),
]


class _HealthCollectThread(QThread):
    """Run ``collect()`` off the GUI thread (Dynamixel health check is slow)."""

    finished_ok = pyqtSignal(list)

    def __init__(self, service: NinaService) -> None:
        super().__init__()
        self._service = service

    def run(self) -> None:
        try:
            self.finished_ok.emit(collect(self._service))
        except Exception:
            self.finished_ok.emit([])


class _QuickTile(QPushButton):
    def __init__(self, glyph: str, label: str, blurb: str, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("card")
        self.setFlat(True)
        self.setCursor(Qt.PointingHandCursor)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        # 80 (was 110) so the 2-row tile grid fits beside the hero on
        # a 600-tall panel after chrome (~70) and outer margins (~20).
        # Still well above the 44 px touch-target minimum.
        self.setMinimumHeight(80)
        self.setStyleSheet(
            """
            QPushButton#card {
                background-color: white;
                border: 1px solid #e3e3e6;
                border-radius: 12px;
                text-align: left;
            }
            QPushButton#card:hover, QPushButton#card:pressed {
                border-color: #c8102e;
                background-color: #fbe7eb;
            }
            """
        )
        v = QVBoxLayout(self)
        v.setContentsMargins(12, 8, 12, 8)
        v.setSpacing(2)
        glyph_label = QLabel(glyph)
        glyph_label.setStyleSheet(
            "color: #c8102e; font-size: 18px; background-color: transparent;"
        )
        v.addWidget(glyph_label)
        title = QLabel(label)
        title.setStyleSheet(
            "color: #1c1c1e; font-size: 14px; font-weight: 700;"
            " background-color: transparent;"
        )
        v.addWidget(title)
        sub = QLabel(blurb)
        sub.setStyleSheet(
            "color: #6e6e73; font-size: 11px; background-color: transparent;"
        )
        v.addWidget(sub)


def _pill_kind_for_health_status(status: str) -> str:
    s = (status or "").strip().lower()
    if s in ("ok", "ready"):
        return Pill.KIND_OK
    if s == "warn":
        return Pill.KIND_WARN
    if s == "error":
        return Pill.KIND_ERROR
    return Pill.KIND_NEUTRAL


def _health_row_by_key(rows: list, key: str):
    for r in rows:
        if getattr(r, "key", None) == key:
            return r
    return None


def _overview_pill_caption_kind(row: object) -> Tuple[str, str]:
    """Short value + pill objectName for System overview tiles (Health row semantics)."""
    if row is None:
        return ("—", Pill.KIND_NEUTRAL)
    st = (getattr(row, "status", None) or "").strip().lower() or "pending"
    kind = _pill_kind_for_health_status(st)
    detail = (getattr(row, "detail", None) or "").strip()
    key = getattr(row, "key", "") or ""
    if key == "wifi" and detail:
        low = detail.lower()
        if "offline" in low:
            return ("Offline", Pill.KIND_NEUTRAL)
        if "connect" in low:
            return ("Online", Pill.KIND_OK)
    if key == "ir":
        low = detail.lower()
        if "blocked" in low:
            return ("Blocked", Pill.KIND_ERROR)
        if "clear" in low:
            return ("Clear", Pill.KIND_OK)
        if "ready" in low:
            return ("Ready", kind)
    if detail:
        return (detail[:22], kind)
    return ("—", kind)


class HomeScreen(QWidget):
    navigate_requested = pyqtSignal(str)

    def __init__(self, service: NinaService, parent=None) -> None:
        super().__init__(parent)
        self._service = service
        self._git_pull_proc: Optional[QProcess] = None
        self._pull_changes_btn: Optional[QPushButton] = None
        self._pill_bus: Optional[Pill] = None
        self._pill_torque: Optional[Pill] = None
        self._pill_voice: Optional[Pill] = None
        self._ov_pills: Dict[str, Pill] = {}

        outer = QVBoxLayout(self)
        outer.setContentsMargins(10, 10, 10, 10)
        outer.setSpacing(8)

        outer.addWidget(Breadcrumb("Nina", "Home"))

        outer.addWidget(self._build_hero(), stretch=0)

        section = SectionLabel("Quick actions")
        outer.addWidget(section)
        outer.addLayout(self._build_tiles(), stretch=1)

        outer.addWidget(self._build_status_strip(), stretch=0)

        self._hero_pill_timer = QTimer(self)
        self._hero_pill_timer.setInterval(5000)
        self._hero_pill_timer.timeout.connect(self._refresh_hero_pills_async)
        self._battery_ov_timer = QTimer(self)
        self._battery_ov_timer.setInterval(2000)
        self._battery_ov_timer.timeout.connect(self.refresh_battery_pill)
        self._health_collect_thread: Optional[_HealthCollectThread] = None
        self._wire_drive_hero_pills()

    def _wire_drive_hero_pills(self) -> None:
        """Start BLDC init and subscribe so the torque chip updates without opening Drive."""
        try:
            dc = self._service.drive
            dc.state_changed.connect(self._on_drive_state_changed, type=Qt.UniqueConnection)
            dc.ensure_hardware()
        except Exception:
            pass

    def _on_drive_state_changed(self, st: object) -> None:
        if isinstance(st, dict):
            self._apply_torque_pill_from_state(st)

    def _apply_torque_pill_from_state(self, st: dict) -> None:
        pt = self._pill_torque
        if pt is None:
            return
        connected = bool(st.get("connected"))
        msg = str(st.get("driver_message", "") or "").strip()
        if connected:
            pt.setText("Torque ON")
            pt.set_kind(Pill.KIND_OK)
            pt.setToolTip(msg or "BLDC L+R connected")
            return
        low = msg.lower()
        if not msg or any(
            x in low for x in ("initialis", "initializ", "waiting", "not yet", "queued")
        ):
            pt.setText("Drive …")
            pt.set_kind(Pill.KIND_NEUTRAL)
            pt.setToolTip(msg or "Starting drive hardware")
            return
        if "simulation" in low:
            pt.setText("Simulation")
            pt.set_kind(Pill.KIND_WARN)
            pt.setToolTip(msg)
            return
        pt.setText("Torque off")
        pt.set_kind(Pill.KIND_WARN)
        pt.setToolTip(msg)

    # ---------- hero ----------

    def _build_hero(self) -> Card:
        # Was padding=24 spacing=16 with a 180-tall image. On a 600 px
        # panel that hero alone ate ~220 px of the ~510 px content area
        # and pushed the tile grid + status strip off-screen. Trimmed
        # everything by ~40%.
        card = Card(padding=12, spacing=8, hero=True)
        h = QHBoxLayout()
        h.setSpacing(12)
        card.add_layout(h)

        # Nina image, scaled to a comfortable hero size
        image = QLabel()
        image.setAlignment(Qt.AlignCenter)
        image.setStyleSheet("background-color: transparent;")
        pix = QPixmap(asset_path("nina.png"))
        if not pix.isNull():
            image.setPixmap(pix.scaledToHeight(110, Qt.SmoothTransformation))
        image.setFixedWidth(140)
        h.addWidget(image)

        text = QVBoxLayout()
        text.setSpacing(4)
        h.addLayout(text, stretch=1)

        hello = QLabel("Hi, I'm Nina.")
        hello.setStyleSheet(
            "color: #1c1c1e; font-size: 20px; font-weight: 700;"
            " background-color: transparent;"
        )
        text.addWidget(hello)

        sub = QLabel("Sirena Robotics \u00b7 ready when you are.")
        sub.setStyleSheet(
            "color: #6e6e73; font-size: 12px; background-color: transparent;"
        )
        text.addWidget(sub)

        text.addSpacing(4)

        chip_row = QHBoxLayout()
        chip_row.setSpacing(8)
        chip_row.setAlignment(Qt.AlignLeft)
        text.addLayout(chip_row)
        self._pill_bus = Pill("Bus …", Pill.KIND_NEUTRAL)
        self._pill_torque = Pill("Torque …", Pill.KIND_NEUTRAL)
        self._pill_voice = Pill("Voice …", Pill.KIND_NEUTRAL)
        chip_row.addWidget(self._pill_bus)
        chip_row.addWidget(self._pill_torque)
        chip_row.addWidget(self._pill_voice)
        text.addStretch(1)

        # Right-side primary CTA
        cta_col = QVBoxLayout()
        cta_col.setSpacing(8)
        cta_col.setAlignment(Qt.AlignCenter)
        h.addLayout(cta_col)

        play_btn = QPushButton("Play actions")
        play_btn.setObjectName("primaryButton")
        play_btn.setCursor(Qt.PointingHandCursor)
        play_btn.setMinimumWidth(140)
        play_btn.clicked.connect(
            lambda: self.navigate_requested.emit("actions:playback")
        )
        cta_col.addWidget(play_btn)

        record_btn = QPushButton("Record new")
        record_btn.setObjectName("secondaryButton")
        record_btn.setCursor(Qt.PointingHandCursor)
        record_btn.setMinimumWidth(140)
        record_btn.clicked.connect(
            lambda: self.navigate_requested.emit("actions:record")
        )
        cta_col.addWidget(record_btn)

        self._pull_changes_btn = QPushButton("Pull changes")
        self._pull_changes_btn.setObjectName("secondaryButton")
        self._pull_changes_btn.setCursor(Qt.PointingHandCursor)
        self._pull_changes_btn.setMinimumWidth(140)
        self._pull_changes_btn.setToolTip(
            "Run git pull in this checkout, then restart the app"
        )
        self._pull_changes_btn.clicked.connect(self._on_pull_changes_clicked)
        cta_col.addWidget(self._pull_changes_btn)

        return card

    def on_enter(self) -> None:
        # Defer health scan so it does not race MainWindow bus init on first paint.
        QTimer.singleShot(800, self._refresh_hero_pills_async)
        self.refresh_battery_pill()
        if not self._hero_pill_timer.isActive():
            self._hero_pill_timer.start()
        if not self._battery_ov_timer.isActive():
            self._battery_ov_timer.start()

    def on_leave(self) -> None:
        self._hero_pill_timer.stop()
        self._battery_ov_timer.stop()
        ht = self._health_collect_thread
        if ht is not None and ht.isRunning():
            ht.wait(5000)
        self._health_collect_thread = None

    def refresh_battery_pill(self) -> None:
        """Lightweight pack-voltage refresh for System overview (no full health scan)."""
        pill = self._ov_pills.get("battery")
        if pill is None:
            return
        low_v = float(self._service.settings.battery_ads1115.low_voltage_v)
        cap, kind_key = overview_pill_for_battery(low_threshold_v=low_v)
        kind = {
            "ok": Pill.KIND_OK,
            "warn": Pill.KIND_WARN,
            "error": Pill.KIND_ERROR,
        }.get(kind_key, Pill.KIND_NEUTRAL)
        pill.setText(cap)
        pill.set_kind(kind)

    def _refresh_hero_pills_async(self) -> None:
        if self._health_collect_thread is not None and self._health_collect_thread.isRunning():
            return
        thread = _HealthCollectThread(self._service)
        thread.setParent(self)
        self._health_collect_thread = thread
        thread.finished_ok.connect(self._apply_hero_pills_from_rows)

        def _clear_health_thread() -> None:
            if self._health_collect_thread is thread:
                self._health_collect_thread = None

        thread.finished.connect(_clear_health_thread)
        thread.start()

    def _apply_hero_pills_from_rows(self, rows: object) -> None:
        self._health_collect_thread = None
        if not isinstance(rows, list):
            rows = []
        self._refresh_hero_pills(rows)

    def _refresh_hero_pills(self, rows: list) -> None:
        """Match Android companion hero chips: bus + BLDC torque + voice from live service."""
        pb, pt, pv = self._pill_bus, self._pill_torque, self._pill_voice
        if pb is None or pt is None or pv is None:
            return

        bus_row = _health_row_by_key(rows, "bus")
        voice_row = _health_row_by_key(rows, "voice")

        if bus_row is None:
            bus_label, bus_kind = "Bus —", Pill.KIND_NEUTRAL
        else:
            st = (bus_row.status or "").strip().lower() or "pending"
            bus_kind = _pill_kind_for_health_status(st)
            if st in ("ok", "ready"):
                bus_label = "Bus ready"
            elif st == "pending":
                bus_label = "Bus idle"
            elif st in ("warn", "error"):
                d = (bus_row.detail or "").strip()
                bus_label = (d[:22] if d else "Bus issue") or "Bus issue"
            else:
                d = (bus_row.detail or "").strip()
                bus_label = (d[:22] if d else "Bus") or "Bus"

        try:
            dc = self._service.drive
            dc.ensure_hardware()
            self._apply_torque_pill_from_state(dc.state())
        except Exception as exc:
            self._apply_torque_pill_from_state(
                {"connected": False, "driver_message": f"{type(exc).__name__}: {exc}"},
            )

        if voice_row is None:
            voice_label, voice_kind = "Voice —", Pill.KIND_NEUTRAL
        else:
            vst = (voice_row.status or "").strip().lower() or "pending"
            voice_kind = _pill_kind_for_health_status(vst)
            if vst in ("ok", "ready"):
                voice_label = "Voice ready"
            elif vst == "pending":
                voice_label = "Voice idle"
            else:
                d = (voice_row.detail or "").strip()
                voice_label = (d[:22] if d else "Voice") or "Voice"

        pb.setText(bus_label)
        pb.set_kind(bus_kind)
        pv.setText(voice_label)
        pv.set_kind(voice_kind)
        self._refresh_overview_strip(rows)

    def _refresh_overview_strip(self, rows: list) -> None:
        """System overview row under quick actions — same subsystem keys as Health."""
        for key in ("bus", "camera", "lidar", "ir", "battery", "wifi"):
            pill = self._ov_pills.get(key)
            if pill is None:
                continue
            r = _health_row_by_key(rows, key)
            cap, kind = _overview_pill_caption_kind(r)
            pill.setText(cap)
            pill.set_kind(kind)

    # ---------- tiles ----------

    def _build_tiles(self) -> QGridLayout:
        grid = QGridLayout()
        grid.setSpacing(12)
        grid.setContentsMargins(0, 0, 0, 0)
        cols = 4
        for i, (key, label, glyph, blurb) in enumerate(QUICK_ACTIONS):
            tile = _QuickTile(glyph, label, blurb)
            tile.clicked.connect(lambda _checked=False, k=key: self.navigate_requested.emit(k))
            grid.addWidget(tile, i // cols, i % cols)
        return grid

    # ---------- status strip ----------

    def _build_status_strip(self) -> Card:
        card = Card(padding=10, spacing=6)
        title_row = QHBoxLayout()
        title_row.setSpacing(8)
        card.add_layout(title_row)
        title_row.addWidget(CardTitle("System overview"))
        title_row.addStretch(1)
        title_row.addWidget(MutedLabel("Tap Health for details"))

        row = QHBoxLayout()
        row.setSpacing(8)
        card.add_layout(row)
        self._ov_pills.clear()
        for key, title in (
            ("bus", "Bus"),
            ("camera", "Camera"),
            ("lidar", "Lidar"),
            ("ir", "IR"),
            ("battery", "Battery"),
            ("wifi", "Wi-Fi"),
        ):
            box = Card(padding=8, spacing=2, subtle=True)
            box.add(SectionLabel(title))
            pill = Pill("\u2014", Pill.KIND_NEUTRAL)
            self._ov_pills[key] = pill
            box.add(pill)
            row.addWidget(box, stretch=1)

        return card

    def _on_pull_changes_clicked(self) -> None:
        from sirena_ui.workers import git_pull_restart

        reply = QMessageBox.question(
            self,
            "Pull changes",
            "Download the latest code with git pull and restart Nina?\n\n"
            "The window will close briefly.",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if reply != QMessageBox.Yes:
            return

        root = git_pull_restart.repository_root()
        if not (root / ".git").is_dir():
            QMessageBox.warning(
                self,
                "Not a git checkout",
                f"No .git directory at:\n{root}\n\n"
                "Pull changes only works when Nina is run from a git clone.",
            )
            return

        btn = self._pull_changes_btn
        if btn is not None:
            btn.setEnabled(False)

        proc = QProcess(self)
        self._git_pull_proc = proc
        proc.setWorkingDirectory(str(root))
        proc.setProcessChannelMode(QProcess.MergedChannels)
        proc.finished.connect(self._on_git_pull_finished)
        proc.start("git", ["pull"])

    def _on_git_pull_finished(self, exit_code: int, exit_status: QProcess.ExitStatus) -> None:
        _ = exit_status
        btn = self._pull_changes_btn
        proc = self._git_pull_proc
        if btn is not None:
            btn.setEnabled(True)

        log_text = ""
        if proc is not None:
            log_text = bytes(proc.readAllStandardOutput()).decode(
                "utf-8", errors="replace"
            ).strip()
        if log_text:
            print(f"[git pull]\n{log_text}", flush=True)

        if exit_code != 0:
            QMessageBox.warning(
                self,
                "Git pull failed",
                (log_text or "(no output)")
                + f"\n\n(exit code {exit_code})\n\n"
                "Fix the problem in a terminal, then try again.",
            )
            return

        from sirena_ui.workers import git_pull_restart

        try:
            self._service.shutdown()
        except Exception:
            pass
        try:
            git_pull_restart.restart_application()
        except OSError as exc:
            QMessageBox.critical(
                self,
                "Restart failed",
                f"Could not restart the app:\n\n{exc}\n\nStart Nina manually.",
            )
            return

        app = QApplication.instance()
        if app is not None:
            app.quit()
        sys.exit(0)
