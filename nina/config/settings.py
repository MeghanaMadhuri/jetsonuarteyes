from dataclasses import dataclass
from pathlib import Path
import logging
import os
from typing import Optional


def _env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on", "y")


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        return int(raw.strip(), 0)
    except ValueError:
        return default


def _env_sign(name: str, default: int) -> int:
    return -1 if _env_int(name, default) < 0 else 1


def _env_float(name: str, default: float) -> float:
    raw = os.environ.get(name)
    if raw is None or str(raw).strip() == "":
        return default
    try:
        return float(str(raw).strip())
    except ValueError:
        return default


def _env_pulse_ramp_profile() -> str:
    raw = (os.environ.get("NINA_HOVER_PULSE_RAMP_PROFILE") or "smootherstep").strip().lower()
    if raw in ("smoothstep", "smootherstep", "cubic_io", "trapezoid"):
        return raw
    return "smootherstep"


def _env_optional_int_clamped(name: str, lo: int, hi: int) -> Optional[int]:
    raw = os.environ.get(name)
    if raw is None or str(raw).strip() == "":
        return None
    try:
        return max(lo, min(hi, int(str(raw).strip(), 0)))
    except ValueError:
        return None


def _env_pulse_waveform() -> str:
    raw = (os.environ.get("NINA_HOVER_PULSE_WAVEFORM") or "cosine").strip().lower()
    if raw in ("cosine", "dual_ramp", "dual", "legacy"):
        if raw in ("dual", "legacy"):
            return "dual_ramp"
        return raw
    return "cosine"


# Upper bound for breakaway timing (seconds). Longer holds behave like
# sustained drive at kick duty, not a start pulse. Env/clamped values
# cannot exceed this.
NAV_START_KICK_SEC_MAX = 1.0

_log = logging.getLogger("nina.config.settings")

# Removed with Pi UART bridge — drop from process env so stale /etc or .bashrc
# cannot make tooling report "remote" after reboot.
_OBSOLETE_NAV_ENV_KEYS = (
    "NINA_NAV_MODE",
    "NINA_NAV_REMOTE_PORT",
    "NINA_NAV_REMOTE_BAUD",
    "NINA_NAV_REMOTE_TIMEOUT_SEC",
    "NINA_NAV_REMOTE_TURN_TICK_SEC",
    "NINA_NAV_LEGACY_PI_BRIDGE",
)
_scrub_obsolete_nav_env_logged = False

# Removed brake-centered straight tilt (use NINA_HOVER_FWD_POS_* / REV_* only).
_OBSOLETE_HOVER_ENV_KEYS = ("NINA_HOVER_STRAIGHT_TILT_DEG",)
_scrub_obsolete_hover_env_logged = False


def _scrub_obsolete_navigation_env() -> None:
    """Pop legacy Pi-bridge keys from ``os.environ`` (once-per-process log)."""
    global _scrub_obsolete_nav_env_logged
    removed = [k for k in _OBSOLETE_NAV_ENV_KEYS if k in os.environ]
    for k in _OBSOLETE_NAV_ENV_KEYS:
        os.environ.pop(k, None)
    if removed and not _scrub_obsolete_nav_env_logged:
        _log.info(
            "Cleared obsolete Pi UART navigation env vars: %s",
            ", ".join(removed),
        )
        _scrub_obsolete_nav_env_logged = True


def _scrub_obsolete_hover_env() -> None:
    """Pop removed hover tuning keys from ``os.environ`` (once-per-process log)."""
    global _scrub_obsolete_hover_env_logged
    removed = [k for k in _OBSOLETE_HOVER_ENV_KEYS if k in os.environ]
    for k in _OBSOLETE_HOVER_ENV_KEYS:
        os.environ.pop(k, None)
    if removed and not _scrub_obsolete_hover_env_logged:
        _log.info(
            "Cleared obsolete hover env vars: %s",
            ", ".join(removed),
        )
        _scrub_obsolete_hover_env_logged = True


@dataclass(frozen=True)
class NavigationSettings:
    """Tunables for Jetson GPIO navigation (`NavigationManager`).

    Drives 2× JYQD from the 40-pin header (``backend_name``, ``pwm_frequency_hz``,
    pins from ``DEFAULT_PINS`` / ``NINA_NAV_*`` env).

    Kick, straight nudge, pivot prep, and settle delays are documented on each
    field group; most are overridden via ``NINA_NAV_*`` env in ``load_settings``.
    """
    backend_name: str
    pwm_frequency_hz: int
    default_speed_percent: int
    turn_duration_sec: float
    invert_left_dir: bool
    invert_right_dir: bool
    el_active_low: bool = False
    start_kick_percent: int = 14
    start_kick_sec: float = NAV_START_KICK_SEC_MAX
    dir_pwm_gap_sec: float = 0.03
    pwm_reassert_sec: float = 0.02
    straight_opposite_nudge_sec: float = 0.5
    straight_opposite_nudge_pct: int = 20
    opposite_zero_settle_sec: float = 0.04
    settle_delay_sec: float = 0.1
    pivot_turn_left_extra_pp: int = 6
    turn_left_prep_back_sec: float = 0.12
    turn_left_prep_fwd_sec: float = 0.12


@dataclass(frozen=True)
class HoverboardAxisSettings:
    """Hoverboard locomotion via lean axes (MX-28 on Dynamixel bus, joint mode 0–4095).

    On the **hall idle** pose (magnets not triggering the board brake), both lean servos
    sit at **2048** raw. That maps to ``NINA_HOVER_BRAKE_POS_LEFT`` /
    ``NINA_HOVER_BRAKE_POS_RIGHT`` defaults.

    ``DriveController`` always uses ``HoverboardAxisDrive``. Env:
    ``NINA_HOVER_ID_LEFT`` / ``RIGHT`` (default 12 / 13), ``NINA_HOVER_BRAKE_POS_LEFT`` /
    ``NINA_HOVER_BRAKE_POS_RIGHT`` — brake / idle / boot goals (defaults 2048 each;
    legacy ``NINA_HOVER_NEUTRAL_*`` is read if the ``BRAKE`` vars are unset),
    ``NINA_HOVER_TILT_DEG``, ``NINA_HOVER_MOVING_SPEED`` (0–1023; **0** = fastest
    joint move on Protocol 1, higher = slower — default 0),
    ``NINA_HOVER_SIGN_LEFT`` / ``RIGHT`` (+1 or -1). Straight-line forward
    uses ``NINA_HOVER_FWD_POS_*`` (defaults 2020 / 2083); straight backward uses ``NINA_HOVER_REV_POS_*``
    (defaults 2068 / 2028). In-place pivots pair
    ``backward_pos_*`` on one side with ``forward_pos_*`` on the other.
    ``NINA_HOVER_SWAP_TURN_LR`` defaults on so GUI pivots match this mount;
    set ``NINA_HOVER_SWAP_TURN_LR=0`` if yaw sense is reversed.
    ``NINA_HOVER_TURN_PUSH_TICKS`` (default 20). ``tilt_deg`` remains for any legacy
    asymmetric fallback (non-straight paths).

    Optional **forward pulse** (manual D-pad forward + Straight bench forward only):
    ``NINA_HOVER_PULSE_FORWARD`` (default **on** in code; set ``NINA_HOVER_PULSE_FORWARD=0``
    to disable) oscillates between **full forward** lean (calibrated ``forward_pos_*``) and a
    **coast** pose near brake (see ``NINA_HOVER_PULSE_COAST_BLEND``) so the lean never fully
    “dead-stops” at brake—smoother, hoverboard-like reversals.
    ``NINA_HOVER_PULSE_RETURN_RAMP_SEC`` is the ramp time per leg for **dual_ramp** mode, or
    **half** the full coast→forward→coast period for **cosine** mode (default ``NINA_HOVER_PULSE_WAVEFORM``):
    cosine runs one symmetric S-curve over ``2×`` ramp seconds so outbound and return use the
    same velocity law (no ease-curve “restart” at coast between legs). ``dual_ramp`` restores
    two independent ramps with ``NINA_HOVER_PULSE_RAMP_PROFILE`` (``smootherstep``, ``smoothstep``,
    ``cubic_io``, ``trapezoid``; edge ``NINA_HOVER_PULSE_RAMP_TRAP_EDGE`` 0.08–0.35).
    If ramp and holds are all ``0``, a safe minimum ramp is applied in code.
    Optional ``NINA_HOVER_PULSE_RAMP_MOVING_SPEED`` (0–1023): MX Moving Speed **only during**
    pulse ramps; unset uses ``NINA_HOVER_MOVING_SPEED``. Optional hold ``NINA_HOVER_PULSE_FWD_SEC`` /
    ``NINA_HOVER_PULSE_BRAKE_SEC`` at endpoints (default **0** / **0** s for continuous coast↔FWD;
    wave is continuous when ``ramp`` > 0). ``NINA_HOVER_PULSE_BRAKE_SEC`` is a **coast dwell after**
    each full cycle before the next; it is capped at **0.5** s so the next pulse cannot start late
    by more than that. Set ``NINA_HOVER_PULSE_SYNC_PRESENT`` to wait each ramp
    step until both servos' **Present Position** is within ``NINA_HOVER_PULSE_PRESENT_TOL`` ticks of
    goal (exact equality is not practical), up to ``NINA_HOVER_PULSE_PRESENT_STEP_TIMEOUT`` s —
    slows the ramp slightly but avoids outpacing small moves.
    """

    id_left: int
    id_right: int
    brake_pos_left: int
    brake_pos_right: int
    forward_pos_left: int
    forward_pos_right: int
    backward_pos_left: int
    backward_pos_right: int
    swap_turn_lr: bool
    turn_push_ticks: int
    tilt_deg: float
    moving_speed: int
    sign_left: int
    sign_right: int
    pulse_forward_enabled: bool
    pulse_forward_on_sec: float
    pulse_forward_brake_sec: float
    pulse_forward_return_ramp_sec: float
    pulse_forward_coast_blend: float
    pulse_forward_sync_present: bool
    pulse_forward_present_tol_ticks: int
    pulse_forward_present_step_timeout_sec: float
    pulse_ramp_profile: str
    pulse_ramp_trap_edge: float
    pulse_ramp_moving_speed: Optional[int]
    pulse_waveform: str


@dataclass(frozen=True)
class AutonomySettings:
    """Shared knobs for the autonomous pilot.

    All distances are millimetres so they line up with the sensor data
    types. Speeds are percent (0..100) feeding straight into
    `NavigationManager.set_wheels()`.
    """
    tick_hz: float                       # autonomy decision rate
    cruise_speed_pct: int                # straight-line speed
    turn_speed_pct: int                  # in-place spin speed
    forward_clear_mm: int                # at >= this, go straight
    side_clear_mm: int                   # min side clearance for forward
    emergency_stop_mm: int               # below this any direction => stop+back-off
    cliff_min_mm: int                    # IR reading below this = abort
    turn_duration_ms: int                # min time committed to a chosen turn
    backoff_duration_ms: int             # time to reverse before re-evaluating
    fwd_blocked_backup_sec: float        # reverse when forward blocked this long (0=off)


@dataclass(frozen=True)
class LidarSettings:
    """Lidar transport + model configuration.

    The factory in `nina.sensors.lidar_factory.build_lidar` reads
    these to decide which physical lidar driver to instantiate. Two
    are supported:

      ``model='s2e'``  - SLAMTEC RPLIDAR S2E (Ethernet / UDP). Uses
                          `host` + `udp_port`. ~30 m range, ~10 Hz scan
                          rate, ~32 kHz sample rate. CURRENT default.
      ``model='a1'``   - SLAMTEC RPLIDAR A1M8 (USB-serial). Uses
                          `serial_port` + `baudrate`. ~12 m range,
                          ~5.5 Hz scan rate. Legacy bring-up.
      ``model='auto'`` - try S2E first, fall back to A1.

    `host`/`serial_port` only matter for the matching transport; the
    other one is ignored.
    """
    model: str
    host: str
    udp_port: int
    serial_port: str
    baudrate: int


@dataclass(frozen=True)
class SlamSettings:
    """BreezySLAM map / pose configuration."""
    map_size_pixels: int                 # square map; default 1000
    map_size_meters: float               # physical extent of the map
    update_hz: float                     # SLAM update rate (cap)
    hole_width_mm: int                   # smallest passable opening
    random_seed: int
    laser_max_range_mm: int              # detection envelope of the lidar (drives BreezySLAM laser model)
    laser_scan_size: int                 # samples per sweep (laser model)
    laser_scan_rate_hz: float            # rev/sec (laser model)


@dataclass(frozen=True)
class GotoSettings:
    """Tunables for the goto-point pilot.

    Goto navigation is goal-directed (vs the wander pilot's
    obstacle-only reactive behaviour). The pilot plans an A* path on
    the live BreezySLAM occupancy grid, follows it with pure-pursuit,
    and re-runs the planner whenever the reactive obstacle layer
    vetoes a path step. All distances are millimetres, speeds are
    percent (0..100).

    The planner inflates walls by an effective radius of:

        max(footprint_radius_mm, ceil(min_passage_width_mm / 2))

    so paths leave a Nina-shaped buffer to walls AND any corridor
    the planner routes through is at least `min_passage_width_mm`
    wide between walls. The two knobs are intentionally separate:
    `footprint_radius_mm` is "this is how big my body is" (geometry)
    while `min_passage_width_mm` is "this is the tightest gap I'm
    willing to send the bot through" (safety policy). Default
    passage width is 2 ft / 610 mm.

    Emergency stop and forward-clear for **goto** are separate from
    wander (`NINA_AUTO_ESTOP_MM` / `NINA_AUTO_FWD_CLEAR_MM`): wander
    targets open-space roaming; goto must tolerate closer forward
    lidar returns along a planned path indoors.
    """
    arrival_radius_mm: int                 # within this -> 'arrived'
    footprint_radius_mm: int               # bot body half-width (geometry)
    min_passage_width_mm: int              # smallest corridor width the planner is allowed to use
    cruise_speed_pct: int                  # straight-line speed
    turn_speed_pct: int                    # in-place spin speed
    heading_deadband_deg: float            # |heading_err| <= this -> drive forward
    lookahead_mm: int                      # pure-pursuit lookahead distance
    replan_period_sec: float               # automatic replans, even if path looks fine
    stuck_window_sec: float                # window we look back over for "is the bot moving?"
    stuck_motion_mm: int                   # if pose moved < this in window -> 'stuck'
    tick_hz: float                         # control loop rate
    unknown_pixel_cost: float              # A* extra cost per unknown grey pixel (>=1.0)
    forward_clear_mm: int                  # min forward clearance (mm) to command forward
    emergency_stop_mm: int                 # goto-only: reverse if forward sector < this (mm)


@dataclass(frozen=True)
class NinaSettings:
    serial_port: str
    baudrate: int
    neutral_action_name: str
    actions_dir: Path
    manifest_path: Path
    recordings_dir: Path
    recording_sample_hz: float
    navigation: NavigationSettings
    autonomy: AutonomySettings
    slam: SlamSettings
    lidar: LidarSettings
    goto: GotoSettings
    hoverboard_axis: HoverboardAxisSettings


def serial_collision_warnings(settings: NinaSettings) -> list[str]:
    """Return likely serial-port contention warnings."""
    # Pi UART ``remote_serial_port`` was removed from NavigationSettings;
    # tolerate older pickled settings objects via getattr.
    nav_remote = str(
        getattr(settings.navigation, "remote_serial_port", "") or ""
    ).strip()
    ports = {
        "dynamixel": settings.serial_port.strip(),
        "nav_remote": nav_remote,
        "lidar": settings.lidar.serial_port.strip(),
    }
    owners: dict[str, list[str]] = {}
    for name, path in ports.items():
        if not path:
            continue
        owners.setdefault(path, []).append(name)
    warnings: list[str] = []
    for path, names in owners.items():
        if len(names) >= 2:
            warnings.append(f"{path} shared by {', '.join(sorted(names))}")
    return warnings


def load_settings(repo_root: Path) -> NinaSettings:
    _scrub_obsolete_navigation_env()
    _scrub_obsolete_hover_env()

    actions_dir = repo_root / "nina" / "actions"
    recordings_dir = repo_root / "nina" / "actions" / "recordings"
    manifest_path = actions_dir / "manifest.json"

    recordings_dir.mkdir(parents=True, exist_ok=True)

    navigation = NavigationSettings(
        backend_name=os.environ.get("NINA_NAV_BACKEND", "jetson"),
        pwm_frequency_hz=int(os.environ.get("NINA_NAV_PWM_HZ", "2000")),
        default_speed_percent=int(os.environ.get("NINA_NAV_SPEED", "8")),
        turn_duration_sec=float(os.environ.get("NINA_NAV_TURN_SEC", "2.3")),
        # Flip if a wheel spins opposite of what the GUI expects (the
        # JYQD ZF level for "forward" depends on motor wiring polarity).
        invert_left_dir=_env_bool("NINA_NAV_INVERT_LEFT", False),
        invert_right_dir=_env_bool("NINA_NAV_INVERT_RIGHT", False),
        el_active_low=_env_bool("NINA_NAV_EL_ACTIVE_LOW", False),
        start_kick_percent=int(os.environ.get("NINA_NAV_START_KICK_PCT", "14")),
        start_kick_sec=min(
            NAV_START_KICK_SEC_MAX,
            max(
                0.0,
                float(
                    os.environ.get(
                        "NINA_NAV_START_KICK_SEC", str(NAV_START_KICK_SEC_MAX)
                    )
                ),
            ),
        ),
        dir_pwm_gap_sec=float(os.environ.get("NINA_NAV_DIR_SETTLE_SEC", "0.03")),
        pwm_reassert_sec=float(os.environ.get("NINA_NAV_PWM_REASSERT_SEC", "0.02")),
        straight_opposite_nudge_sec=float(
            os.environ.get("NINA_NAV_STRAIGHT_OPPOSITE_NUDGE_SEC", "0.5")
        ),
        straight_opposite_nudge_pct=int(
            os.environ.get("NINA_NAV_STRAIGHT_OPPOSITE_NUDGE_PCT", "20")
        ),
        opposite_zero_settle_sec=float(
            os.environ.get("NINA_NAV_OPPOSITE_ZERO_SETTLE_SEC", "0.04")
        ),
        settle_delay_sec=float(os.environ.get("NINA_NAV_SETTLE_SEC", "0.1")),
        pivot_turn_left_extra_pp=max(
            0,
            min(
                20,
                int(
                    (
                        os.environ.get("NINA_NAV_PIVOT_TURN_LEFT_EXTRA_PP")
                        or os.environ.get("NINA_NAV_PIVOT_R_FWD_EXTRA_PP")
                        or "6"
                    )
                ),
            ),
        ),
        turn_left_prep_back_sec=max(
            0.0,
            min(
                0.5,
                float(
                    os.environ.get("NINA_NAV_TURN_LEFT_PREP_BACK_SEC", "0.12")
                ),
            ),
        ),
        turn_left_prep_fwd_sec=max(
            0.0,
            min(
                0.5,
                float(
                    os.environ.get("NINA_NAV_TURN_LEFT_PREP_FWD_SEC", "0.12")
                ),
            ),
        ),
    )

    hoverboard_axis = HoverboardAxisSettings(
        id_left=_env_int("NINA_HOVER_ID_LEFT", 12),
        id_right=_env_int("NINA_HOVER_ID_RIGHT", 13),
        brake_pos_left=(
            _env_int("NINA_HOVER_BRAKE_POS_LEFT", 2048)
            if "NINA_HOVER_BRAKE_POS_LEFT" in os.environ
            else _env_int("NINA_HOVER_NEUTRAL_LEFT", 2048)
        ),
        brake_pos_right=(
            _env_int("NINA_HOVER_BRAKE_POS_RIGHT", 2048)
            if "NINA_HOVER_BRAKE_POS_RIGHT" in os.environ
            else _env_int("NINA_HOVER_NEUTRAL_RIGHT", 2048)
        ),
        forward_pos_left=_env_int("NINA_HOVER_FWD_POS_LEFT", 2020),
        forward_pos_right=_env_int("NINA_HOVER_FWD_POS_RIGHT", 2083),
        backward_pos_left=_env_int("NINA_HOVER_REV_POS_LEFT", 2068),
        backward_pos_right=_env_int("NINA_HOVER_REV_POS_RIGHT", 2028),
        swap_turn_lr=_env_bool("NINA_HOVER_SWAP_TURN_LR", True),
        turn_push_ticks=max(
            0, min(100, _env_int("NINA_HOVER_TURN_PUSH_TICKS", 20))
        ),
        tilt_deg=float(os.environ.get("NINA_HOVER_TILT_DEG", "5")),
        moving_speed=max(0, min(1023, _env_int("NINA_HOVER_MOVING_SPEED", 0))),
        sign_left=_env_sign("NINA_HOVER_SIGN_LEFT", 1),
        sign_right=_env_sign("NINA_HOVER_SIGN_RIGHT", 1),
        pulse_forward_enabled=_env_bool("NINA_HOVER_PULSE_FORWARD", True),
        pulse_forward_on_sec=max(
            0.0, min(10.0, _env_float("NINA_HOVER_PULSE_FWD_SEC", 0.0))
        ),
        pulse_forward_brake_sec=max(
            0.0, min(0.5, _env_float("NINA_HOVER_PULSE_BRAKE_SEC", 0.0))
        ),
        pulse_forward_return_ramp_sec=max(
            0.0,
            min(10.0, _env_float("NINA_HOVER_PULSE_RETURN_RAMP_SEC", 0.75)),
        ),
        pulse_forward_coast_blend=max(
            0.0,
            min(1.0, _env_float("NINA_HOVER_PULSE_COAST_BLEND", 0.22)),
        ),
        pulse_forward_sync_present=_env_bool(
            "NINA_HOVER_PULSE_SYNC_PRESENT", False
        ),
        pulse_forward_present_tol_ticks=max(
            0, min(50, _env_int("NINA_HOVER_PULSE_PRESENT_TOL", 4))
        ),
        pulse_forward_present_step_timeout_sec=max(
            0.02,
            min(1.0, _env_float("NINA_HOVER_PULSE_PRESENT_STEP_TIMEOUT", 0.25)),
        ),
        pulse_ramp_profile=_env_pulse_ramp_profile(),
        pulse_ramp_trap_edge=max(
            0.08,
            min(0.35, _env_float("NINA_HOVER_PULSE_RAMP_TRAP_EDGE", 0.18)),
        ),
        pulse_ramp_moving_speed=_env_optional_int_clamped(
            "NINA_HOVER_PULSE_RAMP_MOVING_SPEED", 0, 1023
        ),
        pulse_waveform=_env_pulse_waveform(),
    )

    from nina.config.hover_calibration import (  # noqa: PLC0415 — after HoverboardAxisSettings
        merge_hover_calibration_into_axis,
    )

    hoverboard_axis = merge_hover_calibration_into_axis(hoverboard_axis)

    autonomy = AutonomySettings(
        # 8 Hz (was 5 Hz) so the pilot reacts every 125 ms instead of
        # every 200 ms. At low cruise PWM the bot still coasts a few
        # cm during one tick, but the extra ticks per second cut the
        # worst-case "saw obstacle / decided to turn / actually started
        # turning" latency by ~75 ms.
        tick_hz=float(os.environ.get("NINA_AUTO_TICK_HZ", "8")),
        # Matches the GUI manual-mode floor (MIN_SPEED_PCT) so an
        # operator dropping out of autonomy doesn't see the wheels
        # change pace mid-handoff. Bump via NINA_AUTO_CRUISE_PCT for
        # tests that want a faster wander.
        cruise_speed_pct=int(os.environ.get("NINA_AUTO_CRUISE_PCT", "8")),
        # In-place pivots need more torque than straight cruise; 9% often
        # stalls until the wheels get a push (see DriveController prep/kick).
        turn_speed_pct=int(os.environ.get("NINA_AUTO_TURN_PCT", "12")),
        # 1200 mm (was 700 mm) is the new commit-to-forward
        # threshold. The previous 700 mm gave the BLDCs no room to
        # decelerate before reaching the obstacle: at ~0.4 m/s with
        # ~200 ms tick + ~200 ms wheel coast the bot would stop
        # 50-60 cm from a person -> "almost hitting" was the user
        # report. 1200 mm leaves the bot a clean ~1 m of buffer at
        # the moment it commits to a turn, so the actual stopping
        # distance lands closer to personal-space (~1 m) than
        # arm's-length.
        forward_clear_mm=int(os.environ.get("NINA_AUTO_FWD_CLEAR_MM", "1200")),
        side_clear_mm=int(os.environ.get("NINA_AUTO_SIDE_CLEAR_MM", "450")),
        # 850 mm. Between this and forward_clear_mm the pilot turns;
        # at or inside this radius it reverses immediately (layer 2).
        # 600 mm still let the bot coast into bump range before backoff;
        # 850 matches ~1 m "personal space" when combined with
        # forward_clear=1200 and the timed / dead-end backoff below.
        emergency_stop_mm=int(os.environ.get("NINA_AUTO_ESTOP_MM", "850")),
        cliff_min_mm=int(os.environ.get("NINA_AUTO_CLIFF_MIN_MM", "60")),
        turn_duration_ms=int(os.environ.get("NINA_AUTO_TURN_MS", "350")),
        backoff_duration_ms=int(os.environ.get("NINA_AUTO_BACKOFF_MS", "500")),
        fwd_blocked_backup_sec=float(
            os.environ.get("NINA_AUTO_FWD_BLOCKED_BACKUP_SEC", "2.5")
        ),
    )

    lidar = LidarSettings(
        # 's2e' is the current shipping default (Slamtec S2E,
        # Ethernet / UDP). Set NINA_LIDAR_MODEL=a1 for the legacy
        # USB-serial RPLIDAR A1M8 bring-up, or =auto to probe S2E
        # first and fall through to A1 on failure.
        model=os.environ.get("NINA_LIDAR_MODEL", "s2e").strip().lower(),
        host=os.environ.get("NINA_LIDAR_HOST", "192.168.11.2"),
        udp_port=int(os.environ.get("NINA_LIDAR_UDP_PORT", "8089")),
        # A1-only fields. Ignored when model='s2e'.
        serial_port=os.environ.get("NINA_LIDAR_PORT", "/dev/ttyUSB0"),
        baudrate=int(os.environ.get("NINA_LIDAR_BAUD", "115200")),
    )

    # Default world size is now sized for the S2E (effective ~25 m
    # indoors). With a 12 m square world at 1000 px we get
    # 12 mm/px - still above the wall-resolution floor (15 mm/px,
    # see test_slam_resolution_fine_enough_for_walls) and gives a
    # meaningful map of an 8 m hallway without flooding RAM. The
    # earlier A1-era default of 8 m / 800 px was chosen because the
    # A1's 6 m range left a 20 m world looking empty; the S2E sees
    # walls at 25 m so we let the world grow.
    if lidar.model == "a1":
        # Legacy A1 path: keep the proven 8 m / 800 px sizing - the
        # A1 won't fill a bigger world anyway.
        slam_pixels_default = "800"
        slam_meters_default = "8"
        slam_max_range_mm_default = "12000"
        slam_scan_size_default = "360"
        slam_scan_rate_default = "5.5"
    else:
        # S2E (or auto - same defaults). 12 mm/px keeps walls > 1
        # pixel after letterboxing into the Perception card.
        slam_pixels_default = "1000"
        slam_meters_default = "12"
        # The S2E's published max is 30 m but multipath inside small
        # rooms makes returns past ~28 m unreliable; clip there.
        slam_max_range_mm_default = "28000"
        # 400 samples/rev is the S2E's typical compressed-mode output
        # at 10 Hz. BreezySLAM resamples internally, so this is
        # effectively a hint about angular resolution.
        slam_scan_size_default = "400"
        slam_scan_rate_default = "10"

    slam = SlamSettings(
        map_size_pixels=int(os.environ.get("NINA_SLAM_PIXELS", slam_pixels_default)),
        map_size_meters=float(os.environ.get("NINA_SLAM_METERS", slam_meters_default)),
        update_hz=float(os.environ.get("NINA_SLAM_HZ", "5")),
        hole_width_mm=int(os.environ.get("NINA_SLAM_HOLE_MM", "600")),
        random_seed=int(os.environ.get("NINA_SLAM_SEED", "0xdeadbeef"), 0),
        laser_max_range_mm=int(
            os.environ.get("NINA_SLAM_LASER_MAX_MM", slam_max_range_mm_default)
        ),
        laser_scan_size=int(
            os.environ.get("NINA_SLAM_LASER_SCAN_SIZE", slam_scan_size_default)
        ),
        laser_scan_rate_hz=float(
            os.environ.get("NINA_SLAM_LASER_SCAN_RATE_HZ", slam_scan_rate_default)
        ),
    )

    goto = GotoSettings(
        # 250 mm -> roughly Nina's chassis half-width. The pilot
        # transitions to 'arrived' once the bot is inside this radius
        # of the goal, which keeps it from oscillating on the spot
        # trying to land on the exact pixel the operator tapped.
        arrival_radius_mm=int(os.environ.get("NINA_GOTO_ARRIVAL_MM", "250")),
        # Footprint inflation: A* treats every wall pixel as
        # "wall + this many mm of buffer" so the planner never
        # picks a route the bot physically can't take. Default
        # 250 mm = ~half-body width. Bump for wider bots; tighter
        # safety margins are better expressed via
        # `min_passage_width_mm` below.
        footprint_radius_mm=int(os.environ.get("NINA_GOTO_INFLATE_MM", "250")),
        # Minimum corridor width (between facing walls) the planner
        # is allowed to route through. Default 610 mm = 24 in / 2 ft,
        # which is the smallest gap Nina is supposed to fit through
        # in the lab + corridor environments she's used in. Drive
        # this from operator policy ("I want 3 ft of buffer in the
        # showroom") rather than from the bot's geometry.
        min_passage_width_mm=int(
            os.environ.get("NINA_GOTO_MIN_PASSAGE_MM", "610")
        ),
        # Match the wander pilot's cruise so a goto handoff doesn't
        # change the bot's perceived "speed" mid-run.
        cruise_speed_pct=int(os.environ.get("NINA_GOTO_CRUISE_PCT", "8")),
        turn_speed_pct=int(os.environ.get("NINA_GOTO_TURN_PCT", "12")),
        # 18 deg deadband: inside this we drive forward (still with
        # a small heading correction); outside, we turn in place.
        # Wider than the wander pilot's implicit binary so we don't
        # flip into in-place spin during normal forward driving on a
        # noisy SLAM heading estimate.
        heading_deadband_deg=float(os.environ.get("NINA_GOTO_HEAD_DEG", "18.0")),
        # 600 mm pure-pursuit lookahead. Longer = smoother arc,
        # shorter = tighter follow but more wobble.
        lookahead_mm=int(os.environ.get("NINA_GOTO_LOOKAHEAD_MM", "600")),
        # Periodic replan even if everything looks fine - the SLAM
        # map keeps growing and the optimal path may shorten as
        # walls fill in.
        replan_period_sec=float(os.environ.get("NINA_GOTO_REPLAN_SEC", "3.0")),
        # 5 s window x 50 mm of motion = "the bot is genuinely stuck".
        # The wander pilot's reactive veto would otherwise mask a
        # locked-rotor / dead-driver scenario.
        stuck_window_sec=float(os.environ.get("NINA_GOTO_STUCK_SEC", "5.0")),
        stuck_motion_mm=int(os.environ.get("NINA_GOTO_STUCK_MM", "50")),
        # Same 8 Hz as the wander pilot's tick.
        tick_hz=float(os.environ.get("NINA_GOTO_TICK_HZ", "8")),
        # Unknown-grey pixels cost slightly more than known-free
        # ones in the planner, so A* will prefer mapped corridors
        # but still happily route into unmapped rooms when needed.
        unknown_pixel_cost=float(os.environ.get("NINA_GOTO_UNKNOWN_COST", "1.5")),
        forward_clear_mm=int(os.environ.get("NINA_GOTO_FWD_CLEAR_MM", "700")),
        # Below wander's NINA_AUTO_ESTOP_MM (850): indoor goto routinely
        # has real lidar returns in the 0.65--0.85 m band in the forward cone.
        emergency_stop_mm=int(os.environ.get("NINA_GOTO_ESTOP_MM", "580")),
    )

    return NinaSettings(
        serial_port=os.environ.get("NINA_DXL_PORT", "/dev/ttyUSB0"),
        baudrate=int(os.environ.get("NINA_DXL_BAUD", "222222")),
        neutral_action_name=os.environ.get("NINA_NEUTRAL_ACTION", "neutral"),
        actions_dir=actions_dir,
        manifest_path=manifest_path,
        recordings_dir=recordings_dir,
        recording_sample_hz=float(os.environ.get("NINA_RECORD_HZ", "20")),
        navigation=navigation,
        autonomy=autonomy,
        slam=slam,
        lidar=lidar,
        goto=goto,
        hoverboard_axis=hoverboard_axis,
    )
