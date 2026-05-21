"""Full-screen startup splash using ``assets/nina_splash.mp4``."""

from __future__ import annotations

import logging
import os
import subprocess
import time
from pathlib import Path
from typing import Optional

from PyQt5.QtCore import QEventLoop, Qt, QTimer, pyqtSignal
from PyQt5.QtGui import QColor, QImage, QPalette, QPixmap
from PyQt5.QtWidgets import (
    QApplication,
    QLabel,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from sirena_ui.styles import asset_path, repo_asset_path

log = logging.getLogger("sirena_ui.splash_video")

SPLASH_VIDEO_NAME = "nina_splash.mp4"


def splash_video_path() -> Optional[Path]:
    """Resolve ``assets/nina_splash.mp4`` (env, repo root, package-relative)."""
    candidates: list[Path] = []

    override = (os.environ.get("NINA_UI_SPLASH_VIDEO") or "").strip()
    if override:
        candidates.append(Path(override))

    repo_root = (os.environ.get("NINA_REPO_ROOT") or "").strip()
    if repo_root:
        candidates.append(Path(repo_root) / "assets" / SPLASH_VIDEO_NAME)

    candidates.append(Path(repo_asset_path(SPLASH_VIDEO_NAME)))
    candidates.append(
        Path(__file__).resolve().parents[2] / "assets" / SPLASH_VIDEO_NAME
    )

    seen: set[str] = set()
    for raw in candidates:
        p = raw.expanduser().resolve()
        key = str(p)
        if key in seen:
            continue
        seen.add(key)
        if p.is_file():
            log.info("splash: using video %s", p)
            return p

    log.warning(
        "splash: %s not found (checked NINA_UI_SPLASH_VIDEO, NINA_REPO_ROOT, "
        "repo assets/)",
        SPLASH_VIDEO_NAME,
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
    """Full-screen ``nina_splash.mp4``; logo fallback only if video cannot play."""

    finished = pyqtSignal()

    def __init__(
        self,
        video_path: Optional[Path] = None,
        *,
        min_ms: int = 3500,
        post_ms: int = 400,
        max_ms: int = 90_000,
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
        self._opencv_cap = None
        self._opencv_timer: Optional[QTimer] = None

        self.setWindowFlags(
            Qt.Window | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint
        )
        self.setAttribute(Qt.WA_DeleteOnClose, True)
        pal = self.palette()
        pal.setColor(QPalette.Window, QColor(0, 0, 0))
        self.setPalette(pal)
        self.setAutoFillBackground(True)

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        self._stack = QStackedWidget(self)
        root.addWidget(self._stack)

        # Page 0 — video (Qt Multimedia or OpenCV frames).
        self._video_page = QWidget()
        self._video_page.setStyleSheet("background-color: #000000;")
        video_layout = QVBoxLayout(self._video_page)
        video_layout.setContentsMargins(0, 0, 0, 0)
        self._video_surface = QLabel()
        self._video_surface.setAlignment(Qt.AlignCenter)
        self._video_surface.setStyleSheet("background-color: #000000;")
        video_layout.addWidget(self._video_surface)
        self._stack.addWidget(self._video_page)

        # Page 1 — static fallback (only when no video file / all backends fail).
        self._brand_page = QWidget()
        self._brand_page.setStyleSheet("background-color: #000000;")
        brand_layout = QVBoxLayout(self._brand_page)
        brand_layout.setContentsMargins(0, 0, 0, 0)
        self._logo = QLabel()
        self._logo.setAlignment(Qt.AlignCenter)
        self._logo.setStyleSheet("background: transparent;")
        pix = QPixmap(asset_path("nina.png"))
        if not pix.isNull():
            self._logo.setPixmap(
                pix.scaled(320, 320, Qt.KeepAspectRatio, Qt.SmoothTransformation)
            )
        else:
            self._logo.setText("Nina")
            self._logo.setStyleSheet(
                "color: #c8102e; font-size: 42px; font-weight: 700;"
                " background: transparent;"
            )
        brand_layout.addStretch(1)
        brand_layout.addWidget(self._logo, alignment=Qt.AlignCenter)
        self._status = QLabel("Loading Nina…")
        self._status.setAlignment(Qt.AlignCenter)
        self._status.setStyleSheet(
            "color: #8e8e93; font-size: 14px; background: transparent;"
            " padding-bottom: 48px;"
        )
        brand_layout.addWidget(self._status)
        brand_layout.addStretch(1)
        self._stack.addWidget(self._brand_page)

        self._stack.setCurrentWidget(self._video_page if video_path else self._brand_page)

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
            log.info("splash: no video — static hold")
            self._stack.setCurrentWidget(self._brand_page)
            self._schedule_finish()
            return

        self._stack.setCurrentWidget(self._video_page)
        if self._try_qt_multimedia():
            return
        if self._try_opencv_playback():
            return
        if self._try_external_player():
            return
        log.warning("splash: all video backends failed — static hold")
        self._video_finished = True
        self._stack.setCurrentWidget(self._brand_page)
        self._schedule_finish()

    def _try_qt_multimedia(self) -> bool:
        try:
            from PyQt5.QtCore import QUrl
            from PyQt5.QtMultimedia import QMediaContent, QMediaPlayer
            from PyQt5.QtMultimediaWidgets import QVideoWidget
        except Exception as exc:
            log.info("splash: QtMultimedia import failed: %s", exc)
            return False

        try:
            video = QVideoWidget(self._video_page)
            video.setStyleSheet("background: #000000;")
            layout = self._video_page.layout()
            if layout is not None:
                layout.addWidget(video)
            self._video_surface.hide()

            player = QMediaPlayer(self)
            player.setVideoOutput(video)
            player.setMedia(
                QMediaContent(QUrl.fromLocalFile(str(self._video_path.resolve())))
            )
            player.mediaStatusChanged.connect(self._on_media_status)
            player.error.connect(self._on_player_error)  # type: ignore[attr-defined]
            player.stateChanged.connect(self._on_state_changed)
            self._player = player
            self._video_widget = video
            player.play()
            log.info("splash: QMediaPlayer started")
            return True
        except Exception as exc:
            log.warning("splash: QMediaPlayer failed: %s", exc)
            if self._video_widget is not None:
                self._video_widget.deleteLater()
                self._video_widget = None
            self._video_surface.show()
            return False

    def _try_opencv_playback(self) -> bool:
        try:
            import cv2
        except Exception as exc:
            log.info("splash: OpenCV unavailable: %s", exc)
            return False

        cap = cv2.VideoCapture(str(self._video_path.resolve()))
        if not cap.isOpened():
            log.warning("splash: OpenCV cannot open %s", self._video_path)
            return False

        fps = float(cap.get(cv2.CAP_PROP_FPS) or 0.0)
        if fps < 5.0 or fps > 120.0:
            fps = 30.0
        interval_ms = max(16, int(1000.0 / fps))

        self._opencv_cap = cap
        self._video_surface.show()
        if self._video_widget is not None:
            self._video_widget.hide()

        def _tick() -> None:
            if self._opencv_cap is None:
                return
            ok, frame = self._opencv_cap.read()
            if not ok:
                self._stop_opencv()
                self._on_video_done()
                return
            try:
                import cv2

                rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                h, w, ch = rgb.shape
                img = QImage(rgb.data, w, h, ch * w, QImage.Format_RGB888)
                pix = QPixmap.fromImage(img.copy())
                target = self._video_surface.size()
                if target.width() > 0 and target.height() > 0:
                    pix = pix.scaled(
                        target,
                        Qt.KeepAspectRatio,
                        Qt.SmoothTransformation,
                    )
                self._video_surface.setPixmap(pix)
            except Exception as exc:
                log.warning("splash: OpenCV frame render failed: %s", exc)
                self._stop_opencv()
                self._on_video_done()

        self._opencv_timer = QTimer(self)
        self._opencv_timer.timeout.connect(_tick)
        self._opencv_timer.start(interval_ms)
        log.info("splash: OpenCV playback @ %.1f fps", fps)
        return True

    def _stop_opencv(self) -> None:
        if self._opencv_timer is not None:
            self._opencv_timer.stop()
            self._opencv_timer = None
        if self._opencv_cap is not None:
            try:
                self._opencv_cap.release()
            except Exception:
                pass
            self._opencv_cap = None

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
        try:
            from PyQt5.QtMultimedia import QMediaPlayer

            if self._player is not None:
                log.warning(
                    "splash: QMediaPlayer error %s — %s",
                    self._player.error(),
                    self._player.errorString(),
                )
        except Exception:
            pass
        if self._try_opencv_playback():
            return
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
        self._stop_opencv()
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
        path = str(self._video_path.resolve())
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
        self._stop_opencv()
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
    """Block until splash completes."""
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
