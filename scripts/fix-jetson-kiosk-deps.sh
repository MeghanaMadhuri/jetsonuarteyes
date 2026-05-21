#!/usr/bin/env bash
# One-shot repair for a Jetson where pip left ML/audio deps in ~/.local instead
# of .venv-link (sympy equal_valued, wrong torch cu130 wheel, vision install fail).
#
# Run from repo root as user ``nina``:
#   cd ~/BLDC_HARI/Nvidia-jetson-platform   # or ~/Nvidia-jetson-platform
#   git pull
#   chmod +x scripts/fix-jetson-kiosk-deps.sh
#   ./scripts/fix-jetson-kiosk-deps.sh
#
# Then:
#   systemctl --user restart nina-ui-kiosk.service
#   ./scripts/verify-vision-kiosk.sh

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
VENV="${REPO_ROOT}/.venv-link"
PY="${VENV}/bin/python"
PIP="${VENV}/bin/pip"

say() { printf '\n\033[1m%s\033[0m\n' "$*"; }
ok()  { printf '  [\033[32mOK\033[0m] %s\n' "$*"; }
warn(){ printf '  [\033[33m!!\033[0m] %s\n' "$*"; }
bad() { printf '  [\033[31m!!\033[0m] %s\n' "$*" >&2; }

if [[ ! -x "${PY}" ]]; then
    bad "Missing ${VENV} — create it first:"
    bad "  ./scripts/install-sirena-companion-jetson.sh"
    bad "  or: ./scripts/install-nina-link-jetson.sh --all"
    exit 1
fi

export PIP_USER=0
export PIP_BREAK_SYSTEM_PACKAGES="${PIP_BREAK_SYSTEM_PACKAGES:-1}"

cd "${REPO_ROOT}"

say "1/4 — Reinstall vision deps into .venv-link (not ~/.local)"
"${PIP}" install -U pip setuptools wheel
"${PIP}" uninstall -y opencv-python opencv-contrib-python 2>/dev/null || true
"${PIP}" install --force-reinstall 'opencv-python-headless>=4.5.4' 'numpy>=1.20'
"${PIP}" install -U 'sympy>=1.13'
"${PIP}" install --force-reinstall 'ultralytics>=8.0.0' || {
    warn "ultralytics pip failed — install JetPack-matching PyTorch into .venv-link first"
    warn "  see REQUIREMENTS.md, then: ${PIP} install --no-deps ultralytics"
}

say "2/4 — Reinstall action audio (gTTS) into .venv-link"
if [[ -x "${SCRIPT_DIR}/install-action-audio-jetson.sh" ]]; then
    bash "${SCRIPT_DIR}/install-action-audio-jetson.sh" || warn "action audio install had warnings"
else
    "${PIP}" install --force-reinstall 'gTTS>=2.3'
fi

say "3/4 — Kiosk import probe (ignores ~/.local)"
export PYTHONNOUSERSITE=1
export PYTHONPATH="${REPO_ROOT}"
_venv_site="$("${PY}" -c "import site; print(site.getsitepackages()[0])")"
export PYTHONPATH="${REPO_ROOT}:${_venv_site}"
_sys_d="/usr/lib/python3/dist-packages"
if [[ -d "${_sys_d}/PyQt5" ]]; then
    export PYTHONPATH="${PYTHONPATH}:${_sys_d}"
fi

"${PY}" -c "
import sympy
print('sympy', sympy.__version__, sympy.__file__)
assert 'dist-packages' not in sympy.__file__, 'still using apt sympy'
" && ok "sympy in venv"

if "${PY}" -c "import torch; print('torch', torch.__version__, torch.__file__, 'cuda', torch.cuda.is_available())"; then
    ok "torch imports from venv"
else
    warn "torch missing in .venv-link — install NVIDIA JetPack wheel (REQUIREMENTS.md)"
fi

if "${PY}" -c "from ultralytics import YOLO; print('ultralytics OK')"; then
    ok "ultralytics YOLO imports"
else
    bad "ultralytics still fails — run: ./scripts/diagnose-vision-kiosk-import.sh"
    exit 1
fi

say "4/4 — Refresh kiosk unit + optional vision verify"
if [[ -x "${SCRIPT_DIR}/install-nina-ui-kiosk.sh" ]]; then
    warn "Re-run kiosk installer if unit file changed: ./scripts/install-nina-ui-kiosk.sh"
fi
if [[ -x "${SCRIPT_DIR}/verify-vision-kiosk.sh" ]]; then
    PYTHONNOUSERSITE=1 bash "${SCRIPT_DIR}/verify-vision-kiosk.sh" || true
fi

echo ""
ok "Done. Restart the UI:"
echo "  systemctl --user restart nina-ui-kiosk.service"
echo "  tail -f ~/.cache/sirena/launch.log"
