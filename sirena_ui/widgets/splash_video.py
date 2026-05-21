"""Full-screen startup splash; blocks until minimum display time + video end."""

from __future__ import annotations

import logging
import os
import subprocess
import time
from pathlib import Path
from typing import Optional

from PyQt5.QtCore import QEventLoop, Qt, QTimer, pyqtSignal
from PyQt5.QtGui import QColor, QPalette, QPixmap
from PyQt5.QtWidgets import QApplication, QLabel, QVBoxLayout, QWidget

from sirena_ui.styles import asset_path, repo_asset_path

log = logging.getLogger("sirena_ui.splash_video")

# Startup video: repository ``assets/nina_splash.mp4`` (Android parity).
SPLASH_VIDEO_NAME = "nina_splash.mp4"


def splash_video_path() -> Optional[Path]:
    """Resolve ``assets/nina_splash.mp4`` at repo root (optional env override)."""
    override = (os.environ.get("NINA_UI_SPLASH_VIDEO") or "").strip()
    if override:
        p = Path(override)
        if p.is_file():
            log.info("splash: using NINA_UI_SPLASH_VIDEO=%s", p)
            return p
    primary = Path(repo_asset_path(SPLASH_VIDEO_NAME))
    if primary.is_file():
        log.info("splash: using %s", primary)
        return primary
    log.warning(
        "splash: %s not found at %s (place video in repo assets/)",
        SPLASH_VIDEO_NAME,
        primary,
    )
    return None


def _splash_min_ms() -> int:
    raw = (os.environ.get("NINA_UI_SPLASH_MIN_MS") or "3500").strip()
    try:
        return max(1500, int(raw))
    except ValueError:
        return 3500


def _splash_post_ms() -> int:
    raw = (os.environ.get("NINA_UI_SPLASH_POST_MS") or "400").strip()
    try:
        return max(0, int(raw))
    except ValueError:
        return 400


class SplashScreen(QWidget):
    """Borderless splash; emits ``finished`` after video (if any) + minimum hold."""

    finished = pyqtSignal()

    def __init__(
        self,
        video_path: Optional[Path] = None,
        *,
        min_ms: int = 3500,
        post_ms: int = 400,
        max_ms: int = 60_000,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self._video_path = video_path
        self._min_ms = int(min_ms)
        self._post_ms = int(post_ms)
        self._t0 = time.monotonic()
        self._done = False
        self._finish_scheduled = False
        self._video_finished = video_path is None
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
        layout.setSpacing(0)

        self._logo = QLabel(self)
        self._logo.setAlignment(Qt.AlignCenter)
        self._logo.setStyleSheet("background: transparent;")
        pix = QPixmap(asset_path("nina.png"))
        if not pix.isNull():
            self._logo.setPixmap(
                pix.scaled(
                    320,
                    320,
                    Qt.KeepAspectRatio,
                    Qt.SmoothTransformation,
                )
            )
        else:
            self._logo.setText("Nina")
            self._logo.setStyleSheet(
                "color: #c8102e; font-size: 42px; font-weight: 700;"
                " background: transparent;"
            )
        layout.addStretch(1)
        layout.addWidget(self._logo, alignment=Qt.AlignCenter)
        self._status = QLabel("Loading Nina…", self)
        self._status.setAlignment(Qt.AlignCenter)
        self._status.setStyleSheet(
            "color: #8e8e93; font-size: 14px; background: transparent;"
            " padding-bottom: 48px;"
        )
        layout.addWidget(self._status)
        layout.addStretch(1)

        self._guard = QTimer(self)
        self._guard.setSingleShot(True)
        self._guard.timeout.connect(self._schedule_finish)
        self._guard.start(max(5000, int(max_ms)))

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
        if self._video_path is None:
            log.info("splash: no video file — logo hold only")
            self._schedule_finish()
            return
        if self._try_qt_multimedia():
            return
        if self._try_external_player():
            return
        log.warning("splash: no video backend — logo hold only")
        self._video_finished = True
        self._schedule_finish()

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
            video.setStyleSheet("background: #000000;")
            self.layout().insertWidget(0, video, stretch=1)
            player = QMediaPlayer(self)
            player.setVideoOutput(video)
            player.setMedia(
                QMediaContent(QUrl.fromLocalFile(str(self._video_path)))
            )
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
                self._on_video_done()
        except Exception:
            pass

    def _on_player_error(self, *_args) -> None:
        self._on_video_done()

    def _on_state_changed(self, state) -> None:
        try:
            from PyQt5.QtMultimedia import QMediaPlayer

            if state == QMediaPlayer.StoppedState and self._player:
                if self._player.mediaStatus() == QMediaPlayer.EndOfMedia:
                    self._on_video_done()
        except Exception:
            pass

    def _on_video_done(self) -> None:
        if self._video_finished:
            return
        self._video_finished = True
        if self._video_widget is not None:
            self._video_widget.hide()
        self._schedule_finish()

    def _try_external_player(self) -> bool:
        if os.environ.get("NINA_UI_SPLASH_EXTERNAL", "").strip().lower() not in (
            "1",
            "true",
            "yes",
            "on",
        ):
            return False
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
            self._on_video_done()
            return
        if self._external_proc.poll() is not None:
            self._on_video_done()

    def _schedule_finish(self) -> None:
        if self._finish_scheduled:
            return
        self._finish_scheduled = True
        elapsed_ms = (time.monotonic() - self._t0) * 1000.0
        delay_ms = max(0.0, float(self._min_ms) - elapsed_ms) + float(self._post_ms)
        QTimer.singleShot(int(delay_ms), self._emit_finished)

    def _emit_finished(self) -> None:
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
        self.hide()

    def closeEvent(self, event) -> None:
        if not self._done:
            self._emit_finished()
        super().closeEvent(event)


def run_startup_splash(app: QApplication) -> None:
    """Block until splash completes (logo + optional video). Gateway starts after."""
    if os.environ.get("NINA_UI_SPLASH", "1").strip().lower() in (
        "0",
        "false",
        "no",
        "off",
    ):
        return

    splash = SplashScreen(
        splash_video_path(),
        min_ms=_splash_min_ms(),
        post_ms=_splash_post_ms(),
    )
    loop = QEventLoop()
    splash.finished.connect(loop.quit)
    splash.finished.connect(splash.deleteLater)
    app._splash_screen = splash  # type: ignore[attr-defined]
    splash.showFullScreen()
    app.processEvents()
    loop.exec_()


def show_splash_then(*, on_finished, parent=None, app=None) -> bool:
    """Deprecated: use :func:`run_startup_splash` before creating ``MainWindow``."""
    del on_finished, parent, app
    return False
