"""Top red Sirena header. Shows the current screen title in the centre."""

from __future__ import annotations

from typing import Optional

from PyQt5.QtCore import Qt, QTimer, pyqtSignal
from PyQt5.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSlider,
    QVBoxLayout,
    QWidget,
    QWidgetAction,
    QMenu,
)


class HeaderBar(QFrame):
    volume_changed = pyqtSignal(int)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("headerBar")
        self.setFixedHeight(44)

        h = QHBoxLayout(self)
        h.setContentsMargins(16, 4, 12, 4)
        h.setSpacing(10)

        self._left_spacer = QLabel("")
        self._left_spacer.setMinimumWidth(40)
        h.addWidget(self._left_spacer)

        h.addStretch(1)
        self._title = QLabel("")
        self._title.setObjectName("headerTitle")
        self._title.setAlignment(Qt.AlignCenter)
        h.addWidget(self._title)
        h.addStretch(1)

        self._wifi = QLabel("\u2706")
        self._wifi.setObjectName("headerTray")
        self._wifi.setStyleSheet(
            "color: white; font-size: 16px; padding: 0 6px;"
            " background-color: transparent;"
        )
        h.addWidget(self._wifi)

        self._battery = QLabel("\u25AE")
        self._battery.setStyleSheet(
            "color: white; font-size: 16px; padding: 0 6px;"
            " background-color: transparent;"
        )
        h.addWidget(self._battery)

        self._volume_btn = QPushButton("\U0001f50a")
        self._volume_btn.setObjectName("headerTray")
        self._volume_btn.setCursor(Qt.PointingHandCursor)
        self._volume_btn.setFixedSize(36, 36)
        self._volume_btn.setToolTip("System speaker volume")
        self._volume_btn.clicked.connect(self._open_volume_menu)
        h.addWidget(self._volume_btn)

        self._clock = QLabel("00:00")
        self._clock.setStyleSheet(
            "color: white; font-size: 14px; padding: 0 8px;"
            " background-color: transparent;"
        )
        h.addWidget(self._clock)

        self._menu = QPushButton("\u22EE")
        self._menu.setObjectName("headerTray")
        self._menu.setCursor(Qt.PointingHandCursor)
        self._menu.setFixedSize(36, 36)
        h.addWidget(self._menu)

        self._volume_menu = QMenu(self)
        self._volume_menu.setStyleSheet(
            "QMenu { background: white; border: 1px solid #e3e3e6; }"
        )
        vol_host = QWidget()
        vol_layout = QVBoxLayout(vol_host)
        vol_layout.setContentsMargins(12, 10, 12, 10)
        vol_layout.setSpacing(6)
        vol_title = QLabel("Jetson speaker")
        vol_title.setStyleSheet(
            "color: #1c1c1e; font-size: 13px; font-weight: 700;"
            " background: transparent;"
        )
        vol_layout.addWidget(vol_title)
        self._volume_hint = QLabel("")
        self._volume_hint.setWordWrap(True)
        self._volume_hint.setStyleSheet(
            "color: #6e6e73; font-size: 11px; background: transparent;"
        )
        vol_layout.addWidget(self._volume_hint)
        slider_row = QHBoxLayout()
        slider_row.setSpacing(8)
        self._volume_slider = QSlider(Qt.Horizontal)
        self._volume_slider.setRange(0, 100)
        self._volume_slider.setSingleStep(5)
        self._volume_slider.setPageStep(10)
        self._volume_slider.valueChanged.connect(self._emit_volume_changed)
        slider_row.addWidget(self._volume_slider, stretch=1)
        self._volume_pct_label = QLabel("0%")
        self._volume_pct_label.setFixedWidth(40)
        self._volume_pct_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self._volume_pct_label.setStyleSheet(
            "color: #1c1c1e; font-size: 12px; font-weight: 600;"
            " background: transparent;"
        )
        slider_row.addWidget(self._volume_pct_label)
        vol_layout.addLayout(slider_row)
        action = QWidgetAction(self._volume_menu)
        action.setDefaultWidget(vol_host)
        self._volume_menu.addAction(action)

        self._volume_available = False
        self._volume_busy = False

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._refresh_clock)
        self._timer.start(30_000)
        self._refresh_clock()

    def set_title(self, text: str) -> None:
        self._title.setText(text)

    def set_battery_text(self, text: str) -> None:
        """Show pack voltage in the header tray (e.g. ``26.3 V``)."""
        t = (text or "").strip()
        self._battery.setText(t if t else "\u25AE")
        self._battery.setToolTip(
            "Pack voltage (ADS1115)" if t else "Battery voltage unavailable"
        )

    def set_volume_state(
        self,
        pct: Optional[int],
        *,
        available: bool = True,
        hint: str = "",
    ) -> None:
        """Update tray icon + slider from system/app volume (read-only sync)."""
        self._volume_available = bool(available and pct is not None)
        self._volume_btn.setEnabled(self._volume_available)
        if not self._volume_available:
            self._volume_btn.setToolTip(
                hint or "Volume control unavailable (no ALSA/Pulse sink)"
            )
            return
        value = max(0, min(100, int(pct)))
        self._volume_btn.setToolTip(f"System speaker: {value}%")
        if self._volume_busy:
            return
        self._volume_slider.blockSignals(True)
        self._volume_slider.setValue(value)
        self._volume_slider.blockSignals(False)
        self._volume_pct_label.setText(f"{value}%")
        if hint:
            self._volume_hint.setText(hint)
        elif not self._volume_hint.text():
            self._volume_hint.setText("System output volume")

    def _emit_volume_changed(self, value: int) -> None:
        self._volume_busy = True
        self._volume_pct_label.setText(f"{value}%")
        self.volume_changed.emit(int(value))
        self._volume_busy = False

    def _open_volume_menu(self) -> None:
        if not self._volume_available:
            return
        pos = self._volume_btn.mapToGlobal(self._volume_btn.rect().bottomLeft())
        self._volume_menu.popup(pos)

    def _refresh_clock(self) -> None:
        from datetime import datetime

        self._clock.setText(datetime.now().strftime("%H:%M"))
