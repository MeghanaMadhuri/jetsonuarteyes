"""Optional mDNS (Bonjour / DNS-SD) for the tablet HTTP gateway.

Enable with ``NINA_LINK_MDNS=1``. Requires the ``zeroconf`` package
(``pip install zeroconf`` or ``requirements-ui.txt``).

Advertises ``_http._tcp`` so generic browsers and Android NSD can find
``http://<discovered-host>:<port>/`` (use ``/health`` for a tiny JSON probe).
"""

from __future__ import annotations

import logging
import re
import socket
import threading
import time
from typing import TYPE_CHECKING, List, Optional

log = logging.getLogger("nina.jetson_net.mdns")

if TYPE_CHECKING:
    from nina.jetson_net.state import LinkCoordinator

_zc_lock = threading.Lock()
_zc_instance: Optional[object] = None  # Zeroconf
_registered_infos: List[object] = []  # list[ServiceInfo]

_INSTANCE_RE = re.compile(r"[^a-zA-Z0-9\- ]")


def _sanitize_instance_name(raw: str, *, max_len: int = 52) -> str:
    s = _INSTANCE_RE.sub("", (raw or "").strip()) or "nina"
    if len(s) > max_len:
        s = s[:max_len].rstrip()
    return s


def gather_ipv4_for_mdns(coordinator: Optional["LinkCoordinator"]) -> List[str]:
    """Collect non-loopback IPv4s (NM primary first, then getaddrinfo)."""
    seen: set[str] = set()
    out: List[str] = []
    if coordinator is not None:
        try:
            ip = coordinator.nm.get_ipv4_address()
            if ip:
                ip = ip.strip().split("/")[0]
                if ip and not ip.startswith("127.") and ip not in seen:
                    seen.add(ip)
                    out.append(ip)
        except Exception:
            log.debug("mdns: get_ipv4_address failed", exc_info=True)
    try:
        hn = socket.gethostname()
        for row in socket.getaddrinfo(hn, None, socket.AF_INET, socket.SOCK_STREAM):
            ip = row[4][0]
            if not ip or ip.startswith("127.") or ip in seen:
                continue
            seen.add(ip)
            out.append(ip)
    except OSError:
        pass
    return out


def _stop_locked() -> None:
    global _zc_instance, _registered_infos
    zc = _zc_instance
    if zc is None:
        _registered_infos = []
        return
    try:
        from zeroconf import Zeroconf
    except ImportError:
        _zc_instance = None
        _registered_infos = []
        return
    assert isinstance(zc, Zeroconf)
    for info in _registered_infos:
        try:
            zc.unregister_service(info)
        except Exception:
            log.debug("mdns unregister failed", exc_info=True)
    try:
        zc.close()
    except Exception:
        log.debug("mdns zeroconf close failed", exc_info=True)
    _zc_instance = None
    _registered_infos = []


def stop_mdns() -> None:
    """Unregister and close the global Zeroconf client (idempotent)."""
    with _zc_lock:
        _stop_locked()


def _run_register(
    *,
    bind_host: str,
    port: int,
    instance_label: str,
    system_id: str,
    hostname: str,
    coordinator: Optional["LinkCoordinator"],
    delay_sec: float,
) -> None:
    global _zc_instance, _registered_infos
    time.sleep(max(0.05, delay_sec))
    try:
        from zeroconf import ServiceInfo, Zeroconf
    except ImportError:
        log.warning(
            "NINA_LINK_MDNS is enabled but zeroconf is not installed; "
            "install with: pip install zeroconf"
        )
        return

    addrs = gather_ipv4_for_mdns(coordinator)
    if not addrs and bind_host not in ("", "0.0.0.0", "::", "::0"):
        try:
            addrs = [bind_host.strip().split("/")[0]]
        except Exception:
            addrs = []
    packed: List[bytes] = []
    for a in addrs:
        try:
            packed.append(socket.inet_aton(a))
        except OSError:
            continue
    if not packed:
        log.warning(
            "mDNS: no non-loopback IPv4 to advertise (Wi-Fi up?); skipping registration"
        )
        return

    short_host = (hostname or "nina").split(".")[0]
    server_name = f"{_sanitize_instance_name(short_host, max_len=40)}.local."
    inst = _sanitize_instance_name(instance_label, max_len=52)
    full_name = f"{inst}._http._tcp.local."
    props = {
        b"path": b"/health",
        b"product": b"nina-tablet-gateway",
    }
    if system_id:
        props[b"id"] = system_id[:32].encode("utf-8", errors="ignore")

    info = ServiceInfo(
        "_http._tcp.local.",
        full_name,
        addresses=packed,
        port=int(port),
        properties=props,
        server=server_name,
    )

    with _zc_lock:
        _stop_locked()
        zc = Zeroconf()
        try:
            zc.register_service(info)
        except Exception:
            log.exception("mDNS register_service failed")
            try:
                zc.close()
            except Exception:
                pass
            return
        _zc_instance = zc
        _registered_infos = [info]
        log.info(
            "mDNS: advertised %s port=%s addrs=%s (Bonjour / DNS-SD _http._tcp)",
            full_name,
            port,
            addrs,
        )


def start_mdns_background(
    *,
    bind_host: str,
    port: int,
    instance_label: str,
    system_id: str,
    hostname: str,
    coordinator: Optional["LinkCoordinator"],
    delay_sec: float = 0.45,
) -> None:
    """Start a daemon thread that registers _http._tcp after the HTTP server has bound."""
    t = threading.Thread(
        target=_run_register,
        kwargs={
            "bind_host": bind_host,
            "port": port,
            "instance_label": instance_label,
            "system_id": system_id,
            "hostname": hostname,
            "coordinator": coordinator,
            "delay_sec": delay_sec,
        },
        name="nina-mdns-register",
        daemon=True,
    )
    t.start()
