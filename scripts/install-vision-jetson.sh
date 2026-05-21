#!/usr/bin/env bash
# -----------------------------------------------------------------------------
# Install Vision / YOLO (ultralytics) into .venv-link — same Python as the kiosk.
#
# install-nina-ui-kiosk.sh and bring-up-nina-jetson.sh run this on every Jetson
# fleet install. Re-run manually after JetPack PyTorch upgrades.
#
# Usage (repo root on Jetson):
#   chmod +x scripts/install-vision-jetson.sh
#   ./scripts/install-vision-jetson.sh
#   ./scripts/install-vision-jetson.sh --full    # entire sirena_ui/requirements-headless.txt
#
# Then restart the UI:
#   systemctl --user restart nina-ui-kiosk.service
# -----------------------------------------------------------------------------

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
VENV_PATH="${REPO_ROOT}/.venv-link"
PY="${VENV_PATH}/bin/python"
PIP="${VENV_PATH}/bin/pip"
FULL=0

for arg in "$@"; do
    case "${arg}" in
        --full) FULL=1 ;;
        -h|--help)
            sed -n '2,18p' "$0"
            exit 0
            ;;
        *)
            echo "Unknown option: ${arg} (try --help)" >&2
            exit 2
            ;;
    esac
done

say() { printf '\n\033[1m%s\033[0m\n' "$*"; }
ok()  { printf '  [\033[32mOK\033[0m] %s\n' "$*"; }
warn(){ printf '  [\033[33m!!\033[0m] %s\n' "$*"; }
bad() { printf '  [\033[31m!!\033[0m] %s\n' "$*" >&2; }

if [[ ! -x "${PY}" ]]; then
    bad "No ${VENV_PATH} — run first:"
    bad "  ./scripts/install-sirena-companion-jetson.sh"
    exit 1
fi

cd "${REPO_ROOT}"

# Never satisfy deps from ~/.local — that leaves torch/ultralytics outside
# .venv-link and breaks the kiosk import check (sympy equal_valued, cu130 torch).
export PIP_USER=0
export PIP_BREAK_SYSTEM_PACKAGES="${PIP_BREAK_SYSTEM_PACKAGES:-1}"

if [[ "${FULL}" -eq 1 ]]; then
    HEADLESS="${REPO_ROOT}/sirena_ui/requirements-headless.txt"
    if [[ ! -f "${HEADLESS}" ]]; then
        bad "Missing ${HEADLESS}"
        exit 1
    fi
    say "pip install -r sirena_ui/requirements-headless.txt (SLAM + vision + sensors)"
    warn "On Jetson: if torch/ultralytics fails, install JetPack-matching PyTorch first,"
    warn "then: ${PIP} install --no-deps ultralytics"
    "${PIP}" install -U pip wheel
    "${PIP}" install 'setuptools>=70,<82'
    "${PIP}" install -r "${HEADLESS}"
else
    say "pip install OpenCV + ultralytics (YOLO object detection only)"
    "${PIP}" install -U pip wheel
    "${PIP}" install 'setuptools>=70,<82'
    say "remove opencv-python wheels that bundle Qt (breaks PyQt5 kiosk)"
    "${PIP}" uninstall -y opencv-python opencv-contrib-python 2>/dev/null || true
    "${PIP}" install --force-reinstall 'opencv-python-headless>=4.5.4' 'numpy>=1.20'
    # Ubuntu apt sympy is too old for torch 2.x (ImportError: equal_valued).
    "${PIP}" install -U 'sympy>=1.13'
    if ! "${PIP}" install --force-reinstall 'ultralytics>=8.0.0'; then
        bad "ultralytics install failed (often wrong/missing PyTorch on Jetson)."
        echo ""
        echo "  1) Install NVIDIA's PyTorch wheel for your JetPack (see REQUIREMENTS.md)."
        echo "  2) Retry: ${PIP} install --no-deps ultralytics"
        exit 1
    fi
    # Ubuntu apt sympy on dist-packages is too old for torch/ultralytics (equal_valued).
    "${PIP}" install -U 'sympy>=1.13' || true
fi

say "import check (same interpreter + lib paths as nina-ui-kiosk)"
export PYTHONPATH="${REPO_ROOT}"
_venv_site="$("${PY}" -c "import site; print(site.getsitepackages()[0])" 2>/dev/null || true)"
if [[ -n "${_venv_site}" && -d "${_venv_site}" ]]; then
    export PYTHONPATH="${REPO_ROOT}:${_venv_site}:${PYTHONPATH#${REPO_ROOT}:}"
fi
_sys_py_d="/usr/lib/python3/dist-packages"
if [[ -d "${_sys_py_d}/PyQt5" ]]; then
    export PYTHONPATH="${PYTHONPATH}:${_sys_py_d}"
fi
# Match scripts/launch-sirena.sh so a passing check here means the kiosk can import ML libs.
for _dir in \
    "/usr/local/cuda/lib64" \
    "/usr/local/cuda/targets/aarch64-linux/lib" \
    "/usr/lib/aarch64-linux-gnu/tegra" \
    "/usr/lib/aarch64-linux-gnu/nvidia" \
    "/usr/lib/aarch64-linux-gnu" \
    "${HOME}/.local/lib"
do
    if [[ -d "${_dir}" ]]; then
        case ":${LD_LIBRARY_PATH:-}:" in
            *":${_dir}:"*) : ;;
            *) export LD_LIBRARY_PATH="${LD_LIBRARY_PATH:+${LD_LIBRARY_PATH}:}${_dir}" ;;
        esac
    fi
done
if ! "${PY}" -c "
import torch
print('torch', torch.__version__, 'cuda', torch.cuda.is_available())
"; then
    bad "import torch failed — install JetPack PyTorch + cuDSS/cuSPARSELt (REQUIREMENTS.md)"
    exit 1
fi
ok "torch imports in ${PY}"
if ! "${PY}" -c "
from ultralytics import YOLO
import sympy
print('ultralytics YOLO OK, sympy', sympy.__version__, sympy.__file__)
"; then
    bad "from ultralytics import YOLO failed (often apt sympy on PYTHONPATH — see launch-sirena.sh)"
    bad "  cd ${REPO_ROOT} && ./scripts/diagnose-vision-kiosk-import.sh"
    exit 1
fi
ok "ultralytics YOLO imports in ${PY} (kiosk PYTHONPATH)"

_cv2_qt="$("${PY}" -c "import os, cv2; print(os.path.join(os.path.dirname(cv2.__file__), 'qt'))" 2>/dev/null || true)"
if [[ -n "${_cv2_qt}" && -d "${_cv2_qt}" ]]; then
    warn "cv2 still has a qt/ tree (can crash the GUI) — removing ${_cv2_qt}"
    rm -rf "${_cv2_qt}"
fi

echo ""
ok "Vision/YOLO deps ready. Restart the kiosk:"
echo "  systemctl --user restart nina-ui-kiosk.service"
echo ""
echo "First time you enable Object detection, TensorRT export can take 2–3 min"
echo "and needs ~4 GB swap on Nano — see sirena_ui/README.md."
