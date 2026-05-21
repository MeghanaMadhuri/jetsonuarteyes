"""Full-screen startup splash (``assets/nina_splash.mp4``), Android parity."""

from __future__ import annotations

import logging
import os
import subprocess
from pathlib import Path
from typing import Callable, Optional

from PyQt5.QtCore import Qt, QTimer, pyqtSignal
from PyQt5.QtGui import QPalette, QColor
from PyQt5.QtWidgets import QLabel, QVBoxLayout, QWidget

log = logging.getLogger("sirena_ui.splash_video")


def _repo_asset_path(name: str) -> Path:
    """``assets/`` at repository root (sibling of ``sirena_ui/``)."""
    return Path(__file__).resolve().parents[2] / "assets" / name


def splash_video_path() -> Optional[Path]:
    """Resolve ``nina_splash.mp4`` (env override, repo ``assets/``, UI bundle)."""
    override = (os.environ.get("NINA_UI_SPLASH_VIDEO") or "").strip()
    if override:
        p = Path(override)
        if p.is_file():
            return p
    for candidate in (
        _repo_asset_path("nina_splash.mp4"),
        Path(__file__).resolve().parents[1] / "assets" / "nina_splash.mp4",
    ):
        if candidate.is_file():
            return candidate
    return None


class SplashScreen(QWidget):
    """Borderless splash; emits ``finished`` when video ends or on timeout/error."""

    finished = pyqtSignal()

    def __init__(
        self,
        video_path: Path,
        *,
        max_ms: int = 45_000,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self._video_path = video_path
        self._done = False
        self._external_proc: Optional[subprocess.Popen] = None

        self.setWindowFlags(
            Qt.Window | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint
        )
        self.setAttribute(Qt.WA_DeleteOnClose, True)
        pal = self.palette()
        pal.setColor(QPalette.Window, QColor(0, 0, 0))
        self.setPalette(pal)
        self.setAutoFillBackground(True)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self._fallback = QLabel("Nina", self)
        self._fallback.setAlignment(Qt.AlignCenter)
        self._fallback.setStyleSheet(
            "color: #c8102e; font-size: 42px; font-weight: 700;"
            " background: transparent;"
        )
        self._fallback.hide()
        layout.addWidget(self._fallback)

        self._guard = QTimer(self)
        self._guard.setSingleShot(True)
        self._guard.timeout.connect(self._finish)
        self._guard.start(max(3000, int(max_ms)))

        self._player = None
        self._video_widget = None
        self._started = False

    def showEvent(self, event) -> None:
        super().showEvent(event)
        if self._started:
            return
        self._started = True
        QTimer.singleShot(0, self._start_playback)

    def _start_playback(self) -> None:
        if self._try_qt_multimedia():
            return
        if self._try_external_player():
            return
        log.warning("splash: no video backend; showing brief placeholder")
        self._fallback.show()
        QTimer.singleShot(1500, self._finish)

    def _try_qt_multimedia(self) -> bool:
        try:
            from PyQt5.QtMultimedia import QMediaContent, QMediaPlayer
            from PyQt5.QtMultimediaWidgets import QVideoWidget
            from PyQt5.QtCore import QUrl
        except Exception as exc:
            log.debug("splash: QtMultimedia unavailable: %s", exc)
            return False

        try:
            video = QVideoWidget(self)
            video.setSizePolicy(
                video.sizePolicy().horizontalPolicy(),
                video.sizePolicy().verticalPolicy(),
            )
            self.layout().addWidget(video, stretch=1)
            player = QMediaPlayer(self)
            player.setVideoOutput(video)
            player.setMedia(QMediaContent(QUrl.fromLocalFile(str(self._video_path))))
            player.mediaStatusChanged.connect(self._on_media_status)
            player.error.connect(self._on_player_error)  # type: ignore[attr-defined]
            player.stateChanged.connect(self._on_state_changed)
            self._player = player
            self._video_widget = video
            player.play()
            return True
        except Exception as exc:
            log.warning("splash: QMediaPlayer failed: %s", exc)
            return False

    def _on_media_status(self, status) -> None:
        try:
            from PyQt5.QtMultimedia import QMediaPlayer

            if status in (
                QMediaPlayer.EndOfMedia,
                QMediaPlayer.InvalidMedia,
            ):
                self._finish()
        except Exception:
            pass

    def _on_player_error(self, *_args) -> None:
        self._finish()

    def _on_state_changed(self, state) -> None:
        try:
            from PyQt5.QtMultimedia import QMediaPlayer

            if state == QMediaPlayer.StoppedState and self._player:
                if self._player.mediaStatus() == QMediaPlayer.EndOfMedia:
                    self._finish()
        except Exception:
            pass

    def _try_external_player(self) -> bool:
        path = str(self._video_path)
        for cmd in (
            ["ffplay", "-autoexit", "-nostats", "-loglevel", "quiet", "-fs", path],
            ["mpv", "--fs", "--really-quiet", path],
        ):
            try:
                proc = subprocess.Popen(cmd)
            except FileNotFoundError:
                continue
            except Exception as exc:
                log.debug("splash: %s failed: %s", cmd[0], exc)
                continue
            self._external_proc = proc
            self._poll = QTimer(self)
            self._poll.setInterval(200)
            self._poll.timeout.connect(self._poll_external)
            self._poll.start()
            return True
        return False

    def _poll_external(self) -> None:
        if self._external_proc is None:
            self._finish()
            return
        rc = self._external_proc.poll()
        if rc is not None:
            self._finish()

    def _finish(self) -> None:
        if self._done:
            return
        self._done = True
        if self._player is not None:
            try:
                self._player.stop()
            except Exception:
                pass
        if self._external_proc is not None:
            try:
                if self._external_proc.poll() is None:
                    self._external_proc.terminate()
            except Exception:
                pass
        self.finished.emit()
        self.close()

    def closeEvent(self, event) -> None:
        self._finish()
        super().closeEvent(event)


def show_splash_then(
    *,
    on_finished: Callable[[], None],
    parent=None,
) -> bool:
    """Show splash if enabled and file exists; return True if splash was shown."""
    if os.environ.get("NINA_UI_SPLASH", "1").strip().lower() in (
        "0",
        "false",
        "no",
        "off",
    ):
        return False
    path = splash_video_path()
    if path is None:
        log.info("splash: nina_splash.mp4 not found — skipping")
        return False
    splash = SplashScreen(path, parent=parent)
    splash.finished.connect(on_finished)
    splash.showFullScreen()
    return True
