"""Start uvicorn for the tablet gateway in a background thread."""

from __future__ import annotations

import logging
import os
import threading
from typing import Any, Optional

from PyQt5.QtWidgets import QApplication

from nina.jetson_net.config import LinkDaemonConfig, load_config
from nina.jetson_net.mdns_advertise import start_mdns_background, stop_mdns
from nina.jetson_net.nm import NMBackend, mock_backend
from nina.jetson_net.state import LinkCoordinator
from nina.jetson_net.wifi_boot import maybe_boot_ap
from sirena_ui.android_gateway.command_plane import QtCommandPlane
from sirena_ui.android_gateway.fastapi_app import (
    TabletGateway,
    _system_identity,
    create_tablet_app,
)
from sirena_ui.android_gateway.robot_actions import RobotActionController
from sirena_ui.android_gateway.vision_mjpeg import VisionMjpegHub
from sirena_ui.workers.nina_service import NinaService

log = logging.getLogger("sirena_ui.android_gateway.server")

_server_thread: Optional[threading.Thread] = None
_uvicorn_server: Any = None


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name, "").strip().lower()
    if raw in ("1", "true", "yes", "on", "y"):
        return True
    if raw in ("0", "false", "no", "off", "n"):
        return False
    return default


def _apply_embedded_feature_defaults(cfg: LinkDaemonConfig) -> None:
    """When running inside Sirena, turn on robot/vision/slam/… unless opted out.

    Set ``NINA_ANDROID_GATEWAY_ALL=0`` to keep legacy per-flag behaviour only.
    """
    if not _env_bool("NINA_ANDROID_GATEWAY_ALL", False):
        return
    cfg.enable_robot_bridge = True
    cfg.enable_record_bridge = True
    cfg.enable_vision_bridge = True
    cfg.enable_actions_static = True
    cfg.enable_slam_bridge = True
    cfg.enable_depth_bridge = True
    cfg.enable_autonomy_bridge = True


def start_tablet_gateway(service: NinaService) -> None:
    """Bind FastAPI (default ``NINA_ANDROID_HTTP_HOST`` / ``PORT`` or ``NINA_LINK_*``)."""
    global _server_thread, _uvicorn_server

    if not _env_bool("NINA_ANDROID_GATEWAY", True):
        log.info("NINA_ANDROID_GATEWAY=0 — tablet HTTP disabled")
        return

    app = QApplication.instance()
    if app is None:
        log.warning("tablet gateway: no QApplication — not starting")
        return

    cfg = load_config()
    _apply_embedded_feature_defaults(cfg)

    nm: NMBackend = (
        mock_backend()
        if cfg.mock_nm
        else NMBackend(
            mock=False,
            disable_wifi_autoconnect=cfg.disable_wifi_autoconnect,
            wifi_ready_timeout=float(cfg.wifi_ready_timeout_sec),
            wifi_ready_poll=float(cfg.wifi_ready_poll_sec),
            hotspot_attempts=cfg.hotspot_attempts,
        )
    )
    coordinator = LinkCoordinator(cfg, nm)
    # Always run boot Wi-Fi policy: restore ``last_sta_profile_id`` STA when possible,
    # independent of ``NINA_ANDROID_GATEWAY_BOOT_AP`` (that flag only gated this call before).
    maybe_boot_ap(coordinator)

    plane = QtCommandPlane(parent=app)
    actions = RobotActionController(service, parent=app)
    vision_hub = VisionMjpegHub(parent=app)

    gw = TabletGateway(
        service=service,
        plane=plane,
        actions=actions,
        vision_hub=vision_hub,
        coordinator=coordinator,
        cfg=cfg,
    )
    fastapi_app = create_tablet_app(gw)

    import uvicorn

    config = uvicorn.Config(
        fastapi_app,
        host=cfg.host,
        port=int(cfg.port),
        log_level="info",
        loop="asyncio",
    )
    _uvicorn_server = uvicorn.Server(config)

    def serve() -> None:
        log.info(
            "tablet gateway listening on http://%s:%s/health",
            cfg.host,
            cfg.port,
        )
        try:
            _uvicorn_server.run()
        except Exception:
            log.exception("tablet gateway server stopped")

    _server_thread = threading.Thread(
        target=serve,
        daemon=True,
        name="sirena-tablet-uvicorn",
    )
    _server_thread.start()

    if cfg.mdns_advertise:
        ident = _system_identity(cfg, coordinator)
        label = os.environ.get("NINA_LINK_MDNS_NAME", "").strip() or (
            f"Nina-{ident['display_name']}"
            if ident.get("display_name")
            else f"Nina-{ident['hostname']}"
        )
        start_mdns_background(
            bind_host=cfg.host,
            port=int(cfg.port),
            instance_label=label,
            system_id=ident["system_id"],
            hostname=ident["hostname"],
            coordinator=coordinator,
        )


def stop_tablet_gateway() -> None:
    global _uvicorn_server
    stop_mdns()
    srv = _uvicorn_server
    if srv is not None:
        try:
            srv.should_exit = True
        except Exception:
            log.exception("tablet gateway shutdown")
