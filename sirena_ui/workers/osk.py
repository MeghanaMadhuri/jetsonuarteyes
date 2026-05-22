"""
Touchscreen on-screen-keyboard (OSK) integration for the Nina kiosk.

The Nina ships on a 10.1" 1024x600 capacitive touchscreen with no
physical keyboard, so any time a text field gets focus the operator
needs a virtual keyboard to type into it. This module wires that up
by:

  1. Installing a global QApplication event filter that watches for
     FocusIn events on text-input widgets (QLineEdit, QTextEdit,
     QPlainTextEdit, QSpinBox, QDoubleSpinBox, editable QComboBox,
     and anything with Qt.WA_InputMethodEnabled).
  2. The first time such a widget is focused, spawning the system
     OSK as a subprocess (Ubuntu ships `onboard` for this purpose;
     it's apt-installable and the kiosk installer does that for you).
  3. Leaving the OSK running for the rest of the session - the
     operator dismisses it via its own X button, or it's torn down
     when the GUI exits.
  4. If the operator dismisses it, the next FocusIn re-spawns it
     (we poll process.poll() before deciding whether to launch).

Behaviour is configurable via env vars - see the docstring on
`OnScreenKeyboardManager.__init__` for the full list. None of this
runs on dev hosts (Mac, headless CI) by default - if the configured
OSK binary isn't on PATH we log once and silently disable, so import
of this module is always safe.

Why a subprocess and not an embedded Qt widget:
  Embedding an OSK inside the app would mean reimplementing key
  layouts, accessibility, language support, and theming for every
  locale we ship in. `onboard` already does all of that, integrates
  with the X input methods, and the user can swap it for `florence`
  / `matchbox-keyboard` / etc. via NINA_UI_OSK_BIN without us
  caring.
"""
from __future__ import annotations

import logging
import os
import shlex
import shutil
import subprocess
import time
from typing import Iterable, Optional, Tuple

from PyQt5.QtCore import QEvent, QObject, Qt, QTimer
from PyQt5.QtWidgets import (
    QApplication,
    QAbstractSpinBox,
    QComboBox,
    QDialog,
    QLineEdit,
    QPlainTextEdit,
    QSpinBox,
    QTextEdit,
    QWidget,
)

# Optional widgets - QDoubleSpinBox lives in QtWidgets but if a future
# refactor renames things, we'd rather skip the type than fail import.
try:
    from PyQt5.QtWidgets import QDoubleSpinBox  # noqa: WPS433
except ImportError:  # pragma: no cover - PyQt5 always ships it today
    QDoubleSpinBox = None  # type: ignore[assignment]


log = logging.getLogger("sirena_ui.osk")


# Widgets we treat as "the user wants to type text" and that should
# pop up the OSK on focus. We list explicit classes (not a duck-typed
# hasattr) so a future read-only QLineEdit subclass that the user
# can't actually type into doesn't summon the keyboard.
_TEXT_INPUT_TYPES: Tuple[type, ...] = tuple(
    cls
    for cls in (
        QLineEdit,
        QTextEdit,
        QPlainTextEdit,
        QSpinBox,
        QDoubleSpinBox,
    )
    if cls is not None
)

# Touchscreens often deliver MouseButtonPress / TouchBegin without a
# reliable FocusIn on the inner QLineEdit (especially QSpinBox and
# editable QComboBox). Treat those the same as focus for OSK purposes.
_OSK_TRIGGER_EVENTS: Tuple[int, ...] = (
    QEvent.FocusIn,
    QEvent.MouseButtonPress,
    QEvent.TouchBegin,
)

# Modal dialogs often open without FocusIn on the line editor; Show is
# the reliable hook to focus the first field and raise the OSK.
_OSK_DIALOG_PREPARE_DELAY_MS = 120

# Debounce rapid duplicate events (mouse + FocusIn on the same tap).
_SHOW_DEBOUNCE_SEC = 0.35

# If the keyboard still is not up shortly after activation, retry once.
_SHOW_RETRY_DELAY_MS = 450

# Default onboard flags when NINA_UI_OSK_ARGS is unset (compact layout,
# no launcher entry — sized for the 1024×600 kiosk panel).
_DEFAULT_ONBOARD_ARGS: Tuple[str, ...] = (
    "--not-show-in-launcher",
    "--layout=Compact",
)


def _env_truthy(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in ("1", "true", "yes", "y", "on")


def _resolve_mode(raw: Optional[str]) -> str:
    """Normalise the NINA_UI_OSK env var. Recognised values:

      auto (default) - pop up on focus IF the OSK binary is on PATH;
                       silently disabled otherwise (matches dev-host
                       behaviour without an env var change).
      always         - keep the OSK running for the entire session,
                       independent of focus events. Useful when the
                       operator has a permanent docking position
                       configured in onboard.
      off            - never spawn the OSK. Useful on a kiosk that
                       has a real keyboard plugged in.
    """
    if raw is None:
        return "auto"
    val = raw.strip().lower()
    if val in ("auto", "always", "off"):
        return val
    log.warning(
        "Unknown NINA_UI_OSK=%r, falling back to 'auto'. "
        "Recognised: auto / always / off.",
        raw,
    )
    return "auto"


def activate_text_input(widget: QWidget) -> None:
    """Focus ``widget`` (or its text child) and show the OSK if enabled.

    Call from screens after building a modal or switching to an editor
    pane so the keyboard appears without requiring an extra tap.
    """
    app = QApplication.instance()
    if app is None:
        return
    osk = getattr(app, "_osk", None)
    if isinstance(osk, OnScreenKeyboardManager):
        osk.activate_text_input(widget)


def _env_int(name: str, default: int, lo: int, hi: int) -> int:
    raw = (os.environ.get(name) or "").strip()
    if not raw:
        return default
    try:
        return max(lo, min(hi, int(raw)))
    except ValueError:
        return default


def _kiosk_panel_height_px() -> int:
    raw = (os.environ.get("NINA_UI_PANEL_HEIGHT") or "600").strip()
    try:
        return max(360, int(raw))
    except ValueError:
        return 600


def _kiosk_keyboard_height_px() -> int:
    return _env_int("NINA_UI_OSK_HEIGHT", 220, 120, 400)


def _resolve_osk_binary(name: str) -> Optional[str]:
    """Resolve OSK binary even when systemd gives a minimal PATH."""
    if os.path.isabs(name) and os.access(name, os.X_OK):
        return name
    found = shutil.which(name)
    if found:
        return found
    for candidate in (f"/usr/bin/{name}", f"/usr/local/bin/{name}"):
        if os.access(candidate, os.X_OK):
            return candidate
    return None


def _split_args(raw: Optional[str]) -> Tuple[str, ...]:
    """Split a shell-style arg string into argv pieces. Empty / None
    -> no extra args. Used for NINA_UI_OSK_ARGS so the operator can
    pass `--theme=Nightshade --not-show-in-launcher` etc."""
    if not raw:
        return ()
    try:
        return tuple(shlex.split(raw))
    except ValueError as exc:
        log.warning("Could not parse NINA_UI_OSK_ARGS=%r: %s", raw, exc)
        return ()


class OnScreenKeyboardManager(QObject):
    """Pops up an OSK whenever a text-input widget gets focus.

    Lifetime is bound to the QApplication: the manager is parented to
    `app` so it goes away when the app does, and it connects to
    `app.aboutToQuit` to terminate the OSK subprocess at shutdown.

    Construct once after `QApplication` and before `window.show()`:

        app = QApplication(sys.argv)
        osk = OnScreenKeyboardManager(app)   # installs the filter
        ...

    Idempotent: calling `show()` while the OSK is already running is
    a no-op. Safe to use on dev hosts - if the OSK binary isn't
    available, the manager logs once and disables itself. No PyQt5
    state is mutated on the disabled path.
    """

    def __init__(
        self,
        app: QApplication,
        *,
        mode: Optional[str] = None,
        binary: Optional[str] = None,
        extra_args: Optional[Iterable[str]] = None,
    ) -> None:
        super().__init__(app)
        self._app = app

        self._mode = _resolve_mode(
            mode if mode is not None else os.environ.get("NINA_UI_OSK")
        )
        self._binary = (
            binary
            if binary is not None
            else os.environ.get("NINA_UI_OSK_BIN", "onboard")
        )
        self._binary_path = _resolve_osk_binary(self._binary)
        self._extra_args = (
            tuple(extra_args)
            if extra_args is not None
            else _split_args(os.environ.get("NINA_UI_OSK_ARGS"))
        )

        self._process: Optional[subprocess.Popen] = None
        self._enabled: bool = self._resolve_enabled()
        self._missing_binary_logged: bool = False
        # One-shot flag so we log the first FocusIn-on-text-widget exactly
        # once per session. That single log line is what proves to the
        # operator that the event filter is actually firing on touchscreen
        # taps - without it, "no keyboard appeared" could equally mean
        # the filter never saw a focus event OR onboard died on launch.
        self._first_focus_logged: bool = False
        # One-shot flag for the onboard window-mode gsettings tweak
        # (see _configure_onboard_window_mode). Only meaningful when
        # the binary is "onboard" - we deliberately do nothing for
        # custom OSK binaries because we don't know their config keys.
        self._onboard_configured: bool = False
        self._last_show_mono: float = -1e30
        self._last_activation_mono: float = -1e30
        self._last_activation_target_id: Optional[int] = None
        self._spawn_failures = 0
        self._show_retry_timer: Optional[QTimer] = None

        if not self._enabled:
            return

        # Filter goes on the QApplication so we see focus events from
        # every widget in every window/screen, including dialogs that
        # open later (Audio Editor, Face Enroll, etc.). Per-widget
        # installation would miss those.
        self._app.installEventFilter(self)
        self._app.focusChanged.connect(self._on_app_focus_changed)
        self._app.aboutToQuit.connect(self.shutdown)

        # 'always' mode launches immediately; auto waits for the first
        # FocusIn so the keyboard doesn't pop up over the Home screen
        # on a fresh boot.
        if self._mode == "always":
            self._spawn()
        print(
            f"[osk] manager enabled={self._enabled} mode={self._mode} "
            f"binary={self._binary_path or self._binary!r} "
            f"PATH={os.environ.get('PATH', '')[:120]}",
            flush=True,
        )
        log.info(
            "OnScreenKeyboardManager active mode=%s binary=%r extra_args=%s",
            self._mode, self._binary_path or self._binary, list(self._extra_args),
        )

    # ------------------------------------------------------------------
    # Public surface
    # ------------------------------------------------------------------

    @property
    def enabled(self) -> bool:
        """True iff an OSK will actually be spawned. Useful for the
        kiosk health screen / status pill in a future iteration."""
        return self._enabled

    @property
    def is_running(self) -> bool:
        """True iff the OSK subprocess is currently alive. Tests use
        this to assert spawn/teardown without poking the private
        member directly."""
        return self._process is not None and self._process.poll() is None

    def show(self, *, force: bool = False) -> None:
        """Ensure a single OSK is visible (spawn Nina-owned onboard, then raise)."""
        if not self._enabled:
            return
        now = time.monotonic()
        if not force and (now - self._last_show_mono) < _SHOW_DEBOUNCE_SEC:
            return
        self._last_show_mono = now

        if not self._is_onboard_binary():
            if self.is_running:
                return
            self._spawn()
            return

        self._configure_onboard_window_mode()

        if self.is_running:
            self._raise_onboard()
            self._schedule_show_retry()
            return

        # A stale session onboard on D-Bus often accepts Show but stays invisible
        # (docked off-screen, wrong WM stacking). Quit it and spawn our own.
        if self._onboard_dbus_name_owned():
            print("[osk] quitting stale session onboard on D-Bus", flush=True)
            self._quit_onboard_dbus()

        self._spawn()
        QTimer.singleShot(150, self._raise_onboard)
        QTimer.singleShot(400, self._raise_onboard)
        self._schedule_show_retry()

    def _schedule_show_retry(self) -> None:
        """One delayed retry when D-Bus Show may have lied (keyboard invisible)."""
        if not self._enabled or not self._is_onboard_binary():
            return
        timer = self._show_retry_timer
        if timer is not None and timer.isActive():
            return
        timer = QTimer(self)
        timer.setSingleShot(True)
        timer.timeout.connect(self._retry_show_after_activation)
        self._show_retry_timer = timer
        timer.start(_SHOW_RETRY_DELAY_MS)

    def _retry_show_after_activation(self) -> None:
        if not self._enabled:
            return
        if (time.monotonic() - self._last_activation_mono) > 1.5:
            return
        if self.is_running:
            return
        if self._raise_onboard():
            return
        self._hide_onboard()
        if self._raise_onboard():
            return
        self._quit_onboard_dbus()
        self._spawn()

    def activate_text_input(self, widget: QWidget) -> None:
        """Focus a text field and raise the OSK (dialogs / editor panes)."""
        if not self._enabled:
            return
        target = self._resolve_text_target(widget)
        if target is None and isinstance(widget, QWidget):
            if self._matches_text_input(widget):
                target = widget
        if target is None:
            return
        self._focus_for_keyboard(target)
        self._last_activation_target_id = id(target)
        self._last_activation_mono = time.monotonic()
        self.show(force=True)

    def shutdown(self) -> None:
        """Tear down the OSK subprocess and disconnect from the app.

        Safe to call repeatedly. After shutdown, the manager no longer
        listens for focus events - construct a fresh one if you need
        the OSK back. This explicit teardown is what lets tests run in
        the same QApplication session without each test leaking a live
        event filter into the next.
        """
        if self._app is not None:
            try:
                self._app.removeEventFilter(self)
            except Exception:
                pass
            try:
                self._app.focusChanged.disconnect(self._on_app_focus_changed)
            except Exception:
                pass
        self._kill_process()

    def _kill_process(self) -> None:
        """Terminate the OSK subprocess without uninstalling the event filter."""
        if self._process is None:
            return
        if self._process.poll() is None:
            try:
                self._process.terminate()
                self._process.wait(timeout=2.0)
            except Exception:
                try:
                    self._process.kill()
                except Exception:
                    pass
        self._process = None

    # ------------------------------------------------------------------
    # Qt event filter
    # ------------------------------------------------------------------

    def _on_app_focus_changed(self, _old: Optional[QWidget], new: Optional[QWidget]) -> None:
        """Backup path when FocusIn events are swallowed by scroll areas."""
        if new is None or not self._enabled:
            return
        try:
            target = self._resolve_text_target(new)
            if target is None:
                return
            self._on_text_widget_activated(target, QEvent.FocusIn)
        except Exception as exc:  # noqa: BLE001
            log.warning("OSK focusChanged handler raised: %s", exc)

    def eventFilter(self, obj: QObject, event) -> bool:  # type: ignore[override]
        """Spawn or raise the OSK when the operator taps a text field.

        We deliberately do NOT consume the event (return False) so
        normal Qt focus handling proceeds untouched. Errors inside
        the spawn path are swallowed and disabling the manager - a
        broken OSK must never break the app.
        """
        try:
            if event.type() == QEvent.Show and isinstance(obj, QDialog):
                QTimer.singleShot(
                    _OSK_DIALOG_PREPARE_DELAY_MS,
                    lambda dlg=obj: self._prepare_dialog(dlg),
                )
                return False
            if event.type() not in _OSK_TRIGGER_EVENTS:
                return False
            if event.type() == QEvent.MouseButtonPress:
                btn = getattr(event, "button", lambda: Qt.LeftButton)()
                if btn != Qt.LeftButton:
                    return False
            target = self._resolve_text_target(obj)
            if target is None:
                return False
            self._on_text_widget_activated(target, event.type())
        except Exception as exc:  # noqa: BLE001 - never propagate from filter
            log.warning("OSK event filter raised: %s", exc)
        return False

    def _on_text_widget_activated(self, target: QWidget, event_type: int) -> None:
        target_id = id(target)
        now = time.monotonic()
        if (
            event_type == QEvent.FocusIn
            and self._last_activation_target_id == target_id
            and (now - self._last_activation_mono) < _SHOW_DEBOUNCE_SEC
        ):
            return
        self._last_activation_target_id = target_id
        self._last_activation_mono = now

        if event_type != QEvent.FocusIn:
            self._focus_for_keyboard(target)
        if not self._first_focus_logged:
            msg = (
                f"OSK: first text-widget activation ({type(target).__name__}, "
                f"event={int(event_type)}) - calling show()"
            )
            print(f"[osk] {msg}", flush=True)
            log.info(msg)
            self._first_focus_logged = True
        # Touch / mouse taps bypass show debounce — FocusIn alone is debounced.
        force_show = event_type != QEvent.FocusIn
        self.show(force=force_show)

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _resolve_enabled(self) -> bool:
        if self._mode == "off":
            log.info("OnScreenKeyboardManager disabled via NINA_UI_OSK=off")
            return False
        if self._binary_path is None:
            log.warning(
                "On-screen keyboard %r not found on PATH - touchscreen text "
                "entry will not pop up a keyboard. Install with "
                "`sudo apt install onboard` (or set NINA_UI_OSK_BIN to a "
                "different OSK binary, or NINA_UI_OSK=off to silence this). "
                "PATH=%r",
                self._binary,
                os.environ.get("PATH", ""),
            )
            print(
                f"[osk] DISABLED: binary {self._binary!r} not found "
                f"(PATH={os.environ.get('PATH', '')})",
                flush=True,
            )
            return False
        return True

    @staticmethod
    def _matches_text_input(obj: QObject) -> bool:
        """True when ``obj`` itself is a text-entry target."""
        if isinstance(obj, (QLineEdit, QTextEdit, QPlainTextEdit)):
            return obj.isEnabled() and not obj.isReadOnly()
        if isinstance(obj, QAbstractSpinBox):
            return obj.isEnabled()
        if isinstance(obj, QComboBox) and obj.isEditable():
            return obj.isEnabled()
        if isinstance(obj, QWidget) and obj.testAttribute(Qt.WA_InputMethodEnabled):
            if not obj.isEnabled():
                return False
            policy = obj.focusPolicy()
            if policy not in (Qt.NoFocus,):
                return True
        return False

    @classmethod
    def _resolve_text_target(cls, obj: QObject) -> Optional[QWidget]:
        """Walk ancestors so taps on spinbox internals still count."""
        widget = obj if isinstance(obj, QWidget) else None
        while widget is not None:
            if cls._matches_text_input(widget):
                return widget
            widget = widget.parent()
        return None

    @staticmethod
    def _focus_for_keyboard(widget: QWidget) -> None:
        """Ensure the actual line editor has focus before onboard shows."""
        try:
            if isinstance(widget, QAbstractSpinBox):
                editor = widget.lineEdit()
                if editor is not None:
                    editor.setFocus(Qt.MouseFocusReason)
                    return
            if isinstance(widget, QComboBox) and widget.isEditable():
                editor = widget.lineEdit()
                if editor is not None:
                    editor.setFocus(Qt.MouseFocusReason)
                    return
            widget.setFocus(Qt.MouseFocusReason)
        except Exception:
            pass

    @staticmethod
    def _is_onboard_binary(binary: str) -> bool:
        return os.path.basename(binary) == "onboard"

    def _is_onboard_binary(self) -> bool:
        return self._is_onboard_binary(self._binary)

    def _onboard_dbus_name_owned(self) -> bool:
        """True when a session onboard service is already registered."""
        if not self._is_onboard_binary():
            return False
        if shutil.which("dbus-send") is None:
            return False
        try:
            result = subprocess.run(
                [
                    "dbus-send",
                    "--print-reply",
                    "--dest=org.freedesktop.DBus",
                    "/org/freedesktop/DBus",
                    "org.freedesktop.DBus.NameHasOwner",
                    "string:org.onboard.Onboard",
                ],
                capture_output=True,
                text=True,
                timeout=1.0,
            )
            if result.returncode != 0:
                return False
            return "boolean true" in (result.stdout or "").lower()
        except Exception:
            return False

    def _prepare_dialog(self, dialog: QDialog) -> None:
        """Focus the first text field when a modal opens."""
        if not self._enabled:
            return
        try:
            if not dialog.isVisible():
                return
        except RuntimeError:
            return
        target = self._find_first_text_input(dialog)
        if target is None:
            return
        if not self._first_focus_logged:
            log.info(
                "OSK: dialog %r opened with text field %s — activating keyboard",
                dialog.windowTitle(),
                type(target).__name__,
            )
            self._first_focus_logged = True
        self.activate_text_input(target)

    def _find_first_text_input(self, root: QWidget) -> Optional[QWidget]:
        """First enabled text widget in tab order under ``root``."""
        for child in root.findChildren(QWidget):
            if self._matches_text_input(child):
                return child
        return None

    def _quit_onboard_dbus(self) -> bool:
        """Stop a stale session-owned onboard so a fresh spawn can take over."""
        if not self._is_onboard_binary() or shutil.which("dbus-send") is None:
            return False
        try:
            result = subprocess.run(
                [
                    "dbus-send",
                    "--type=method_call",
                    "--dest=org.onboard.Onboard",
                    "/org/onboard/Onboard/Keyboard",
                    "org.onboard.Onboard.Keyboard.Quit",
                ],
                capture_output=True,
                text=True,
                timeout=2.0,
            )
            return result.returncode == 0
        except Exception as exc:  # noqa: BLE001
            log.debug("OSK: dbus Quit failed: %s", exc)
            return False

    def _hide_onboard(self) -> bool:
        """Hide onboard via D-Bus so a subsequent Show can unstick a dock."""
        if not self._is_onboard_binary() or shutil.which("dbus-send") is None:
            return False
        try:
            result = subprocess.run(
                [
                    "dbus-send",
                    "--type=method_call",
                    "--dest=org.onboard.Onboard",
                    "/org/onboard/Onboard/Keyboard",
                    "org.onboard.Onboard.Keyboard.Hide",
                ],
                capture_output=True,
                text=True,
                timeout=2.0,
            )
            return result.returncode == 0
        except Exception as exc:  # noqa: BLE001
            log.debug("OSK: dbus Hide failed: %s", exc)
            return False

    def _raise_onboard(self) -> bool:
        """Show an already-running onboard via D-Bus and wmctrl (best effort)."""
        if not self._is_onboard_binary():
            return False
        raised = False
        if shutil.which("dbus-send") is not None:
            try:
                result = subprocess.run(
                    [
                        "dbus-send",
                        "--type=method_call",
                        "--dest=org.onboard.Onboard",
                        "/org/onboard/Onboard/Keyboard",
                        "org.onboard.Onboard.Keyboard.Show",
                    ],
                    capture_output=True,
                    text=True,
                    timeout=2.0,
                )
                raised = result.returncode == 0
            except Exception as exc:  # noqa: BLE001
                log.debug("OSK: dbus Show failed: %s", exc)
        if self._raise_onboard_with_wmctrl():
            raised = True
        return raised

    def _raise_onboard_with_wmctrl(self) -> bool:
        wmctrl = shutil.which("wmctrl")
        if not wmctrl:
            return False
        for title in ("Onboard", "onboard"):
            try:
                result = subprocess.run(
                    [wmctrl, "-a", title],
                    capture_output=True,
                    text=True,
                    timeout=2.0,
                )
                if result.returncode == 0:
                    return True
            except Exception:
                pass
        return False

    def _configure_onboard_window_mode(self) -> None:
        """One-shot gsettings tweak so onboard renders above the kiosk.

        Two writes:

        - `org.onboard.window force-to-top = true`
            Onboard claims `_NET_WM_STATE_ABOVE` so the WM keeps it
            above the kiosk window.

        - `org.onboard.window docking-enabled = false`
            Force-clear the dock flag. We don't want it set, but a
            previous version of this file (commit 408f1e0) DID set
            it true, and dconf is persistent - upgrading past that
            commit doesn't undo the bad value, the keyboard ends up
            in dock+auto-hide mode and stays invisible. Writing
            false here lets a `git pull` actually fix the issue.
            Dock+auto-hide breaks because Qt apps don't reliably
            emit the AT-SPI focus events onboard's auto-show needs,
            so the dock stays alive but hidden after the first use.
            Plain floating-window mode means each spawn pops a
            fresh visible keyboard, dismissal cleanly exits the
            process, and the next FocusIn re-spawns it.

        Best-effort: if gsettings is missing (no GNOME stack) or
        the schema isn't installed (different OSK binary, partial
        install), we log once and carry on. onboard will still come
        up - the kiosk's manual-sized non-fullscreen window already
        lets ABOVE-state windows stack above it.

        Only runs for the literal binary "onboard" - custom OSK
        binaries pointed at via NINA_UI_OSK_BIN don't get touched
        because we don't know their config schema.
        """
        if self._onboard_configured:
            return
        self._onboard_configured = True  # set first so a failed run
        # doesn't loop on every spawn

        binary_basename = os.path.basename(self._binary)
        if binary_basename != "onboard":
            return
        if shutil.which("gsettings") is None:
            log.info(
                "OSK: gsettings not on PATH - skipping onboard window-mode "
                "config. The keyboard may render below the kiosk window or "
                "stay invisible if a prior session left docking-enabled=true "
                "in dconf. Install glib2.0-bin (provides gsettings) to enable "
                "the force-to-top + dock-clear auto-config."
            )
            return

        tweaks = (
            ("org.onboard.window", "force-to-top", "true"),
            # docking-enabled MUST be false - see docstring for why.
            # This is a remediation write, not a feature toggle.
            ("org.onboard.window", "docking-enabled", "false"),
            # Nina shows/hides the keyboard explicitly; AT-SPI auto-show
            # would stack a second keyboard on top of our D-Bus Show/spawn.
            ("org.onboard.auto-show", "enabled", "false"),
        )
        panel_h = _kiosk_panel_height_px()
        kb_h = _kiosk_keyboard_height_px()
        kb_y = max(0, panel_h - kb_h)
        tweaks += (
            ("org.onboard.window.landscape", "width", "1024"),
            ("org.onboard.window.landscape", "height", str(kb_h)),
            ("org.onboard.window.landscape", "x", "0"),
            ("org.onboard.window.landscape", "y", str(kb_y)),
        )
        for schema, key, value in tweaks:
            try:
                # Short timeout so a hung dconf service can't stall the
                # whole UI startup. capture_output keeps gsettings'
                # error chatter out of launch.log unless the call
                # actually fails - then we surface stderr explicitly.
                result = subprocess.run(
                    ["gsettings", "set", schema, key, value],
                    capture_output=True,
                    text=True,
                    timeout=2.0,
                )
                if result.returncode != 0:
                    log.info(
                        "OSK: gsettings set %s %s %s -> rc=%s stderr=%r "
                        "(non-fatal; onboard may still come up but stacking "
                        "behaviour will be whatever the user has configured)",
                        schema, key, value, result.returncode,
                        (result.stderr or "").strip(),
                    )
            except Exception as exc:  # noqa: BLE001
                log.info(
                    "OSK: gsettings set %s %s failed (%s) - skipping",
                    schema, key, exc,
                )

    def _spawn(self) -> None:
        """Start the OSK subprocess. Failures disable the manager.

        stderr is intentionally NOT redirected to DEVNULL - we let
        onboard's complaints (no DISPLAY, no D-Bus session bus, missing
        layout file, etc.) bubble up to the parent's stderr so they
        land in launch.log / journalctl. The previous DEVNULL silenced
        every "I died because X" message and made "no keyboard" cases
        impossible to diagnose without bench access. stdin/stdout do
        get muted because onboard is a GUI app whose prompts and
        startup chatter aren't useful and would muddy the launcher
        log.
        """
        # Apply onboard's force-to-top + docking config exactly once
        # before its first launch. Done inline (not in __init__) so
        # we don't pay the gsettings cost on dev hosts where the OSK
        # is configured but never triggered, and so the tweak runs
        # only when we actually intend to spawn onboard.
        self._configure_onboard_window_mode()

        argv = [self._binary_path or self._binary, *self._extra_args]
        if self._is_onboard_binary() and not self._extra_args:
            argv.extend(_DEFAULT_ONBOARD_ARGS)
        try:
            self._process = subprocess.Popen(
                argv,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                # stderr inherited so launch.log captures onboard startup errors.
                start_new_session=True,
            )
            print(f"[osk] launched: {' '.join(argv)} pid={self._process.pid}", flush=True)
            log.info("OSK launched: %s (pid=%s)", " ".join(argv), self._process.pid)
        except FileNotFoundError:
            # Race: shutil.which said yes but exec failed. Disable so
            # we don't keep retrying on every focus event.
            if not self._missing_binary_logged:
                log.warning("OSK binary %r vanished between check and spawn", self._binary)
                self._missing_binary_logged = True
            self._enabled = False
            self._process = None
            return
        except Exception as exc:  # noqa: BLE001
            log.warning("Failed to spawn OSK %r: %s", self._binary, exc)
            self._enabled = False
            self._process = None
            return

        # Schedule a one-shot health check 500 ms after spawn. If the
        # OSK process exited that fast it never came up - log the exit
        # code and disable the manager so we don't spawn-storm onboard
        # on every subsequent FocusIn (which would also re-trigger
        # whatever environmental problem killed it). Done via QTimer
        # so we don't block the GUI thread, and only registered when
        # we actually have a Qt event loop available (the manager is
        # parented to QApplication, so this is always true at runtime;
        # tests construct the manager without an event loop and would
        # hit the QTimer path harmlessly - the singleShot just fires
        # later if/when an event loop runs).
        try:
            QTimer.singleShot(500, self._check_spawn_health)
        except Exception:
            pass

    def _check_spawn_health(self) -> None:
        """Called ~500 ms after `_spawn()` to detect immediate-death.

        If the OSK is still alive, do nothing. If it died, log the
        return code and disable the manager - nine times out of ten
        the cause is environmental (no DISPLAY, no D-Bus, conflicting
        keyboard already grabbing the input device) and won't fix
        itself on a retry. With the manager disabled, focus events
        no longer trigger spawn attempts, the GUI keeps working, and
        the operator gets one clear log line to act on instead of
        an infinitely-restarting onboard subprocess.
        """
        if self._process is None:
            return
        rc = self._process.poll()
        if rc is None:
            self._spawn_failures = 0
            return
        self._spawn_failures += 1
        log.warning(
            "OSK %r exited %s within 500 ms of spawn (failure %d/3). "
            "Try running %r from a terminal to see why; common causes "
            "are missing DISPLAY env on the systemd user service, no D-Bus "
            "session bus, or a conflicting OSK already holding the input grab.",
            self._binary,
            rc,
            self._spawn_failures,
            self._binary,
        )
        self._process = None
        if self._spawn_failures >= 3:
            log.warning(
                "OSK disabled after %d immediate-death spawns — "
                "fix the environment and restart Nina.",
                self._spawn_failures,
            )
            self._enabled = False
