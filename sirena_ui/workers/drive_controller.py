"""
Real BLDC drive controller for the Drive screen.

Wraps `nina.controllers.navigation_manager.NavigationManager` (Jetson GPIO
JYQD control) with a Qt-friendly worker so the UI never blocks on GPIO calls.
The public surface mirrors the old `DriveStub` exactly, so it is a drop-in
replacement:

  state_changed(dict)  signal
  state()              snapshot
  set_speed(pct)
  set_brake(on)
  set_reverse(on)
  drive(direction)     direction in {forward, back, left, right}
  turn_90(which)       \"left\" or \"right\" — timed partial in-place pivot (~15° default)
  stop()

Hardware-touching operations (init, brake, drive, stop, shutdown) are
serialised onto a dedicated worker thread via a command queue so:

  * **All** Nina UI motion (manual D-pad, bench straight tests, autonomy,
    goto, ArUco follow, face follow, Android HTTP momentary FWD/BACK when
    wired through ``DriveController``) runs the same hoverboard primitives:
    straight pulse series + kick/cruise fallbacks, timed ``turn_left`` /
    ``turn_right`` for Turn left/right buttons (~15° lean default), and asymmetric pivot duties
    (``NINA_HOVER_TURN_SLOW_WHEEL_PCT``) for held L/R and in-loop pivots.
  * `forward`/`backward` calls (which include a 0.1s settle sleep)
    don't stall the GUI.
  * `turn_left`/`turn_right` (which block for ~2s by design) run
    concurrently with UI updates.
  * Commands always execute in the order they were issued.

Pure state changes (speed, reverse) are applied synchronously since
they only affect the next drive command.

If the hardware backend is not available - typically when the GUI is
run on a developer Mac without `Jetson.GPIO` installed, or when the
PWM pins haven't been enabled via `jetson-io.py` - the controller
falls back to a "simulation" mode: the in-memory state machine still
updates so the screen behaves normally, but no PWM is sent. The
failure reason is exposed through `state()["driver_message"]` so the
UI can render an informative pill instead of pretending everything is
fine.
"""

from __future__ import annotations

import json
import logging
import os
import queue
import threading
import time
from pathlib import Path
from typing import Callable, Optional, Tuple

from PyQt5.QtCore import QObject, pyqtSignal

from nina.controllers.hoverboard_axis_drive import (
    HoverboardAxisDrive,
    _hover_turn_slow_wheel_pct,
)
from nina.controllers.navigation_manager import (
    DEFAULT_PINS,
    NavigationConfig,
    NavigationManager,
)
from nina.sensors.ads1115 import is_battery_motion_blocked
from nina.services.sensor_alert_audio import maybe_speak_low_battery

# `nav_manager` may be any object with the navigation surface (tests use fakes).
NavigationManagerLike = object


log = logging.getLogger("sirena_ui.drive")


_DIR_FORWARD = "forward"
_DIR_BACK = "back"
_DIR_LEFT = "left"
_DIR_RIGHT = "right"

_VALID_DIRECTIONS = {_DIR_FORWARD, _DIR_BACK, _DIR_LEFT, _DIR_RIGHT}


# Operator-facing speed envelope. The Yalu hub motors + JYQD drivers on
# the current Nina build are not safe to run above ~14% PWM duty on
# smooth floors — the wheels slip less and the bot runs faster than on
# carpet at the same duty. 8% is the lowest we ship as the GUI floor;
# on some benches wheels may need a higher floor after a cold start.
#
#   * Manual drive uses a fixed in-range duty (`FIXED_MANUAL_DRIVE_SPEED_PCT`);
#     `set_speed()` still clamps for programmatic callers.
#   * Factory tests may pass `default_speed_percent` on the controller.
#
# Bump these together (and re-test on a wheels-up bench) when the
# mechanical build can handle more. They're module-level so screens /
# tests can import the same constants instead of re-deriving them.
#
# Note: `NavigationManager` also applies `NINA_NAV_START_KICK_PCT` from
# settings (default aligned with MAX_SPEED_PCT). If that env is left at
# an old high value (e.g. 35), every start-from-rest will pulse that
# duty and low GUI speeds will feel ignored.
MIN_SPEED_PCT = 8
MAX_SPEED_PCT = 14

# Single manual-drive duty (no slider): midpoint of the safe envelope.
FIXED_MANUAL_DRIVE_SPEED_PCT = (MIN_SPEED_PCT + MAX_SPEED_PCT) // 2

# When both wheels share the same **forward** direction, optional duty deltas
# (START / RUN, PWM points). Reverse (both backward), turns, and coast stay
# symmetric.
# Right: RUN=2 biases cruise / held forward (historical right-hub trim).
# Left: default 0; some builds need +1..+4 on the left for JYQD breakaway —
#       set ``NINA_DRIVE_LEFT_FWD_EXTRA_PP`` (see `_left_fwd_extra_pp()`).
RIGHT_WHEEL_EXTRA_START_PP = 0
RIGHT_WHEEL_EXTRA_RUN_PP = 2

# When manual drive begins from a full stop (`_active_drive` is None),
# apply a short kick at FROM_STOP_KICK_PCT, then drop to FROM_STOP_CRUISE_PCT
# so logs, lidar, and bench observation can characterise motion at a lower
# duty. The cruise value can be below MIN_SPEED_PCT; it is sent only to the
# nav layer (UI clamp does not apply to hardware PWM).
# Straight-line manual cruise from rest (D-pad W/S only; L/R pivots use
# DEFAULT_PIVOT_SPEED_PCT below).
FROM_STOP_KICK_PCT = 14
FROM_STOP_CRUISE_PCT = 5

# In-place pivot duty: D-pad A/D from rest and Drive "Turn left/right" buttons
# (unless overridden by env). Using the straight-line cruise (~5%) or the tiny
# manual midpoint (~11%) often fails to spin both hubs from rest.
DEFAULT_PIVOT_SPEED_PCT = 20


def _drive_pivot_speed_pct() -> int:
    """D-pad left/right from stop. Env ``NINA_DRIVE_PIVOT_PCT`` overrides."""
    raw = (os.environ.get("NINA_DRIVE_PIVOT_PCT") or "").strip()
    if raw:
        try:
            return max(MIN_SPEED_PCT, min(100, int(raw)))
        except ValueError:
            pass
    return int(DEFAULT_PIVOT_SPEED_PCT)


def _drive_turn_90_duration_sec(nav: Optional[object] = None) -> float:
    """Hold time for Drive **Turn left/right** buttons only.

    Does **not** use ``NavigationSettings.turn_duration_sec``
    on ``nav.config`` (that value was forcing long holds). Env only:

    ``NINA_DRIVE_TURN_90_SEC`` → else ``NINA_NAV_TURN_SEC`` (default **0.3** s).
    """
    _ = nav
    raw = (os.environ.get("NINA_DRIVE_TURN_90_SEC") or "").strip()
    if raw:
        try:
            return max(0.0, min(60.0, float(raw)))
        except ValueError:
            pass
    try:
        return max(0.0, min(60.0, float(os.environ.get("NINA_NAV_TURN_SEC", "0.3"))))
    except ValueError:
        return 0.3


def _left_fwd_extra_pp() -> int:
    """Extra left duty when both wheels command **forward** together.

    Mirrors the optional right bias for bots where the left hub/JYQD needs
    slightly more PWM to start rolling (opposite nudge + low cruise can leave
    the left side stalled while the right moves). Env-only; default 0.
    """
    raw = (os.environ.get("NINA_DRIVE_LEFT_FWD_EXTRA_PP") or "").strip()
    if raw:
        try:
            return max(0, min(20, int(raw)))
        except ValueError:
            pass
    return 0


def _drive_turn_90_speed_pct() -> int:
    raw = (os.environ.get("NINA_DRIVE_TURN_90_PCT") or "").strip()
    if raw:
        try:
            return max(MIN_SPEED_PCT, min(100, int(raw)))
        except ValueError:
            pass
    return int(DEFAULT_PIVOT_SPEED_PCT)


def _clamp_speed(pct: int) -> int:
    """Clamp `pct` into the operator-safe envelope. Negative / non-int
    inputs are coerced to MIN_SPEED_PCT rather than 0 - inside this
    project there's no legitimate caller asking for speed=0 (stops go
    through `set_brake()` / `stop()` which don't touch speed_pct), so
    treating speed=0 as "minimum cruise" is safer than letting it slip
    through as a literal halt that bypasses the brake state machine.
    """
    return max(MIN_SPEED_PCT, min(MAX_SPEED_PCT, int(pct)))


def _pair_duties_with_right_bias(
    left_dir: str,
    left_base: int,
    right_dir: str,
    right_base: int,
    *,
    start_phase: bool,
) -> Tuple[int, int]:
    """Apply optional left/right forward trim when both wheels move **forward**
    together. Reverse (both **backward**), opposite directions
    (turn-in-place), and coast (any zero duty) stay symmetric."""
    lb, rb = int(left_base), int(right_base)
    if lb == 0 and rb == 0:
        return 0, 0
    if left_dir != right_dir:
        return lb, rb
    if lb == 0 or rb == 0:
        return lb, rb
    if left_dir == NavigationManager.DIR_BACKWARD:
        return lb, rb
    extra_l = _left_fwd_extra_pp()
    extra_r = (
        RIGHT_WHEEL_EXTRA_START_PP if start_phase else RIGHT_WHEEL_EXTRA_RUN_PP
    )
    lb2 = max(0, min(100, int(lb) + int(extra_l)))
    rb2 = max(0, min(100, int(rb) + int(extra_r)))
    return lb2, rb2


def _hoverboard_pivot_outer_slow(sym_speed: int) -> Tuple[int, int]:
    """Outer / slow-wheel duties matching :meth:`HoverboardAxisDrive.turn_left`."""
    slow = _hover_turn_slow_wheel_pct()
    outer = max(int(sym_speed), slow)
    return outer, slow


def _apply_hoverboard_equal_pivot_blend(
    nav: object,
    ldir: str,
    rdir: str,
    left_speed: int,
    right_speed: int,
) -> Tuple[int, int]:
    """When both pivot sides use the same duty, split like timed ``turn_*``."""
    if getattr(nav, "DRIVER_LABEL", None) != HoverboardAxisDrive.DRIVER_LABEL:
        return left_speed, right_speed
    if left_speed != right_speed or left_speed <= 0:
        return left_speed, right_speed
    if ldir == rdir:
        return left_speed, right_speed
    outer, slow = _hoverboard_pivot_outer_slow(left_speed)
    if (
        ldir == HoverboardAxisDrive.DIR_FORWARD
        and rdir == HoverboardAxisDrive.DIR_BACKWARD
    ):
        return outer, slow
    if (
        ldir == HoverboardAxisDrive.DIR_BACKWARD
        and rdir == HoverboardAxisDrive.DIR_FORWARD
    ):
        return slow, outer
    return left_speed, right_speed


# Heartbeat interval for re-issuing the current SET while a D-pad
# button or arrow key is held. Re-writing the same duty is cheap on
# Jetson GPIO; the loop keeps one code path for held presses.
_HEARTBEAT_INTERVAL_SEC = 0.3
# If the worker queue already has more than this many commands
# pending, we skip enqueueing the next heartbeat tick instead of
# piling up. Prevents runaway growth if the worker stalls and heartbeats
# start landing slower than `_HEARTBEAT_INTERVAL_SEC`.
_HEARTBEAT_MAX_QUEUED = 2


# ---------------------------------------------------------------------
# Wheel-polarity persistence
#
# The Nina hardware comes off the bench with one or both motors phase-
# wired backward, depending on which JYQD got soldered to which hub
# motor. The historical fix was an env var (NINA_NAV_INVERT_LEFT /
# RIGHT) seeded into the kiosk systemd unit, which required SSHing in
# and re-running the installer every time. The Drive screen now exposes
# Flip L / Flip R toggles that can be flipped at runtime - the chosen
# polarity is persisted here so it survives a reboot of the Jetson.
#
# Precedence on startup:
#   1. ~/.config/sirena/drive_polarity.json if present
#   2. NINA_NAV_INVERT_LEFT / NINA_NAV_INVERT_RIGHT env vars
#   3. False (no flip)
# ---------------------------------------------------------------------


def _polarity_state_path() -> Path:
    """Where we persist the chosen wheel polarity. XDG-friendly."""
    base = os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config")
    return Path(base) / "sirena" / "drive_polarity.json"


def _env_truthy(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in ("1", "true", "yes", "y", "on")


def _load_persisted_polarity() -> Tuple[Optional[bool], Optional[bool]]:
    """Return `(invert_left, invert_right)` if the JSON file is present
    and well-formed; `(None, None)` otherwise (caller falls back to env
    vars). Logs but never raises - a corrupted file should never stop
    the GUI from booting."""
    path = _polarity_state_path()
    try:
        if not path.exists():
            return (None, None)
        with path.open("r", encoding="utf-8") as fh:
            data = json.load(fh)
        left = data.get("invert_left")
        right = data.get("invert_right")
        return (
            bool(left) if left is not None else None,
            bool(right) if right is not None else None,
        )
    except Exception as exc:  # noqa: BLE001 - persistence is best-effort
        log.warning("Could not read polarity state from %s: %s", path, exc)
        return (None, None)


def _save_persisted_polarity(invert_left: bool, invert_right: bool) -> None:
    """Write the polarity to disk so the next boot picks it up. Best-
    effort: a write failure logs a warning but does not raise, so the
    operator can still flip the toggle and drive (just with a one-shot
    setting that won't survive a restart)."""
    path = _polarity_state_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        # Write to a sibling temp file then rename so we never leave
        # half-written JSON on disk if the process is killed mid-write.
        tmp = path.with_suffix(path.suffix + ".tmp")
        with tmp.open("w", encoding="utf-8") as fh:
            json.dump(
                {
                    "invert_left": bool(invert_left),
                    "invert_right": bool(invert_right),
                },
                fh,
                indent=2,
            )
            fh.write("\n")
        tmp.replace(path)
    except Exception as exc:  # noqa: BLE001 - persistence is best-effort
        log.warning("Could not save polarity state to %s: %s", path, exc)


def _resolve_initial_polarity() -> Tuple[bool, bool]:
    """Highest-precedence source wins: persisted JSON, then env vars,
    then False. Centralised so DriveController and any future tools
    derive the same boot-time defaults."""
    left, right = _load_persisted_polarity()
    if left is None:
        left = _env_truthy("NINA_NAV_INVERT_LEFT")
    if right is None:
        right = _env_truthy("NINA_NAV_INVERT_RIGHT")
    return (bool(left), bool(right))


class DriveController(QObject):
    """Qt facade over `NavigationManager` for the Drive screen."""

    state_changed = pyqtSignal(dict)

    def __init__(
        self,
        config: Optional[NavigationConfig] = None,
        parent=None,
        *,
        nav_manager: Optional[NavigationManagerLike] = None,
        default_speed_percent: Optional[int] = None,
    ) -> None:
        """Construct the Qt-side facade.

        Two construction modes are supported:

          1. Local (legacy): pass a `NavigationConfig` (or nothing, to get
             env-driven defaults). DriveController instantiates
             `NavigationManager` itself when the worker thread runs `_do_init`.

          2. Factory-injected: pass a pre-built `nav_manager`. `_do_init` calls
             its `initialize()` instead of constructing one. Use this from
             `NinaService`.
        """
        super().__init__(parent)

        self._injected_nav: Optional[NavigationManagerLike] = nav_manager
        self._config = config or NavigationConfig(pins=DEFAULT_PINS)
        self._nav: Optional[NavigationManagerLike] = None
        self._init_attempted = False

        if default_speed_percent is not None:
            initial_speed = _clamp_speed(default_speed_percent)
        else:
            initial_speed = _clamp_speed(FIXED_MANUAL_DRIVE_SPEED_PCT)

        self._lock = threading.RLock()
        # Wheel polarity is resolved here (persisted JSON > env var >
        # False) and re-applied to the nav manager from `_do_init`.
        # That way the polarity survives a boot AND the operator can
        # flip it at runtime from the Drive screen without touching
        # the kiosk service or env vars.
        initial_invert_left, initial_invert_right = _resolve_initial_polarity()
        self._state = {
            "connected": False,
            "speed_pct": initial_speed,
            "direction": "idle",
            "brake": True,
            "reverse": False,
            "heading_deg": 0,
            "distance_m": 0.0,
            "driver_message": "",
            "invert_left": initial_invert_left,
            "invert_right": initial_invert_right,
        }

        # Last (left_dir, left_speed, right_dir, right_speed) that was
        # actually sent to the underlying nav backend. The heartbeat
        # thread replays this verbatim rather than re-deriving from
        # `state["direction"]` so the autonomous pilot's per-wheel
        # speeds (which can differ from the GUI slider) are preserved.
        # Cleared whenever the wheels are commanded to stop / brake /
        # estop so the heartbeat goes quiet between drives.
        self._active_drive: Optional[Tuple[str, int, str, int]] = None

        # Hoverboard: allow one straight FWD/BACK pulse series per "segment"
        # (until stop/brake/zero-wheels). Without this, autonomy would
        # restart ``start_pulse_straight_*`` every tick once ``_active_drive``
        # is cleared while the pulse thread runs.
        self._hover_straight_pulse_next: bool = True

        # All hardware-touching work runs on a single worker thread, in
        # the order commands were issued, so GUI clicks never collide
        # with a still-blocking turn.
        self._cmd_q: "queue.Queue[Optional[Callable[[], None]]]" = queue.Queue()
        self._stop_evt = threading.Event()
        self._worker = threading.Thread(
            target=self._worker_loop,
            name="DriveController",
            daemon=True,
        )
        self._worker.start()

        # Heartbeat thread: while a direction is active and the brake
        # is off, re-issues the current SET at _HEARTBEAT_INTERVAL_SEC.
        self._heartbeat_stop = threading.Event()
        self._heartbeat = threading.Thread(
            target=self._heartbeat_loop,
            name="DriveControllerHeartbeat",
            daemon=True,
        )
        self._heartbeat.start()

    # ------------------------------------------------------------------
    # Public API (matches the old DriveStub)
    # ------------------------------------------------------------------

    @property
    def connected(self) -> bool:
        with self._lock:
            return bool(self._state["connected"])

    def state(self) -> dict:
        with self._lock:
            return dict(self._state)

    def is_in_motion(self) -> bool:
        """True when wheels are commanded away from idle/brake neutral."""
        with self._lock:
            if self._state.get("brake", True):
                return False
            direction = str(self._state.get("direction", "idle"))
            if direction == "idle":
                return False
        active = self._active_drive
        if active is not None:
            _, ls, _, rs = active
            if ls > 0 or rs > 0:
                return True
        nav = self._nav if self._nav is not None else self._injected_nav
        if nav is not None and hasattr(nav, "is_straight_pulse_series_active"):
            try:
                if nav.is_straight_pulse_series_active():
                    return True
            except Exception:
                pass
        return False

    def nav_manager(self) -> Optional[NavigationManagerLike]:
        """Return the underlying navigation backend (live or injected pre-init).

        Used by ``NinaService`` to wire late-arriving IMU hooks into
        ``HoverboardAxisDrive`` without poking private attributes.
        """
        return self._nav if self._nav is not None else self._injected_nav

    def _should_start_straight_pulse(self, direction: str) -> bool:
        """Whether the next FWD/BACK command should run the hoverboard pulse series.

        Uses ``_hover_straight_pulse_next`` (same gate as :meth:`_do_drive_wheels`) so a
        pivot or timed turn followed immediately by straight FWD/BACK still gets the pulse
        algorithm even when ``_active_drive`` is still set from the prior pivot hold.
        """
        if direction not in (_DIR_FORWARD, _DIR_BACK):
            return False
        if not self.supports_forward_pulse():
            return False
        if getattr(self._nav, "is_forward_pulse_active", lambda: False)():
            return False
        with self._lock:
            return bool(self._hover_straight_pulse_next)

    def supports_forward_pulse(self) -> bool:
        """True when nav offers straight pulse series and ``pulse_forward_enabled`` is on.

        When true, symmetric D-pad forward uses ``start_pulse_straight_forward`` and symmetric
        backward uses ``start_pulse_straight_backward`` from rest (bench Straight / Straight back too).
        """
        with self._lock:
            nav = self._nav
        if nav is None:
            return False
        if not callable(getattr(nav, "start_pulse_straight_forward", None)):
            return False
        if not callable(getattr(nav, "start_pulse_straight_backward", None)):
            return False
        en = getattr(nav, "is_forward_pulse_enabled", None)
        return callable(en) and bool(en())

    def start_forward_pulse_bench(self, speed_pct: int) -> None:
        """Start hoverboard forward pulse (Straight bench forward); no-op if unavailable."""
        sp = max(0, min(100, int(speed_pct)))
        self._enqueue(lambda: self._do_start_forward_pulse_bench(sp))

    def start_backward_pulse_bench(self, speed_pct: int) -> None:
        """Start hoverboard backward pulse (Straight back bench); no-op if unavailable."""
        sp = max(0, min(100, int(speed_pct)))
        self._enqueue(lambda: self._do_start_backward_pulse_bench(sp))

    def _do_start_forward_pulse_bench(self, speed_pct: int) -> None:
        if self._refuse_if_battery_low("bench forward pulse"):
            return
        if self._nav is None or not self.supports_forward_pulse():
            return
        self._nav.start_pulse_straight_forward(int(speed_pct))
        with self._lock:
            self._active_drive = None
            self._hover_straight_pulse_next = False

    def _do_start_backward_pulse_bench(self, speed_pct: int) -> None:
        if self._refuse_if_battery_low("bench backward pulse"):
            return
        if self._nav is None or not self.supports_forward_pulse():
            return
        self._nav.start_pulse_straight_backward(int(speed_pct))
        with self._lock:
            self._active_drive = None
            self._hover_straight_pulse_next = False

    def ensure_hardware(self) -> None:
        """Kick off lazy initialisation of the BLDC drivers.

        Safe to call repeatedly; the worker dedupes via
        `_init_attempted` so re-entry from `on_enter()` is free.
        """
        self._enqueue(self._do_init)

    def shutdown(self) -> None:
        """Tear down the worker thread and release GPIO."""
        self._heartbeat_stop.set()
        self._enqueue(self._do_shutdown)
        self._cmd_q.put(None)
        self._stop_evt.set()
        # Best-effort join: both threads are daemon so we don't hang
        # shutdown forever if something inside NavigationManager wedges.
        self._worker.join(timeout=2.0)
        self._heartbeat.join(timeout=2.0)

    def set_speed(self, pct: int) -> None:
        pct = _clamp_speed(pct)
        with self._lock:
            self._state["speed_pct"] = pct
            direction = self._state["direction"]
            brake = self._state["brake"]
        self._emit_state()

        # If the wheels are currently moving, push the new duty cycle
        # straight through so the speed slider acts live. We deliberately
        # use `set_wheels` (no settle / no kick-start) here - those only
        # matter when starting from rest, and re-running them on every
        # slider tick would chop the motors. The order is preserved by
        # the worker queue so a still-pending start command will run
        # first and this update will follow.
        if not brake and direction != "idle":
            self._enqueue(
                lambda d=direction, s=pct: self._do_apply_live_speed(d, s)
            )

    def set_reverse(self, on: bool) -> None:
        # Reverse is interpreted as "swap forward/back at the hardware
        # layer", which is the intuitive meaning when the operator is
        # watching a rear-facing camera. Left/right are unaffected.
        with self._lock:
            self._state["reverse"] = bool(on)
        self._emit_state()

    def set_invert_left(self, on: bool) -> None:
        """Flip the left wheel's forward/backward polarity at runtime.

        The setting is persisted to disk so it survives a reboot. If
        the wheels are currently moving, the change applies on the
        very next SET command - which the heartbeat will issue within
        ~300 ms - so the operator sees the wheel direction flip
        without releasing the D-pad.
        """
        on = bool(on)
        with self._lock:
            if self._state["invert_left"] == on:
                return
            self._state["invert_left"] = on
        log.info("DriveController.set_invert_left(%s)", on)
        self._enqueue(self._do_apply_polarity)
        _save_persisted_polarity(*self._snapshot_polarity())
        self._emit_state()

    def set_invert_right(self, on: bool) -> None:
        """Flip the right wheel's forward/backward polarity at runtime.
        See set_invert_left for semantics."""
        on = bool(on)
        with self._lock:
            if self._state["invert_right"] == on:
                return
            self._state["invert_right"] = on
        log.info("DriveController.set_invert_right(%s)", on)
        self._enqueue(self._do_apply_polarity)
        _save_persisted_polarity(*self._snapshot_polarity())
        self._emit_state()

    def _snapshot_polarity(self) -> Tuple[bool, bool]:
        with self._lock:
            return (
                bool(self._state["invert_left"]),
                bool(self._state["invert_right"]),
            )

    def _commit_wheels(
        self,
        left_dir: str,
        left_base: int,
        right_dir: str,
        right_base: int,
        *,
        start_phase: bool,
    ) -> None:
        """set_wheels with right-wheel bias; update _active_drive."""
        if self._nav is None:
            return
        tl, tr = _pair_duties_with_right_bias(
            left_dir,
            left_base,
            right_dir,
            right_base,
            start_phase=start_phase,
        )
        self._nav.set_wheels(
            left_dir=left_dir,
            left_speed=tl,
            right_dir=right_dir,
            right_speed=tr,
        )
        with self._lock:
            self._active_drive = (left_dir, tl, right_dir, tr)

    def _do_apply_polarity(self) -> None:
        """Worker-thread side of set_invert_*. Pushes the current
        polarity into the nav manager. Safe to call multiple times -
        it just re-applies whatever is in `state`."""
        self._apply_polarity_to_nav()

    def set_brake(self, on: bool) -> None:
        """Toggle brake (servo brake pose for hoverboard lean axes)."""
        with self._lock:
            self._state["brake"] = bool(on)
            if on:
                self._state["direction"] = "idle"
        self._emit_state()
        if on:
            self._enqueue(self._do_brake_on)
        else:
            self._enqueue(self._do_brake_off)

    def drive(self, direction: str) -> None:
        if direction not in _VALID_DIRECTIONS:
            log.warning("drive(): unknown direction '%s'", direction)
            return
        with self._lock:
            if self._state["brake"]:
                return
            reverse = self._state["reverse"]

        if reverse and direction in (_DIR_FORWARD, _DIR_BACK):
            direction = _DIR_BACK if direction == _DIR_FORWARD else _DIR_FORWARD

        with self._lock:
            self._state["direction"] = direction
        self._emit_state()
        if direction in (_DIR_LEFT, _DIR_RIGHT):
            speed = _drive_pivot_speed_pct()
        else:
            speed = FIXED_MANUAL_DRIVE_SPEED_PCT
        self._enqueue(lambda d=direction, s=speed: self._do_drive(d, s))

    def turn_90(self, which: str) -> None:
        """One in-place pivot (~90°): *which* is ``\"left\"`` or ``\"right\"``.

        Uses the nav layer ``turn_left`` / ``turn_right`` (blocks the worker
        for ~``NINA_NAV_TURN_SEC`` or ``NINA_DRIVE_TURN_90_SEC``). No-op if
        brake is on or *which* is invalid.
        """
        if which not in (_DIR_LEFT, _DIR_RIGHT):
            log.warning("turn_90: expected '%s' or '%s', got %r", _DIR_LEFT, _DIR_RIGHT, which)
            return
        with self._lock:
            if self._state["brake"]:
                log.info("turn_90(%s) ignored: brake engaged", which)
                return
        self._enqueue(lambda w=which: self._do_turn_90(w))

    def stop(self, *, drain: bool = False) -> None:
        """Request soft stop. With ``drain=True``, drop pending worker
        commands first so a queued heartbeat SET cannot run after this
        stop (critical for face-follow / autonomy hand-off)."""
        with self._lock:
            self._state["direction"] = "idle"
        self._emit_state()
        if drain:
            self._drain_queue()
        self._enqueue(self._do_stop)

    def emergency_stop(self) -> None:
        """Hard stop: set duty=0, engage brake, light the red+green+blue
        status LED. Independent of the regular brake toggle so the user
        can fire it without first releasing the D-pad.

        Drains any pending drive commands from the worker queue so a
        kick-start or settle that was queued just before the panic
        click can't sneak in after the e-stop. The command currently
        in flight (if any) still has to complete - we can't safely
        interrupt mid-sleep - but nothing else queued behind it will
        run before the stop+brake+EL-disable.
        """
        with self._lock:
            self._state["direction"] = "idle"
            self._state["brake"] = True
        self._emit_state()
        self._drain_queue()
        self._enqueue(self._do_emergency_stop)

    def _drain_queue(self) -> None:
        """Pop every pending command. Safe to call any time; the worker
        thread will simply find an empty queue and block on get()."""
        while True:
            try:
                item = self._cmd_q.get_nowait()
            except queue.Empty:
                return
            # Preserve the shutdown sentinel if shutdown was already
            # requested; otherwise drop the callable on the floor.
            if item is None:
                self._cmd_q.put(None)
                return

    def drive_wheels(
        self,
        left_dir: str,
        left_speed: int,
        right_dir: str,
        right_speed: int,
    ) -> None:
        """Continuous, non-blocking wheel control.

        Used by the autonomous pilot - calling this at 5-20 Hz steers
        the robot smoothly without each call blocking on the timed-turn
        sleep that `drive('left')` / `drive('right')` use.

        `left_dir` / `right_dir` are 'forward' or 'back'; speeds are
        0..100. Brake state is honoured: if the operator engaged the
        brake, this call is a no-op.
        """
        for d in (left_dir, right_dir):
            if d not in (_DIR_FORWARD, _DIR_BACK):
                log.warning("drive_wheels: unknown direction '%s'", d)
                return
        with self._lock:
            if self._state["brake"]:
                return
        ls = max(0, min(100, int(left_speed)))
        rs = max(0, min(100, int(right_speed)))
        # Reflect direction in state so the screen pill shows what
        # autonomy is actually doing right now.
        with self._lock:
            if ls == 0 and rs == 0:
                self._state["direction"] = "idle"
            elif left_dir == right_dir:
                self._state["direction"] = (
                    "forward" if left_dir == _DIR_FORWARD else "back"
                )
            else:
                lv_f = left_dir == _DIR_FORWARD
                rv_f = right_dir == _DIR_FORWARD
                self._state["direction"] = (
                    "left" if lv_f and not rv_f else "right"
                )
        self._emit_state()
        self._enqueue(
            lambda: self._do_drive_wheels(left_dir, ls, right_dir, rs)
        )

    # ------------------------------------------------------------------
    # Worker thread
    # ------------------------------------------------------------------

    def _enqueue(self, fn: Callable[[], None]) -> None:
        self._cmd_q.put(fn)

    def _worker_loop(self) -> None:
        while not self._stop_evt.is_set():
            try:
                cmd = self._cmd_q.get(timeout=0.5)
            except queue.Empty:
                continue
            if cmd is None:
                break
            try:
                cmd()
            except Exception as exc:
                log.exception("DriveController worker raised: %s", exc)

    def _heartbeat_loop(self) -> None:
        """Re-issue the most recent SET while the wheels are active.

        Held D-pad / arrow keys enqueue `_do_heartbeat_tick` at
        `_HEARTBEAT_INTERVAL_SEC`. If the worker queue already has more than
        `_HEARTBEAT_MAX_QUEUED` pending commands, we skip the enqueue so a
        stalled worker cannot grow the queue without bound.

        `wait()` returns True only when shutdown has been requested, so this
        loop exits cleanly.
        """
        while not self._heartbeat_stop.wait(_HEARTBEAT_INTERVAL_SEC):
            with self._lock:
                if self._active_drive is None:
                    continue
            if self._cmd_q.qsize() > _HEARTBEAT_MAX_QUEUED:
                continue
            self._enqueue(self._do_heartbeat_tick)

    def _do_heartbeat_tick(self) -> None:
        """Worker-thread side of the heartbeat: re-read the last SET we
        actually sent and replay it verbatim.

        Replays the cached per-wheel command (not derived from
        `state["direction"]`) so the autonomous pilot's per-wheel
        speeds - which can differ from the GUI slider - aren't
        overridden by the heartbeat. The user/pilot may have released
        the button between when the heartbeat enqueued and when this
        runs; in that case `_active_drive` is None and we no-op.
        """
        if self._nav is None:
            return
        with self._lock:
            active = self._active_drive
        if active is None:
            return
        ldir, lspeed, rdir, rspeed = active
        try:
            self._nav.set_wheels(
                left_dir=ldir, left_speed=lspeed,
                right_dir=rdir, right_speed=rspeed,
            )
        except Exception as exc:
            log.exception("heartbeat tick failed: %s", exc)

    # ------------------------------------------------------------------
    # Hardware ops (run on the worker thread)
    # ------------------------------------------------------------------

    def _refuse_if_battery_low(self, action_label: str) -> bool:
        """Refuse a motion command when the battery pack is latched low.

        Returns ``True`` if the caller should abort. Speaks the canonical
        low-battery alert via the bldc espeak path (deduplicated by the
        :envvar:`NINA_BLDC_ALERT_COOLDOWN_SEC` cooldown, so mashing the
        D-pad cannot spam the speaker) and emits a single WARNING log so
        the operator can grep ``launch.log`` for refused commands.

        Gates all user-issued motion entry points:
        :meth:`_do_drive`, :meth:`_do_drive_wheels` (non-zero speed),
        :meth:`_do_turn_90`, :meth:`_do_start_forward_pulse_bench`, and
        :meth:`_do_start_backward_pulse_bench`. ``emergency_stop``,
        brake on/off, and shutdown are intentionally NOT gated — those
        either stop motion or change non-moving state, and must keep
        working while the latch is held so the operator can safely
        bring the chassis to rest.
        """
        if not is_battery_motion_blocked():
            return False
        log.warning(
            "DriveController: %s blocked — battery latched low (motion will "
            "remain disabled until the pack recovers to the clear voltage)",
            action_label,
        )
        try:
            maybe_speak_low_battery()
        except Exception:
            log.exception("Low battery speak alert failed")
        return True

    def _do_init(self) -> None:
        if self._init_attempted:
            return
        nav: Optional[NavigationManagerLike] = None
        try:
            if self._injected_nav is not None:
                nav = self._injected_nav
            else:
                nav = NavigationManager(self._config)
            nav.initialize()
            self._nav = nav
            # Push the persisted/env-seeded wheel polarity into the
            # nav manager BEFORE we settle into engage_brake() so the
            # very first SET issued from the GUI honours it. This is
            # what makes the runtime Flip L / Flip R toggles 'just
            # work' even on a fresh kiosk service that was never given
            # NINA_NAV_INVERT_* env vars.
            self._apply_polarity_to_nav()
            # JYQD_V7.3E2 has no software brake unless the BRK pin is
            # wired - the safest "armed but stationary" resting state
            # is brake engaged + PWM 0, which is what initialize()
            # leaves us in. Make that explicit anyway.
            nav.engage_brake()
            self._init_attempted = True
            drv_msg = getattr(nav, "DRIVER_LABEL", "BLDC L+R — Jetson GPIO")
            with self._lock:
                self._state["connected"] = True
                self._state["driver_message"] = drv_msg
            log.info("DriveController: drives connected (%s)", drv_msg)
        except Exception as exc:
            if nav is not None:
                try:
                    nav.shutdown()
                except Exception:
                    pass
            self._nav = None
            with self._lock:
                self._state["connected"] = False
                self._state["driver_message"] = f"BLDC init failed — {exc}"
            log.warning(
                "DriveController init failed (%s) - running without motors",
                exc,
            )
            # Intentionally silent — the BLDC status pill in the
            # status bar already shows "BLDC not connected" with the
            # driver_message, and the operator complained that the
            # espeak male voice announcing "BLDC init failed ..." on
            # every reboot (often mentioning Dynamixel / servo wiring
            # in the exception text) was noisy.
        self._emit_state()

    def _apply_polarity_to_nav(self) -> None:
        """Push the current `state["invert_left/right"]` into the nav
        manager. No-op if the nav backend doesn't expose runtime
        polarity setters (older NavigationManager versions or a test
        fake) - in that case the polarity from the frozen config is
        already applied at construction time, so the user just doesn't
        get the runtime override - which is fine."""
        if self._nav is None:
            return
        with self._lock:
            left = bool(self._state["invert_left"])
            right = bool(self._state["invert_right"])
        try:
            if hasattr(self._nav, "set_invert_left"):
                self._nav.set_invert_left(left)
            if hasattr(self._nav, "set_invert_right"):
                self._nav.set_invert_right(right)
        except Exception as exc:  # noqa: BLE001
            log.warning("Could not push polarity to nav: %s", exc)

    def _do_shutdown(self) -> None:
        if self._nav is None:
            return
        try:
            self._nav.shutdown()
        except Exception as exc:
            log.exception("DriveController shutdown raised: %s", exc)
        finally:
            self._nav = None
            with self._lock:
                self._state["connected"] = False
                self._state["driver_message"] = "Disconnected"
            self._emit_state()

    def _do_brake_on(self) -> None:
        if self._nav is None:
            return
        try:
            self._nav.engage_brake()
            with self._lock:
                self._active_drive = None
                self._hover_straight_pulse_next = True
        except Exception as exc:
            log.exception("engage_brake failed: %s", exc)

    def _do_brake_off(self) -> None:
        if self._nav is None:
            return
        try:
            self._nav.release_brake()
        except Exception as exc:
            log.exception("release_brake failed: %s", exc)

    def _do_drive(self, direction: str, speed_pct: int) -> None:
        if self._refuse_if_battery_low(f"drive({direction})"):
            return
        if self._nav is None:
            log.warning(
                "Drive dropped (%s): BLDC backend not ready yet — wait for green "
                "pill or fix init (see driver_message). Brake must be OFF.",
                direction,
            )
            # Silent on purpose — see _do_init for rationale. The drive
            # screen's BLDC pill already surfaces the readiness state.
            return
        try:
            ldir, rdir = self._wheel_dirs_for(direction)
            if ldir is None or rdir is None:
                return
            with self._lock:
                start_from_stop = self._active_drive is None
            use_straight_pulse = self._should_start_straight_pulse(direction)
            # Use drive_continuous for all four directions so L/R is
            # held-while-pressed (matches forward/back) instead of the
            # old timed turn that auto-stopped after a few seconds.
            if use_straight_pulse:
                if direction == _DIR_FORWARD:
                    self._nav.start_pulse_straight_forward(int(speed_pct))
                else:
                    self._nav.start_pulse_straight_backward(int(speed_pct))
                with self._lock:
                    self._active_drive = None
                    self._hover_straight_pulse_next = False
                log.info(
                    "drive: hover %s pulse speed=%s%%",
                    "forward" if direction == _DIR_FORWARD else "backward",
                    speed_pct,
                )
            elif start_from_stop:
                if direction in (_DIR_LEFT, _DIR_RIGHT):
                    pivot = _drive_pivot_speed_pct()
                    kick = max(MIN_SPEED_PCT, min(100, pivot))
                    cruise = max(MIN_SPEED_PCT, min(100, pivot))
                    if (
                        getattr(self._nav, "DRIVER_LABEL", None)
                        == HoverboardAxisDrive.DRIVER_LABEL
                    ):
                        ko, ksl = _hoverboard_pivot_outer_slow(kick)
                        co, csl = _hoverboard_pivot_outer_slow(cruise)
                        if direction == _DIR_LEFT:
                            self._nav.drive_continuous(
                                ldir, rdir, ko, right_speed_percent=ksl,
                            )
                            self._commit_wheels(
                                ldir, ko, rdir, ksl, start_phase=True,
                            )
                            self._commit_wheels(
                                ldir, co, rdir, csl, start_phase=False,
                            )
                        else:
                            self._nav.drive_continuous(
                                ldir, rdir, ksl, right_speed_percent=ko,
                            )
                            self._commit_wheels(
                                ldir, ksl, rdir, ko, start_phase=True,
                            )
                            self._commit_wheels(
                                ldir, csl, rdir, co, start_phase=False,
                            )
                    else:
                        self._nav.drive_continuous(ldir, rdir, kick)
                        self._commit_wheels(
                            ldir, kick, rdir, kick, start_phase=True,
                        )
                        self._commit_wheels(
                            ldir, cruise, rdir, cruise, start_phase=False,
                        )
                    log.info(
                        "drive from stop (pivot): kick %s%% then cruise %s%%",
                        kick,
                        cruise,
                    )
                else:
                    kick = max(MIN_SPEED_PCT, int(FROM_STOP_KICK_PCT))
                    cruise = max(0, min(100, int(FROM_STOP_CRUISE_PCT)))
                    self._nav.drive_continuous(ldir, rdir, kick)
                    self._commit_wheels(
                        ldir, kick, rdir, kick, start_phase=True,
                    )
                    self._commit_wheels(
                        ldir, cruise, rdir, cruise, start_phase=False,
                    )
                    with self._lock:
                        self._hover_straight_pulse_next = False
                    log.info(
                        "drive from stop (straight): kick %s%% then cruise %s%%",
                        kick,
                        cruise,
                    )
            else:
                self._commit_wheels(
                    ldir, speed_pct, rdir, speed_pct, start_phase=False,
                )
        except Exception as exc:
            log.exception("drive(%s, %s) failed: %s", direction, speed_pct, exc)

    def _do_turn_90(self, which: str) -> None:
        if self._refuse_if_battery_low(f"turn_90({which})"):
            return
        if self._nav is None:
            log.warning(
                "turn_90(%s) dropped: BLDC backend not ready yet", which
            )
            # Silent on purpose — see _do_init for rationale.
            return
        try:
            with self._lock:
                self._active_drive = None
                self._state["direction"] = (
                    "left" if which == _DIR_LEFT else "right"
                )
            self._emit_state()
            # Prefer the closed-loop IMU-driven 90° turn (reuses the
            # same iterative micro-step machinery as the forward
            # straight-leg drift correction, anchored to a yaw budget
            # instead of a drift sample). ``pulse_turn_90`` already
            # halts any in-flight pulse series and brackets the turn
            # with brake / settle dwells on both ends, and falls back
            # to the legacy timed pivot internally when the IMU yaw
            # sampler is not wired. Older nav backends (GPIO
            # ``NavigationManager``, test fakes) don't implement
            # ``pulse_turn_90`` — for those, fall back here so the
            # Drive button still works.
            label = "left" if which == _DIR_LEFT else "right"
            pulse_turn_90 = getattr(self._nav, "pulse_turn_90", None)
            if callable(pulse_turn_90):
                log.info("turn_90(%s): closed-loop IMU pulse turn", label)
                pulse_turn_90(label)
            else:
                speed = _drive_turn_90_speed_pct()
                duration = _drive_turn_90_duration_sec(self._nav)
                log.info(
                    "turn_90(%s): backend lacks pulse_turn_90 — timed "
                    "fallback %.3fs (NINA_DRIVE_TURN_90_SEC / "
                    "NINA_NAV_TURN_SEC)",
                    label,
                    duration,
                )
                if which == _DIR_LEFT:
                    self._nav.turn_left(speed_percent=speed, duration=duration)
                else:
                    self._nav.turn_right(speed_percent=speed, duration=duration)
        except Exception as exc:
            log.exception("turn_90(%s) failed: %s", which, exc)
        finally:
            # Closed-loop turn already brakes + settles before returning;
            # on error (or the timed-fallback path) ensure PWM is parked
            # so the next Straight / drive_wheels sequence does not
            # inherit stale nav bookkeeping.
            try:
                self._nav.stop()
            except Exception:
                pass
            with self._lock:
                self._state["direction"] = "idle"
                self._active_drive = None
                self._hover_straight_pulse_next = True
            self._emit_state()

    def _do_apply_live_speed(self, direction: str, speed_pct: int) -> None:
        """Update PWM duty on the running motors without re-issuing the
        settle / kick-start sequence. Called from set_speed() while a
        D-pad button is held."""
        if self._nav is None:
            return
        if (
            direction in (_DIR_FORWARD, _DIR_BACK)
            and getattr(self._nav, "is_forward_pulse_active", lambda: False)()
        ):
            return
        ldir, rdir = self._wheel_dirs_for(direction)
        if ldir is None or rdir is None:
            return
        try:
            if (
                direction in (_DIR_LEFT, _DIR_RIGHT)
                and getattr(
                    self._nav, "DRIVER_LABEL", None
                )
                == HoverboardAxisDrive.DRIVER_LABEL
            ):
                o, sl = _hoverboard_pivot_outer_slow(speed_pct)
                if direction == _DIR_LEFT:
                    self._commit_wheels(
                        ldir, o, rdir, sl, start_phase=False,
                    )
                else:
                    self._commit_wheels(
                        ldir, sl, rdir, o, start_phase=False,
                    )
                return
            self._commit_wheels(
                ldir, speed_pct, rdir, speed_pct, start_phase=False,
            )
        except Exception as exc:
            log.exception(
                "apply_live_speed(%s, %s) failed: %s",
                direction, speed_pct, exc,
            )

    def _wheel_dirs_for(self, direction: str):
        """Map a UI direction to a (left, right) pair of nav directions."""
        if self._nav is None:
            return None, None
        if direction == _DIR_FORWARD:
            return self._nav.DIR_FORWARD, self._nav.DIR_FORWARD
        if direction == _DIR_BACK:
            return self._nav.DIR_BACKWARD, self._nav.DIR_BACKWARD
        if direction == _DIR_LEFT:
            return self._nav.DIR_FORWARD, self._nav.DIR_BACKWARD
        if direction == _DIR_RIGHT:
            return self._nav.DIR_BACKWARD, self._nav.DIR_FORWARD
        return None, None

    def _do_stop(self) -> None:
        if self._nav is None:
            return
        try:
            self._nav.stop()
        except Exception as exc:
            log.exception("stop() failed: %s", exc)
        finally:
            # Always drop commanded motion: if stop() failed or a prior tick
            # wedged, the heartbeat thread must not replay a stale SET forever.
            with self._lock:
                self._active_drive = None
                self._hover_straight_pulse_next = True

    def _do_emergency_stop(self) -> None:
        if self._nav is None:
            with self._lock:
                self._active_drive = None
                self._hover_straight_pulse_next = True
                self._state["driver_message"] = (
                    "EMERGENCY STOP requested - hardware not connected"
                )
            self._emit_state()
            return
        try:
            self._nav.emergency_stop()
            with self._lock:
                self._state["driver_message"] = (
                    "EMERGENCY STOP - brake engaged, release brake to resume"
                )
            self._emit_state()
            log.warning("DriveController: emergency_stop fired")
        except Exception as exc:
            log.exception("emergency_stop failed: %s", exc)
        finally:
            with self._lock:
                self._active_drive = None
                self._hover_straight_pulse_next = True

    def _do_drive_wheels(
        self,
        left_dir: str,
        left_speed: int,
        right_dir: str,
        right_speed: int,
    ) -> None:
        # Only block when the operator is actually asking for motion;
        # an all-zeros set_wheels is a stop and must always be honoured.
        if (int(left_speed) > 0 or int(right_speed) > 0) and self._refuse_if_battery_low(
            f"drive_wheels({left_dir},{left_speed}/{right_dir},{right_speed})"
        ):
            return
        if self._nav is None:
            log.warning(
                "drive_wheels dropped: BLDC backend not ready yet "
                "(init still running or failed — check pill / driver_message)"
            )
            # Silent on purpose — see _do_init for rationale.
            return
        try:
            ldir = (
                self._nav.DIR_FORWARD
                if left_dir == _DIR_FORWARD
                else self._nav.DIR_BACKWARD
            )
            rdir = (
                self._nav.DIR_FORWARD
                if right_dir == _DIR_FORWARD
                else self._nav.DIR_BACKWARD
            )
            ls = max(0, min(100, int(left_speed)))
            rs = max(0, min(100, int(right_speed)))
            if ls == 0 and rs == 0:
                self._nav.set_wheels(
                    left_dir=ldir,
                    left_speed=0,
                    right_dir=rdir,
                    right_speed=0,
                )
                with self._lock:
                    self._active_drive = None
                    self._hover_straight_pulse_next = True
                return
            ls, rs = _apply_hoverboard_equal_pivot_blend(
                self._nav, ldir, rdir, ls, rs
            )
            is_pivot_left = (
                ldir == self._nav.DIR_FORWARD
                and rdir == self._nav.DIR_BACKWARD
                and ls > 0
                and rs > 0
            )
            is_pivot_right = (
                ldir == self._nav.DIR_BACKWARD
                and rdir == self._nav.DIR_FORWARD
                and ls > 0
                and rs > 0
            )
            is_symmetric_straight = (
                ldir == rdir
                and ls == rs
                and ls > 0
            )
            entering_symmetric_motion = False
            if is_pivot_left or is_pivot_right or is_symmetric_straight:
                with self._lock:
                    prev = self._active_drive
                if prev is None:
                    entering_symmetric_motion = True
                else:
                    p_ld, p_ls, p_rd, p_rs = prev
                    if not (
                        p_ld == ldir
                        and p_rd == rdir
                        and p_ls == ls
                        and p_rs == rs
                    ):
                        entering_symmetric_motion = True
            entering_symmetric_pivot = entering_symmetric_motion and (
                is_pivot_left or is_pivot_right
            )
            entering_symmetric_straight = (
                entering_symmetric_motion and is_symmetric_straight
            )
            run_turn_left_prep = is_pivot_left and entering_symmetric_motion
            if run_turn_left_prep:
                cfg = getattr(self._nav, "config", None)
                if cfg is not None:
                    tb = float(getattr(cfg, "turn_left_prep_back_sec", 0))
                    tf = float(getattr(cfg, "turn_left_prep_fwd_sec", 0))
                    if tb <= 0 and tf <= 0:
                        run_turn_left_prep = False
            if run_turn_left_prep:
                prime = getattr(self._nav, "prime_turn_left_straight", None)
                if callable(prime):
                    settle = max(
                        0.0,
                        float(getattr(self._nav.config, "settle_delay_sec", 0.1)),
                    )
                    self._nav.stop()
                    time.sleep(settle)
                    # Seat gears at least at D-pad pivot torque; autonomy
                    # often uses NINA_AUTO_TURN_PCT (~9%) which is too weak
                    # alone for straight back/fwd pulses.
                    prep_sp = max(int(ls), int(_drive_pivot_speed_pct()))
                    prep_sp = max(1, min(100, prep_sp))
                    prime(prep_sp)
            if entering_symmetric_pivot and (is_pivot_left or is_pivot_right):
                # Break static friction on the outer wheel, then cruise duties.
                pivot_boost = max(1, min(100, int(_drive_pivot_speed_pct())))
                if is_pivot_left:
                    kick_o = max(int(ls), pivot_boost)
                    self._commit_wheels(
                        ldir, kick_o, rdir, rs, start_phase=True,
                    )
                    self._commit_wheels(
                        ldir, ls, rdir, rs, start_phase=False,
                    )
                else:
                    kick_o = max(int(rs), pivot_boost)
                    self._commit_wheels(
                        ldir, ls, rdir, kick_o, start_phase=True,
                    )
                    self._commit_wheels(
                        ldir, ls, rdir, rs, start_phase=False,
                    )
            elif entering_symmetric_straight:
                pulse_series_live = bool(
                    getattr(
                        self._nav, "is_forward_pulse_active", lambda: False
                    )()
                )
                if pulse_series_live:
                    return
                if (
                    self.supports_forward_pulse()
                    and self._hover_straight_pulse_next
                ):
                    if ldir == self._nav.DIR_FORWARD:
                        self._nav.start_pulse_straight_forward(int(ls))
                        with self._lock:
                            self._active_drive = None
                            self._hover_straight_pulse_next = False
                        return
                    if ldir == self._nav.DIR_BACKWARD:
                        self._nav.start_pulse_straight_backward(int(ls))
                        with self._lock:
                            self._active_drive = None
                            self._hover_straight_pulse_next = False
                        return
                with self._lock:
                    self._hover_straight_pulse_next = False
                kick = max(MIN_SPEED_PCT, int(FROM_STOP_KICK_PCT))
                kick = max(kick, int(ls))
                kick = min(100, kick)
                cruise = max(0, min(100, int(ls)))
                self._nav.drive_continuous(ldir, rdir, kick)
                self._commit_wheels(
                    ldir, kick, rdir, kick, start_phase=True,
                )
                self._commit_wheels(
                    ldir, cruise, rdir, cruise, start_phase=False,
                )
            else:
                self._commit_wheels(
                    ldir, ls, rdir, rs, start_phase=False,
                )
        except Exception as exc:
            log.exception(
                "drive_wheels(%s/%s, %s/%s) failed: %s",
                left_dir, left_speed, right_dir, right_speed, exc,
            )

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _emit_state(self) -> None:
        with self._lock:
            snapshot = dict(self._state)
        last = getattr(self, "_last_emitted_state", None)
        if last == snapshot:
            return
        self._last_emitted_state = snapshot
        self.state_changed.emit(snapshot)
