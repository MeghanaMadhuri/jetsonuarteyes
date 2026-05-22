"""Entry point: `python3 -m sirena_ui` launches the Sirena Control Center."""

from __future__ import annotations

import atexit
import logging
import os
import sys
from sirena_ui.resource_limits import log_startup_limits, raise_nofile_limit

raise_nofile_limit()

from PyQt5.QtCore import QLibraryInfo, Qt
from PyQt5.QtGui import QIcon
from PyQt5.QtWidgets import QApplication

from sirena_ui.main_window import MainWindow
from sirena_ui.styles import STYLESHEET, asset_path
from sirena_ui.android_gateway.server import start_tablet_gateway
from sirena_ui.widgets.splash_video import run_startup_splash
from sirena_ui.workers.nina_service import NinaService
from sirena_ui.workers.osk import OnScreenKeyboardManager


def _env_truthy(name: str) -> bool:
    raw = os.environ.get(name, "").strip().lower()
    return raw in ("1", "true", "yes", "y", "on")


def _configure_logging() -> None:
    """Honour ``NINA_LOG_LEVEL`` so on-bot diagnostics actually reach stderr.

    Without this the UI never calls :func:`logging.basicConfig`, so every
    ``log.info(...)`` in the drive / hover / sensor stack is silently dropped
    by the root logger's WARNING default. Set ``NINA_LOG_LEVEL=INFO`` (or
    ``DEBUG``) to surface them while debugging.
    """
    raw = (os.environ.get("NINA_LOG_LEVEL") or "").strip().upper()
    if not raw:
        return
    level = getattr(logging, raw, None)
    if not isinstance(level, int):
        try:
            level = int(raw)
        except ValueError:
            return
    logging.basicConfig(
        level=level,
        format="%(asctime)s.%(msecs)03d %(levelname)s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
        force=True,
    )


def _configure_qt_display_scaling() -> None:
    """10.1\" kiosk: one logical pixel = one design pixel (1024×600).

    High-DPI scaling on Jetson often reports a fractional ``devicePixelRatio``
    and shrinks the whole UI even when the window is fullscreen. Dev desktops
    keep scaling enabled.
    """
    if _env_truthy("NINA_UI_FULLSCREEN"):
        os.environ.setdefault("QT_AUTO_SCREEN_SCALE_FACTOR", "0")
        os.environ.setdefault("QT_SCALE_FACTOR", "1")
        os.environ.setdefault("QT_ENABLE_HIGHDPI_SCALING", "0")
        QApplication.setAttribute(Qt.AA_DisableHighDpiScaling, True)
    else:
        QApplication.setAttribute(Qt.AA_EnableHighDpiScaling, True)
        QApplication.setAttribute(Qt.AA_UseHighDpiPixmaps, True)


def _pin_qt_platform_plugins() -> None:
    """Keep OpenCV's bundled Qt plugin directory from hijacking PyQt.

    The pip ``opencv-python`` wheel ships its own Qt plugins under
    ``cv2/qt/plugins`` and its import side-effect can rewrite
    ``QT_QPA_PLATFORM_PLUGIN_PATH``. On Jetson that directory often
    contains an ``xcb`` plugin compiled against a different Qt build than
    the system ``python3-pyqt5`` bindings, causing:

        Could not load the Qt platform plugin "xcb" in ".../cv2/qt/plugins"

    The launcher pins this too, but do it again in-process immediately
    before creating ``QApplication`` so any earlier ``cv2`` import cannot
    win. ``QT_QPA_PLATFORM_PLUGIN_PATH`` should point at the directory
    containing ``libqxcb.so`` itself (``.../plugins/platforms``), not the
    parent plugin root.
    """
    os.environ.pop("QT_PLUGIN_PATH", None)
    plugin_root = QLibraryInfo.location(QLibraryInfo.PluginsPath)
    platforms = os.path.join(plugin_root, "platforms") if plugin_root else ""
    if platforms and os.path.isfile(os.path.join(platforms, "libqxcb.so")):
        os.environ["QT_QPA_PLATFORM_PLUGIN_PATH"] = platforms
    os.environ.setdefault("QT_QPA_PLATFORM", "xcb")


def main() -> int:
    _configure_logging()
    log_startup_limits()
    _pin_qt_platform_plugins()
    _configure_qt_display_scaling()
    app = QApplication(sys.argv)
    app.setApplicationName("Sirena")
    app.setOrganizationName("Sirena Technologies")
    app.setWindowIcon(QIcon(asset_path("sirena_app_icon.png")))
    app.setStyleSheet(STYLESHEET)

    if _env_truthy("NINA_UI_CPROFILE"):
        import cProfile

        _pr = cProfile.Profile()
        _pr.enable()

        def _dump_cprofile() -> None:
            _pr.disable()
            out = os.environ.get(
                "NINA_UI_CPROFILE_OUT",
                "sirena_ui_cprofile.stats",
            )
            _pr.dump_stats(out)
            print(
                f"NINA_UI_CPROFILE: wrote {out} "
                f"(python -m pstats {out} / snakeviz)"
            )

        atexit.register(_dump_cprofile)

    # Touchscreen on-screen keyboard. Pops up `onboard` (or whatever
    # NINA_UI_OSK_BIN is set to) the first time a text-input widget
    # gets focus. Silently disabled on dev hosts that don't have a
    # touchscreen OSK installed - see workers/osk.py for the env-var
    # surface (NINA_UI_OSK=auto|always|off, NINA_UI_OSK_BIN,
    # NINA_UI_OSK_ARGS). Kept on `app` so it isn't garbage-collected
    # when main() returns.
    app._osk = OnScreenKeyboardManager(app)  # type: ignore[attr-defined]

    # Splash before gateway / MainWindow so nmcli logs and bus threads do not
    # cover the splash and QThreads are not started while splash is visible.
    run_startup_splash(app)

    service = NinaService()
    start_tablet_gateway(service)

    window = MainWindow(service)
    window.setWindowIcon(QIcon(asset_path("sirena_app_icon.png")))
    window.show()
    # Kiosk geometry is owned by MainWindow (frameless 1024×600). Calling
    # showFullScreen() here made showEvent's showNormal()+setGeometry shrink the UI.

    return app.exec_()


if __name__ == "__main__":
    raise SystemExit(main())
