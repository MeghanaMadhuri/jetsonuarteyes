#!/usr/bin/env bash
# -----------------------------------------------------------------------------
# Install gTTS + verify ffmpeg for Actions-screen audio generation (.venv-link).
#
#   ./scripts/install-action-audio-jetson.sh
#
# Needs internet when you click Generate (Google TTS). Restart kiosk after install.
# -----------------------------------------------------------------------------

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
VENV_PATH="${REPO_ROOT}/.venv-link"
PY="${VENV_PATH}/bin/python"
PIP="${VENV_PATH}/bin/pip"

say() { printf '\n\033[1m%s\033[0m\n' "$*"; }
ok()  { printf '  [\033[32mOK\033[0m] %s\n' "$*"; }
warn(){ printf '  [\033[33m!!\033[0m] %s\n' "$*"; }
bad() { printf '  [\033[31m!!\033[0m] %s\n' "$*" >&2; }

if [[ ! -x "${PY}" ]]; then
    bad "No ${VENV_PATH} — run ./scripts/install-sirena-companion-jetson.sh first"
    exit 1
fi

cd "${REPO_ROOT}"

export PIP_USER=0
export PIP_BREAK_SYSTEM_PACKAGES="${PIP_BREAK_SYSTEM_PACKAGES:-1}"

if ! command -v ffmpeg >/dev/null 2>&1; then
    say "install ffmpeg (re-encode gTTS to 44.1 kHz / 48 kbps MP3)"
    if command -v apt-get >/dev/null 2>&1; then
        sudo apt-get update -qq
        sudo apt-get install -y ffmpeg
    else
        bad "ffmpeg not found — install it manually, then re-run this script"
        exit 1
    fi
fi
ok "ffmpeg: $(command -v ffmpeg)"

say "pip install gTTS into .venv-link (kiosk Python)"
"${PIP}" install -U pip wheel
"${PIP}" install 'setuptools>=70,<82'
"${PIP}" install --force-reinstall 'gTTS>=2.3'

say "import check (kiosk-like PYTHONPATH)"
export PYTHONPATH="${REPO_ROOT}"
_venv_site="$("${PY}" -c "import site; print(site.getsitepackages()[0])" 2>/dev/null || true)"
if [[ -n "${_venv_site}" && -d "${_venv_site}" ]]; then
    export PYTHONPATH="${REPO_ROOT}:${_venv_site}:${PYTHONPATH#${REPO_ROOT}:}"
fi
_sys_py_d="/usr/lib/python3/dist-packages"
if [[ -d "${_sys_py_d}/PyQt5" ]]; then
    export PYTHONPATH="${PYTHONPATH}:${_sys_py_d}"
fi

if ! "${PY}" -c "
from nina.services.audio_generator import AudioGenerator
err = AudioGenerator.is_available()
if err:
    raise SystemExit(err)
print('AudioGenerator OK')
"; then
    bad "AudioGenerator.is_available() failed — see message above"
    exit 1
fi
ok "gTTS imports in ${PY}"

echo ""
ok "Action audio generation ready. Restart kiosk:"
echo "  systemctl --user restart nina-ui-kiosk.service"
echo ""
echo "Actions → Audio → Generate needs internet on first use."
