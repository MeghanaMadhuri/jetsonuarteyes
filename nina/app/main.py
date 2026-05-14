import argparse
import json
import threading
import time
from pathlib import Path
from typing import List

from nina.config.settings import load_settings
from nina.controllers.action_runner import ActionRunner
from nina.controllers.dynamixel_manager import DynamixelManager
from nina.config.motor_ids import EXPECTED_DYNAMIXEL_IDS
from nina.controllers.hoverboard_axis_drive import HoverboardAxisDrive
from nina.controllers.navigation_manager import DEFAULT_PINS, jetson_orin_nano_board_pin
from nina.services.audio_player import AudioPlayer
from nina.services.startup_service import StartupService


DEFAULT_MOTOR_IDS: List[int] = list(EXPECTED_DYNAMIXEL_IDS)


def ensure_motors_ready(dxl: DynamixelManager) -> None:
    dxl.initialize_bus()
    health = dxl.run_health_check()
    if not health.connected:
        print(
            f"[warn] Motor health check: {health.detected_motors}/"
            f"{health.expected_motors} motors responded. {health.detail} "
            "(continuing; missing motors will simply not move)"
        )
    dxl.set_torque_all(True)


def build_app():
    repo_root = Path(__file__).resolve().parents[2]
    settings = load_settings(repo_root)

    dxl = DynamixelManager(
        serial_port=settings.serial_port,
        baudrate=settings.baudrate,
        expected_motor_ids=DEFAULT_MOTOR_IDS,
    )
    action_runner = ActionRunner(
        manifest_path=settings.manifest_path,
        actions_dir=settings.actions_dir,
        dxl=dxl,
    )
    startup_service = StartupService(
        dxl, action_runner, settings.neutral_action_name, settings.hoverboard_axis
    )
    return settings, dxl, action_runner, startup_service


def build_navigation(settings, dxl=None, bus_lock=None):
    """Return ``HoverboardAxisDrive`` (MX-28 lean axes on the Dynamixel bus).

    Pass ``dxl`` + ``bus_lock`` from the running app when available; otherwise a
    temporary ``DynamixelManager`` is opened (CLI / link-daemon use).
    """
    import threading

    lock = bus_lock or threading.RLock()
    if dxl is None:
        tmp = DynamixelManager(
            settings.serial_port,
            settings.baudrate,
            list(EXPECTED_DYNAMIXEL_IDS),
        )
        tmp.initialize_bus()
        dxl = tmp
    return HoverboardAxisDrive(
        dxl, lock, settings.hoverboard_axis, settings.navigation
    )



def run_nav_command(nav, command: str,
                    speed: int, duration: float, hold: float) -> None:
    nav.initialize()
    try:
        if command == "nav-forward":
            nav.forward(speed_percent=speed)
            time.sleep(hold)
            nav.stop()
        elif command == "nav-back":
            nav.backward(speed_percent=speed)
            time.sleep(hold)
            nav.stop()
        elif command == "nav-left":
            nav.turn_left(speed_percent=speed, duration=duration)
        elif command == "nav-right":
            nav.turn_right(speed_percent=speed, duration=duration)
        elif command == "nav-stop":
            nav.stop()
        elif command == "nav-brake":
            nav.engage_brake()
        elif command == "nav-release":
            nav.release_brake()
        else:
            raise ValueError(f"Unknown nav command: {command}")
    finally:
        nav.shutdown()


def main() -> None:
    parser = argparse.ArgumentParser(description="Nina app bootstrap and controls.")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("startup", help="Initialize motors, run health checks, and go neutral.")
    run_action = sub.add_parser("run-action", help="Run a named action from the manifest.")
    run_action.add_argument("name", type=str, help="Action name (example: namaste)")
    run_action.add_argument(
        "--no-smooth",
        action="store_true",
        help="Disable interpolated playback (use raw frame-by-frame stepping).",
    )
    run_action.add_argument(
        "--sub-hz",
        type=float,
        default=50.0,
        help="Interpolated update rate during smooth playback in Hz (default: 50).",
    )
    run_action.add_argument(
        "--max-speed",
        type=int,
        default=1023,
        help="Per-motor speed limit during smooth playback (0-1023, 1023 = max).",
    )
    run_action.add_argument(
        "--speed",
        type=float,
        default=1.0,
        help=(
            "Playback time multiplier (1.0 = recorded tempo, 0.5 = half speed, "
            "2.0 = double speed). Smoothness is preserved at any value."
        ),
    )
    run_action.add_argument(
        "--no-audio",
        action="store_true",
        help="Skip the audio clip associated with this action (if any).",
    )
    run_action.add_argument(
        "--audio-offset",
        type=float,
        default=None,
        help=(
            "Override the manifest audio_offset for this action (seconds to wait "
            "after motion starts before firing the audio clip)."
        ),
    )
    sub.add_parser("list-actions", help="List available action names.")

    record_action = sub.add_parser("record-action", help="Record a new action file from live motors.")
    record_action.add_argument("--name", required=True, type=str, help="Action name")
    record_action.add_argument("--seconds", type=float, default=5.0, help="Recording duration in seconds")
    record_action.add_argument("--hz", type=float, default=20.0, help="Sampling rate in Hz")
    record_action.add_argument(
        "--countdown",
        type=float,
        default=3.0,
        help="Seconds between releasing torque and the start of sampling (gives you time to grab the arm).",
    )
    record_action.add_argument(
        "--hold-after",
        action="store_true",
        help="Re-enable torque on every motor after recording so the arm holds its final pose (default: leave released).",
    )
    record_action.add_argument(
        "--register",
        action="store_true",
        help="Register action name in manifest after saving JSON",
    )

    for nav_cmd, nav_help in (
        ("nav-forward", "Drive forward, then stop after --hold seconds."),
        ("nav-back", "Drive backward, then stop after --hold seconds."),
        ("nav-left", "Turn left for --duration seconds, then stop."),
        ("nav-right", "Turn right for --duration seconds, then stop."),
    ):
        nav_parser = sub.add_parser(nav_cmd, help=nav_help)
        nav_parser.add_argument("--speed", type=int, default=None, help="Speed percent 0-100 (default from env)")
        nav_parser.add_argument("--duration", type=float, default=None, help="Turn duration in seconds")
        nav_parser.add_argument("--hold", type=float, default=1.0, help="Forward/back hold seconds before stop")

    sub.add_parser("nav-stop", help="Immediately stop the BLDC drive motors.")
    sub.add_parser("nav-brake", help="Engage ZF brake on both wheels.")
    sub.add_parser("nav-release", help="Release ZF brake on both wheels.")
    sub.add_parser(
        "nav-bridge-ping",
        help=(
            "Open the Jetson GPIO navigation backend and run a quick "
            "initialisation check."
        ),
    )
    sub.add_parser(
        "nav-print-config",
        help=(
            "Print resolved invert flags, EL polarity, and BCM pin map for this "
            "process (no GPIO). Use to verify env vars are seen."
        ),
    )
    nav_probe_wiring = sub.add_parser(
        "nav-probe-wiring",
        help=(
            "Local only: slow HIGH/LOW on each EL + Z/F BCM, then PWM on both VR "
            "BCMs. Meter header then JYQD screw each step to find harness breaks."
        ),
    )
    nav_probe_wiring.add_argument(
        "--dwell",
        type=float,
        default=4.0,
        help="Seconds per HIGH/LOW phase on digital lines (default 4)",
    )
    nav_probe_wiring.add_argument(
        "--pwm-duty",
        type=float,
        default=50.0,
        help="VR PWM duty 0-100 during PWM phase (default 50)",
    )

    nav_diag = sub.add_parser(
        "nav-diag-forward",
        help=(
            "Bench: symmetric forward with the same start path as normal forward "
            "(kick, DIR gap, PWM reassert). If hubs still do not spin, check 24 V "
            "and wiring. Use NINA_NAV_STRAIGHT_OPPOSITE_NUDGE_SEC=0 to skip the "
            "straight preload nudge. Use --side left|right to run one hub only "
            "(other driver's EL off) for bring-up."
        ),
    )
    nav_diag.add_argument(
        "--side",
        choices=("left", "right", "both"),
        default="both",
        help=(
            "Which wheel: both (default, symmetric), or one side with the other "
            "JYQD disabled (EL off, PWM 0)."
        ),
    )
    nav_diag.add_argument(
        "--speed",
        type=int,
        default=50,
        help="PWM duty 0-100 (default 50)",
    )
    nav_diag.add_argument(
        "--hold",
        type=float,
        default=3.0,
        help="Seconds to hold torque (default 3)",
    )

    nav_probe_eldir = sub.add_parser(
        "nav-probe-eldir",
        help=(
            "Bench: arm both wheels forward with PWM 0 — probe EL and Z/F at the "
            "JYQD screws with no hub torque (multimeter vs GND)."
        ),
    )
    nav_probe_eldir.add_argument(
        "--hold",
        type=float,
        default=15.0,
        help="Seconds to hold armed-forward / zero-speed (default 15)",
    )

    nav_test = sub.add_parser(
        "nav-test-pin",
        help="Drive a single GPIO pin or PWM output for diagnostics. Probe with a multimeter.",
    )
    nav_test.add_argument("--pin", required=True, type=int, help="BCM pin number to drive")
    nav_test.add_argument("--mode", choices=("high", "low", "pwm"), default="high",
                          help="Drive HIGH, LOW, or PWM at --duty")
    nav_test.add_argument("--duty", type=float, default=50.0, help="PWM duty 0-100 (only for --mode pwm)")
    nav_test.add_argument("--hold", type=float, default=5.0, help="Seconds to hold the signal")

    nav_dir = sub.add_parser(
        "nav-test-direction",
        help="Run motors alternating forward/back every --interval seconds for diagnosing F/R wiring.",
    )
    nav_dir.add_argument("--side", choices=("left", "right", "both"), default="both",
                         help="Which wheel to test")
    nav_dir.add_argument("--speed", type=int, default=80, help="Speed percent")
    nav_dir.add_argument("--interval", type=float, default=3.0, help="Seconds per direction phase")
    nav_dir.add_argument("--cycles", type=int, default=3, help="Number of fwd/back cycles")

    args = parser.parse_args()
    settings, dxl, action_runner, startup_service = build_app()

    if args.command == "startup":
        try:
            result = startup_service.boot()
            if not result.success:
                raise SystemExit(result.message)
            print(result.message)
        finally:
            dxl.close()
        return

    if args.command == "run-action":
        audio_timer: "threading.Timer | None" = None
        try:
            ensure_motors_ready(dxl)
            mode = "smooth" if not args.no_smooth else "stepped"
            audio_rel = action_runner.get_action_audio(args.name)
            audio_path = (
                settings.actions_dir / audio_rel
                if (audio_rel and not args.no_audio)
                else None
            )
            audio_offset = (
                args.audio_offset
                if args.audio_offset is not None
                else action_runner.get_action_audio_offset(args.name)
            )
            audio_offset = max(0.0, float(audio_offset))

            audio_note = ""
            if audio_path and audio_path.exists():
                offset_note = f" +{audio_offset:.2f}s" if audio_offset > 0 else ""
                audio_note = f", audio={audio_path.name}{offset_note}"
                player = AudioPlayer()
                if audio_offset > 0:
                    audio_timer = threading.Timer(
                        audio_offset, player.play, args=(audio_path,)
                    )
                    audio_timer.daemon = True
                    audio_timer.start()
                else:
                    player.play(audio_path)
            elif audio_rel and not args.no_audio:
                audio_note = f", audio MISSING ({audio_rel})"
            print(
                f"Playing '{args.name}' ({mode} mode, sub_hz={args.sub_hz}, "
                f"max_speed={args.max_speed}, speed={args.speed}x{audio_note})..."
            )
            action_path = action_runner.run_named_action(
                args.name,
                smooth=not args.no_smooth,
                sub_hz=args.sub_hz,
                max_speed=args.max_speed,
                speed=args.speed,
            )
            print(f"Action '{args.name}' executed from {action_path}")
        except Exception:
            if audio_timer is not None:
                audio_timer.cancel()
            raise
        finally:
            dxl.close()
        return

    if args.command == "list-actions":
        actions = action_runner.list_actions()
        for name, file_path in actions.items():
            print(f"{name}: {file_path}")
        return

    if args.command == "record-action":
        try:
            ensure_motors_ready(dxl)

            print("Releasing torque on all motors so you can move the arm by hand...")
            dxl.set_torque_all(False)

            countdown = max(0.0, float(args.countdown))
            if countdown > 0:
                whole = int(countdown)
                for remaining in range(whole, 0, -1):
                    print(f"  starting in {remaining}...")
                    time.sleep(1.0)
                fractional = countdown - whole
                if fractional > 0:
                    time.sleep(fractional)

            interval = 1.0 / args.hz
            sample_count = max(1, int(args.seconds * args.hz))
            frames = []
            print(f"Recording '{args.name}' for {args.seconds}s at {args.hz} Hz ({sample_count} samples)...")
            for _ in range(sample_count):
                frames.append(dxl.capture_frame(duration=interval))
                time.sleep(interval)
            print("Recording complete.")

            if args.hold_after:
                print("Re-enabling torque so the arm holds its current pose.")
                dxl.set_torque_all(True)
            else:
                print("Leaving torque released; the arm is free to move. Run `startup` or `run-action <name>` to re-engage torque.")

            out_path = settings.recordings_dir / f"{args.name}.json"
            payload = {
                "robot": "nina",
                "description": f"Recorded action: {args.name}",
                "frame_count": len(frames),
                "frames": frames,
            }
            out_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
            print(f"Recording saved: {out_path}")

            if args.register:
                action_runner.register_action(args.name, f"recordings/{args.name}.json")
                print(f"Registered action '{args.name}' in manifest.")
        finally:
            dxl.close()
        return

    if args.command in ("nav-forward", "nav-back", "nav-left", "nav-right",
                        "nav-stop", "nav-brake", "nav-release"):
        nav = build_navigation(settings)
        speed = getattr(args, "speed", None)
        duration = getattr(args, "duration", None)
        hold = getattr(args, "hold", 1.0)
        run_nav_command(nav, args.command, speed=speed, duration=duration, hold=hold)
        print(f"Navigation command '{args.command}' completed.")
        return

    if args.command == "nav-bridge-ping":
        nav = build_navigation(settings)
        print("[INIT] Navigation: Jetson GPIO")
        try:
            nav.initialize()
            print("[OK] Navigation backend initialised.")
        except Exception as exc:
            print(f"[FAIL] {exc}")
            raise SystemExit(1)
        finally:
            try:
                nav.shutdown()
            except Exception:
                pass
        return

    if args.command == "nav-print-config":
        n = settings.navigation
        p = DEFAULT_PINS
        l_lvl = "LOW" if n.invert_left_dir else "HIGH"
        r_lvl = "HIGH" if n.invert_right_dir else "LOW"
        print(
            "Navigation config for this Python process.\n"
            "Export NINA_NAV_* in the **same shell** before "
            "`python3 -m nina.app.main ...` so values apply.\n"
        )
        print(f"  backend:             {n.backend_name}")
        print(f"  pwm_frequency_hz:    {n.pwm_frequency_hz}")
        print(f"  invert_left_dir:     {n.invert_left_dir}  (NINA_NAV_INVERT_LEFT)")
        print(f"  invert_right_dir:    {n.invert_right_dir}  (NINA_NAV_INVERT_RIGHT)")
        print(f"  el_active_low:       {n.el_active_low}  (NINA_NAV_EL_ACTIVE_LOW)")
        print(
            "\nBCM map (from DEFAULT_PINS at import; override with "
            "NINA_NAV_L_EN / L_DIR / L_PWM / R_*):\n"
        )
        print(
            f"  L_EN={p.l_en}  L_DIR(Z/F)={p.l_dir}  L_PWM={p.pwm_l}\n"
            f"  R_EN={p.r_en}  R_DIR(Z/F)={p.r_dir}  R_PWM={p.pwm_r}"
        )
        print(
            "\nLogical **forward** with **these** invert flags: "
            f"left Z/F → {l_lvl}, right Z/F → {r_lvl}."
        )
        if l_lvl == r_lvl:
            print(
                "  Both sides use the same level for forward "
                "(common with twin JYQD wiring + `NINA_NAV_INVERT_LEFT=1`)."
            )
        else:
            print(
                "  Baseline with no inverts is left HIGH / right LOW for forward; "
                "each `NINA_NAV_INVERT_*` flips that wheel only."
            )
        print(
            "\n`NINA_NAV_INVERT_*` only swaps forward vs backward Z/F sense — not "
            "missing EL, VR PWM, 24 V, or a misrouted DIR wire."
        )
        return

    if args.command == "nav-probe-wiring":
        from nina.controllers.gpio_backend import create_backend

        n = settings.navigation
        p = DEFAULT_PINS
        dwell = max(1.0, min(30.0, float(args.dwell)))
        pwm_d = max(0.0, min(100.0, float(args.pwm_duty)))
        phys = jetson_orin_nano_board_pin

        print(
            "\nnav-probe-wiring — one signal at a time (no NavigationManager).\n"
            "For each step: meter **Jetson header** (BCM→physical below), then "
            "**JYQD screw**.\n"
            "Expect ~3.3 V HIGH / ~0 V LOW on digital lines; VR ~time-averaged "
            "voltage at partial duty.\n"
            "Remove any `NINA_NAV_L_EN=18` / legacy overrides unless that is "
            "still your harness.\n"
        )
        print(
            f"  L_EL={p.l_en} phys{phys(p.l_en) or '?'}  "
            f"L_ZF={p.l_dir} phys{phys(p.l_dir) or '?'}  "
            f"L_VR={p.pwm_l} phys{phys(p.pwm_l) or '?'}"
        )
        print(
            f"  R_EL={p.r_en} phys{phys(p.r_en) or '?'}  "
            f"R_ZF={p.r_dir} phys{phys(p.r_dir) or '?'}  "
            f"R_VR={p.pwm_r} phys{phys(p.pwm_r) or '?'}\n"
        )
        backend = create_backend(n.backend_name)
        backend.setup()
        try:
            for label, pin in (
                ("L_EL", p.l_en),
                ("L_ZF", p.l_dir),
                ("R_EL", p.r_en),
                ("R_ZF", p.r_dir),
            ):
                print(f"\n--- {label} BCM{pin}: HIGH {dwell}s — probe now ---")
                backend.configure_output(pin)
                backend.write(pin, 1)
                time.sleep(dwell)
                print(f"--- {label} BCM{pin}: LOW {dwell}s — probe now ---")
                backend.write(pin, 0)
                time.sleep(dwell)
            for label, pin in (("L_VR", p.pwm_l), ("R_VR", p.pwm_r)):
                print(
                    f"\n--- {label} BCM{pin}: PWM {pwm_d}% @ {n.pwm_frequency_hz}Hz "
                    f"for {dwell}s — probe VR screw ---"
                )
                backend.configure_pwm(pin, n.pwm_frequency_hz)
                backend.set_duty(pin, pwm_d)
                time.sleep(dwell)
                backend.set_duty(pin, 0.0)
        finally:
            backend.shutdown()
        print(
            "\n[OK] Sequence complete. If header is clean but JYQD screw is not, "
            "the harness is still wrong or the screw is the wrong net."
        )
        return

    if args.command == "nav-diag-forward":
        nav = build_navigation(settings)
        try:
            nav.initialize()
            if args.side == "both":
                nav.diag_symmetric_forward(int(args.speed), float(args.hold))
                print(
                    f"[OK] nav-diag-forward: held {int(args.speed)}% symmetric forward "
                    f"for {float(args.hold)}s — if hubs did not move, probe EL/DIR/VR "
                    f"and 24 V (see pi_motor_bridge/PINMAP.md troubleshooting)."
                )
            else:
                nav.diag_single_side_forward(
                    args.side, int(args.speed), float(args.hold)
                )
                print(
                    f"[OK] nav-diag-forward: held {int(args.speed)}% forward on "
                    f"{args.side} wheel only ({float(args.hold)}s); other side EL off — "
                    "probe that hub's EL/DIR/VR and 24 V."
                )
        except Exception as exc:
            print(f"[FAIL] {exc}")
            raise SystemExit(1)
        finally:
            try:
                nav.shutdown()
            except Exception:
                pass
        return

    if args.command == "nav-probe-eldir":
        nav = build_navigation(settings)
        try:
            nav.initialize()
            print("[INIT] Navigation: Jetson GPIO")
            pins = nav.config.pins
            print(
                "Jetson BCM (probe vs header GND; default polarity, no "
                "NINA_NAV_INVERT_*):"
            )
            print(
                f"  Left:  EL=BCM{pins.l_en}  Z/F=BCM{pins.l_dir}  "
                f"VR=BCM{pins.pwm_l}"
            )
            print(
                f"  Right: EL=BCM{pins.r_en}  Z/F=BCM{pins.r_dir}  "
                f"VR=BCM{pins.pwm_r}"
            )
            print(
                f"  NINA_NAV_EL_ACTIVE_LOW={int(settings.navigation.el_active_low)} "
                f"(from env or default)"
            )
            if settings.navigation.el_active_low:
                print(
                    "Expected while held (active-low EL): both EL ~0 V (armed); "
                    "Left Z/F high; Right Z/F low (forward, mirrored). "
                    "VR lines ~0% duty."
                )
            else:
                print(
                    "Expected while held (default active-high EL): Left EL high, "
                    "Z/F high (forward); Right EL high, Z/F low (forward, mirrored). "
                    "VR lines should stay at 0% duty."
                )
            nav.diag_arm_forward_pwm_zero_hold(float(args.hold))
            print(
                f"[OK] nav-probe-eldir: held forward + 0% speed for "
                f"{float(args.hold)}s."
            )
        except Exception as exc:
            print(f"[FAIL] {exc}")
            raise SystemExit(1)
        finally:
            try:
                nav.shutdown()
            except Exception:
                pass
        return

    if args.command == "nav-test-direction":
        nav = build_navigation(settings)
        nav.initialize()
        pins = nav.config.pins
        try:
            sides_label = args.side
            print(
                f"Direction test on {sides_label} side(s). "
                f"L_ZF/DIR=BCM{pins.l_dir} R_ZF/DIR=BCM{pins.r_dir}. "
                f"effective_invert L={nav.get_invert_left()} R={nav.get_invert_right()}. "
                f"Watch the wheel(s) — they should physically reverse between phases."
            )
            for cycle in range(args.cycles):
                for direction in ("forward", "backward"):
                    dir_const = nav.DIR_FORWARD if direction == "forward" else nav.DIR_BACKWARD
                    if args.side in ("left", "both"):
                        zf_level = (1 if direction == "forward" else 0)
                        if nav.get_invert_left():
                            zf_level = 0 if zf_level else 1
                        print(
                            f"[cycle {cycle + 1}/{args.cycles}] LEFT -> {direction} @ {args.speed}% "
                            f"(BCM{pins.l_dir} -> {'HIGH' if zf_level else 'LOW'})"
                        )
                        nav._control_speed(nav.SIDE_LEFT, True, args.speed, dir_const)
                    if args.side in ("right", "both"):
                        # Right wheel polarity is mirrored on the RPi
                        # reference: forward = LOW on R_DIR.
                        zf_level = (0 if direction == "forward" else 1)
                        if nav.get_invert_right():
                            zf_level = 0 if zf_level else 1
                        print(
                            f"[cycle {cycle + 1}/{args.cycles}] RIGHT -> {direction} @ {args.speed}% "
                            f"(BCM{pins.r_dir} -> {'HIGH' if zf_level else 'LOW'})"
                        )
                        nav._control_speed(nav.SIDE_RIGHT, True, args.speed, dir_const)
                    time.sleep(args.interval)
                    nav.stop()
                    time.sleep(0.3)
        finally:
            nav.shutdown()
        print(
            "Direction test done.\n"
            "  If hubs **never** spun: `NINA_NAV_INVERT_*` will not change that — "
            "fix EL, VR (PWM), 24 V, and Z/F wiring first. Run "
            "`python3 -m nina.app.main nav-print-config` in the same shell you "
            "`export` vars in to confirm flags.\n"
            "  If the wheel spun the **same way** in both phases:\n"
            "  1. Confirm JYQD Z/F is wired to the BCM pin printed above.\n"
            "  2. `nav-test-pin --pin <BCM> --mode high` then `low` — meter at the screw.\n"
            "  3. JYQD Z/F threshold is ~3 V; Jetson 3.3 V should be fine.\n"
            "  4. Try `NINA_NAV_INVERT_LEFT=1` or `NINA_NAV_INVERT_RIGHT=1` if "
            "levels toggle but F/R is wrong (two identical drivers may need "
            "`INVERT_RIGHT=1` so both sides use HIGH for forward — see PINMAP)."
        )
        return

    if args.command == "nav-test-pin":
        from nina.controllers.gpio_backend import create_backend
        backend = create_backend(settings.navigation.backend_name)
        backend.setup()
        try:
            if args.mode == "pwm":
                backend.configure_pwm(args.pin, settings.navigation.pwm_frequency_hz)
                backend.set_duty(args.pin, args.duty)
                print(f"BCM {args.pin} -> PWM @ {args.duty}% duty, {settings.navigation.pwm_frequency_hz} Hz for {args.hold}s")
            else:
                backend.configure_output(args.pin)
                value = 1 if args.mode == "high" else 0
                backend.write(args.pin, value)
                print(f"BCM {args.pin} -> {'HIGH' if value else 'LOW'} for {args.hold}s")
            time.sleep(args.hold)
        finally:
            backend.shutdown()
        return


if __name__ == "__main__":
    main()
