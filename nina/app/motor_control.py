"""
Operator CLI for Nina's drive (hoverboard lean via Dynamixel).

Run with the package path so imports resolve correctly:

    python3 -m nina.app.motor_control

Drives hoverboard lean axes (Dynamixel MX-28 IDs from ``NINA_HOVER_*``).

Navigation timing / polarity tunables (``NINA_NAV_*``, ``invert_*_dir``) come
from `nina.config.settings.load_settings()` so this CLI behaves
identically to the GUI's Drive screen - the same env-var overrides
apply to both.
"""

import logging
from pathlib import Path

from nina.config.settings import load_settings
from nina.app.main import build_navigation


CONTROLS_HELP = (
    "\nControls:\n"
    "  w : Forward\n"
    "  s : Backward\n"
    "  a : Left Turn\n"
    "  d : Right Turn\n"
    "  b : Engage Brake (ZF)\n"
    "  r : Release Brake (ZF)\n"
    "  space / q : Stop\n"
    "  x : Exit\n"
    "--------------------------------------------------"
)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

    print("--------------------------------------------------")
    print("Sirena Technologies - Nina Manual Motor Control")
    print("--------------------------------------------------")

    repo_root = Path(__file__).resolve().parents[2]
    settings = load_settings(repo_root)
    print("[INIT] Navigation: Hoverboard lean (Dynamixel MX-28)")
    nav = build_navigation(settings)
    try:
        nav.initialize()
    except Exception as exc:
        print(f"[CRITICAL ERROR] {exc}")
        return

    print(CONTROLS_HELP)
    nav.stop()

    try:
        while True:
            try:
                user_input = input("Command >> ").lower().strip()
            except EOFError:
                break

            if user_input == "w":
                print("[ACTION] Moving Forward...")
                nav.forward()
            elif user_input == "s":
                print("[ACTION] Moving Backward...")
                nav.backward()
            elif user_input == "a":
                print("[ACTION] Turning Left...")
                nav.turn_left()
            elif user_input == "d":
                print("[ACTION] Turning Right...")
                nav.turn_right()
            elif user_input == "b":
                print("[ACTION] Engaging Brake...")
                nav.engage_brake()
            elif user_input == "r":
                print("[ACTION] Releasing Brake...")
                nav.release_brake()
            elif user_input in (" ", "q", ""):
                print("[ACTION] Stopping...")
                nav.stop()
            elif user_input == "x":
                print("[EXIT] Exiting program...")
                break
            else:
                print("Unknown command. Use w, a, s, d, space, or x.")

    except KeyboardInterrupt:
        print("\n[INTERRUPT] Program stopped by user.")
    finally:
        print("[CLEANUP] Stopping robot safely...")
        try:
            nav.emergency_stop()
        finally:
            nav.shutdown()
        print("Cleanup completed. Goodbye!")


if __name__ == "__main__":
    main()
