"""SLAMTEC RPLIDAR A1M8 driver wrapper.

The A1M8 is a 360-degree 2D laser scanner connected via USB-serial
(the bundled adapter board exposes a CP2102 / CH340). Stock spec:

  * detection range ~12 m
  * angular resolution ~1 deg (360 samples / rev)
  * scan rate 5.5 Hz (configurable 5 - 10 Hz on the A1M8)
  * baudrate 115200

We use the `rplidar` Python package (Roboticia port of the SLAMTEC
SDK). On dev hosts where the package isn't installed, or the device
file isn't present, the driver gracefully reports unavailability so
the rest of the stack can keep running in simulation mode.
"""

from __future__ import annotations

import logging
import os
import threading
import time
from glob import glob
from typing import List, Optional, Tuple

from nina.sensors.types import LidarScan


log = logging.getLogger("nina.sensors.rplidar")


DEFAULT_PORT = os.environ.get("NINA_LIDAR_PORT", "/dev/ttyUSB0")
DEFAULT_BAUD = int(os.environ.get("NINA_LIDAR_BAUD", "115200"))
DEFAULT_BINS = int(os.environ.get("NINA_LIDAR_BINS", "360"))


def _reserved_ports() -> List[str]:
    """Ports the rest of the stack has already laid claim to.

    The Jetson commonly has both a Dynamixel FTDI adapter and the
    RPLIDAR A1's USB-serial cable plugged in. Both default to
    ``/dev/ttyUSB0`` in our env templates, which leads to a serial
    collision: whichever process opens the device first wins and the
    other one fails with a confusing 'device or resource busy' /
    'permission denied' / immediate scan timeout. Treat the Dynamixel
    and nav-remote ports as reserved so a non-explicit lidar fallback
    can skip them.
    """
    reserved: List[str] = []
    for env in ("NINA_DXL_PORT", "NINA_NAV_REMOTE_PORT"):
        v = os.environ.get(env, "").strip()
        if v:
            reserved.append(v)
    return reserved


def _serial_candidates(configured: str, *, exclude: Optional[List[str]] = None) -> List[str]:
    seen = set()
    ordered: List[str] = []
    blocklist = {p for p in (exclude or []) if p}

    def _add(path: str) -> None:
        if not path or path in seen or path in blocklist:
            return
        seen.add(path)
        ordered.append(path)

    _add(configured)
    for path in sorted(glob("/dev/ttyUSB*")):
        _add(path)
    for path in sorted(glob("/dev/ttyACM*")):
        _add(path)
    return ordered


def is_available() -> Tuple[bool, str]:
    try:
        import rplidar  # noqa: F401  type: ignore
    except Exception as exc:  # pragma: no cover - depends on host
        return False, f"rplidar package not installed ({exc})"
    if not os.path.exists(DEFAULT_PORT):
        candidates = _serial_candidates(DEFAULT_PORT, exclude=_reserved_ports())
        present = [p for p in candidates if os.path.exists(p)]
        if present:
            return False, f"{DEFAULT_PORT} not present (found: {', '.join(present[:3])})"
        return False, f"{DEFAULT_PORT} not present"
    return True, ""


def _format_open_error(port: str, raw: str, *, explicit: bool) -> str:
    """Turn rplidar's raw open-error into something the operator can act on.

    On a fresh-boot Jetson the most common failure modes are:

      1. **No USB-serial adapter visible at all** ("No such file or
         directory" + nothing under /dev/ttyUSB* /dev/ttyACM*). The
         lidar's USB cable is unplugged, the adapter is a PL2303 /
         CH340 (JetPack ships only cp210x.ko / ftdi_sio.ko), or the
         device is powered off.

      2. **Adapter visible but on a different node** (port is busy
         or doesn't exist, but other /dev/ttyUSB* are present). The
         Dynamixel manager already grabbed /dev/ttyUSB0 and the
         lidar is now /dev/ttyUSB1, or vice versa.

      3. **Port exists but is in use** ("Device or resource busy" /
         "could not exclusively lock"). Two drivers are fighting
         over the same adapter.

    For each case we attach a short remediation block so the GUI
    operator can fix it without grepping logs.
    """
    raw_lower = raw.lower()
    reserved = _reserved_ports()
    candidates = _serial_candidates(port, exclude=reserved)
    present = [p for p in candidates if os.path.exists(p) and p != port]
    base = f"open {port}: {raw}"

    no_such = (
        "no such file or directory" in raw_lower
        or "could not open port" in raw_lower
    )
    busy = (
        "device or resource busy" in raw_lower
        or "resource busy" in raw_lower
        or "could not exclusively lock" in raw_lower
    )

    if no_such and not present:
        return (
            base
            + "\n\nNo USB-serial adapter visible to the Jetson kernel. "
            "On the bot, check:\n"
            "  lsusb                         # is the lidar adapter listed?\n"
            "  ls /dev/ttyUSB* /dev/ttyACM*  # which serial nodes exist?\n"
            "  dmesg | tail -20              # last USB events\n\n"
            "Most common causes:\n"
            " * Lidar USB cable unplugged or loose.\n"
            " * Lidar power adapter unplugged (the A1's adapter board "
            "is bus-powered, but some bots route 5 V through a separate "
            "header — check the harness).\n"
            " * Adapter is a PL2303 or CH340. JetPack ships only "
            "cp210x.ko and ftdi_sio.ko, so those adapters enumerate in "
            "lsusb but never create a /dev/ttyUSB*. Swap for a CP2102 "
            "or FT232.\n"
            " * If you don't have a lidar on this build, set "
            "`export NINA_LIDAR_MODEL=disabled` to silence this row."
        )
    if no_such and present:
        suggestion = present[0]
        return (
            base
            + f"\n\n{port} is not enumerated, but {', '.join(present[:3])} "
            "is. Likely the USB enumeration order changed at boot.\n"
            f"  export NINA_LIDAR_PORT={suggestion}\n"
            "Restart the app (or just relaunch Nina from the desktop)."
        )
    if busy:
        owner_hint = ""
        if reserved:
            owner_hint = f" The Dynamixel/nav-remote stack is configured for {', '.join(reserved)}."
        alt = next((p for p in present if p not in reserved), None)
        alt_hint = f" Try `export NINA_LIDAR_PORT={alt}`." if alt else ""
        return (
            base
            + f"\n\n{port} is held by another process.{owner_hint}{alt_hint}"
        )
    if "permission denied" in raw_lower:
        return (
            base
            + f"\n\nPermission denied on {port}. Add the kiosk user to "
            "the dialout group and re-login:\n"
            "  sudo usermod -aG dialout $USER\n"
            "  groups | grep dialout"
        )
    return base if explicit else (
        base
        + "\n\nIf the lidar is plugged in but on a different node, "
        "set `export NINA_LIDAR_PORT=/dev/ttyUSB1` (or whichever node "
        "`ls /dev/ttyUSB*` shows)."
    )


class RPLidarA1:
    """Background-thread RPLIDAR A1 reader.

    Spawns one thread that pulls scans from `iter_scans()` and stores
    the latest distance array as a `LidarScan`. `read()` returns the
    most recent scan (or None) without blocking.
    """

    def __init__(
        self,
        port: str = DEFAULT_PORT,
        baudrate: int = DEFAULT_BAUD,
        bins: int = DEFAULT_BINS,
    ) -> None:
        self._port = port
        self._baud = baudrate
        self._bins = max(72, int(bins))     # don't go below 5-deg resolution
        self._lidar = None                  # rplidar.RPLidar | None
        self._thread: Optional[threading.Thread] = None
        self._stop_evt = threading.Event()
        self._lock = threading.Lock()
        self._latest: Optional[LidarScan] = None
        self._connected = False
        self._message = ""
        self._scans_received = 0
        self._last_scan_at = 0.0
        self._explicit_port = "NINA_LIDAR_PORT" in os.environ

    def _resolve_port(self) -> str:
        if self._explicit_port:
            return self._port
        reserved = _reserved_ports()
        # If the lidar would land on a port already configured for the
        # Dynamixel bus or the Pi nav-remote bridge, both processes
        # would race for the same FTDI / CP210x and the lidar would
        # silently fail. Pick a different /dev/ttyUSB* instead — the
        # operator can override either side with the matching env var
        # (NINA_LIDAR_PORT / NINA_DXL_PORT) if they intentionally want
        # that mapping.
        if self._port in reserved:
            for cand in _serial_candidates(self._port, exclude=reserved):
                if cand == self._port:
                    continue
                if os.path.exists(cand):
                    log.warning(
                        "RPLIDAR port %s is reserved by Dynamixel/nav-remote; "
                        "falling back to %s",
                        self._port, cand,
                    )
                    return cand
            log.warning(
                "RPLIDAR port %s is reserved by Dynamixel/nav-remote and no "
                "other /dev/ttyUSB* is available; lidar will likely fail to "
                "open. Set NINA_LIDAR_PORT explicitly to silence this.",
                self._port,
            )
            return self._port
        if os.path.exists(self._port):
            return self._port
        for cand in _serial_candidates(self._port, exclude=reserved):
            if cand == self._port:
                continue
            if os.path.exists(cand):
                log.info("RPLIDAR port fallback: %s -> %s", self._port, cand)
                return cand
        return self._port

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def open(self) -> None:
        try:
            from rplidar import RPLidar  # type: ignore
        except Exception as exc:
            self._message = f"rplidar package not installed ({exc})"
            raise RuntimeError(self._message) from exc

        try:
            resolved_port = self._resolve_port()
            self._lidar = RPLidar(resolved_port, baudrate=self._baud, timeout=2.0)
            self._port = resolved_port
            # Probe the device. Older rplidar packages don't expose
            # get_info() reliably; the first iter_scans call will surface
            # the actual error if there is one.
            try:
                info = self._lidar.get_info()
                log.info("RPLIDAR info: %s", info)
            except Exception:
                pass
        except Exception as exc:
            self._lidar = None
            self._message = _format_open_error(
                self._port, str(exc), explicit=self._explicit_port
            )
            raise RuntimeError(self._message) from exc

        self._stop_evt.clear()
        self._thread = threading.Thread(
            target=self._run, name="RPLidarA1", daemon=True
        )
        self._thread.start()
        self._connected = True
        self._message = f"connected on {self._port}"

    def close(self) -> None:
        self._stop_evt.set()
        thread = self._thread
        self._thread = None
        if thread is not None:
            thread.join(timeout=2.0)
        if self._lidar is not None:
            try:
                self._lidar.stop()
                self._lidar.stop_motor()
                self._lidar.disconnect()
            except Exception as exc:
                log.warning("rplidar close: %s", exc)
            self._lidar = None
        self._connected = False
        self._message = "disconnected"

    # ------------------------------------------------------------------
    # Public reads
    # ------------------------------------------------------------------

    def read(self) -> Optional[LidarScan]:
        with self._lock:
            return self._latest

    def status(self) -> Tuple[bool, str]:
        return self._connected, self._message

    # ------------------------------------------------------------------
    # Worker thread
    # ------------------------------------------------------------------

    def _run(self) -> None:
        assert self._lidar is not None
        try:
            for scan in self._lidar.iter_scans(min_len=80, max_buf_meas=5000):
                if self._stop_evt.is_set():
                    break
                self._publish(scan)
        except Exception as exc:
            self._message = f"scan loop error: {exc}"
            self._connected = False
            log.warning("rplidar scan loop: %s", exc)

    def _publish(self, raw_scan) -> None:
        bins = self._bins
        bucket = [0] * bins
        valid = 0
        for measurement in raw_scan:
            # iter_scans yields (quality, angle_deg, distance_mm)
            try:
                _q, angle, distance = measurement
            except Exception:
                continue
            if distance <= 0:
                continue
            idx = int(angle / 360.0 * bins) % bins
            existing = bucket[idx]
            distance_int = int(distance)
            if existing == 0 or distance_int < existing:
                bucket[idx] = distance_int
                if existing == 0:
                    valid += 1

        now = time.monotonic()
        rpm = 0.0
        if self._last_scan_at > 0:
            dt = now - self._last_scan_at
            if dt > 0:
                rpm = 60.0 / dt

        scan = LidarScan(
            distances_mm=bucket,
            timestamp_s=now,
            rpm=rpm,
            quality=valid / float(bins),
        )
        with self._lock:
            self._latest = scan
        self._scans_received += 1
        self._last_scan_at = now


def latest_distances(scan: Optional[LidarScan]) -> List[int]:
    """Convenience helper for SLAM / autonomy: returns the distance
    array (or an empty list when the lidar hasn't produced a scan yet)."""
    if scan is None:
        return []
    return list(scan.distances_mm)
