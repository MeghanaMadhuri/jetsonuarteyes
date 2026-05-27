"""Edge voice assistant — push-to-talk mic → ASR → LLM → TTS on Jetson."""

from __future__ import annotations

from typing import Callable, Optional

from PyQt5.QtCore import Qt, QTimer
from PyQt5.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from sirena_ui.widgets.common import Breadcrumb, Card, CardTitle, MutedLabel, Pill
from sirena_ui.workers.nina_service import NinaService


class _MicButton(QPushButton):
    """Large mic control: press-and-hold to stream USB mic audio to ASR."""

    def __init__(self, parent=None) -> None:
        super().__init__("\U0001F3A4", parent)
        self.setObjectName("voiceMicButton")
        self.setCursor(Qt.PointingHandCursor)
        self.setCheckable(False)
        self.setMinimumSize(120, 120)
        self.setMaximumSize(140, 140)
        self.setStyleSheet(
            "QPushButton#voiceMicButton {"
            "  font-size: 52px;"
            "  border-radius: 60px;"
            "  background-color: #c41230;"
            "  color: white;"
            "  border: 3px solid #9e0e26;"
            "}"
            "QPushButton#voiceMicButton:pressed {"
            "  background-color: #8f0b1e;"
            "}"
            "QPushButton#voiceMicButton:disabled {"
            "  background-color: #b0b0b5;"
            "  border-color: #9a9a9f;"
            "}"
        )
        self._on_hold_start: Optional[Callable[[], None]] = None
        self._on_hold_end: Optional[Callable[[], None]] = None
        self.setAttribute(Qt.WA_AcceptTouchEvents, True)

    def set_hold_handlers(
        self,
        on_start: Callable[[], None],
        on_end: Callable[[], None],
    ) -> None:
        self._on_hold_start = on_start
        self._on_hold_end = on_end

    def _start_hold(self) -> None:
        if self._on_hold_start is not None:
            self._on_hold_start()

    def _end_hold(self) -> None:
        if self._on_hold_end is not None:
            self._on_hold_end()

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.LeftButton and self.isEnabled():
            self._start_hold()
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        if event.button() == Qt.LeftButton:
            self._end_hold()
        super().mouseReleaseEvent(event)

    def touchEvent(self, event) -> None:
        for point in event.touchPoints():
            if point.state() == Qt.TouchPointPressed and self.isEnabled():
                self._start_hold()
            elif point.state() == Qt.TouchPointReleased:
                self._end_hold()
        event.accept()


class VoiceScreen(QWidget):
    """Local voice loop triggered from the sidebar Voice screen."""

    def __init__(self, service: NinaService, parent=None) -> None:
        super().__init__(parent)
        self._service = service
        self._holding = False

        outer = QVBoxLayout(self)
        outer.setContentsMargins(10, 10, 10, 10)
        outer.setSpacing(8)

        top = QHBoxLayout()
        top.addWidget(Breadcrumb("Nina", "Voice"))
        top.addStretch(1)
        self._status_pill = Pill("Checking…", kind=Pill.KIND_WARN)
        top.addWidget(self._status_pill)
        outer.addLayout(top)

        outer.addWidget(CardTitle("Talk to Nina"))
        hint = MutedLabel(
            "Press and hold the microphone while you speak. Release when finished — "
            "your words go to on-device ASR, then Ollama, then TTS plays on this Jetson. "
            "Requires voice-edge services (see docs/NINA_VOICE_EDGE.md)."
        )
        hint.setWordWrap(True)
        outer.addWidget(hint)

        card = Card(spacing=12)
        outer.addWidget(card, stretch=1)

        self._mic_btn = _MicButton()
        self._mic_btn.set_hold_handlers(self._on_mic_press, self._on_mic_release)
        mic_row = QHBoxLayout()
        mic_row.addStretch(1)
        mic_row.addWidget(self._mic_btn)
        mic_row.addStretch(1)
        card.add_layout(mic_row)

        self._phase_label = QLabel("Hold mic to speak")
        self._phase_label.setAlignment(Qt.AlignCenter)
        self._phase_label.setStyleSheet("font-size: 14px; font-weight: 600; color: #1c1c1e;")
        card.add(self._phase_label)

        self._heard_label = MutedLabel("Heard: —")
        self._heard_label.setWordWrap(True)
        card.add(self._heard_label)

        self._reply_label = MutedLabel("Reply: —")
        self._reply_label.setWordWrap(True)
        card.add(self._reply_label)

        self._device_label = MutedLabel("")
        self._device_label.setWordWrap(True)
        outer.addWidget(self._device_label)

        self._poll = QTimer(self)
        self._poll.setInterval(2000)
        self._poll.timeout.connect(self._refresh_service_pill)

    def on_enter(self) -> None:
        ve = self._service.settings.voice_edge
        mic = ve.mic_device if ve.enabled else "—"
        self._device_label.setText(f"USB mic (ALSA): {mic}")
        self._service.set_voice_ui_callbacks(
            on_status=self._on_voice_status,
            on_transcript=self._on_voice_transcript,
            on_response=self._on_voice_response,
        )
        self._refresh_service_pill()
        self._poll.start()

    def on_leave(self) -> None:
        self._poll.stop()
        if self._holding:
            self._service.end_voice_ptt()
            self._holding = False
        self._service.cancel_voice_ptt()
        self._service.set_voice_ui_callbacks()

    def _on_mic_press(self) -> None:
        if self._holding:
            return
        err = self._service.begin_voice_ptt()
        if err:
            self._set_phase(err)
            self._status_pill.set_kind(Pill.KIND_ERROR)
            self._status_pill.setText("Unavailable")
            return
        self._holding = True
        self._mic_btn.setEnabled(False)
        self._set_phase("Listening…")

    def _on_mic_release(self) -> None:
        if not self._holding:
            return
        self._holding = False
        self._service.end_voice_ptt()
        self._set_phase("Processing…")

    def _on_voice_status(self, msg: str) -> None:
        # Callbacks may arrive off the GUI thread.
        QTimer.singleShot(0, lambda m=msg: self._set_phase(m))

    def _on_voice_transcript(self, text: str) -> None:
        QTimer.singleShot(0, lambda t=text: self._heard_label.setText(f"Heard: {t}"))

    def _on_voice_response(self, text: str) -> None:
        def _apply() -> None:
            self._reply_label.setText(f"Reply: {text}")
            self._set_phase("Hold mic to speak")
            self._mic_btn.setEnabled(True)

        QTimer.singleShot(0, _apply)

    def _set_phase(self, msg: str) -> None:
        self._phase_label.setText(msg)

    def _refresh_service_pill(self) -> None:
        st = self._service.voice_assistant_status()
        if not st.get("enabled"):
            self._status_pill.set_kind(Pill.KIND_WARN)
            self._status_pill.setText("Off")
            self._mic_btn.setEnabled(False)
            self._set_phase("Set NINA_VOICE_EDGE_ENABLE=1 in navigation.env")
            return
        busy = self._service.voice_ptt_busy()
        llm_ok = st.get("llm_ok")
        tts_ok = st.get("tts_ok")
        if busy:
            self._mic_btn.setEnabled(False)
            return
        if llm_ok and tts_ok:
            self._status_pill.set_kind(Pill.KIND_OK)
            self._status_pill.setText("Ready")
            self._mic_btn.setEnabled(True)
        else:
            self._status_pill.set_kind(Pill.KIND_WARN)
            self._status_pill.setText("Starting…")
            detail = st.get("detail") or "start voice-edge services"
            self._set_phase(detail)
            self._mic_btn.setEnabled(False)
