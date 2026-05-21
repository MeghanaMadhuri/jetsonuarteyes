#!/usr/bin/env bash
# Verify torch + ultralytics in .venv-link with the same PYTHONPATH / LD_LIBRARY_PATH
# the kiosk uses (scripts/launch-sirena.sh). Run on the Jetson after PyTorch/cuDSS work.
#
#   cd ~/Nvidia-jetson-platform
#   ./scripts/verify-vision-kiosk.sh

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
PY="${REPO_ROOT}/.venv-link/bin/python"

if [[ ! -x "${PY}" ]]; then
    echo "Missing ${PY} — run ./scripts/install-sirena-companion-jetson.sh first" >&2
    exit 1
fi

export PYTHONPATH="${REPO_ROOT}"
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

echo "python: ${PY}"
echo "PYTHONPATH=${PYTHONPATH}"
echo "LD_LIBRARY_PATH=${LD_LIBRARY_PATH:-<unset>}"
echo ""

"${PY}" -m pip show torch ultralytics 2>/dev/null | grep -E '^(Name|Version):' || true
echo ""

"${PY}" -c "
import sys
print('executable:', sys.executable)
import torch
print('torch', torch.__version__, 'cuda', torch.cuda.is_available())
if torch.cuda.is_available():
    print('device', torch.cuda.get_device_name(0))
from ultralytics import YOLO
print('ultralytics', YOLO.__module__, 'OK')
print('ultralytics file:', __import__('ultralytics').__file__)
"

# Kiosk also puts dist-packages on PYTHONPATH (PyQt5). Must not shadow venv ML wheels.
_sys_d="/usr/lib/python3/dist-packages"
_venv_site="$("${PY}" -c "import site; print(site.getsitepackages()[0])")"
if [[ -d "${_sys_d}/PyQt5" ]]; then
    echo ""
    echo "kiosk PYTHONPATH probe (repo + venv site-packages + dist-packages):"
    export PYTHONPATH="${REPO_ROOT}:${_venv_site}:${_sys_d}"
    "${PY}" -c "
import ultralytics, torch
print('torch', torch.__version__, torch.__file__)
print('ultralytics', ultralytics.__version__, ultralytics.__file__)
from ultralytics import YOLO
print('YOLO under kiosk PYTHONPATH OK')
" || {
        echo "FAILED: apt dist-packages is shadowing .venv-link torch/ultralytics." >&2
        echo "Pull latest launch-sirena.sh (prepends venv site-packages) and restart kiosk." >&2
        exit 1
    }
fi

echo ""
echo "If this passes but the Vision screen still fails, restart the kiosk and check launch.log:"
echo "  systemctl --user restart nina-ui-kiosk.service"
echo "  tail -n 80 ~/.cache/sirena/launch.log | grep -i object"
