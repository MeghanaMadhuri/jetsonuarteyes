"""FastAPI app: same routes as legacy ``nina.link_daemon.api`` where applicable."""

from __future__ import annotations

import ipaddress
import hashlib
import logging
import os
import time
import secrets
import socket
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterator, Optional

from fastapi import FastAPI, Header, HTTPException, Query, Request, Response, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel, ConfigDict, Field

from nina.jetson_net import actions_manifest
from nina.jetson_net.config import LinkDaemonConfig
from nina.jetson_net import host_control
from nina.jetson_net import manifest_audio
from nina.jetson_net import manifest_delete
from nina.jetson_net import media_static
from nina.jetson_net import session_claim
from nina.jetson_net.state import LinkCoordinator, UserMode
from nina.jetson_net.nm import NMError
from sirena_ui.android_gateway.command_plane import QtCommandPlane
from sirena_ui.android_gateway.drive_http import (
    drive_hold_start,
    drive_hold_stop,
    drive_turn,
    emergency_stop,
    momentary_drive,
    navigation_hw_status,
    robot_set_brake,
    set_drive_reverse,
    set_wheel_invert,
)
from sirena_ui.workers import drive_controller as drive_controller_mod
from sirena_ui.android_gateway.health_build import build_robot_health, safe_map_filename
from sirena_ui.android_gateway.robot_actions import RobotActionController
from sirena_ui.android_gateway import depth_stream
from sirena_ui.android_gateway.vision_mjpeg import VisionMjpegHub
from sirena_ui.android_gateway import vision_tablet
from sirena_ui.workers import slam_worker as slam_mod
from sirena_ui.workers.background_tasks import run_blocking as _run_bg
from sirena_ui.workers.nina_service import NinaService

log = logging.getLogger("sirena_ui.android_gateway.fastapi_app")

_UI_POLL_GET_PATHS = frozenset(
    {
        "/v1/status",
        "/v1/robot/capabilities",
        "/v1/robot/health",
        "/v1/robot/drive/status",
        "/v1/vision/status",
        "/v1/vision/detections",
        "/v1/vision/enroll/status",
        "/v1/vision/announce/status",
        "/v1/vision/follow/status",
        "/v1/vision/faces",
        "/v1/slam/status",
        "/v1/slam/snapshot",
        "/v1/slam/occupancy",
        "/v1/depth/status",
        "/v1/autonomy/status",
        "/v1/actions/record/status",
    }
)


def _client_ip(request: Request) -> str:
    xff = request.headers.get("x-forwarded-for")
    if xff:
        return xff.split(",")[0].strip()
    if request.client:
        return request.client.host
    return ""


def _is_loopback(ip: str) -> bool:
    try:
        return ipaddress.ip_address(ip).is_loopback
    except ValueError:
        return ip == "127.0.0.1"


def _effective_robot_display_name(
    cfg: Optional[LinkDaemonConfig],
    coordinator: Optional[LinkCoordinator],
) -> str:
    """Env ``NINA_LINK_ROBOT_NAME`` wins; else value persisted in ``link_state.json``."""
    if cfg is not None:
        env_dn = (cfg.robot_display_name or "").strip()
        if env_dn:
            return env_dn
    if coordinator is not None:
        return (coordinator.ps.robot_display_name or "").strip()
    return ""


def _system_identity(
    cfg: Optional[LinkDaemonConfig] = None,
    coordinator: Optional[LinkCoordinator] = None,
) -> Dict[str, str]:
    machine_id = ""
    for p in ("/etc/machine-id", "/var/lib/dbus/machine-id"):
        try:
            if os.path.exists(p):
                machine_id = Path(p).read_text(encoding="utf-8").strip()
                if machine_id:
                    break
        except OSError:
            continue
    if not machine_id:
        machine_id = socket.gethostname()
    stable = hashlib.sha256(machine_id.encode("utf-8")).hexdigest()[:16]
    hostname = (
        os.environ.get("NINA_LINK_DISPLAY_HOSTNAME", "").strip()
        or socket.gethostname()
    )
    out: Dict[str, str] = {"system_id": stable, "hostname": hostname}
    dn = _effective_robot_display_name(cfg, coordinator)
    if dn:
        out["display_name"] = dn
    return out


class ModeBody(BaseModel):
    mode: str = Field(..., description="boot_default | force_ap | force_sta")


class HomeWifiBody(BaseModel):
    ssid: str = Field(..., min_length=1, max_length=128)
    password: str = Field(default="", max_length=128)


class PairBody(BaseModel):
    pin: str = Field(..., min_length=4, max_length=12)


class DriveBody(BaseModel):
    direction: str = Field(
        ...,
        description="forward | back | left | right | stop",
    )
    duration_ms: int = Field(default=280, ge=50, le=5000)
    speed_percent: Optional[int] = Field(default=None, ge=5, le=100)


class DriveInvertBody(BaseModel):
    left: Optional[bool] = None
    right: Optional[bool] = None


class DriveBrakeBody(BaseModel):
    """Same semantics as kiosk ``DriveScreen`` brake pill (``DriveController.set_brake``)."""

    on: bool = Field(..., description="True = brake engaged (servos to brake pose)")


class DriveHoldBody(BaseModel):
    direction: str = Field(..., description="forward | back | left | right")


class DriveTurnBody(BaseModel):
    which: str = Field(..., description="left | right")


class DriveReverseBody(BaseModel):
    on: bool = Field(..., description="True = swap forward/back at the drive layer")


class PlayActionBody(BaseModel):
    action: str = Field(..., min_length=1, max_length=160)


class AutonomyEnabledBody(BaseModel):
    enabled: bool = True


class SlamSaveBody(BaseModel):
    filename: str = Field(default="nina_map.pgm", max_length=120)


class SlamRunningBody(BaseModel):
    """Start or stop the SLAM worker thread (matches Qt Map \"Start / Stop mapping\")."""

    running: bool = True


class AutonomyGoalBody(BaseModel):
    x_mm: float = Field(...)
    y_mm: float = Field(...)


class RecordStartBody(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    name: str = Field(..., min_length=1, max_length=64)
    seconds: float = Field(default=5.0, ge=0.5, le=120.0)
    hz: float = Field(default=20.0, ge=0.5, le=60.0)
    countdown: float = Field(default=3.0, ge=0.0, le=60.0)
    hold_after: bool = Field(default=False)
    register_manifest: bool = Field(default=True, alias="register")


class VisionOptionsBody(BaseModel):
    face: Optional[bool] = None
    objects: Optional[bool] = None
    object_confidence: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    resolution: Optional[str] = Field(
        default=None,
        description="e.g. 640x480 — forwarded to vision worker when camera open",
    )


class HoverStraightBody(BaseModel):
    backward: bool = False


class HoverCalibrationPreviewBody(BaseModel):
    left: int = Field(..., ge=0, le=4095)
    right: int = Field(..., ge=0, le=4095)


class HoverCalibrationSaveBody(BaseModel):
    forward_pos_left: Optional[int] = Field(default=None, ge=0, le=4095)
    forward_pos_right: Optional[int] = Field(default=None, ge=0, le=4095)
    backward_pos_left: Optional[int] = Field(default=None, ge=0, le=4095)
    backward_pos_right: Optional[int] = Field(default=None, ge=0, le=4095)
    turn_left_pos_left: Optional[int] = Field(default=None, ge=0, le=4095)
    turn_left_pos_right: Optional[int] = Field(default=None, ge=0, le=4095)
    turn_right_pos_left: Optional[int] = Field(default=None, ge=0, le=4095)
    turn_right_pos_right: Optional[int] = Field(default=None, ge=0, le=4095)
    turn_duration_sec: Optional[float] = Field(default=None, ge=0.01, le=1.0)


class ArucoStartBody(BaseModel):
    marker_id: int = Field(default=0, ge=0, le=999)


class VisionEnrollBody(BaseModel):
    name: str = Field(..., min_length=1, max_length=160)
    target_samples: int = Field(default=8, ge=1, le=32)


class VisionFollowStartBody(BaseModel):
    """Empty ``target`` follows the largest face; otherwise match enrolled identity."""

    target: str = Field(default="", max_length=160)


class ActionAudioOffsetBody(BaseModel):
    action: str = Field(..., min_length=1, max_length=160)
    audio_offset: float = Field(default=0.0, ge=0.0, le=120.0)


class ActionNameBody(BaseModel):
    action: str = Field(..., min_length=1, max_length=160)


class ActionAudioGenerateBody(BaseModel):
    action: str = Field(..., min_length=1, max_length=160)
    text: str = Field(..., min_length=1, max_length=2000)
    lang: str = Field(default="en", min_length=2, max_length=16)
    tld: str = Field(default="us", min_length=2, max_length=16)
    audio_offset: float = Field(default=0.0, ge=0.0, le=120.0)
    slow: bool = Field(default=False)


class DeleteManifestActionBody(BaseModel):
    action: str = Field(..., min_length=1, max_length=160)
    delete_recording: bool = Field(default=True)
    delete_audio: bool = Field(default=False)


class SystemVolumeBody(BaseModel):
    volume_pct: int = Field(ge=0, le=100)


class RobotDisplayNameBody(BaseModel):
    """Persisted robot label (``link_state.json``). Empty clears saved name; env override unchanged."""

    display_name: str = Field(default="", max_length=128)


@dataclass
class TabletGateway:
    service: NinaService
    plane: QtCommandPlane
    actions: RobotActionController
    vision_hub: VisionMjpegHub
    coordinator: LinkCoordinator
    cfg: LinkDaemonConfig


def create_tablet_app(gw: TabletGateway) -> FastAPI:
    app = FastAPI(title="Sirena Tablet Gateway", version="1.0.0")
    cfg = gw.cfg
    coordinator = gw.coordinator

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.middleware("http")
    async def http_request_trace(request: Request, call_next):
        """Per-request DEBUG timing when ``NINA_LINK_HTTP_TRACE=1`` (pair with LOGLEVEL=DEBUG)."""
        if not cfg.log_http_trace:
            return await call_next(request)
        t0 = time.perf_counter()
        ip = _client_ip(request)
        path = request.url.path
        qs = request.url.query
        qs_tail = f"?{qs[:120]}" if qs else ""
        log.debug(
            "gateway_trace >> method=%s path=%s%s client=%s",
            request.method,
            path,
            qs_tail,
            ip or "-",
        )
        try:
            response = await call_next(request)
        except Exception:
            elapsed_ms = (time.perf_counter() - t0) * 1000.0
            log.exception(
                "gateway_trace !! method=%s path=%s client=%s elapsed_ms=%.1f",
                request.method,
                path,
                ip or "-",
                elapsed_ms,
            )
            raise
        elapsed_ms = (time.perf_counter() - t0) * 1000.0
        log.debug(
            "gateway_trace << method=%s path=%s status=%s elapsed_ms=%.1f",
            request.method,
            path,
            getattr(response, "status_code", "?"),
            elapsed_ms,
        )
        return response

    def ensure_pairing_pin() -> None:
        if coordinator.ps.pairing_pin:
            return
        coordinator.ps.pairing_pin = f"{secrets.randbelow(1000000):06d}"
        coordinator.store.save(coordinator.ps)

    @app.middleware("http")
    async def touch_clients(request: Request, call_next):
        coordinator.record_http_client(_client_ip(request))
        ip = _client_ip(request)
        method = request.method.upper()
        path = request.url.path
        if path in ("/health", "/docs", "/openapi.json", "/redoc", "/favicon.ico"):
            return await call_next(request)
        skip_log = False
        if method == "GET" and path in _UI_POLL_GET_PATHS and not cfg.log_ui_poll_gets:
            skip_log = True
        if cfg.log_ui_requests and path.startswith("/v1/") and not skip_log:
            log.info("tablet http >> %s %s client=%s", method, path, ip or "-")
        response = await call_next(request)
        if cfg.log_ui_requests and path.startswith("/v1/") and not skip_log:
            log.info(
                "tablet http << %s %s status=%s client=%s",
                method,
                path,
                getattr(response, "status_code", "?"),
                ip or "-",
            )
        return response

    def auth_mutate(authorization: Optional[str], request: Request) -> None:
        ip = _client_ip(request)
        if _is_loopback(ip):
            return
        if cfg.token:
            token_ok = authorization == f"Bearer {cfg.token}"
            sess = coordinator.ps.session_token
            sess_ok = bool(sess and authorization == f"Bearer {sess}")
            if not (token_ok or sess_ok):
                raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Unauthorized")
            return
        return

    @app.get("/health")
    def health() -> Dict[str, Any]:
        """Minimal JSON for LAN scanners / load balancers (no NM calls)."""
        ident = _system_identity(cfg, coordinator)
        body: Dict[str, Any] = {
            "ok": True,
            "service": "nina-link",
            "mock_nm": cfg.mock_nm,
            "hostname": ident["hostname"],
            "system_id": ident["system_id"],
            "http_port": int(cfg.port),
        }
        if "display_name" in ident:
            body["display_name"] = ident["display_name"]
        return body

    @app.get("/v1/status")
    def get_status(request: Request) -> Dict[str, Any]:
        ensure_pairing_pin()
        ip = _client_ip(request)
        loop = _is_loopback(ip)
        role = coordinator.effective_wifi_role()
        ipv4 = coordinator.nm.get_ipv4_address()
        try:
            saved = coordinator.saved_networks_public()
        except Exception:
            log.exception("saved networks")
            saved = []
            coordinator.set_error("saved networks failed")

        wait_left = coordinator.boot_wait_remaining_sec()
        um = coordinator.user_mode_enum()

        sta_info: Dict[str, Optional[str]] = {"ssid": None, "profile_name": None}
        try:
            sta_info = coordinator.nm.active_wifi_station_info()
        except Exception:
            log.exception("active_wifi_station_info")

        try:
            coordinator.sync_last_sta_from_active_connection(sta_info)
        except Exception:
            log.exception("sync_last_sta_from_active_connection")

        body: Dict[str, Any] = {
            "wifi_role": role,
            "ipv4": ipv4,
            "user_mode": um.value,
            "boot_wait_remaining_sec": wait_left,
            "client_seen": coordinator.ps.client_seen,
            "saved_networks": saved,
            "last_error": coordinator.ps.last_error,
            "paired": bool(coordinator.ps.session_token),
            "ap_ssid": cfg.ap_ssid,
            "active_sta_ssid": sta_info.get("ssid"),
            "active_sta_profile": sta_info.get("profile_name"),
        }
        ident = _system_identity(cfg, coordinator)
        body["system_id"] = ident["system_id"]
        body["hostname"] = ident["hostname"]
        if "display_name" in ident:
            body["display_name"] = ident["display_name"]
        if loop and coordinator.ps.pairing_pin:
            body["pairing_pin"] = coordinator.ps.pairing_pin
        if loop:
            body["session_token_present"] = bool(coordinator.ps.session_token)
        return body

    @app.post("/v1/pair")
    def pair(body: PairBody) -> Dict[str, str]:
        ensure_pairing_pin()
        pin = coordinator.ps.pairing_pin or ""
        if not pin or body.pin.strip() != pin:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid PIN")
        token = coordinator.issue_session_token()
        return {"token": token, "token_type": "Bearer"}

    @app.post("/v1/mode")
    def set_mode(
        body: ModeBody,
        request: Request,
        authorization: Optional[str] = Header(None),
    ) -> Dict[str, Any]:
        auth_mutate(authorization, request)
        mode_map = {
            "boot_default": UserMode.BOOT_DEFAULT,
            "force_ap": UserMode.FORCE_AP,
            "force_sta": UserMode.FORCE_STA,
        }
        if body.mode not in mode_map:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "invalid mode")
        um = mode_map[body.mode]
        coordinator.set_user_mode(um)
        try:
            if um.value == "force_ap":
                coordinator.nm.start_hotspot(cfg.ap_ssid, cfg.ap_password)
                coordinator.ps.ap_started = True
                coordinator.store.save(coordinator.ps)
            elif um.value == "force_sta":
                saved = coordinator.refresh_saved_networks()
                if not saved:
                    coordinator.set_error("No saved Wi-Fi profiles")
                    return {
                        "ok": False,
                        "user_mode": body.mode,
                        "message": "Save home Wi-Fi credentials first.",
                    }
                coordinator.nm.activate_connection(saved[0].uuid)
                coordinator.remember_last_sta_profile(saved[0].uuid)
            coordinator.clear_error()
        except NMError as e:
            coordinator.set_error(str(e))
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                detail={"message": str(e), "details": e.details},
            ) from e
        return {"ok": True, "user_mode": body.mode}

    @app.post("/v1/wifi/home-credentials")
    def save_home_wifi(
        body: HomeWifiBody,
        request: Request,
        authorization: Optional[str] = Header(None),
    ) -> Dict[str, Any]:
        auth_mutate(authorization, request)
        try:
            sn = coordinator.nm.add_wifi_connection(
                body.ssid,
                body.password,
                id_hint=None,
            )
            coordinator.clear_error()
            return {"ok": True, "profile": {"id": sn.id, "uuid": sn.uuid, "ssid": sn.ssid}}
        except NMError as e:
            coordinator.set_error(str(e))
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                detail={"message": str(e), "details": e.details},
            ) from e

    @app.post("/v1/wifi/connect-home")
    def connect_home(
        request: Request,
        authorization: Optional[str] = Header(None),
        ssid: Optional[str] = Query(None),
    ) -> Dict[str, Any]:
        auth_mutate(authorization, request)
        saved = coordinator.refresh_saved_networks()
        if not saved:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "No saved Wi-Fi profiles")
        chosen = saved[0]
        if ssid:
            for p in saved:
                if p.ssid == ssid:
                    chosen = p
                    break
        try:
            coordinator.nm.activate_connection(chosen.uuid)
            coordinator.set_user_mode(UserMode.FORCE_STA)
            coordinator.remember_last_sta_profile(chosen.uuid)
            coordinator.clear_error()
            return {"ok": True, "connected_profile": chosen.ssid}
        except NMError as e:
            coordinator.set_error(str(e))
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                detail={"message": str(e), "details": e.details},
            ) from e

    @app.post("/v1/wifi/start-ap")
    def start_ap(
        request: Request,
        authorization: Optional[str] = Header(None),
    ) -> Dict[str, Any]:
        auth_mutate(authorization, request)
        try:
            coordinator.nm.start_hotspot(cfg.ap_ssid, cfg.ap_password)
            coordinator.ps.ap_started = True
            coordinator.store.save(coordinator.ps)
            coordinator.set_user_mode(UserMode.FORCE_AP)
            coordinator.clear_error()
            return {"ok": True, "ssid": cfg.ap_ssid}
        except NMError as e:
            coordinator.set_error(str(e))
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                detail={"message": str(e), "details": e.details},
            ) from e

    @app.delete("/v1/wifi/saved/{profile_id}")
    def delete_saved(
        profile_id: str,
        request: Request,
        authorization: Optional[str] = Header(None),
    ) -> Dict[str, bool]:
        auth_mutate(authorization, request)
        try:
            coordinator.nm.delete_connection(profile_id)
            coordinator.clear_error()
            return {"ok": True}
        except NMError as e:
            coordinator.set_error(str(e))
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                detail={"message": str(e), "details": e.details},
            ) from e

    @app.get("/v1/robot/capabilities")
    def capabilities() -> Dict[str, Any]:
        neutral = getattr(gw.service.settings, "neutral_action_name", None)
        if not isinstance(neutral, str) or not neutral.strip():
            neutral = "neutral"
        return {
            "neutral_action_name": neutral.strip(),
            "drive": "momentary" if cfg.enable_robot_bridge else "disabled",
            "robot_bridge_enabled": cfg.enable_robot_bridge,
            "drive_endpoint": "/v1/robot/drive",
            "drive_brake_endpoint": "/v1/robot/drive/brake",
            "default_duration_ms": cfg.robot_drive_default_duration_ms,
            "default_speed_percent": cfg.robot_drive_speed_percent,
            "drive_speed_min_percent": 8,
            "drive_speed_max_percent": 14,
            "drive_status_endpoint": "/v1/robot/drive/status",
            "drive_hold_endpoint": "/v1/robot/drive/hold",
            "drive_hold_stop_endpoint": "/v1/robot/drive/hold/stop",
            "drive_turn_endpoint": "/v1/robot/drive/turn",
            "drive_reverse_endpoint": "/v1/robot/drive/reverse",
            "manual_drive_speed_pct": int(drive_controller_mod.FIXED_MANUAL_DRIVE_SPEED_PCT),
            "drive_invert_endpoint": "/v1/robot/drive/invert",
            "actions_endpoint": "/v1/actions",
            "action_play_endpoint": "/v1/actions/play",
            "action_bridge_enabled": True,
            "action_delegate_configured": False,
            "record_bridge_enabled": cfg.enable_record_bridge,
            "record_start_endpoint": "/v1/actions/record/start",
            "record_stop_endpoint": "/v1/actions/record/stop",
            "record_status_endpoint": "/v1/actions/record/status",
            "recordings_list_endpoint": "/v1/actions/recordings",
            "vision_stream_endpoint": "/v1/vision/stream",
            "vision_snapshot_endpoint": "/v1/vision/snapshot",
            "vision_status_endpoint": "/v1/vision/status",
            "vision_options_endpoint": "/v1/vision/options",
            "vision_faces_endpoint": "/v1/vision/faces",
            "vision_follow_start_endpoint": "/v1/vision/follow/start",
            "vision_follow_stop_endpoint": "/v1/vision/follow/stop",
            "vision_follow_status_endpoint": "/v1/vision/follow/status",
            "vision_bridge_enabled": cfg.enable_vision_bridge,
            "actions_static_enabled": cfg.enable_actions_static,
            "media_file_endpoint": "/v1/media/file",
            "action_audio_info_endpoint": "/v1/actions/audio/info",
            "action_audio_offset_endpoint": "/v1/actions/audio/offset",
            "action_audio_clear_endpoint": "/v1/actions/audio/clear",
            "action_audio_generate_endpoint": "/v1/actions/audio/generate",
            "action_audio_preview_endpoint": "/v1/actions/audio/preview",
            "action_delete_endpoint": "/v1/actions/delete",
            "slam_status_endpoint": "/v1/slam/status",
            "slam_snapshot_endpoint": "/v1/slam/snapshot",
            "slam_occupancy_endpoint": "/v1/slam/occupancy",
            "slam_save_endpoint": "/v1/slam/save",
            "slam_running_endpoint": "/v1/slam/running",
            "slam_clear_endpoint": "/v1/slam/clear",
            "slam_bridge_enabled": cfg.enable_slam_bridge,
            "robot_health_endpoint": "/v1/robot/health",
            "depth_status_endpoint": "/v1/depth/status",
            "depth_stream_endpoint": "/v1/depth/stream",
            "depth_bridge_enabled": cfg.enable_depth_bridge,
            "autonomy_status_endpoint": "/v1/autonomy/status",
            "autonomy_enabled_endpoint": "/v1/autonomy/enabled",
            "autonomy_goal_endpoint": "/v1/autonomy/goal",
            "autonomy_bridge_enabled": cfg.enable_autonomy_bridge,
            "autonomy_supports_goto": cfg.enable_autonomy_bridge,
            "manifest_path": str(cfg.actions_manifest_path),
            "session_script_configured": bool(cfg.session_script),
            "message": "Embedded Sirena tablet gateway (NinaService).",
        }

    @app.get("/v1/robot/health")
    def robot_health_http() -> Dict[str, Any]:
        return _run_bg(
            lambda: build_robot_health(gw.service, cfg, coordinator),
            timeout=60.0,
        )

    @app.get("/v1/system/volume")
    def system_volume_get_http() -> Dict[str, Any]:
        from nina.services.audio_player import get_system_output_volume_pct

        pct = get_system_output_volume_pct()
        return {
            "ok": True,
            "available": pct is not None,
            "volume_pct": pct,
        }

    @app.post("/v1/system/volume")
    def system_volume_set_http(
        body: SystemVolumeBody,
        request: Request,
        authorization: Optional[str] = Header(None),
    ) -> Dict[str, Any]:
        auth_mutate(authorization, request)
        from nina.services.audio_player import (
            get_system_output_volume_pct,
            set_system_output_volume_pct,
        )

        ok = set_system_output_volume_pct(int(body.volume_pct))
        pct = get_system_output_volume_pct()
        return {
            "ok": ok,
            "available": pct is not None,
            "volume_pct": pct if pct is not None else int(body.volume_pct),
        }

    @app.post("/v1/robot/drive")
    def robot_drive(
        body: DriveBody,
        request: Request,
        authorization: Optional[str] = Header(None),
    ) -> Dict[str, Any]:
        auth_mutate(authorization, request)
        if not cfg.enable_robot_bridge:
            raise HTTPException(
                status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Robot bridge disabled — set NINA_LINK_ENABLE_ROBOT_BRIDGE=1",
            )
        direction = body.direction.strip().lower()
        allowed = frozenset({"forward", "back", "left", "right", "stop"})
        if direction not in allowed:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                detail=f"direction must be one of {sorted(allowed)}",
            )
        speed = (
            body.speed_percent
            if body.speed_percent is not None
            else cfg.robot_drive_speed_percent
        )
        duration_ms = body.duration_ms or cfg.robot_drive_default_duration_ms
        return gw.plane.submit(
            lambda: momentary_drive(
                gw.service,
                direction=direction,
                duration_ms=duration_ms,
                speed_percent=speed,
            ),
            timeout=30.0,
            urgent=direction == "stop",
        )

    @app.post("/v1/robot/drive/brake")
    def robot_drive_brake(
        body: DriveBrakeBody,
        request: Request,
        authorization: Optional[str] = Header(None),
    ) -> Dict[str, Any]:
        auth_mutate(authorization, request)
        if not cfg.enable_robot_bridge:
            raise HTTPException(
                status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Robot bridge disabled — set NINA_LINK_ENABLE_ROBOT_BRIDGE=1",
            )
        return gw.plane.submit(
            lambda: robot_set_brake(gw.service, on=body.on),
            timeout=30.0,
            urgent=bool(body.on),
        )

    @app.post("/v1/robot/drive/hold")
    def robot_drive_hold_http(
        body: DriveHoldBody,
        request: Request,
        authorization: Optional[str] = Header(None),
    ) -> Dict[str, Any]:
        auth_mutate(authorization, request)
        if not cfg.enable_robot_bridge:
            raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, detail="Robot bridge disabled")
        direction = body.direction.strip().lower()
        if direction not in frozenset({"forward", "back", "left", "right"}):
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                detail="direction must be forward, back, left, or right",
            )
        return gw.plane.submit(
            lambda: drive_hold_start(gw.service, direction=direction),
            timeout=30.0,
        )

    @app.post("/v1/robot/drive/hold/stop")
    def robot_drive_hold_stop_http(
        request: Request,
        authorization: Optional[str] = Header(None),
    ) -> Dict[str, Any]:
        auth_mutate(authorization, request)
        if not cfg.enable_robot_bridge:
            raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, detail="Robot bridge disabled")
        return gw.plane.submit(
            lambda: drive_hold_stop(gw.service),
            timeout=30.0,
            urgent=True,
        )

    @app.post("/v1/robot/drive/turn")
    def robot_drive_turn_http(
        body: DriveTurnBody,
        request: Request,
        authorization: Optional[str] = Header(None),
    ) -> Dict[str, Any]:
        auth_mutate(authorization, request)
        if not cfg.enable_robot_bridge:
            raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, detail="Robot bridge disabled")
        which = body.which.strip().lower()
        if which not in frozenset({"left", "right"}):
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                detail="which must be left or right",
            )
        return gw.plane.submit(
            lambda: drive_turn(gw.service, which=which),
            timeout=60.0,
        )

    @app.post("/v1/robot/drive/reverse")
    def robot_drive_reverse_http(
        body: DriveReverseBody,
        request: Request,
        authorization: Optional[str] = Header(None),
    ) -> Dict[str, Any]:
        auth_mutate(authorization, request)
        if not cfg.enable_robot_bridge:
            raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, detail="Robot bridge disabled")
        return gw.plane.submit(
            lambda: set_drive_reverse(gw.service, on=body.on),
            timeout=30.0,
        )

    @app.post("/v1/robot/emergency-stop")
    def robot_emergency_stop(
        request: Request,
        authorization: Optional[str] = Header(None),
    ) -> Dict[str, Any]:
        auth_mutate(authorization, request)
        if not cfg.enable_robot_bridge:
            raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, detail="Robot bridge disabled")
        return gw.plane.submit(
            lambda: emergency_stop(gw.service),
            timeout=30.0,
            urgent=True,
        )

    @app.get("/v1/robot/drive/status")
    def robot_drive_status_http() -> Dict[str, Any]:
        if not cfg.enable_robot_bridge:
            return {
                "ok": True,
                "bridge_enabled": False,
                "connected": False,
                "message": "Robot bridge disabled — set NINA_LINK_ENABLE_ROBOT_BRIDGE=1.",
                "invert_left": False,
                "invert_right": False,
                "brake": True,
            }
        from sirena_ui.android_gateway.drive_http import drive_status_payload

        try:
            st = gw.plane.submit(
                lambda: drive_status_payload(gw.service),
                timeout=30.0,
            )
        except Exception as exc:
            log.exception("GET /v1/robot/drive/status failed")
            st = {
                "ok": True,
                "connected": False,
                "hardware_initializing": False,
                "message": f"{type(exc).__name__}: {exc}",
                "invert_left": False,
                "invert_right": False,
                "brake": True,
            }
        st["bridge_enabled"] = True
        return st

    @app.post("/v1/robot/drive/straight")
    def robot_drive_straight_http(
        body: HoverStraightBody,
        request: Request,
        authorization: Optional[str] = Header(None),
    ) -> Dict[str, Any]:
        auth_mutate(authorization, request)
        if not cfg.enable_robot_bridge:
            raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, detail="Robot bridge disabled")
        from sirena_ui.android_gateway.tablet_drive_extras import hover_straight_start

        return gw.plane.submit(
            lambda: hover_straight_start(gw.service, backward=body.backward),
            timeout=30.0,
        )

    @app.post("/v1/robot/drive/straight/stop")
    def robot_drive_straight_stop_http(
        request: Request,
        authorization: Optional[str] = Header(None),
    ) -> Dict[str, Any]:
        auth_mutate(authorization, request)
        if not cfg.enable_robot_bridge:
            raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, detail="Robot bridge disabled")
        from sirena_ui.android_gateway.tablet_drive_extras import hover_straight_stop

        return gw.plane.submit(
            lambda: hover_straight_stop(gw.service),
            timeout=30.0,
            urgent=True,
        )

    @app.get("/v1/robot/drive/calibration")
    def robot_drive_calibration_get_http() -> Dict[str, Any]:
        if not cfg.enable_robot_bridge:
            return {"ok": False, "bridge_enabled": False}
        from sirena_ui.android_gateway.tablet_drive_extras import hover_calibration_snapshot

        return _run_bg(lambda: hover_calibration_snapshot(gw.service), timeout=30.0)

    @app.post("/v1/robot/drive/calibration/preview")
    def robot_drive_calibration_preview_http(
        body: HoverCalibrationPreviewBody,
        request: Request,
        authorization: Optional[str] = Header(None),
    ) -> Dict[str, Any]:
        auth_mutate(authorization, request)
        if not cfg.enable_robot_bridge:
            raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, detail="Robot bridge disabled")
        from sirena_ui.android_gateway.tablet_drive_extras import hover_calibration_preview

        return gw.plane.submit(
            lambda: hover_calibration_preview(gw.service, left=body.left, right=body.right),
            timeout=30.0,
        )

    @app.post("/v1/robot/drive/calibration/neutral")
    def robot_drive_calibration_neutral_http(
        request: Request,
        authorization: Optional[str] = Header(None),
    ) -> Dict[str, Any]:
        auth_mutate(authorization, request)
        if not cfg.enable_robot_bridge:
            raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, detail="Robot bridge disabled")
        from sirena_ui.android_gateway.tablet_drive_extras import hover_calibration_neutral

        return gw.plane.submit(lambda: hover_calibration_neutral(gw.service), timeout=30.0)

    @app.post("/v1/robot/drive/calibration/save")
    def robot_drive_calibration_save_http(
        body: HoverCalibrationSaveBody,
        request: Request,
        authorization: Optional[str] = Header(None),
    ) -> Dict[str, Any]:
        auth_mutate(authorization, request)
        if not cfg.enable_robot_bridge:
            raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, detail="Robot bridge disabled")
        from sirena_ui.android_gateway.tablet_drive_extras import hover_calibration_persist

        return gw.plane.submit(
            lambda: hover_calibration_persist(
                gw.service,
                forward_pos_left=body.forward_pos_left,
                forward_pos_right=body.forward_pos_right,
                backward_pos_left=body.backward_pos_left,
                backward_pos_right=body.backward_pos_right,
                turn_left_pos_left=body.turn_left_pos_left,
                turn_left_pos_right=body.turn_left_pos_right,
                turn_right_pos_left=body.turn_right_pos_left,
                turn_right_pos_right=body.turn_right_pos_right,
                turn_duration_sec=body.turn_duration_sec,
            ),
            timeout=60.0,
        )

    @app.post("/v1/robot/drive/invert")
    def robot_drive_invert(
        body: DriveInvertBody,
        request: Request,
        authorization: Optional[str] = Header(None),
    ) -> Dict[str, Any]:
        auth_mutate(authorization, request)
        if body.left is None and body.right is None:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                detail="Set at least one of left, right",
            )
        if not cfg.enable_robot_bridge:
            raise HTTPException(
                status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Robot bridge disabled — set NINA_LINK_ENABLE_ROBOT_BRIDGE=1.",
            )
        return gw.plane.submit(
            lambda: set_wheel_invert(gw.service, left=body.left, right=body.right),
            timeout=30.0,
        )

    @app.post("/v1/system/poweroff")
    def system_poweroff_http(
        request: Request,
        authorization: Optional[str] = Header(None),
    ) -> Dict[str, Any]:
        auth_mutate(authorization, request)
        return host_control.queue_poweroff()

    @app.post("/v1/system/reboot")
    def system_reboot_http(
        request: Request,
        authorization: Optional[str] = Header(None),
    ) -> Dict[str, Any]:
        auth_mutate(authorization, request)
        return host_control.queue_reboot()

    @app.post("/v1/system/display-name")
    def set_robot_display_name_http(
        body: RobotDisplayNameBody,
        request: Request,
        authorization: Optional[str] = Header(None),
    ) -> Dict[str, Any]:
        auth_mutate(authorization, request)
        coordinator.set_robot_display_name(body.display_name)
        ident = _system_identity(cfg, coordinator)
        return {"ok": True, "display_name": ident.get("display_name")}

    @app.get("/v1/actions")
    def list_actions_http() -> Dict[str, Any]:
        path = cfg.actions_manifest_path
        actions = actions_manifest.load_manifest_actions(path)
        return {"actions": actions, "manifest_path": str(path)}

    @app.post("/v1/actions/play")
    def play_action_http(
        body: PlayActionBody,
        request: Request,
        authorization: Optional[str] = Header(None),
    ) -> Dict[str, Any]:
        auth_mutate(authorization, request)
        name = body.action.strip()
        return gw.plane.submit(lambda: gw.actions.try_play(name), timeout=30.0)

    @app.get("/v1/actions/recordings")
    def list_recordings_http() -> Dict[str, Any]:
        items = actions_manifest.list_recordings_on_disk(cfg.actions_manifest_path)
        return {"recordings": items}

    @app.get("/v1/actions/record/status")
    def record_status_http() -> Dict[str, Any]:
        return gw.plane.submit(lambda: gw.actions.record_status(), timeout=10.0)

    @app.post("/v1/actions/record/start")
    def record_start_http(
        body: RecordStartBody,
        request: Request,
        authorization: Optional[str] = Header(None),
    ) -> Dict[str, Any]:
        auth_mutate(authorization, request)
        if not cfg.enable_record_bridge:
            raise HTTPException(
                status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Record bridge disabled — set NINA_LINK_ENABLE_RECORD_BRIDGE=1",
            )
        return gw.plane.submit(
            lambda: gw.actions.try_start_record(
                body.name.strip(),
                body.seconds,
                body.hz,
                body.countdown,
                body.hold_after,
                body.register_manifest,
            ),
            timeout=30.0,
        )

    @app.post("/v1/actions/record/stop")
    def record_stop_http(
        request: Request,
        authorization: Optional[str] = Header(None),
    ) -> Dict[str, Any]:
        auth_mutate(authorization, request)
        if not cfg.enable_record_bridge:
            raise HTTPException(
                status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Record bridge disabled",
            )
        return gw.plane.submit(lambda: gw.actions.try_stop_record(), timeout=30.0)

    @app.get("/v1/media/file")
    def media_file_http(relative: str = Query(..., min_length=1, max_length=512)):
        if not cfg.enable_actions_static:
            raise HTTPException(
                status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Static media disabled — set NINA_LINK_ENABLE_ACTIONS_STATIC=1",
            )
        root = cfg.actions_manifest_path.parent
        path = media_static.resolve_safe_media_path(root, relative)
        return FileResponse(
            path,
            media_type=media_static.guess_content_type(path),
        )

    def _require_actions_static_for_audio_edit() -> None:
        if not cfg.enable_actions_static:
            raise HTTPException(
                status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Enable NINA_LINK_ENABLE_ACTIONS_STATIC for audio edit endpoints.",
            )

    @app.get("/v1/actions/audio/info")
    def action_audio_info_http(action: str = Query(..., min_length=1, max_length=160)):
        _require_actions_static_for_audio_edit()
        try:
            return manifest_audio.action_audio_info(cfg.actions_manifest_path, action)
        except ValueError as exc:
            raise HTTPException(status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc

    @app.post("/v1/actions/audio/offset")
    def action_audio_offset_http(
        body: ActionAudioOffsetBody,
        request: Request,
        authorization: Optional[str] = Header(None),
    ) -> Dict[str, Any]:
        auth_mutate(authorization, request)
        _require_actions_static_for_audio_edit()
        try:
            return manifest_audio.set_action_audio_offset(
                cfg.actions_manifest_path,
                body.action.strip(),
                float(body.audio_offset),
            )
        except ValueError as exc:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    @app.post("/v1/actions/audio/clear")
    def action_audio_clear_http(
        body: ActionNameBody,
        request: Request,
        authorization: Optional[str] = Header(None),
    ) -> Dict[str, Any]:
        auth_mutate(authorization, request)
        _require_actions_static_for_audio_edit()
        try:
            return manifest_audio.clear_action_audio(
                cfg.actions_manifest_path, body.action.strip()
            )
        except ValueError as exc:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    @app.post("/v1/actions/audio/generate")
    def action_audio_generate_http(
        body: ActionAudioGenerateBody,
        request: Request,
        authorization: Optional[str] = Header(None),
    ) -> Dict[str, Any]:
        auth_mutate(authorization, request)
        _require_actions_static_for_audio_edit()
        actions_root = cfg.actions_manifest_path.parent

        def _run() -> Dict[str, Any]:
            out_path = gw.service.generate_action_audio(
                body.action.strip(),
                body.text,
                lang=body.lang,
                tld=body.tld,
                offset=float(body.audio_offset),
                slow=bool(body.slow),
            )
            return manifest_audio.action_audio_info(
                cfg.actions_manifest_path, body.action.strip()
            ) | {"generated_path": str(out_path)}

        return gw.plane.submit(_run, timeout=120.0)

    @app.post("/v1/actions/audio/preview")
    def action_audio_preview_http(
        body: ActionNameBody,
        request: Request,
        authorization: Optional[str] = Header(None),
    ) -> Dict[str, Any]:
        auth_mutate(authorization, request)
        _require_actions_static_for_audio_edit()

        def _run() -> Dict[str, Any]:
            actions_root = cfg.actions_manifest_path.parent
            action = body.action.strip()
            info = manifest_audio.get_action_audio_info(
                cfg.actions_manifest_path, actions_root, action
            )
            rel = info.get("audio_rel")
            if not rel:
                raise ValueError("This action has no attached audio clip.")
            rel_path = Path(rel)
            if rel_path.is_absolute() or ".." in rel_path.parts:
                raise ValueError("Invalid audio path.")
            audio_path = (actions_root / rel_path).resolve()
            root_res = actions_root.resolve()
            try:
                audio_path.relative_to(root_res)
            except ValueError as exc:
                raise ValueError("Invalid audio path.") from exc
            if not audio_path.is_file():
                raise FileNotFoundError("Audio clip file is missing on the robot.")
            from nina.services.audio_player import AudioPlayer

            AudioPlayer().play(audio_path)
            return {"ok": True}

        try:
            return gw.plane.submit(_run, timeout=120.0)
        except ValueError as exc:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST, detail=str(exc)
            ) from exc
        except FileNotFoundError as exc:
            raise HTTPException(
                status.HTTP_404_NOT_FOUND, detail=str(exc)
            ) from exc

    @app.post("/v1/actions/delete")
    def delete_manifest_action_http(
        body: DeleteManifestActionBody,
        request: Request,
        authorization: Optional[str] = Header(None),
    ) -> Dict[str, Any]:
        auth_mutate(authorization, request)
        _require_actions_static_for_audio_edit()
        actions_root = cfg.actions_manifest_path.parent
        try:
            return manifest_delete.delete_manifest_action(
                cfg.actions_manifest_path,
                actions_root,
                body.action.strip(),
                delete_recording=body.delete_recording,
                delete_audio=body.delete_audio,
            )
        except ValueError as exc:
            raise HTTPException(status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
        except FileNotFoundError as exc:
            raise HTTPException(
                status.HTTP_503_SERVICE_UNAVAILABLE,
                detail=str(exc),
            ) from exc

    @app.get("/v1/vision/status")
    def vision_status_http() -> Dict[str, Any]:
        if not cfg.enable_vision_bridge:
            raise HTTPException(
                status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Vision bridge disabled — set NINA_LINK_ENABLE_VISION_BRIDGE=1",
            )

        def _st() -> Dict[str, Any]:
            st = gw.service.vision.status()
            vw = gw.service.vision
            fps_val = float(getattr(vw, "_last_loop_fps", 0.0) or 0.0)
            cap_w, cap_h = vw._pipeline.capture_dimensions()  # noqa: SLF001
            return {
                "ok": True,
                "camera_open": st.camera_open,
                "face_ready": st.face_ready,
                "object_ready": st.object_ready,
                "message": st.message,
                "face_enabled": st.face_enabled,
                "object_enabled": st.object_enabled,
                "object_confidence": float(gw.service.vision.get_object_confidence()),
                "fps": round(fps_val, 2),
                "capture_width": int(cap_w),
                "capture_height": int(cap_h),
                "resolution": f"{int(cap_w)}x{int(cap_h)}",
            }

        return _run_bg(_st, timeout=30.0)

    @app.post("/v1/vision/options")
    def vision_options_http(
        body: VisionOptionsBody,
        request: Request,
        authorization: Optional[str] = Header(None),
    ) -> Dict[str, Any]:
        auth_mutate(authorization, request)
        if not cfg.enable_vision_bridge:
            raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, detail="Vision bridge disabled")

        def _opt() -> Dict[str, Any]:
            err_face = None
            err_obj = None
            if body.face is not None:
                try:
                    err_face = gw.service.vision._pipeline.set_face_enabled(bool(body.face))  # noqa: SLF001
                except Exception as e:
                    err_face = str(e)
            if body.objects is not None:
                try:
                    err_obj = gw.service.vision._pipeline.set_object_enabled(bool(body.objects))  # noqa: SLF001
                except Exception as e:
                    err_obj = str(e)
            if body.object_confidence is not None:
                gw.service.vision.set_object_confidence(float(body.object_confidence))
            if body.resolution:
                try:
                    w, h = (int(x) for x in body.resolution.lower().split("x", 1))
                    gw.service.vision.set_resolution(w, h)
                except ValueError:
                    pass
            st = gw.service.vision.status()
            cap_w, cap_h = gw.service.vision._pipeline.capture_dimensions()  # noqa: SLF001
            return {
                "ok": True,
                "camera_open": st.camera_open,
                "face_ready": st.face_ready,
                "object_ready": st.object_ready,
                "face_enabled": st.face_enabled,
                "object_enabled": st.object_enabled,
                "message": st.message,
                "object_confidence": float(gw.service.vision.get_object_confidence()),
                "capture_width": int(cap_w),
                "capture_height": int(cap_h),
                "resolution": f"{int(cap_w)}x{int(cap_h)}",
                "toggle_face_error": err_face,
                "toggle_object_error": err_obj,
            }

        return gw.plane.submit(_opt, timeout=30.0)

    @app.post("/v1/vision/open")
    def vision_open_http(
        request: Request,
        authorization: Optional[str] = Header(None),
    ) -> Dict[str, Any]:
        auth_mutate(authorization, request)
        if not cfg.enable_vision_bridge:
            raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, detail="Vision off")

        def _open() -> Dict[str, Any]:
            gw.service.vision.acquire()
            st = gw.service.vision.status()
            return {
                "camera_open": st.camera_open,
                "message": st.message,
                "face_ready": st.face_ready,
                "object_ready": st.object_ready,
            }

        return gw.plane.submit(_open, timeout=60.0)

    @app.post("/v1/vision/stop")
    def vision_stop_http(
        request: Request,
        authorization: Optional[str] = Header(None),
    ) -> Dict[str, str]:
        auth_mutate(authorization, request)
        if not cfg.enable_vision_bridge:
            raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, detail="Vision off")

        def _stop() -> Dict[str, str]:
            gw.service.vision.release()
            return {"ok": "true"}

        return gw.plane.submit(_stop, timeout=60.0)

    @app.get("/v1/vision/detections")
    def vision_detections_http() -> Dict[str, Any]:
        if not cfg.enable_vision_bridge:
            raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, detail="Vision off")

        def _dets() -> Dict[str, Any]:
            dets = list(gw.service.vision._stale_preview_detections)  # noqa: SLF001
            return {"detections": vision_tablet.detections_to_json(dets)}

        return gw.plane.submit(_dets, timeout=30.0)

    @app.post("/v1/vision/enroll")
    def vision_enroll_http(
        body: VisionEnrollBody,
        request: Request,
        authorization: Optional[str] = Header(None),
    ) -> Dict[str, Any]:
        auth_mutate(authorization, request)
        if not cfg.enable_vision_bridge:
            raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, detail="Vision off")

        def _en() -> Dict[str, Any]:
            vision_tablet.install_vision_hooks(gw.service)
            return vision_tablet.start_enroll_face(
                gw.service, body.name.strip(), body.target_samples
            )

        return gw.plane.submit(_en, timeout=30.0)

    @app.get("/v1/vision/enroll/status")
    def vision_enroll_status_http() -> Dict[str, Any]:
        if not cfg.enable_vision_bridge:
            raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, detail="Vision off")
        return vision_tablet.enroll_status_snapshot()

    @app.post("/v1/vision/announce")
    def vision_announce_http(
        request: Request,
        authorization: Optional[str] = Header(None),
    ) -> Dict[str, Any]:
        auth_mutate(authorization, request)
        if not cfg.enable_vision_bridge:
            raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, detail="Vision off")
        return gw.plane.submit(
            lambda: vision_tablet.start_announce_objects(gw.service),
            timeout=30.0,
        )

    @app.get("/v1/vision/announce/status")
    def vision_announce_status_http() -> Dict[str, Any]:
        if not cfg.enable_vision_bridge:
            raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, detail="Vision off")
        return vision_tablet.announce_error_snapshot()

    @app.get("/v1/vision/faces")
    def vision_faces_http() -> Dict[str, Any]:
        if not cfg.enable_vision_bridge:
            raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, detail="Vision off")

        def _faces() -> Dict[str, Any]:
            names = list(gw.service.vision.list_faces())
            return {"ok": True, "faces": sorted(names)}

        return gw.plane.submit(_faces, timeout=30.0)

    @app.get("/v1/vision/follow/status")
    def vision_follow_status_http() -> Dict[str, Any]:
        if not cfg.enable_vision_bridge:
            raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, detail="Vision off")

        def _st() -> Dict[str, Any]:
            ff = gw.service.face_follow
            return {
                "ok": True,
                "active": ff.is_active(),
                "message": vision_tablet.follow_status_message_snapshot(),
            }

        return gw.plane.submit(_st, timeout=30.0)

    @app.post("/v1/vision/follow/start")
    def vision_follow_start_http(
        body: VisionFollowStartBody,
        request: Request,
        authorization: Optional[str] = Header(None),
    ) -> Dict[str, Any]:
        auth_mutate(authorization, request)
        if not cfg.enable_vision_bridge:
            raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, detail="Vision off")

        def _start() -> Dict[str, Any]:
            vision_tablet.install_follow_hooks(gw.service)
            v = gw.service.vision
            st = v.status()
            if not st.camera_open:
                return {
                    "ok": False,
                    "error": "Camera is not open. Turn on the camera stream first.",
                }
            auto = gw.service.autonomy
            if auto.is_enabled():
                auto.set_enabled(False)
            v.set_face_enabled(True)
            ff = gw.service.face_follow
            raw_target = (body.target or "").strip()
            target = raw_target or None
            ok = ff.start(target)
            if not ok:
                return {
                    "ok": False,
                    "error": vision_tablet.follow_status_message_snapshot(),
                }
            try:
                w, h = v.capture_dimensions()
                ff.set_frame_size(w, h)
            except Exception:
                pass
            return {"ok": True, "active": ff.is_active()}

        return gw.plane.submit(_start, timeout=60.0)

    @app.post("/v1/vision/follow/stop")
    def vision_follow_stop_http(
        request: Request,
        authorization: Optional[str] = Header(None),
    ) -> Dict[str, Any]:
        auth_mutate(authorization, request)
        if not cfg.enable_vision_bridge:
            raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, detail="Vision off")

        def _stop() -> Dict[str, Any]:
            gw.service.face_follow.stop()
            return {"ok": True}

        return gw.plane.submit(_stop, timeout=30.0)

    @app.get("/v1/vision/aruco/status")
    def vision_aruco_status_http() -> Dict[str, Any]:
        if not cfg.enable_vision_bridge:
            raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, detail="Vision off")
        from sirena_ui.android_gateway import tablet_aruco

        return tablet_aruco.aruco_status()

    @app.post("/v1/vision/aruco/start")
    def vision_aruco_start_http(
        body: ArucoStartBody,
        request: Request,
        authorization: Optional[str] = Header(None),
    ) -> Dict[str, Any]:
        auth_mutate(authorization, request)
        if not cfg.enable_vision_bridge:
            raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, detail="Vision off")

        def _start() -> Dict[str, Any]:
            from sirena_ui.android_gateway import tablet_aruco

            vision_tablet.install_vision_hooks(gw.service)
            return tablet_aruco.aruco_start(gw.service, body.marker_id)

        return gw.plane.submit(_start, timeout=60.0)

    @app.post("/v1/vision/aruco/stop")
    def vision_aruco_stop_http(
        request: Request,
        authorization: Optional[str] = Header(None),
    ) -> Dict[str, Any]:
        auth_mutate(authorization, request)
        if not cfg.enable_vision_bridge:
            raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, detail="Vision off")

        def _stop() -> Dict[str, Any]:
            from sirena_ui.android_gateway import tablet_aruco

            return tablet_aruco.aruco_stop(gw.service)

        return gw.plane.submit(_stop, timeout=30.0)

    def _mjpeg_iter() -> Iterator[bytes]:
        boundary = b"frame"
        gw.vision_hub.client_enter(gw.service)
        try:
            while True:
                jpeg = gw.vision_hub.latest_jpeg()
                if jpeg:
                    yield (
                        b"--" + boundary + b"\r\n"
                        b"Content-Type: image/jpeg\r\n\r\n"
                        + jpeg
                        + b"\r\n"
                    )
                else:
                    import time as _time

                    _time.sleep(0.016)
        finally:
            gw.vision_hub.client_leave()
            try:
                gw.plane.submit(lambda: gw.service.vision.release(), timeout=30.0)
            except Exception:
                log.exception("vision stream: vision.release after disconnect")

    @app.get("/v1/vision/stream")
    def vision_stream_http() -> StreamingResponse:
        if not cfg.enable_vision_bridge:
            raise HTTPException(
                status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Vision bridge disabled — set NINA_LINK_ENABLE_VISION_BRIDGE=1",
            )

        def _prep() -> None:
            gw.service.vision.acquire()
            gw.vision_hub.attach(gw.service)

        gw.plane.submit(_prep, timeout=60.0)
        return StreamingResponse(
            _mjpeg_iter(),
            media_type="multipart/x-mixed-replace; boundary=frame",
        )

    @app.get("/v1/vision/snapshot")
    def vision_snapshot_http() -> Response:
        if not cfg.enable_vision_bridge:
            raise HTTPException(
                status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Vision bridge disabled — set NINA_LINK_ENABLE_VISION_BRIDGE=1",
            )

        def _prep() -> None:
            gw.service.vision.acquire()
            gw.vision_hub.attach(gw.service)

        gw.plane.submit(_prep, timeout=60.0)
        import time as _time

        for _ in range(150):
            jpeg = gw.vision_hub.latest_jpeg()
            if jpeg:
                return Response(content=jpeg, media_type="image/jpeg")
            _time.sleep(0.02)
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="No JPEG frame yet — ensure the camera pipeline is running.",
        )

    @app.get("/v1/slam/status")
    def slam_status_http() -> Dict[str, Any]:
        if not cfg.enable_slam_bridge:
            raise HTTPException(
                status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="SLAM bridge disabled — set NINA_LINK_ENABLE_SLAM_BRIDGE=1",
            )

        def _slam() -> Dict[str, Any]:
            gw.service.slam.start()
            st = gw.service.slam.status()
            out: Dict[str, Any] = {"ok": True, **st}
            snap = gw.service.slam.latest_snapshot()
            if snap is not None:
                out["snapshot"] = slam_mod._snapshot_to_dict(snap)
            return out

        return gw.plane.submit(_slam, timeout=30.0)

    @app.get("/v1/slam/snapshot")
    def slam_snapshot_http() -> Dict[str, Any]:
        if not cfg.enable_slam_bridge:
            raise HTTPException(
                status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="SLAM bridge disabled",
            )

        def _sn() -> Optional[Dict[str, Any]]:
            gw.service.slam.start()
            snap = gw.service.slam.latest_snapshot()
            if snap is None:
                return None
            meta = slam_mod._snapshot_to_dict(snap)
            return {**meta, "grid_bytes": snap.grid_bytes}

        out = gw.plane.submit(_sn, timeout=30.0)
        if out is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, detail="no SLAM snapshot yet")
        return out

    @app.get("/v1/slam/occupancy")
    def slam_occupancy_http() -> Response:
        if not cfg.enable_slam_bridge:
            raise HTTPException(
                status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="SLAM bridge disabled",
            )

        def _occ() -> Optional[Response]:
            gw.service.slam.start()
            snap = gw.service.slam.latest_snapshot()
            if snap is None:
                return None
            return Response(
                content=snap.grid_bytes,
                media_type="application/octet-stream",
                headers={
                    "X-Slam-Width": str(snap.width),
                    "X-Slam-Height": str(snap.height),
                    "X-Slam-Scale-Mm-Per-Px": str(snap.scale_mm_per_px),
                },
            )

        resp = gw.plane.submit(_occ, timeout=30.0)
        if resp is None:
            return Response(status_code=status.HTTP_204_NO_CONTENT)
        return resp

    @app.post("/v1/slam/save")
    def slam_save_http(
        body: SlamSaveBody,
        request: Request,
        authorization: Optional[str] = Header(None),
    ) -> Dict[str, Any]:
        auth_mutate(authorization, request)
        if not cfg.enable_slam_bridge:
            raise HTTPException(
                status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="SLAM bridge disabled",
            )
        fn = safe_map_filename(body.filename)
        repo_root = Path(__file__).resolve().parents[2]
        out_dir = repo_root / "nina" / "data" / "maps"
        out_dir.mkdir(parents=True, exist_ok=True)
        out_path = out_dir / fn

        def _save() -> Optional[Dict[str, Any]]:
            gw.service.slam.start()
            if not gw.service.slam.save_map(out_path):
                return None
            return {"ok": True, "path": str(out_path), "filename": fn}

        saved = gw.plane.submit(_save, timeout=60.0)
        if saved is None:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                detail="No SLAM map to save yet, or write failed",
            )
        return saved

    @app.post("/v1/slam/running")
    def slam_running_http(
        body: SlamRunningBody,
        request: Request,
        authorization: Optional[str] = Header(None),
    ) -> Dict[str, Any]:
        auth_mutate(authorization, request)
        if not cfg.enable_slam_bridge:
            raise HTTPException(
                status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="SLAM bridge disabled",
            )

        def _run() -> Dict[str, Any]:
            if body.running:
                gw.service.slam.start()
            else:
                gw.service.slam.stop()
            st = gw.service.slam.status()
            out: Dict[str, Any] = {"ok": True, **st}
            out["running"] = bool(st.get("running"))
            return out

        return gw.plane.submit(_run, timeout=30.0)

    @app.post("/v1/slam/clear")
    def slam_clear_http(
        request: Request,
        authorization: Optional[str] = Header(None),
    ) -> Dict[str, Any]:
        """Reset SLAM occupancy like Qt Map **Clear**: stop worker, restart engine.

        Mirrors ``MapScreen._on_clear_map``: temporarily disable autonomy if it was
        on (so motors / goto are not surprised mid-reset), then ``slam.stop()`` +
        ``slam.start()`` to re-initialise the map.
        """
        auth_mutate(authorization, request)
        if not cfg.enable_slam_bridge:
            raise HTTPException(
                status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="SLAM bridge disabled",
            )

        def _clear() -> Dict[str, Any]:
            was_auto = False
            if cfg.enable_autonomy_bridge:
                try:
                    was_auto = bool(gw.service.autonomy.is_enabled())
                    if was_auto:
                        gw.service.autonomy.set_enabled(False)
                except Exception:
                    log.exception("slam_clear: autonomy pre-disable")
            try:
                gw.service.slam.stop()
            except Exception:
                log.exception("slam_clear: slam.stop")
            try:
                gw.service.slam.start()
            except Exception:
                log.exception("slam_clear: slam.start")
            st = gw.service.slam.status()
            out: Dict[str, Any] = {
                "ok": True,
                "slam_reset": True,
                "autonomy_was_enabled": was_auto,
                **st,
            }
            out["running"] = bool(st.get("running"))
            return out

        return gw.plane.submit(_clear, timeout=60.0)

    @app.get("/v1/depth/status")
    def depth_status_http() -> Dict[str, Any]:
        if not cfg.enable_depth_bridge:
            return {
                "ok": False,
                "bridge_enabled": False,
                "message": "Depth bridge disabled",
            }
        return {"ok": True, "bridge_enabled": True, **depth_stream.status_payload()}

    def _depth_mjpeg_iter() -> Iterator[bytes]:
        boundary = b"frame"
        for jpeg in depth_stream.iter_depth_mjpeg(lambda: False, fps_cap=12.0):
            yield (
                b"--" + boundary + b"\r\n"
                b"Content-Type: image/jpeg\r\n\r\n"
                + jpeg
                + b"\r\n"
            )

    @app.get("/v1/depth/stream")
    def depth_stream_http() -> StreamingResponse:
        if not cfg.enable_depth_bridge:
            raise HTTPException(
                status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Depth bridge disabled — set NINA_LINK_ENABLE_DEPTH_BRIDGE=1",
            )
        ok_open, dmsg = depth_stream.acquire("stream_open_probe")
        if ok_open:
            depth_stream.release("stream_open_probe")
        else:
            raise HTTPException(
                status.HTTP_503_SERVICE_UNAVAILABLE,
                detail=f"depth camera unavailable: {dmsg}",
            )
        return StreamingResponse(
            _depth_mjpeg_iter(),
            media_type="multipart/x-mixed-replace; boundary=frame",
        )

    @app.get("/v1/autonomy/status")
    def autonomy_status_http() -> Dict[str, Any]:
        if not cfg.enable_autonomy_bridge:
            return {
                "ok": True,
                "bridge_enabled": False,
                "message": "Autonomy bridge disabled",
            }

        def _au() -> Dict[str, Any]:
            st = gw.service.autonomy.state()
            return {"ok": True, "bridge_enabled": True, **st}

        return gw.plane.submit(_au, timeout=30.0)

    @app.post("/v1/autonomy/enabled")
    def autonomy_enabled_http(
        body: AutonomyEnabledBody,
        request: Request,
        authorization: Optional[str] = Header(None),
    ) -> Dict[str, Any]:
        auth_mutate(authorization, request)
        if not cfg.enable_autonomy_bridge:
            raise HTTPException(
                status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Autonomy bridge disabled — set NINA_LINK_ENABLE_AUTONOMY_BRIDGE=1",
            )

        def _set() -> Dict[str, Any]:
            try:
                gw.service.autonomy.set_enabled(bool(body.enabled))
                return {
                    "ok": True,
                    "enabled": gw.service.autonomy.is_enabled(),
                }
            except Exception as exc:
                log.exception("POST /v1/autonomy/enabled")
                return {
                    "ok": False,
                    "enabled": False,
                    "error": str(exc).strip() or type(exc).__name__,
                }

        return gw.plane.submit(_set, timeout=60.0)

    @app.post("/v1/autonomy/goal")
    def autonomy_goal_set_http(
        body: AutonomyGoalBody,
        request: Request,
        authorization: Optional[str] = Header(None),
    ) -> Dict[str, Any]:
        auth_mutate(authorization, request)
        if not cfg.enable_autonomy_bridge:
            raise HTTPException(
                status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Autonomy bridge disabled — set NINA_LINK_ENABLE_AUTONOMY_BRIDGE=1",
            )

        def _g() -> Dict[str, Any]:
            try:
                gw.service.slam.start()
                return gw.service.autonomy.set_goal(body.x_mm, body.y_mm)
            except Exception as exc:
                log.exception("POST /v1/autonomy/goal")
                return {"ok": False, "message": str(exc).strip() or type(exc).__name__}

        return gw.plane.submit(_g, timeout=60.0)

    @app.delete("/v1/autonomy/goal")
    def autonomy_goal_clear_http(
        request: Request,
        authorization: Optional[str] = Header(None),
    ) -> Dict[str, Any]:
        auth_mutate(authorization, request)
        if not cfg.enable_autonomy_bridge:
            raise HTTPException(
                status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Autonomy bridge disabled",
            )

        def _c() -> Dict[str, Any]:
            try:
                return gw.service.autonomy.clear_goal()
            except Exception as exc:
                log.exception("DELETE /v1/autonomy/goal")
                return {"ok": False, "message": str(exc).strip() or type(exc).__name__}

        return gw.plane.submit(_c, timeout=60.0)

    @app.post("/v1/session/claim")
    def session_claim_http(
        request: Request,
        authorization: Optional[str] = Header(None),
    ) -> Dict[str, Any]:
        auth_mutate(authorization, request)
        if not cfg.session_script:
            return {
                "ok": True,
                "claimed": False,
                "skipped": True,
                "message": (
                    "NINA_LINK_SESSION_SCRIPT not set — kiosk handoff skipped "
                    "(optional on the Jetson)."
                ),
            }
        return session_claim.invoke_script(cfg.session_script, "claim")

    @app.post("/v1/session/release")
    def session_release_http(
        request: Request,
        authorization: Optional[str] = Header(None),
    ) -> Dict[str, Any]:
        auth_mutate(authorization, request)
        if not cfg.session_script:
            return {
                "ok": True,
                "released": False,
                "skipped": True,
                "message": "NINA_LINK_SESSION_SCRIPT not set — nothing to release.",
            }
        return session_claim.invoke_script(cfg.session_script, "release")

    return app
