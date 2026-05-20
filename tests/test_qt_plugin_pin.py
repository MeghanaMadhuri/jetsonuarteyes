from __future__ import annotations

import os

import pytest


def test_pin_qt_platform_plugins_overrides_cv2_path(monkeypatch) -> None:
    """OpenCV's pip wheel can rewrite QT_QPA_PLATFORM_PLUGIN_PATH to
    cv2/qt/plugins during import. The UI entrypoint must force it back to
    PyQt's own platform plugin dir before QApplication is constructed.
    """
    pytest.importorskip("PyQt5.QtCore")
    from sirena_ui.__main__ import _pin_qt_platform_plugins

    monkeypatch.setenv(
        "QT_QPA_PLATFORM_PLUGIN_PATH",
        "/home/nina/.local/lib/python3.10/site-packages/cv2/qt/plugins",
    )
    monkeypatch.setenv("QT_PLUGIN_PATH", "/bad/path")

    _pin_qt_platform_plugins()

    pinned = os.environ.get("QT_QPA_PLATFORM_PLUGIN_PATH", "")
    assert "cv2/qt/plugins" not in pinned
    assert pinned.endswith("platforms")
    assert os.environ.get("QT_PLUGIN_PATH") is None
    assert os.environ.get("QT_QPA_PLATFORM") == "xcb"

