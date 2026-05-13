#!/usr/bin/env bash
# -----------------------------------------------------------------------------
# Nina Link — one-shot Jetson install + diagnosis for the companion-app daemon
#
# The systemd unit enables **all HTTP bridges** used by the Android companion
# (drive, actions, record, vision, static media) so tablet ↔ Jetson comms match
# docs/COMPANION_APP.md without a separate drop-in. Optional vision ML deps:
#   ./scripts/update-nina-link-jetson.sh --sirena-headless --restart --verify
#
# Usage (on the Jetson, from repo root):
#   ./scripts/jetson-tablet-setup.sh              # preferred: + drop-in bridges + UFW hints
#   ./scripts/install-nina-link-jetson.sh --all
#   ./scripts/uninstall-nina-link-jetson.sh --purge   # remove service + venv + state
# Do NOT pass script flags to chmod (e.g. chmod +x foo.sh --smoke is wrong).
#
# Options:
#   --all                   Full Jetson setup: apt deps + venv + smoke test + systemd (AP restarts on boot)
#   --install-system-deps   sudo apt install python3-venv, pip, curl (Ubuntu/Debian Jetson)
#   --with-systemd          Install and enable systemd unit (needs sudo; paths from repo)
#   --systemd-only          Only install/enable nina-link.service (venv must exist); use after sudo password
#   --no-systemd            Skip systemd even if implied by --all (for dev laptops)
#   --smoke                 After install, briefly run daemon and curl /health (needs curl)
#   --venv PATH             Virtualenv directory (default: <repo>/.venv-link)
#
# If venv creation fails with "ensurepip is not available", run:
#   sudo apt install python3-venv
# or re-run with --install-system-deps
# -----------------------------------------------------------------------------

set -u

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
REQ_FILE="${REPO_ROOT}/requirements-link.txt"
UNIT_DST="/etc/systemd/system/nina-link.service"

WITH_SYSTEMD=0
SMOKE=0
INSTALL_SYSTEM_DEPS=0
SYSTEMD_ONLY=0
NO_SYSTEMD=0
VENV_PATH=""

while [[ $# -gt 0 ]]; do
    case "$1" in
        --all)
            INSTALL_SYSTEM_DEPS=1
            SMOKE=1
            WITH_SYSTEMD=1
            shift
            ;;
        --install-system-deps) INSTALL_SYSTEM_DEPS=1; shift ;;
        --with-systemd) WITH_SYSTEMD=1; shift ;;
        --systemd-only) SYSTEMD_ONLY=1; shift ;;
        --no-systemd) NO_SYSTEMD=1; shift ;;
        --smoke)        SMOKE=1; shift ;;
        --venv)
            VENV_PATH="${2:?}"
            shift 2
            ;;
        -h|--help)
            grep '^#' "$0" | grep -v '^#!' | sed 's/^# \{0,1\}//'
            exit 0
            ;;
        *)
            echo "Unknown option: $1" >&2
            exit 2
            ;;
    esac
done

if [[ "${NO_SYSTEMD}" -eq 1 ]]; then
    WITH_SYSTEMD=0
fi

if [[ -z "${VENV_PATH}" ]]; then
    VENV_PATH="${REPO_ROOT}/.venv-link"
fi

say() { printf '\n\033[1m%s\033[0m\n' "$*"; }
ok()  { printf '  [\033[32mOK\033[0m] %s\n' "$*"; }
bad() { printf '  [\033[31m!!\033[0m] %s\n' "$*"; }
warn(){ printf '  [\033[33m!!\033[0m] %s\n' "$*"; }

# Remove legacy Pi UART bridge keys from /etc/nina-link/navigation.env (backup if changed).
_scrub_obsolete_nav_env_file() {
    local f="$1"
    local SUDO=(sudo)
    if [[ "$(id -u)" -eq 0 ]]; then
        SUDO=()
    fi
    [[ -f "$f" ]] || return 0
    local tmp
    tmp="$(mktemp)"
    grep -vE '^(NINA_NAV_MODE|NINA_NAV_REMOTE_PORT|NINA_NAV_REMOTE_BAUD|NINA_NAV_REMOTE_TIMEOUT_SEC|NINA_NAV_REMOTE_TURN_TICK_SEC|NINA_NAV_LEGACY_PI_BRIDGE)=' \
        "$f" > "$tmp" || true
    if cmp -s "$f" "$tmp" 2>/dev/null; then
        rm -f "$tmp"
        return 0
    fi
    local bak="${f}.bak.scrub-$(date +%Y%m%d%H%M%S)"
    "${SUDO[@]}" cp -a "$f" "$bak" 2>/dev/null || true
    "${SUDO[@]}" mv "$tmp" "$f"
    ok "Stripped obsolete Pi-UART keys from ${f} (backup: ${bak})"
}

# Writes /etc/systemd/system/nina-link.service — AP on boot via NINA_LINK_BOOT_AP in unit + daemon.
_install_nina_link_systemd() {
    local SUDO=(sudo)
    if [[ "$(id -u)" -eq 0 ]]; then
        SUDO=()
    fi
    if [[ "${#SUDO[@]}" -gt 0 ]] && ! command -v sudo >/dev/null 2>&1; then
        bad "sudo not installed — cannot register systemd unit"
        return 1
    fi
    # Interactive sudo password if needed
    if [[ "${#SUDO[@]}" -gt 0 ]] && ! sudo -v; then
        warn "sudo authentication failed"
        return 1
    fi
    "${SUDO[@]}" tee "${UNIT_DST}" >/dev/null <<EOF
[Unit]
Description=Sirena Control Center (tablet HTTP API embedded; replaces nina-link)
After=network-online.target NetworkManager.service graphical-session.target
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory=/
# Hoverboard lean via Dynamixel (production): tunables live in
# /etc/nina-link/navigation.env (NINA_HOVER_*, polarity flags). The
# Pi-UART bridge is gone — UnsetEnvironment below clears any stale
# remote-mode env that might survive across reboots.
# Yield CPU to PulseAudio / I2S under SLAM + depth bursts; safe at
# Nice=5 on Jetson Orin Nano (lean drive uses Dynamixel, not RT GPIO).
Nice=5
Environment=NINA_NAV_INVERT_LEFT=1
Environment=NINA_NAV_INVERT_RIGHT=0
EnvironmentFile=-/etc/nina-link/navigation.env
UnsetEnvironment=NINA_NAV_MODE NINA_NAV_REMOTE_PORT NINA_NAV_REMOTE_BAUD NINA_NAV_REMOTE_TIMEOUT_SEC NINA_NAV_REMOTE_TURN_TICK_SEC NINA_NAV_LEGACY_PI_BRIDGE
Environment=PYTHONPATH=${REPO_ROOT}
Environment=DISPLAY=:0
Environment=QT_QPA_PLATFORM=xcb
Environment=NINA_ANDROID_GATEWAY=1
Environment=NINA_ANDROID_GATEWAY_ALL=1
Environment=NINA_ANDROID_GATEWAY_BOOT_AP=0
Environment=NINA_LINK_BOOT_AP=0
Environment=NINA_LINK_DISABLE_WIFI_AUTOCONNECT=0
Environment=NINA_LINK_WIFI_READY_TIMEOUT=240
Environment=NINA_LINK_WIFI_READY_POLL=2
Environment=NINA_LINK_HOTSPOT_ATTEMPTS=5
Environment=NINA_LINK_HOST=0.0.0.0
Environment=NINA_LINK_PORT=8787
Environment=NINA_LINK_DISPLAY_HOSTNAME=jnx
Environment=NINA_LINK_ENABLE_ROBOT_BRIDGE=1
Environment=NINA_LINK_ENABLE_RECORD_BRIDGE=1
Environment=NINA_LINK_ENABLE_VISION_BRIDGE=1
Environment=NINA_LINK_ENABLE_ACTIONS_STATIC=1
Environment=NINA_LINK_ENABLE_SLAM_BRIDGE=1
Environment=NINA_LINK_ENABLE_DEPTH_BRIDGE=1
Environment=NINA_LINK_ENABLE_AUTONOMY_BRIDGE=1
ExecStart=${PY} -m sirena_ui
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF
    NAV_ENV_EX="${REPO_ROOT}/nina/systemd/nina-link-navigation.env.example"
    NAV_ENV_DST="/etc/nina-link/navigation.env"
    if [[ -f "${NAV_ENV_EX}" ]]; then
        "${SUDO[@]}" mkdir -p /etc/nina-link
        if [[ ! -f "${NAV_ENV_DST}" ]]; then
            "${SUDO[@]}" cp "${NAV_ENV_EX}" "${NAV_ENV_DST}"
            ok "Created ${NAV_ENV_DST} from example (edit NINA_NAV_* / polarity if needed)"
        else
            ok "Keeping existing ${NAV_ENV_DST}"
        fi
        _scrub_obsolete_nav_env_file "${NAV_ENV_DST}" || true
    else
        warn "Missing ${NAV_ENV_EX} — create ${NAV_ENV_DST} manually for BLDC parity with Sirena UI"
        if [[ -f "${NAV_ENV_DST}" ]]; then
            _scrub_obsolete_nav_env_file "${NAV_ENV_DST}" || true
        fi
    fi
    _install_pulse_jetson_tweaks "${SUDO[@]}" || true
    "${SUDO[@]}" systemctl daemon-reload
    "${SUDO[@]}" systemctl enable nina-link.service
    "${SUDO[@]}" systemctl restart nina-link.service
    if systemctl is-active --quiet nina-link.service 2>/dev/null; then
        ok "nina-link.service active — AP/restart policy enabled on boot"
        return 0
    fi
    warn "Unit installed but not active — journalctl -u nina-link -e"
    return 1
}

# PulseAudio: larger default sink buffer + mild priority so autonomy/SLAM spikes do not
# underrun I2S (MAX98357 class "static"). See nina/systemd/pulse/README.md
_install_pulse_jetson_tweaks() {
    local -a SUDO=("$@")
    if ! command -v pulseaudio >/dev/null 2>&1; then
        warn "pulseaudio not installed — skipping I2S daemon.conf.d (optional: apt install pulseaudio)"
        return 0
    fi
    local SRC="${REPO_ROOT}/nina/systemd/pulse/daemon-i2s-under-load.conf"
    if [[ ! -f "${SRC}" ]]; then
        warn "Missing ${SRC} — skipping PulseAudio I2S tweaks"
        return 0
    fi
    if [[ "${#SUDO[@]}" -gt 0 ]] && ! command -v sudo >/dev/null 2>&1; then
        warn "sudo not installed — skipping PulseAudio I2S tweaks"
        return 0
    fi
    "${SUDO[@]}" mkdir -p /etc/pulse/daemon.conf.d
    "${SUDO[@]}" cp -f "${SRC}" /etc/pulse/daemon.conf.d/99-nina-i2s-under-load.conf
    ok "Installed /etc/pulse/daemon.conf.d/99-nina-i2s-under-load.conf"
    local DC=/etc/pulse/daemon.conf
    if [[ -f "${DC}" ]]; then
        if grep -qE '^[[:space:]]*\.include[[:space:]]+/etc/pulse/daemon\.conf\.d/\*\.conf[[:space:]]*$' "${DC}"; then
            ok "daemon.conf already includes daemon.conf.d drop-ins"
        else
            "${SUDO[@]}" tee -a "${DC}" >/dev/null <<'INCL'

### Nina — allow /etc/pulse/daemon.conf.d/*.conf (install script; safe to remove if unused)
.include /etc/pulse/daemon.conf.d/*.conf
INCL
            ok "Appended .include /etc/pulse/daemon.conf.d/*.conf to ${DC}"
        fi
    else
        warn "Missing ${DC} — create PulseAudio base config or install pulseaudio package"
    fi
    warn "Reload PulseAudio as the desktop user: pulseaudio -k && pulseaudio --start (or reboot)"
    return 0
}

# systemd-only: register service and exit (run: sudo ./install-nina-link-jetson.sh --systemd-only)
if [[ "${SYSTEMD_ONLY}" -eq 1 ]]; then
    PY="${VENV_PATH}/bin/python"
    say "nina-link systemd-only"
    if [[ ! -x "${PY}" ]]; then
        bad "No Python at ${PY} — run full install first (without --systemd-only)."
        exit 1
    fi
    _install_nina_link_systemd || exit 1
    exit 0
fi

EXIT=0

# ---------------------------------------------------------------------------
say "1. Paths"
echo "  Repo root: ${REPO_ROOT}"
if [[ ! -f "${REQ_FILE}" ]]; then
    bad "Missing ${REQ_FILE}"
    exit 1
fi
ok "requirements-link.txt found"

# ---------------------------------------------------------------------------
say "2. Host diagnosis (no changes)"

if command -v python3 >/dev/null 2>&1; then
    ok "python3: $(command -v python3) ($(python3 --version 2>&1))"
else
    bad "python3 not found — install: sudo apt install python3 python3-pip python3-venv"
    EXIT=1
fi

if command -v nmcli >/dev/null 2>&1; then
    ok "nmcli: $(command -v nmcli)"
else
    warn "nmcli not found — NetworkManager CLI missing (Wi-Fi control needs this)"
    EXIT=1
fi

if systemctl is-active --quiet NetworkManager 2>/dev/null; then
    ok "NetworkManager service is active"
elif command -v systemctl >/dev/null 2>&1; then
    warn "NetworkManager not active — enable Wi-Fi stack on Jetson"
fi

if [[ $EXIT -ne 0 ]] && [[ ! -t 0 ]]; then
    say "Fix the issues above, then re-run."
    exit "$EXIT"
fi

# ---------------------------------------------------------------------------
say "3. Virtualenv + pip packages"

_sudo_apt() {
    local -a cmd=(sudo)
    if [[ "$(id -u)" -eq 0 ]]; then
        cmd=()
    fi
    if [[ "${#cmd[@]}" -gt 0 ]] && ! command -v sudo >/dev/null 2>&1; then
        bad "Need sudo or root to install packages"
        return 1
    fi
    "${cmd[@]}" "$@"
}

if [[ "${INSTALL_SYSTEM_DEPS}" -eq 1 ]]; then
    say "  Installing distro packages (apt)"
    _sudo_apt apt-get update -qq || { bad "apt-get update failed"; exit 1; }
    PY_MINOR="$(python3 -c 'import sys; print(sys.version_info[1])' 2>/dev/null || echo 10)"
    # python3.10-venv provides ensurepip on Ubuntu/Jetson images without full python3-venv metapackage
    _sudo_apt apt-get install -y \
        "python3.${PY_MINOR}-venv" \
        python3-venv \
        python3-pip \
        python3-pyqt5 \
        python3-pyqt5.qtsvg \
        curl \
        || { bad "apt-get install failed"; exit 1; }
    ok "python3-venv, pip, PyQt5 (distro), curl (apt)"
fi

# Remove broken half-created venv from a previous failed run (no interpreter)
if [[ -d "${VENV_PATH}" ]] && [[ ! -x "${VENV_PATH}/bin/python" ]]; then
    warn "Removing incomplete venv: ${VENV_PATH}"
    rm -rf "${VENV_PATH}"
fi

_venv_ready() {
    python3 -c "import ensurepip" >/dev/null 2>&1
}

if ! _venv_ready; then
    if [[ "${INSTALL_SYSTEM_DEPS}" -eq 1 ]]; then
        bad "ensurepip still unavailable after apt — try: sudo apt install python3-venv"
        exit 1
    fi
    bad "ensurepip not available (python3-venv missing on Ubuntu/Debian)"
    echo ""
    echo "  Fix one of:"
    echo "    sudo apt install python3-venv"
    echo "    ./scripts/install-nina-link-jetson.sh --install-system-deps --smoke"
    echo ""
    exit 1
fi

if [[ ! -d "${VENV_PATH}" ]]; then
    if ! python3 -m venv --help >/dev/null 2>&1; then
        bad "python3 -m venv failed — install python3-venv (see above)"
        exit 1
    fi
    say "  Creating venv: ${VENV_PATH} (system site-packages → distro PyQt5 for sirena_ui)"
    if ! python3 -m venv --system-site-packages "${VENV_PATH}"; then
        bad "venv creation failed"
        rm -rf "${VENV_PATH}"
        exit 1
    fi
    ok "Virtualenv created"
else
    ok "Using existing venv: ${VENV_PATH}"
fi

PY="${VENV_PATH}/bin/python"
_venv_has_pip() {
    [[ -x "${VENV_PATH}/bin/pip" ]] || [[ -x "${VENV_PATH}/bin/pip3" ]]
}

# Older failed runs left a venv with python but no pip (ensurepip wasn't on the system yet).
if [[ -x "${PY}" ]] && ! _venv_has_pip; then
    say "  Bootstrapping pip inside venv (python -m ensurepip)"
    if ! "${PY}" -m ensurepip --upgrade; then
        warn "ensurepip failed — recreating venv from scratch"
        rm -rf "${VENV_PATH}"
        say "  Creating venv: ${VENV_PATH} (system site-packages)"
        python3 -m venv --system-site-packages "${VENV_PATH}" || { bad "venv recreate failed"; exit 1; }
        PY="${VENV_PATH}/bin/python"
    fi
fi

if [[ -x "${VENV_PATH}/bin/pip" ]]; then
    PIP="${VENV_PATH}/bin/pip"
elif [[ -x "${VENV_PATH}/bin/pip3" ]]; then
    PIP="${VENV_PATH}/bin/pip3"
else
    bad "pip missing inside venv after ensurepip — try: rm -rf ${VENV_PATH} && re-run this script"
    exit 1
fi

"${PIP}" install -U pip setuptools wheel >/dev/null
"${PIP}" install -r "${REQ_FILE}" || { bad "pip install failed"; exit 1; }
ok "Installed packages from requirements-link.txt"

# ---------------------------------------------------------------------------
say "4. Import / package verification"

# Use a user-owned temp file (fixed paths under /tmp can be root-owned after sudo runs).
IMPORT_ERR="$(mktemp "${TMPDIR:-/tmp}/nina-link-import.XXXXXX.err")"
export PYTHONPATH="${REPO_ROOT}"
if "${PY}" -c "
from nina.jetson_net.config import load_config
from nina.jetson_net.nm import mock_backend
from nina.jetson_net.state import LinkCoordinator
c = load_config()
co = LinkCoordinator(c, mock_backend())
print('import_ok', co.cfg.host, co.cfg.port)
" 2>"${IMPORT_ERR}"; then
    rm -f "${IMPORT_ERR}"
    ok "nina.jetson_net imports successfully"
else
    bad "Import failed:"
    sed 's/^/    /' "${IMPORT_ERR}" >&2
    rm -f "${IMPORT_ERR}"
    exit 1
fi

if "${PY}" -c "from PyQt5.QtCore import QT_VERSION_STR; print('pyqt_ok', QT_VERSION_STR)" 2>"${IMPORT_ERR}"; then
    rm -f "${IMPORT_ERR}"
    ok "PyQt5 importable (required for python -m sirena_ui)"
else
    bad "PyQt5 not importable in this venv — tablet gateway needs Qt."
    sed 's/^/    /' "${IMPORT_ERR}" >&2
    rm -f "${IMPORT_ERR}"
    echo ""
    echo "  Fix on Ubuntu/Jetson:"
    echo "    sudo apt install -y python3-pyqt5 python3-pyqt5.qtsvg"
    echo "    rm -rf ${VENV_PATH}"
    echo "    $0 --install-system-deps --smoke   # or --all"
    echo ""
    exit 1
fi

# ---------------------------------------------------------------------------
say "5. Optional smoke test (HTTP /health)"

if [[ "${SMOKE}" -eq 1 ]]; then
    warn "Automated HTTP smoke test removed (nina-link standalone daemon). After install, run Sirena UI and: curl -s http://127.0.0.1:8787/health"
fi

# ---------------------------------------------------------------------------
say "6. systemd unit (optional)"

if [[ "${WITH_SYSTEMD}" -eq 1 ]]; then
    if ! _install_nina_link_systemd; then
        EXIT=1
        warn "Install service separately (will prompt for sudo password):"
        echo "    sudo $(printf '%q' "${REPO_ROOT}/scripts/install-nina-link-jetson.sh") --systemd-only"
    fi
else
    echo "  Skipped (use --with-systemd or --all for AP + daemon on every boot)"
fi

# ---------------------------------------------------------------------------
say "Done."

cat <<EOF

  Companion app on hotspot (NetworkManager / Jetson typical): http://10.42.0.1:8787

  Manual foreground run (no systemd) — tablet API inside Sirena UI:
    export PYTHONPATH=${REPO_ROOT}
    export NINA_ANDROID_GATEWAY=1 NINA_ANDROID_GATEWAY_ALL=1
    export NINA_LINK_ENABLE_ROBOT_BRIDGE=1 NINA_LINK_ENABLE_RECORD_BRIDGE=1 NINA_LINK_ENABLE_VISION_BRIDGE=1 NINA_LINK_ENABLE_ACTIONS_STATIC=1 NINA_LINK_ENABLE_SLAM_BRIDGE=1 NINA_LINK_ENABLE_DEPTH_BRIDGE=1 NINA_LINK_ENABLE_AUTONOMY_BRIDGE=1
    ${PY} -m sirena_ui

  Verify APIs after install (on Jetson):
    curl -s http://127.0.0.1:8787/v1/robot/capabilities | head

  Optional — vision / gTTS stack for MJPEG + detections (longer pip run):
    ./scripts/update-nina-link-jetson.sh --sirena-headless --restart --verify

  Remove link daemon from this machine:
    ./scripts/uninstall-nina-link-jetson.sh --purge

  Full notes: docs/COMPANION_APP.md

  Jetson I2S + PulseAudio under autonomy load: nina/systemd/pulse/README.md

EOF

exit "${EXIT}"
