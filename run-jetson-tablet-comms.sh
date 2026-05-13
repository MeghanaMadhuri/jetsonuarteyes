#!/usr/bin/env bash
# -----------------------------------------------------------------------------
# Jetson — run the tablet companion HTTP API ("comms") for app testing.
#
# Starts Sirena UI with the embedded FastAPI gateway (same as nina-link.service).
# Point the Android companion at:  http://<jetson-ip>:8787  (default port).
#
# First run creates `.venv-link` (with `--system-site-packages` so distro PyQt5
# is visible) and installs `requirements-link.txt` if `uvicorn` is missing.
#
# Full robot install (optional): ./scripts/install-nina-link-jetson.sh --all
# Docs: docs/COMPANION_APP.md
# -----------------------------------------------------------------------------
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REQ_LINK="${REPO_ROOT}/requirements-link.txt"
VENV_PY="${REPO_ROOT}/.venv-link/bin/python"
VENV_PIP="${REPO_ROOT}/.venv-link/bin/pip"

export PYTHONPATH="${REPO_ROOT}${PYTHONPATH:+:$PYTHONPATH}"

# Prefer LAN IP over docker0 (172.17.0.1) for the printed URL
_primary_ip() {
  local ip=""
  if command -v ip >/dev/null 2>&1; then
    ip="$(ip -4 route get 1.1.1.1 2>/dev/null | sed -n 's/.*src \([^ ]*\).*/\1/p' || true)"
  fi
  if [[ -n "${ip}" ]]; then
    echo "${ip}"
    return 0
  fi
  if command -v hostname >/dev/null 2>&1; then
    local c
    for c in $(hostname -I 2>/dev/null); do
      case "${c}" in
        172.17.*|172.18.*|127.*|"") continue ;;
        *) echo "${c}"; return 0 ;;
      esac
    done
  fi
}

_bootstrap_venv() {
  echo "Bootstrapping ${REPO_ROOT}/.venv-link (includes requirements-link.txt) …"
  if ! python3 -m venv -h >/dev/null 2>&1; then
    echo "Install: sudo apt install -y python3-venv" >&2
    exit 1
  fi
  python3 -m venv "${REPO_ROOT}/.venv-link" --system-site-packages
  "${VENV_PIP}" install -U pip wheel setuptools
  "${VENV_PIP}" install -r "${REQ_LINK}"
}

_resolve_python() {
  if [[ -n "${PYTHON:-}" ]]; then
    if [[ ! -x "${PYTHON}" ]]; then
      echo "PYTHON is not executable: ${PYTHON}" >&2
      exit 1
    fi
    if "${PYTHON}" -c "import uvicorn" 2>/dev/null; then
      return 0
    fi
    echo "PYTHON=${PYTHON} cannot import uvicorn — pip install -r requirements-link.txt there, or unset PYTHON." >&2
    exit 1
  fi

  if [[ -x "${VENV_PY}" ]]; then
    PYTHON="${VENV_PY}"
  else
    PYTHON="python3"
  fi

  if "${PYTHON}" -c "import uvicorn" 2>/dev/null; then
    return 0
  fi

  if [[ ! -x "${VENV_PY}" ]]; then
    _bootstrap_venv
  else
    echo "Installing HTTP deps into existing .venv-link …"
    "${VENV_PIP}" install -r "${REQ_LINK}"
  fi
  PYTHON="${VENV_PY}"

  if ! "${PYTHON}" -c "import uvicorn" 2>/dev/null; then
    echo "Still no uvicorn after bootstrap — check ${REQ_LINK} and network." >&2
    exit 1
  fi
}

_resolve_python

if ! "${PYTHON}" -c "from PyQt5.QtWidgets import QApplication" 2>/dev/null; then
  echo "PyQt5 is required for Sirena UI. On Ubuntu/Jetson install distro packages, then re-run:" >&2
  echo "  sudo apt install -y python3-pyqt5 python3-pyqt5.qtsvg" >&2
  echo "(The venv uses --system-site-packages so system PyQt5 is picked up.)" >&2
  exit 1
fi

# Embedded tablet gateway (FastAPI / uvicorn thread inside Qt app)
export NINA_ANDROID_GATEWAY="${NINA_ANDROID_GATEWAY:-1}"
export NINA_ANDROID_GATEWAY_ALL="${NINA_ANDROID_GATEWAY_ALL:-1}"

export NINA_LINK_HOST="${NINA_LINK_HOST:-0.0.0.0}"
export NINA_LINK_PORT="${NINA_LINK_PORT:-8787}"

export NINA_LINK_BOOT_AP="${NINA_LINK_BOOT_AP:-0}"
export NINA_ANDROID_GATEWAY_BOOT_AP="${NINA_ANDROID_GATEWAY_BOOT_AP:-0}"

if [[ -z "${DISPLAY:-}" ]] && [[ -z "${QT_QPA_PLATFORM:-}" ]]; then
  export QT_QPA_PLATFORM="${QT_QPA_PLATFORM:-offscreen}"
fi

_ip="$(_primary_ip || true)"
if [[ -z "${_ip}" ]]; then
  _ip="<jetson-ip>"
fi

echo "Tablet comms: starting companion HTTP API"
echo "  Base URL (from your phone/tablet):  http://${_ip}:${NINA_LINK_PORT}"
echo "  Health check (on Jetson):             curl -s http://127.0.0.1:${NINA_LINK_PORT}/health"
echo "  Python: ${PYTHON}"
echo "  Repo:   ${REPO_ROOT}"
echo ""

cd "${REPO_ROOT}"
exec "${PYTHON}" -m sirena_ui "$@"
