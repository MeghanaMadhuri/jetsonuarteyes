#!/usr/bin/env python3
"""CLI for ESP8266 eye expressions (UART) and per-action manifest bindings."""

from __future__ import annotations

import argparse
import sys
import threading
from pathlib import Path

from nina.config.settings import load_settings
from nina.controllers.eye_expression_uart import EyeExpressionUartClient, EyeUartConfig
from nina.eye.expressions import EYE_EXPRESSIONS
from nina.jetson_net.manifest_eye import get_action_eye_info, set_action_eye


def _eye_client(settings) -> EyeExpressionUartClient:
    eu = settings.eye_uart
    return EyeExpressionUartClient(
        EyeUartConfig(
            enabled=eu.enabled,
            port=eu.port,
            baudrate=eu.baudrate,
            command_delay_sec=eu.command_delay_sec,
        )
    )


def cmd_list(_args: argparse.Namespace) -> int:
    for expr in EYE_EXPRESSIONS:
        print(f"{expr.id:2d}  {expr.name:16s}  mood {expr.mood}")
    return 0


def cmd_send(args: argparse.Namespace) -> int:
    repo = Path(__file__).resolve().parents[2]
    settings = load_settings(repo)
    client = _eye_client(settings)
    try:
        out = client.send_expression(args.id)
        print(f"Sent expression {out['id']} ({out['name']}) on {settings.eye_uart.port}")
    finally:
        client.close()
    return 0


def cmd_status(_args: argparse.Namespace) -> int:
    repo = Path(__file__).resolve().parents[2]
    settings = load_settings(repo)
    client = _eye_client(settings)
    try:
        st = client.status()
        for k, v in st.items():
            print(f"{k}: {v}")
    finally:
        client.close()
    return 0


def cmd_bind(args: argparse.Namespace) -> int:
    repo = Path(__file__).resolve().parents[2]
    settings = load_settings(repo)
    set_action_eye(
        settings.manifest_path,
        args.action,
        args.id,
        eye_offset=args.offset,
    )
    print(
        f"Bound action '{args.action}' → eye {args.id} "
        f"(offset {args.offset:.2f}s)"
    )
    return 0


def cmd_bind_clear(args: argparse.Namespace) -> int:
    repo = Path(__file__).resolve().parents[2]
    settings = load_settings(repo)
    set_action_eye(settings.manifest_path, args.action, None)
    print(f"Cleared eye binding for '{args.action}'")
    return 0


def cmd_show_action(args: argparse.Namespace) -> int:
    repo = Path(__file__).resolve().parents[2]
    settings = load_settings(repo)
    info = get_action_eye_info(settings.manifest_path, args.action)
    print(info)
    return 0


def cmd_play(args: argparse.Namespace) -> int:
    """Play action with manifest audio + eye (no GUI)."""
    from nina.app.main import build_app, ensure_motors_ready
    from nina.services.audio_player import AudioPlayer

    settings, dxl, action_runner, _startup = build_app()
    audio_timer = None
    eye_timer = None
    try:
        ensure_motors_ready(dxl)
        name = args.action
        audio_rel = action_runner.get_action_audio(name)
        audio_path = settings.actions_dir / audio_rel if audio_rel else None
        audio_off = action_runner.get_action_audio_offset(name)
        eid = action_runner.get_action_eye_expression(name)
        eye_off = action_runner.get_action_eye_offset(name)

        if audio_path and audio_path.exists():
            player = AudioPlayer()
            if audio_off > 0:
                audio_timer = threading.Timer(audio_off, player.play, args=(audio_path,))
                audio_timer.daemon = True
                audio_timer.start()
            else:
                player.play(audio_path)

        if eid is not None:
            client = _eye_client(settings)

            def _fire() -> None:
                try:
                    client.send_expression(eid)
                except Exception as exc:
                    print(f"eye send failed: {exc}", file=sys.stderr)

            if eye_off > 0:
                eye_timer = threading.Timer(eye_off, _fire)
                eye_timer.daemon = True
                eye_timer.start()
            else:
                _fire()

        action_runner.run_named_action(name, smooth=not args.no_smooth)
        print(f"Played '{name}'")
    finally:
        if audio_timer is not None:
            audio_timer.cancel()
        if eye_timer is not None:
            eye_timer.cancel()
        dxl.close()
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Nina ESP8266 eye UART + manifest bindings"
    )
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("list", help="List expression ids 0-33").set_defaults(
        func=cmd_list
    )
    p_send = sub.add_parser("send", help="Send expression id to ESP now")
    p_send.add_argument("id", type=int)
    p_send.set_defaults(func=cmd_send)

    sub.add_parser("status", help="UART client status").set_defaults(func=cmd_status)

    p_bind = sub.add_parser("bind", help="Bind expression to manifest action")
    p_bind.add_argument("action", type=str)
    p_bind.add_argument("id", type=int)
    p_bind.add_argument("--offset", type=float, default=0.0)
    p_bind.set_defaults(func=cmd_bind)

    p_clr = sub.add_parser("unbind", help="Remove eye fields from action")
    p_clr.add_argument("action", type=str)
    p_clr.set_defaults(func=cmd_bind_clear)

    p_show = sub.add_parser("show", help="Show action eye manifest fields")
    p_show.add_argument("action", type=str)
    p_show.set_defaults(func=cmd_show_action)

    p_play = sub.add_parser(
        "play",
        help="Run action (motion + manifest audio/eye offsets)",
    )
    p_play.add_argument("action", type=str)
    p_play.add_argument("--no-smooth", action="store_true")
    p_play.set_defaults(func=cmd_play)

    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
