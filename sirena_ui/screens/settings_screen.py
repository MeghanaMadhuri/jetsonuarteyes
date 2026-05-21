"""Settings screen: nested sub-sidebar + content panel.

Categories: General, Network, Display, Audio, Privacy, Autodock,
Voice Module, Power, OTA. Most of these are scaffolds for now;
General has working fields backed by `NinaSettings`. Power exposes
working Shutdown / Reboot / Quit-app buttons that drive the Jetson
through `nina.jetson_net.host_control`.
"""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Dict, List, Optional, Tuple

from PyQt5.QtCore import QSettings, Qt, QThread, QTimer, pyqtSignal
from PyQt5.QtGui import QPixmap
from PyQt5.QtWidgets import (
    QApplication,
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSlider,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from sirena_ui.styles import asset_path
from sirena_ui.widgets.common import (
    Breadcrumb,
    Card,
    HRule,
    MutedLabel,
    Pill,
    SectionLabel,
)
from sirena_ui.workers.nina_service import NinaService
from nina.services.audio_player import (
    get_app_audio_volume_pct,
    get_system_output_volume_pct,
    set_app_audio_volume_pct,
    set_system_output_volume_pct,
)


# (key, label, glyph)
# Labels intentionally short - the sub-sidebar is 150 px wide on the
# 1024 x 600 panel and longer strings ("Voice Module \u00b7 ESP",
# "Network \u00b7 Wi-Fi") forced the whole pane to overflow.
class _WifiScanWorker(QThread):
    """Run ``GET /v1/wifi/scan`` off the GUI thread (nmcli can take several seconds)."""

    finished_ok = pyqtSignal(object)
    failed = pyqtSignal(str)

    def __init__(self, screen: "SettingsScreen", *, rescan: bool) -> None:
        super().__init__()
        self._screen = screen
        self._rescan = rescan

    def run(self) -> None:
        try:
            payload = self._screen._link_request(
                "/v1/wifi/scan",
                query={"rescan": "1" if self._rescan else "0"},
                timeout=30.0,
            )
        except Exception as exc:
            self.failed.emit(str(exc))
            return
        self.finished_ok.emit(payload)


SETTINGS_CATEGORIES: List[Tuple[str, str, str]] = [
    ("general", "General", "\u2699"),
    ("network", "Network", "\u2706"),
    ("display", "Display", "\u25A1"),
    ("audio", "Audio", "\u266B"),
    ("privacy", "Privacy", "\u26C4"),  # umbrella - placeholder
    ("autodock", "Autodock", "\u2693"),
    ("voice", "Voice", "\u2693"),
    ("power", "Power", "\u26A1"),
    ("ota", "OTA", "\u21BB"),
]


class SettingsScreen(QWidget):
    def __init__(self, service: NinaService, parent=None) -> None:
        super().__init__(parent)
        self._service = service
        self._panes: Dict[str, QWidget] = {}
        self._buttons: Dict[str, QPushButton] = {}

        outer = QVBoxLayout(self)
        outer.setContentsMargins(10, 10, 10, 10)
        outer.setSpacing(8)

        outer.addWidget(Breadcrumb("Nina", "Settings"))

        body = QHBoxLayout()
        body.setSpacing(8)
        outer.addLayout(body, stretch=1)

        body.addWidget(self._build_subsidebar())
        body.addWidget(self._build_content_stack(), stretch=1)

    # ---------- sub-sidebar ----------

    def _build_subsidebar(self) -> QFrame:
        frame = QFrame()
        frame.setObjectName("subSidebar")
        # 150 (was 220). Combined with the 160 main sidebar that's
        # 310 of 1024 wide for navigation chrome - tight but workable.
        frame.setFixedWidth(150)

        v = QVBoxLayout(frame)
        v.setContentsMargins(6, 8, 6, 8)
        v.setSpacing(2)

        group = QButtonGroup(self)
        group.setExclusive(True)
        for key, label, glyph in SETTINGS_CATEGORIES:
            btn = QPushButton(f"  {glyph}    {label}")
            btn.setObjectName("subNavRow")
            btn.setCheckable(True)
            btn.setCursor(Qt.PointingHandCursor)
            btn.clicked.connect(lambda _checked=False, k=key: self._select_pane(k))
            group.addButton(btn)
            v.addWidget(btn)
            self._buttons[key] = btn

        v.addStretch(1)
        footer = QLabel(f"{len(SETTINGS_CATEGORIES)} categories")
        footer.setStyleSheet(
            "color: #8e8e93; font-size: 11px; padding: 8px;"
        )
        footer.setAlignment(Qt.AlignCenter)
        v.addWidget(footer)
        return frame

    # ---------- content stack ----------

    def _build_content_stack(self) -> QStackedWidget:
        self._stack = QStackedWidget()
        for key, label, _glyph in SETTINGS_CATEGORIES:
            pane = self._build_pane(key, label)
            self._panes[key] = pane
            self._stack.addWidget(pane)
        # Default
        self._select_pane("general")
        return self._stack

    def _select_pane(self, key: str) -> None:
        widget = self._panes.get(key)
        if widget is None:
            return
        self._stack.setCurrentWidget(widget)
        btn = self._buttons.get(key)
        if btn is not None:
            btn.setChecked(True)
        if key == "power":
            self._refresh_power_privilege()

    def _build_pane(self, key: str, label: str) -> QWidget:
        if key == "general":
            return self._build_general_pane()
        if key == "network":
            return self._build_network_pane()
        if key == "audio":
            return self._build_audio_pane()
        if key == "power":
            return self._build_power_pane()
        return self._build_placeholder_pane(label)

    def _link_base_url(self) -> str:
        return os.environ.get("NINA_LINK_URL", "http://127.0.0.1:8787").rstrip("/")

    def _link_request(
        self,
        path: str,
        *,
        method: str = "GET",
        body: Any = None,
        query: Optional[Dict[str, Any]] = None,
        timeout: float = 8.0,
    ) -> Dict[str, Any]:
        url = self._link_base_url() + path
        if query:
            url += "?" + urllib.parse.urlencode(query)
        data = None
        headers = {"Accept": "application/json"}
        if body is not None:
            raw = json.dumps(body).encode("utf-8")
            data = raw
            headers["Content-Type"] = "application/json"
        req = urllib.request.Request(url, data=data, method=method, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                payload = resp.read().decode("utf-8")
                return json.loads(payload) if payload else {}
        except urllib.error.HTTPError as e:
            try:
                detail = e.read().decode("utf-8")
                parsed = json.loads(detail)
            except Exception:
                parsed = {"message": e.reason or str(e.code)}
            raise RuntimeError(str(parsed)) from e
        except urllib.error.URLError as e:
            raise RuntimeError(
                f"Tablet gateway unreachable ({self._link_base_url()}). "
                "Run Sirena UI on this Jetson (python -m sirena_ui) with "
                "NINA_ANDROID_GATEWAY=1 and matching NINA_LINK_PORT / NINA_ANDROID_HTTP_PORT."
            ) from e

    def _build_network_pane(self) -> QWidget:
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)

        container = QWidget()
        v = QVBoxLayout(container)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(8)
        scroll.setWidget(container)

        v.addWidget(Breadcrumb("Nina", "Settings", "Network"))

        status_card = Card(padding=10, spacing=6)
        v.addWidget(status_card)
        status_card.add(SectionLabel("Status"))
        self._net_role_pill = Pill("Role —", Pill.KIND_NEUTRAL)
        self._net_ip_pill = Pill("IP —", Pill.KIND_NEUTRAL)
        pill_row = QHBoxLayout()
        pill_row.setSpacing(8)
        status_card.add_layout(pill_row)
        pill_row.addWidget(self._net_role_pill)
        pill_row.addWidget(self._net_ip_pill)
        pill_row.addStretch(1)
        self._net_status = MutedLabel("\u2014")
        self._net_status.setWordWrap(True)
        status_card.add(self._net_status)

        tools = QHBoxLayout()
        tools.setSpacing(6)
        v.addLayout(tools)
        for label, slot in (
            ("Refresh", self._refresh_network_status),
            ("Scan", self._net_scan_wifi),
            ("Start AP", self._net_start_ap),
            ("Home Wi-Fi", self._net_connect_home),
        ):
            btn = QPushButton(label)
            btn.setObjectName("secondaryButton" if label != "Home Wi-Fi" else "primaryButton")
            btn.setCursor(Qt.PointingHandCursor)
            btn.clicked.connect(slot)
            tools.addWidget(btn)
            if label == "Scan":
                self._net_scan_btn = btn
        tools.addStretch(1)

        lists_row = QHBoxLayout()
        lists_row.setSpacing(8)
        v.addLayout(lists_row, stretch=1)

        nearby_card = Card(padding=8, spacing=4)
        lists_row.addWidget(nearby_card, stretch=1)
        nearby_card.add(SectionLabel("Nearby networks"))
        self._net_nearby_list = QListWidget()
        self._net_nearby_list.setMinimumHeight(120)
        self._style_wifi_list(self._net_nearby_list)
        self._net_nearby_list.itemDoubleClicked.connect(
            lambda _item: self._net_connect_selected_scan()
        )
        nearby_card.add(self._net_nearby_list, stretch=1)

        saved_card = Card(padding=8, spacing=4)
        lists_row.addWidget(saved_card, stretch=1)
        saved_card.add(SectionLabel("Saved networks"))
        self._net_saved_list = QListWidget()
        self._net_saved_list.setMinimumHeight(120)
        self._style_wifi_list(self._net_saved_list)
        self._net_saved_list.itemDoubleClicked.connect(
            lambda _item: self._net_connect_saved_selected()
        )
        saved_card.add(self._net_saved_list, stretch=1)

        list_actions = QHBoxLayout()
        list_actions.setSpacing(8)
        v.addLayout(list_actions)
        connect_btn = QPushButton("Connect")
        connect_btn.setObjectName("primaryButton")
        connect_btn.setCursor(Qt.PointingHandCursor)
        connect_btn.clicked.connect(self._net_connect_any_selected)
        list_actions.addWidget(connect_btn)
        forget_btn = QPushButton("Forget saved")
        forget_btn.setObjectName("secondaryButton")
        forget_btn.setCursor(Qt.PointingHandCursor)
        forget_btn.clicked.connect(self._net_forget_saved_selected)
        list_actions.addWidget(forget_btn)
        list_actions.addStretch(1)

        manual_card = Card(padding=10, spacing=6)
        v.addWidget(manual_card)
        manual_card.add(SectionLabel("Manual / advanced"))
        manual_card.add(
            MutedLabel(
                "Pick a network above or enter SSID and password here. "
                f"API: {self._link_base_url()}"
            )
        )
        form = QFormLayout()
        form.setSpacing(6)
        form.setLabelAlignment(Qt.AlignRight)
        manual_card.add_layout(form)

        self._net_mode = QComboBox()
        self._net_mode.addItems(["boot_default", "force_ap", "force_sta"])
        apply_mode = QPushButton("Apply")
        apply_mode.setObjectName("secondaryButton")
        apply_mode.setCursor(Qt.PointingHandCursor)
        apply_mode.clicked.connect(self._net_apply_mode)
        mode_row = QHBoxLayout()
        mode_row.addWidget(self._net_mode, stretch=1)
        mode_row.addWidget(apply_mode)
        mode_wrap = QWidget()
        mode_wrap.setLayout(mode_row)
        form.addRow("User mode", mode_wrap)

        self._net_home_ssid = QLineEdit()
        self._net_home_ssid.setPlaceholderText("SSID")
        form.addRow("SSID", self._net_home_ssid)

        self._net_home_pw = QLineEdit()
        self._net_home_pw.setEchoMode(QLineEdit.Password)
        self._net_home_pw.setPlaceholderText("Password (if required)")
        form.addRow("Password", self._net_home_pw)

        save_wifi = QPushButton("Save credentials")
        save_wifi.setObjectName("secondaryButton")
        save_wifi.setCursor(Qt.PointingHandCursor)
        save_wifi.clicked.connect(self._net_save_home)
        form.addRow("", save_wifi)

        self._net_pin = QLineEdit()
        self._net_pin.setPlaceholderText("Pairing PIN")
        form.addRow("Pair PIN", self._net_pin)
        pair_btn = QPushButton("Pair tablet")
        pair_btn.setObjectName("secondaryButton")
        pair_btn.setCursor(Qt.PointingHandCursor)
        pair_btn.clicked.connect(self._net_pair)
        form.addRow("", pair_btn)

        wrapper = QWidget()
        outer = QVBoxLayout(wrapper)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(scroll, stretch=1)

        self._net_scan_worker: Optional[_WifiScanWorker] = None
        self._saved_profiles: Dict[str, Dict[str, Any]] = {}
        self._refresh_network_status()

        self._net_timer = QTimer(self)
        self._net_timer.timeout.connect(self._refresh_network_status)
        self._net_timer.start(6000)

        return wrapper

    @staticmethod
    def _style_wifi_list(widget: QListWidget) -> None:
        """Readable selection — global QSS sets selected items transparent."""
        widget.setStyleSheet(
            """
            QListWidget {
                background-color: #f5f5f7;
                border: 1px solid #e3e3e6;
                border-radius: 8px;
                outline: none;
                font-size: 12px;
            }
            QListWidget::item {
                color: #1c1c1e;
                padding: 8px 10px;
                border-bottom: 1px solid #ececee;
            }
            QListWidget::item:selected {
                background-color: #fbe7eb;
                color: #1c1c1e;
            }
            QListWidget::item:hover {
                background-color: #ffffff;
            }
            """
        )

    def _refresh_network_status(self) -> None:
        try:
            st = self._link_request("/v1/status")
        except RuntimeError as e:
            self._net_status.setText(str(e))
            self._net_role_pill.setText("Role —")
            self._net_ip_pill.setText("IP —")
            return
        role = str(st.get("wifi_role", "?"))
        ipv4 = st.get("ipv4") or "—"
        self._net_role_pill.setText(f"Role {role}")
        self._net_role_pill.set_kind(
            Pill.KIND_OK if role == "sta" else Pill.KIND_NEUTRAL
        )
        self._net_ip_pill.setText(f"IP {ipv4}")
        lines = [
            f"User mode: {st.get('user_mode')}",
            f"AP SSID: {st.get('ap_ssid', '')}",
            f"Client seen: {st.get('client_seen')}",
        ]
        sta_ssid = st.get("active_sta_ssid")
        if sta_ssid:
            lines.append(f"Connected: {sta_ssid}")
        err = st.get("last_error") or ""
        if err:
            lines.append(f"Last error: {err}")
        pin = st.get("pairing_pin")
        if pin:
            lines.append(f"Pairing PIN: {pin}")
        self._net_status.setText(" · ".join(lines))
        self._populate_saved_networks(st.get("saved_networks") or [])

    def _populate_saved_networks(self, saved: List[Dict[str, Any]]) -> None:
        self._saved_profiles = {}
        self._net_saved_list.clear()
        for entry in saved:
            if not isinstance(entry, dict):
                continue
            ssid = str(entry.get("ssid") or entry.get("id") or "").strip()
            pid = str(entry.get("id") or entry.get("uuid") or "").strip()
            if not ssid or not pid:
                continue
            self._saved_profiles[ssid] = entry
            mode = entry.get("wifi_mode") or "infra"
            ac = "auto" if entry.get("autoconnect") else "manual"
            item = QListWidgetItem(f"{ssid}  ({ac}, {mode})")
            item.setData(Qt.UserRole, ssid)
            self._net_saved_list.addItem(item)

    def _net_scan_wifi(self) -> None:
        if self._net_scan_worker is not None and self._net_scan_worker.isRunning():
            return
        self._net_scan_btn.setEnabled(False)
        worker = _WifiScanWorker(self, rescan=True)
        self._net_scan_worker = worker
        worker.setParent(self)
        worker.finished_ok.connect(self._on_wifi_scan_done)
        worker.failed.connect(self._on_wifi_scan_failed)
        worker.finished.connect(lambda: self._net_scan_btn.setEnabled(True))

        def _clear_scan_worker() -> None:
            if self._net_scan_worker is worker:
                self._net_scan_worker = None

        worker.finished.connect(_clear_scan_worker)
        worker.start()

    def _on_wifi_scan_done(self, payload: object) -> None:
        self._net_scan_worker = None
        if not isinstance(payload, dict):
            return
        self._net_nearby_list.clear()
        for net in payload.get("networks") or []:
            if not isinstance(net, dict):
                continue
            ssid = str(net.get("ssid") or "").strip()
            if not ssid:
                continue
            sig = int(net.get("signal") or 0)
            sec = str(net.get("security") or "")
            in_use = bool(net.get("in_use"))
            label = f"{ssid}  {sig}%"
            if in_use:
                label += "  (connected)"
            if sec:
                label += f"  [{sec}]"
            item = QListWidgetItem(label)
            item.setData(Qt.UserRole, ssid)
            self._net_nearby_list.addItem(item)

    def _on_wifi_scan_failed(self, message: str) -> None:
        self._net_scan_worker = None
        QMessageBox.warning(self, "Wi-Fi scan", message)

    def _selected_nearby_ssid(self) -> str:
        item = self._net_nearby_list.currentItem()
        if item is None:
            return ""
        return str(item.data(Qt.UserRole) or "").strip()

    def _selected_saved_ssid(self) -> str:
        item = self._net_saved_list.currentItem()
        if item is None:
            return ""
        return str(item.data(Qt.UserRole) or "").strip()

    def _net_connect_any_selected(self) -> None:
        ssid = self._selected_nearby_ssid() or self._selected_saved_ssid()
        if not ssid:
            ssid = self._net_home_ssid.text().strip()
        if not ssid:
            QMessageBox.information(self, "Network", "Select or enter a network SSID.")
            return
        self._net_home_ssid.setText(ssid)
        if ssid in self._saved_profiles:
            self._net_connect_saved_ssid(ssid)
        else:
            self._net_connect_new_ssid(ssid)

    def _net_connect_selected_scan(self) -> None:
        ssid = self._selected_nearby_ssid()
        if ssid:
            self._net_home_ssid.setText(ssid)
            self._net_connect_any_selected()

    def _net_connect_saved_selected(self) -> None:
        ssid = self._selected_saved_ssid()
        if ssid:
            self._net_connect_saved_ssid(ssid)

    def _net_connect_saved_ssid(self, ssid: str) -> None:
        try:
            self._link_request(
                "/v1/wifi/connect-home",
                method="POST",
                body={},
                query={"ssid": ssid},
            )
            QMessageBox.information(self, "Network", f"Connecting to {ssid}…")
            self._refresh_network_status()
        except RuntimeError as e:
            QMessageBox.warning(self, "Network", str(e))

    def _net_connect_new_ssid(self, ssid: str) -> None:
        pw = self._net_home_pw.text()
        if not pw:
            QMessageBox.information(
                self,
                "Network",
                "Enter the Wi-Fi password in Manual / advanced, then Connect again.",
            )
            return
        try:
            self._link_request(
                "/v1/wifi/home-credentials",
                method="POST",
                body={"ssid": ssid, "password": pw},
            )
            self._link_request(
                "/v1/wifi/connect-home",
                method="POST",
                body={},
                query={"ssid": ssid},
            )
            QMessageBox.information(self, "Network", f"Saved and connecting to {ssid}…")
            self._refresh_network_status()
        except RuntimeError as e:
            QMessageBox.warning(self, "Network", str(e))

    def _net_forget_saved_selected(self) -> None:
        ssid = self._selected_saved_ssid()
        if not ssid:
            QMessageBox.information(self, "Network", "Select a saved network to forget.")
            return
        entry = self._saved_profiles.get(ssid) or {}
        pid = str(entry.get("id") or entry.get("uuid") or "").strip()
        if not pid:
            QMessageBox.warning(self, "Network", "Could not resolve profile id.")
            return
        reply = QMessageBox.question(
            self,
            "Forget network",
            f"Remove saved profile for “{ssid}”?",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if reply != QMessageBox.Yes:
            return
        try:
            self._link_request(f"/v1/wifi/saved/{pid}", method="DELETE")
            self._refresh_network_status()
        except RuntimeError as e:
            QMessageBox.warning(self, "Network", str(e))

    def _net_apply_mode(self) -> None:
        mode = self._net_mode.currentText()
        try:
            self._link_request("/v1/mode", method="POST", body={"mode": mode})
            self._refresh_network_status()
        except RuntimeError as e:
            QMessageBox.warning(self, "Network", str(e))

    def _net_start_ap(self) -> None:
        try:
            self._link_request("/v1/wifi/start-ap", method="POST", body={})
            self._refresh_network_status()
        except RuntimeError as e:
            QMessageBox.warning(self, "Network", str(e))

    def _net_connect_home(self) -> None:
        try:
            self._link_request("/v1/wifi/connect-home", method="POST", body={})
            self._refresh_network_status()
        except RuntimeError as e:
            QMessageBox.warning(self, "Network", str(e))

    def _net_save_home(self) -> None:
        ssid = self._net_home_ssid.text().strip()
        if not ssid:
            QMessageBox.information(self, "Network", "Enter a home SSID.")
            return
        try:
            self._link_request(
                "/v1/wifi/home-credentials",
                method="POST",
                body={"ssid": ssid, "password": self._net_home_pw.text()},
            )
            QMessageBox.information(self, "Network", "Home Wi-Fi profile saved.")
            self._refresh_network_status()
        except RuntimeError as e:
            QMessageBox.warning(self, "Network", str(e))

    def _net_pair(self) -> None:
        pin = self._net_pin.text().strip()
        if len(pin) < 4:
            QMessageBox.information(self, "Network", "Enter the pairing PIN.")
            return
        try:
            out = self._link_request("/v1/pair", method="POST", body={"pin": pin})
            tok = out.get("token", "")
            QMessageBox.information(
                self,
                "Paired",
                "Session token (paste into companion app / Authorization "
                f"header):\n\n{tok}",
            )
        except RuntimeError as e:
            QMessageBox.warning(self, "Network", str(e))

    # ---------- General ----------

    def _build_general_pane(self) -> QWidget:
        container = QWidget()
        v = QVBoxLayout(container)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(8)

        # About hero
        hero = Card(padding=10, spacing=6)
        h = QHBoxLayout()
        h.setSpacing(10)
        hero.add_layout(h)

        thumb = QLabel()
        thumb.setStyleSheet("background-color: transparent;")
        pix = QPixmap(asset_path("nina.png"))
        if not pix.isNull():
            thumb.setPixmap(pix.scaledToHeight(48, Qt.SmoothTransformation))
        h.addWidget(thumb)

        text = QVBoxLayout()
        text.setSpacing(2)
        h.addLayout(text, stretch=1)
        title = QLabel("Nina")
        title.setStyleSheet(
            "color: #1c1c1e; font-size: 15px; font-weight: 700;"
            " background-color: transparent;"
        )
        text.addWidget(title)
        sub = QLabel("Sirena Robotics \u00b7 v0.4 \u00b7 serial NN-0042")
        sub.setStyleSheet(
            "color: #6e6e73; font-size: 12px; background-color: transparent;"
        )
        text.addWidget(sub)

        view_health = QPushButton("View health")
        view_health.setObjectName("secondaryButton")
        view_health.setCursor(Qt.PointingHandCursor)
        view_health.setFixedWidth(120)
        h.addWidget(view_health, alignment=Qt.AlignTop)
        v.addWidget(hero)

        # Form card - tighter padding to fit on the 1024 x 600 panel.
        form_card = Card(padding=12, spacing=8)
        v.addWidget(form_card, stretch=1)

        section_title = QLabel("General")
        section_title.setStyleSheet(
            "color: #1c1c1e; font-size: 15px; font-weight: 700;"
            " background-color: transparent;"
        )
        form_card.add(section_title)

        form = QFormLayout()
        form.setSpacing(8)
        form.setLabelAlignment(Qt.AlignRight)
        form_card.add_layout(form)

        self._robot_name = QLineEdit("Nina")
        form.addRow("Robot name", self._robot_name)

        self._tz_combo = QComboBox()
        self._tz_combo.addItems([
            "Asia / Kolkata", "Asia / Singapore", "Europe / London",
            "America / New_York", "America / Los_Angeles", "UTC",
        ])
        form.addRow("Time zone", self._tz_combo)

        self._lang_combo = QComboBox()
        self._lang_combo.addItems([
            "English (US)", "English (UK)", "English (IN)", "Hindi",
            "Spanish", "French",
        ])
        form.addRow("Default language", self._lang_combo)

        self._boot_combo = QComboBox()
        try:
            actions = sorted(self._service.list_actions().keys())
        except Exception:
            actions = ["neutral"]
        self._boot_combo.addItems(actions or ["neutral"])
        form.addRow("Boot action", self._boot_combo)

        greet = QCheckBox("Speak greeting on boot")
        greet.setChecked(True)
        form.addRow("", greet)

        diag = QCheckBox("Show diagnostic overlay on screen")
        form.addRow("", diag)

        form_card.add(HRule())
        danger = QHBoxLayout()
        danger.setSpacing(8)
        form_card.add_layout(danger)
        danger.addWidget(SectionLabel("Danger zone"))
        danger.addStretch(1)
        reset = QPushButton("Reset all")
        reset.setObjectName("secondaryButton")
        reset.setCursor(Qt.PointingHandCursor)
        reset.clicked.connect(self._on_reset)
        danger.addWidget(reset)

        # Save / Discard
        cta = QHBoxLayout()
        cta.setSpacing(8)
        form_card.add_layout(cta)
        cta.addStretch(1)
        save = QPushButton("Save changes")
        save.setObjectName("primaryButton")
        save.setCursor(Qt.PointingHandCursor)
        save.clicked.connect(self._on_save_general)
        cta.addWidget(save)
        discard = QPushButton("Discard")
        discard.setObjectName("secondaryButton")
        discard.setCursor(Qt.PointingHandCursor)
        cta.addWidget(discard)

        return container

    # ---------- Audio ----------

    def _build_audio_pane(self) -> QWidget:
        container = QWidget()
        v = QVBoxLayout(container)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(8)

        v.addWidget(Breadcrumb("Nina", "Settings", "Audio"))

        card = Card(padding=12, spacing=10)
        v.addWidget(card, stretch=1)

        title = QLabel("Audio")
        title.setStyleSheet(
            "color: #1c1c1e; font-size: 15px; font-weight: 700;"
            " background-color: transparent;"
        )
        card.add(title)
        card.add(
            MutedLabel(
                "System volume adjusts the Jetson speaker (ALSA/Pulse). "
                "Nina app volume scales greeting and action clips."
            )
        )

        form = QFormLayout()
        form.setSpacing(10)
        form.setLabelAlignment(Qt.AlignRight)
        card.add_layout(form)

        sys_pct = get_system_output_volume_pct()
        if sys_pct is None:
            sys_pct = get_app_audio_volume_pct()
        self._sys_volume_value = QLabel(f"{sys_pct}%")
        self._sys_volume_value.setFixedWidth(56)
        self._sys_volume_value.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self._sys_volume_value.setStyleSheet(
            "color: #1c1c1e; font-size: 13px; font-weight: 700;"
            " background-color: transparent;"
        )
        self._sys_volume_slider = QSlider(Qt.Horizontal)
        self._sys_volume_slider.setRange(0, 100)
        self._sys_volume_slider.setValue(max(0, min(100, sys_pct)))
        self._sys_volume_slider.valueChanged.connect(self._on_system_volume_changed)
        sys_row = QHBoxLayout()
        sys_row.setSpacing(8)
        sys_row.addWidget(self._sys_volume_slider, stretch=1)
        sys_row.addWidget(self._sys_volume_value)
        sys_wrap = QWidget()
        sys_wrap.setLayout(sys_row)
        form.addRow("System volume", sys_wrap)

        current = get_app_audio_volume_pct()
        self._audio_volume_value = QLabel(f"{current}%")
        self._audio_volume_value.setFixedWidth(56)
        self._audio_volume_value.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self._audio_volume_value.setStyleSheet(
            "color: #1c1c1e; font-size: 13px; font-weight: 700;"
            " background-color: transparent;"
        )
        self._audio_volume_slider = QSlider(Qt.Horizontal)
        self._audio_volume_slider.setRange(0, 100)
        self._audio_volume_slider.setSingleStep(5)
        self._audio_volume_slider.setPageStep(10)
        self._audio_volume_slider.setValue(max(0, min(100, current)))
        self._audio_volume_slider.valueChanged.connect(self._on_app_volume_changed)
        vol_row = QHBoxLayout()
        vol_row.setSpacing(8)
        vol_row.addWidget(self._audio_volume_slider, stretch=1)
        vol_row.addWidget(self._audio_volume_value)
        vol_wrap = QWidget()
        vol_wrap.setLayout(vol_row)
        form.addRow("Nina app volume", vol_wrap)

        self._audio_status = QLabel("")
        self._audio_status.setWordWrap(True)
        self._audio_status.setStyleSheet(
            "color: #6e6e73; font-size: 11px; background-color: transparent;"
        )
        card.add(self._audio_status)

        row = QHBoxLayout()
        row.setSpacing(8)
        card.add_layout(row)
        refresh = QPushButton("Refresh")
        refresh.setObjectName("secondaryButton")
        refresh.setCursor(Qt.PointingHandCursor)
        refresh.clicked.connect(self._refresh_audio_volume)
        row.addWidget(refresh)
        row.addStretch(1)

        card.add_stretch()
        self._refresh_audio_volume()
        return container

    def _on_system_volume_changed(self, value: int) -> None:
        ok = set_system_output_volume_pct(value)
        pct = get_system_output_volume_pct()
        shown = pct if pct is not None else value
        if self._sys_volume_value is not None:
            self._sys_volume_value.setText(f"{shown}%")
        if self._audio_status is not None:
            detail = (
                f"System volume {shown}%."
                if ok
                else f"System mixer unchanged ({shown}% readback)."
            )
            self._audio_status.setText(detail)

    def _on_app_volume_changed(self, value: int) -> None:
        value = set_app_audio_volume_pct(value)
        QSettings("Sirena", "Nina").setValue("audio/gain_pct", value)
        if self._audio_volume_value is not None:
            self._audio_volume_value.setText(f"{value}%")
        if self._audio_status is not None:
            self._audio_status.setText(
                f"Nina app volume {value}%. Applies to the next greeting or action clip."
            )

    def _refresh_audio_volume(self) -> None:
        value = get_app_audio_volume_pct()
        if self._audio_volume_slider is not None:
            self._audio_volume_slider.blockSignals(True)
            self._audio_volume_slider.setValue(max(0, min(100, value)))
            self._audio_volume_slider.blockSignals(False)
        if self._audio_volume_value is not None:
            self._audio_volume_value.setText(f"{value}%")
        sys_pct = get_system_output_volume_pct()
        if self._sys_volume_slider is not None and sys_pct is not None:
            self._sys_volume_slider.blockSignals(True)
            self._sys_volume_slider.setValue(max(0, min(100, sys_pct)))
            self._sys_volume_slider.blockSignals(False)
        if self._sys_volume_value is not None:
            self._sys_volume_value.setText(
                f"{sys_pct}%" if sys_pct is not None else "—"
            )
        if self._audio_status is not None:
            sys_text = (
                f"System mixer: {sys_pct}%."
                if sys_pct is not None
                else "No system mixer — use Nina app volume or install alsa-utils."
            )
            self._audio_status.setText(f"Nina app volume: {value}%. {sys_text}")

    # ---------- Power ----------

    def _build_power_pane(self) -> QWidget:
        container = QWidget()
        v = QVBoxLayout(container)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(8)

        v.addWidget(Breadcrumb("Nina", "Settings", "Power"))

        card = Card(padding=12, spacing=10)
        v.addWidget(card)

        title = QLabel("Power")
        title.setStyleSheet(
            "color: #1c1c1e; font-size: 15px; font-weight: 700;"
            " background-color: transparent;"
        )
        card.add(title)
        card.add(
            MutedLabel(
                "Shutdown or reboot the Jetson host without dropping to "
                "a terminal. Quit closes the app but leaves the OS "
                "running - handy for SSH'ing in to debug."
            )
        )

        card.add(HRule())

        # Shutdown row
        shutdown_row = QHBoxLayout()
        shutdown_row.setSpacing(10)
        card.add_layout(shutdown_row)
        col = QVBoxLayout()
        col.setSpacing(2)
        shutdown_row.addLayout(col, stretch=1)
        col.addWidget(SectionLabel("Shutdown Jetson"))
        col.addWidget(
            MutedLabel(
                "Brings the OS down cleanly via `systemctl poweroff`."
                " The kiosk user needs passwordless sudo for this to "
                "work without an admin prompt."
            )
        )
        self._shutdown_btn = QPushButton("Shutdown")
        self._shutdown_btn.setObjectName("dangerButton")
        self._shutdown_btn.setCursor(Qt.PointingHandCursor)
        self._shutdown_btn.setFixedWidth(140)
        self._shutdown_btn.clicked.connect(self._on_power_shutdown)
        shutdown_row.addWidget(self._shutdown_btn, alignment=Qt.AlignTop)

        card.add(HRule())

        # Reboot row
        reboot_row = QHBoxLayout()
        reboot_row.setSpacing(10)
        card.add_layout(reboot_row)
        col = QVBoxLayout()
        col.setSpacing(2)
        reboot_row.addLayout(col, stretch=1)
        col.addWidget(SectionLabel("Reboot Jetson"))
        col.addWidget(
            MutedLabel(
                "Restarts the OS. Use this after `git pull`s that touch "
                "systemd units, kernel modules, or udev rules."
            )
        )
        self._reboot_btn = QPushButton("Reboot")
        self._reboot_btn.setObjectName("dangerButton")
        self._reboot_btn.setCursor(Qt.PointingHandCursor)
        self._reboot_btn.setFixedWidth(140)
        self._reboot_btn.clicked.connect(self._on_power_reboot)
        reboot_row.addWidget(self._reboot_btn, alignment=Qt.AlignTop)

        card.add(HRule())

        # Quit row (no OS-level effect)
        quit_row = QHBoxLayout()
        quit_row.setSpacing(10)
        card.add_layout(quit_row)
        col = QVBoxLayout()
        col.setSpacing(2)
        quit_row.addLayout(col, stretch=1)
        col.addWidget(SectionLabel("Quit Sirena UI"))
        col.addWidget(
            MutedLabel(
                "Closes this app window. The Jetson stays running so "
                "you can re-launch from the desktop / `python -m "
                "sirena_ui`."
            )
        )
        self._quit_btn = QPushButton("Quit app")
        self._quit_btn.setObjectName("secondaryButton")
        self._quit_btn.setCursor(Qt.PointingHandCursor)
        self._quit_btn.setFixedWidth(140)
        self._quit_btn.clicked.connect(self._on_power_quit_app)
        quit_row.addWidget(self._quit_btn, alignment=Qt.AlignTop)

        card.add_stretch()

        # Sudo-hint footer so non-Linux dev hosts (and a fresh Jetson
        # without the sudoers drop-in) get a clear pointer to the fix
        # instead of a silent button.
        self._power_status = QLabel("")
        self._power_status.setWordWrap(True)
        self._power_status.setStyleSheet(
            "color: #6e6e73; font-size: 11px; background-color: transparent;"
        )
        card.add(self._power_status)
        self._refresh_power_privilege()

        return container

    def _refresh_power_privilege(self) -> None:
        if self._power_status is None:
            return
        try:
            from nina.jetson_net import host_control

            st = host_control.probe_power_privilege()
        except Exception as exc:
            self._power_status.setText(f"Power check failed: {exc}")
            return
        lines = [str(st.get("detail") or "").strip()]
        hint = str(st.get("install_hint") or "").strip()
        if hint:
            lines.append(hint)
        if st.get("ok"):
            lines.insert(0, "Shutdown / Reboot: ready.")
        else:
            lines.insert(0, "Shutdown / Reboot: sudo not configured.")
        self._power_status.setText("\n\n".join(x for x in lines if x))

    def _on_power_shutdown(self) -> None:
        if not self._confirm_power_action(
            "Shutdown Jetson",
            "Bring the Jetson down NOW?\n\n"
            "The screen will go dark in a few seconds. To bring "
            "Nina back up you'll need to press the physical power "
            "button on the chassis.",
        ):
            return
        self._do_power_action("poweroff")

    def _on_power_reboot(self) -> None:
        if not self._confirm_power_action(
            "Reboot Jetson",
            "Reboot the Jetson NOW?\n\n"
            "The current session will end. Nina will be back at the "
            "login / kiosk screen in ~45 s.",
        ):
            return
        self._do_power_action("reboot")

    def _on_power_quit_app(self) -> None:
        if not self._confirm_power_action(
            "Quit Sirena UI",
            "Close the Nina control center?\n\n"
            "The Jetson keeps running - relaunch with "
            "`python -m sirena_ui`.",
        ):
            return
        try:
            self._service.shutdown()
        except Exception:
            pass
        app = QApplication.instance()
        if app is not None:
            app.quit()
        else:
            sys.exit(0)

    def _confirm_power_action(self, title: str, body: str) -> bool:
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Question)
        box.setWindowTitle(title)
        box.setText(body)
        box.setStandardButtons(QMessageBox.Yes | QMessageBox.No)
        box.setDefaultButton(QMessageBox.No)
        box.setWindowFlags(
            box.windowFlags() | Qt.WindowStaysOnTopHint
        )
        return box.exec_() == QMessageBox.Yes

    def _do_power_action(self, action: str) -> None:
        """Shut down motors, then queue host poweroff/reboot (HTTP + in-process)."""
        try:
            self._service.shutdown()
        except Exception:
            pass
        from nina.jetson_net import host_control

        result: Dict[str, Any] = {}
        path = (
            "/v1/system/poweroff"
            if action == "poweroff"
            else "/v1/system/reboot"
        )
        try:
            result = self._link_request(path, method="POST", body={}, timeout=6.0)
        except RuntimeError:
            if action == "poweroff":
                result = host_control.queue_poweroff()
            else:
                result = host_control.queue_reboot()

        ok = bool(result.get("ok", False))
        msg = str(result.get("message") or "").strip()
        hint = str(result.get("install_hint") or "").strip()
        if not msg:
            msg = f"{action} requested." if ok else f"{action} failed."

        if not ok:
            body = msg
            if hint:
                body = f"{body}\n\n{hint}"
            warn = QMessageBox(self)
            warn.setIcon(QMessageBox.Warning)
            warn.setWindowTitle("Power")
            warn.setText(body)
            warn.setStandardButtons(QMessageBox.Ok)
            warn.setWindowFlags(warn.windowFlags() | Qt.WindowStaysOnTopHint)
            warn.exec_()
            self._refresh_power_privilege()
            return

        footer = msg
        if self._power_status is not None:
            self._power_status.setText(footer)
        info = QMessageBox(self)
        info.setIcon(QMessageBox.Information)
        info.setWindowTitle("Power")
        info.setText(footer)
        info.setStandardButtons(QMessageBox.Ok)
        info.setWindowFlags(info.windowFlags() | Qt.WindowStaysOnTopHint)
        info.exec_()

    # ---------- placeholder panes ----------

    def _build_placeholder_pane(self, label: str) -> QWidget:
        container = QWidget()
        v = QVBoxLayout(container)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(8)

        card = Card(padding=12, spacing=6)
        v.addWidget(card, stretch=1)

        title = QLabel(label)
        title.setStyleSheet(
            "color: #1c1c1e; font-size: 18px; font-weight: 700;"
            " background-color: transparent;"
        )
        card.add(title)
        card.add(MutedLabel(
            "Controls for this category will land alongside the matching"
            " hardware feature. The layout is locked in so wiring it up"
            " stays a one-line change."
        ))

        # Add a few sensible placeholder rows so each pane feels deliberate.
        placeholders = self._placeholder_rows(label)
        if placeholders:
            form = QFormLayout()
            form.setSpacing(10)
            form.setLabelAlignment(Qt.AlignRight)
            card.add_layout(form)
            for label_, widget in placeholders:
                form.addRow(label_, widget)

        card.add_stretch()

        chip_row = QHBoxLayout()
        chip_row.setSpacing(8)
        card.add_layout(chip_row)
        chip_row.addWidget(Pill("Coming soon", Pill.KIND_NEUTRAL))
        chip_row.addStretch(1)
        return container

    def _placeholder_rows(self, label: str) -> List[Tuple[str, QWidget]]:
        if label.startswith("Network"):
            wifi = QComboBox()
            wifi.addItems(["Sirena-5G", "Sirena-Guest", "Other..."])
            ip = QLabel("\u2014")
            return [("Wi-Fi network", wifi), ("IP address", ip)]
        if label == "Display":
            slider = QSlider(Qt.Horizontal)
            slider.setRange(0, 100)
            slider.setValue(70)
            sleep = QComboBox()
            sleep.addItems(["Never", "1 min", "5 min", "15 min"])
            return [("Brightness", slider), ("Screen sleep", sleep)]
        if label == "Audio":
            vol = QSlider(Qt.Horizontal)
            vol.setRange(0, 100)
            vol.setValue(60)
            mic = QComboBox()
            mic.addItems(["Default", "USB Mic", "Built-in"])
            return [("Speaker volume", vol), ("Microphone", mic)]
        if label == "Privacy":
            return [
                ("Camera privacy", QCheckBox("Disable camera when idle")),
                ("Mic privacy",    QCheckBox("Disable microphone when idle")),
            ]
        if label == "Autodock":
            thr = QSlider(Qt.Horizontal)
            thr.setRange(5, 50)
            thr.setValue(20)
            return [
                ("Return-to-dock at", thr),
                ("Charging type", QComboBox()),
            ]
        if label.startswith("Voice"):
            wake = QLineEdit("Hey Nina")
            return [
                ("Wake word", wake),
                ("ESP firmware", QLabel("0.7")),
            ]
        if label.startswith("OTA"):
            return [
                ("Channel", QComboBox()),
                ("Last update", QLabel("\u2014")),
            ]
        return []

    # ---------- handlers ----------

    def _on_save_general(self) -> None:
        QMessageBox.information(
            self,
            "Settings saved",
            "Robot name, time zone, language and boot action saved locally.\n\n"
            "Persistent storage will be wired up in the next firmware update.",
        )

    def _on_reset(self) -> None:
        confirm = QMessageBox.question(
            self,
            "Reset all settings?",
            "This will clear local UI preferences (it does NOT remove your"
            " recorded actions or audio clips). Continue?",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if confirm != QMessageBox.Yes:
            return
        self._robot_name.setText("Nina")
        self._tz_combo.setCurrentIndex(0)
        self._lang_combo.setCurrentIndex(0)
        self._boot_combo.setCurrentIndex(0)
