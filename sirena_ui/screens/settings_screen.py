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
import urllib.request
from typing import Any, Dict, List, Tuple

from PyQt5.QtCore import QSettings, Qt, QTimer
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
    QMessageBox,
    QPushButton,
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
        timeout: float = 8.0,
    ) -> Dict[str, Any]:
        url = self._link_base_url() + path
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
        container = QWidget()
        v = QVBoxLayout(container)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(8)

        v.addWidget(Breadcrumb("Nina", "Settings", "Network"))

        card = Card(padding=12, spacing=8)
        v.addWidget(card, stretch=1)

        title = QLabel("Connectivity")
        title.setStyleSheet(
            "color: #1c1c1e; font-size: 15px; font-weight: 700;"
            " background-color: transparent;"
        )
        card.add(title)
        card.add(
            MutedLabel(
                "Controls the Jetson Wi-Fi role (access-point vs home network). "
                "Uses the tablet HTTP API embedded in Sirena UI on this machine "
                f"({self._link_base_url()})."
            )
        )

        self._net_status = QLabel("\u2014")
        self._net_status.setWordWrap(True)
        self._net_status.setStyleSheet(
            "color: #1c1c1e; font-size: 12px; background-color: transparent;"
        )
        card.add(self._net_status)

        row = QHBoxLayout()
        row.setSpacing(8)
        card.add_layout(row)
        refresh = QPushButton("Refresh")
        refresh.setObjectName("secondaryButton")
        refresh.setCursor(Qt.PointingHandCursor)
        refresh.clicked.connect(self._refresh_network_status)
        row.addWidget(refresh)
        ap_btn = QPushButton("Start AP")
        ap_btn.setObjectName("secondaryButton")
        ap_btn.setCursor(Qt.PointingHandCursor)
        ap_btn.clicked.connect(self._net_start_ap)
        row.addWidget(ap_btn)
        sta_btn = QPushButton("Use home Wi-Fi")
        sta_btn.setObjectName("primaryButton")
        sta_btn.setCursor(Qt.PointingHandCursor)
        sta_btn.clicked.connect(self._net_connect_home)
        row.addWidget(sta_btn)
        row.addStretch(1)

        form = QFormLayout()
        form.setSpacing(8)
        form.setLabelAlignment(Qt.AlignRight)
        card.add_layout(form)

        self._net_mode = QComboBox()
        self._net_mode.addItems(
            ["boot_default", "force_ap", "force_sta"]
        )
        apply_mode = QPushButton("Apply mode")
        apply_mode.setObjectName("secondaryButton")
        apply_mode.setCursor(Qt.PointingHandCursor)
        apply_mode.clicked.connect(self._net_apply_mode)
        mode_row = QHBoxLayout()
        mode_row.addWidget(self._net_mode, stretch=1)
        mode_row.addWidget(apply_mode)
        mode_wrap = QWidget()
        mode_wrap.setLayout(mode_row)
        form.addRow("User mode", mode_wrap)

        self._net_pin = QLineEdit()
        self._net_pin.setPlaceholderText("Pairing PIN (tablet)")
        form.addRow("Pair PIN", self._net_pin)

        pair_btn = QPushButton("Pair session (copy token to tablet)")
        pair_btn.setObjectName("secondaryButton")
        pair_btn.setCursor(Qt.PointingHandCursor)
        pair_btn.clicked.connect(self._net_pair)
        form.addRow("", pair_btn)

        self._net_home_ssid = QLineEdit()
        self._net_home_ssid.setPlaceholderText("Home SSID")
        form.addRow("Home SSID", self._net_home_ssid)

        self._net_home_pw = QLineEdit()
        self._net_home_pw.setEchoMode(QLineEdit.Password)
        self._net_home_pw.setPlaceholderText("Home Wi-Fi password")
        form.addRow("Home password", self._net_home_pw)

        save_wifi = QPushButton("Save home credentials only")
        save_wifi.setObjectName("secondaryButton")
        save_wifi.setCursor(Qt.PointingHandCursor)
        save_wifi.clicked.connect(self._net_save_home)
        form.addRow("", save_wifi)

        card.add(HRule())
        card.add(MutedLabel("Saved profiles can be removed from the Android companion app."))
        card.add_stretch()

        self._refresh_network_status()

        self._net_timer = QTimer(self)
        self._net_timer.timeout.connect(self._refresh_network_status)
        self._net_timer.start(4000)

        return container

    def _refresh_network_status(self) -> None:
        try:
            st = self._link_request("/v1/status")
        except RuntimeError as e:
            self._net_status.setText(str(e))
            return
        lines = [
            f"Role: {st.get('wifi_role', '?')}",
            f"IPv4: {st.get('ipv4') or '—'}",
            f"AP SSID: {st.get('ap_ssid', '')}",
            f"Boot window remaining: {st.get('boot_wait_remaining_sec', 0)} s",
            f"Client seen: {st.get('client_seen')}",
            f"User mode: {st.get('user_mode')}",
        ]
        sta_ssid = st.get("active_sta_ssid")
        if sta_ssid:
            lines.append(f"STA connected: {sta_ssid}")
            prof = st.get("active_sta_profile")
            if prof:
                lines.append(f"NM profile: {prof}")
        pin = st.get("pairing_pin")
        if pin:
            lines.append(f"Pairing PIN: {pin}")
        err = st.get("last_error") or ""
        if err:
            lines.append(f"Last error: {err}")
        saved = st.get("saved_networks") or []
        if saved:
            brief = []
            for s in saved[:8]:
                ac = "on" if s.get("autoconnect") else "off"
                brief.append(f"{s.get('ssid', '?')} (NM auto:{ac})")
            lines.append("Saved: " + ", ".join(brief))
        self._net_status.setText("\n".join(lines))

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
                "Controls Nina's app playback volume. On MAX98357A I2S this "
                "uses digital gain because the amplifier has no software mixer. "
                "If 100% is still too quiet, increase the MAX98357A hardware gain."
            )
        )

        form = QFormLayout()
        form.setSpacing(10)
        form.setLabelAlignment(Qt.AlignRight)
        card.add_layout(form)

        current = get_app_audio_volume_pct()
        self._audio_volume_value = QLabel(f"{current}%")
        self._audio_volume_value.setFixedWidth(56)
        self._audio_volume_value.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self._audio_volume_value.setStyleSheet(
            "color: #1c1c1e; font-size: 13px; font-weight: 700;"
            " background-color: transparent;"
        )

        self._audio_volume_slider = QSlider(Qt.Horizontal)
        self._audio_volume_slider.setRange(0, 150)
        self._audio_volume_slider.setSingleStep(5)
        self._audio_volume_slider.setPageStep(10)
        self._audio_volume_slider.setTickInterval(25)
        self._audio_volume_slider.setValue(max(0, min(150, current)))
        self._audio_volume_slider.valueChanged.connect(self._on_audio_volume_changed)

        vol_row = QHBoxLayout()
        vol_row.setSpacing(8)
        vol_row.addWidget(self._audio_volume_slider, stretch=1)
        vol_row.addWidget(self._audio_volume_value)
        vol_wrap = QWidget()
        vol_wrap.setLayout(vol_row)
        form.addRow("Speaker volume", vol_wrap)

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

    def _on_audio_volume_changed(self, value: int) -> None:
        value = set_app_audio_volume_pct(value)
        QSettings("Sirena", "Nina").setValue("audio/gain_pct", value)
        if self._audio_volume_value is not None:
            self._audio_volume_value.setText(f"{value}%")
        # Best-effort OS mixer update for systems that expose Master/Pulse.
        sys_ok = set_system_output_volume_pct(min(100, value))
        if self._audio_status is not None:
            detail = "System mixer updated." if sys_ok else "Using Nina digital gain."
            if value > 100:
                detail += " Boost above 100% may distort; hardware gain is cleaner."
            self._audio_status.setText(
                f"Volume set to {value}%. {detail} Changes apply to the next clip."
            )

    def _refresh_audio_volume(self) -> None:
        value = get_app_audio_volume_pct()
        if self._audio_volume_slider is not None:
            self._audio_volume_slider.blockSignals(True)
            self._audio_volume_slider.setValue(max(0, min(150, value)))
            self._audio_volume_slider.blockSignals(False)
        if self._audio_volume_value is not None:
            self._audio_volume_value.setText(f"{value}%")
        sys_pct = get_system_output_volume_pct()
        if self._audio_status is not None:
            sys_text = f"System mixer: {sys_pct}%." if sys_pct is not None else "No system mixer detected."
            self._audio_status.setText(
                f"Nina digital gain: {value}%. {sys_text}"
            )

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

        return container

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
        reply = QMessageBox.question(
            self,
            title,
            body,
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        return reply == QMessageBox.Yes

    def _do_power_action(self, action: str) -> None:
        """Invoke the host_control queue function and surface the result.

        Uses the in-process helpers (not the HTTP gateway) so the
        operator gets the same behaviour whether or not nina-link is
        running on this Jetson. Drives bus shutdown first so motors
        don't keep holding torque while the OS is brining services
        down.
        """
        try:
            self._service.shutdown()
        except Exception:
            pass
        try:
            from nina.jetson_net import host_control
        except Exception as exc:
            QMessageBox.critical(
                self,
                "Power",
                f"Could not import host_control: {exc}",
            )
            return
        try:
            if action == "poweroff":
                result = host_control.queue_poweroff()
            elif action == "reboot":
                result = host_control.queue_reboot()
            else:
                raise ValueError(f"unknown action: {action}")
        except Exception as exc:
            QMessageBox.critical(self, "Power", f"{action} failed: {exc}")
            return
        msg = (
            result.get("message")
            if isinstance(result, dict)
            else f"{action} queued"
        )
        if self._power_status is not None:
            self._power_status.setText(
                f"{action} dispatched: {msg}\n"
                "If nothing happens within ~10 s, add a passwordless sudo "
                "entry for systemctl/poweroff/reboot (see "
                "nina/jetson_net/host_control.py for the drop-in)."
            )

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
