#!/usr/bin/env bash
# Print the *real* import error the kiosk hits (launch.log only shows a generic wrapper).
# Run on the Jetson:  ./scripts/diagnose-vision-kiosk-import.sh

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
PY="${REPO_ROOT}/.venv-link/bin/python"

if [[ ! -x "${PY}" ]]; then
    echo "Missing ${PY}" >&2
    exit 1
fi

set +u
[[ -r "${HOME}/.profile" ]] && . "${HOME}/.profile"
[[ -r "${HOME}/.bashrc" ]] && . "${HOME}/.bashrc"
set -u

unset PYTHONHOME PYTHONUSERBASE

export PYTHONPATH="${REPO_ROOT}${PYTHONPATH:+:${PYTHONPATH}}"
_venv_site="$("${PY}" -c "import site; print(site.getsitepackages()[0])" 2>/dev/null || true)"
if [[ -n "${_venv_site}" && -d "${_venv_site}" ]]; then
    export PYTHONPATH="${REPO_ROOT}:${_venv_site}:${PYTHONPATH#${REPO_ROOT}:}"
fi
_sys_py_d="/usr/lib/python3/dist-packages"
if [[ -d "${_sys_py_d}/PyQt5" ]]; then
    export PYTHONPATH="${PYTHONPATH}:${_sys_py_d}"
fi
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

echo "=== launch-like environment ==="
echo "python: ${PY}"
echo "PYTHONHOME=${PYTHONHOME-<unset>} (should be unset)"
echo "PYTHONPATH=${PYTHONPATH}"
echo "LD_LIBRARY_PATH=${LD_LIBRARY_PATH:-<unset>}"
echo ""

cd "${REPO_ROOT}"
exec "${PY}" -c "
import sys
import traceback

print('executable:', sys.executable)
print()

def section(title):
    print('---', title, '---')

section('torch')
try:
    import torch
    print('OK', torch.__version__, torch.__file__)
    print('cuda', torch.cuda.is_available())
except Exception:
    traceback.print_exc()

section('ultralytics (direct)')
try:
    from ultralytics import YOLO
    import ultralytics
    print('OK', ultralytics.__version__, ultralytics.__file__)
except Exception:
    traceback.print_exc()

section('old kiosk order: ultralytics.nn then YOLO')
try:
    from ultralytics.nn import tasks  # noqa: F401
    print('nn.tasks OK')
except Exception:
    traceback.print_exc()
try:
    from ultralytics import YOLO
    print('YOLO OK')
except Exception:
    traceback.print_exc()

section('VisionPipeline.set_object_enabled(True)')
try:
    from sirena_ui.workers.vision_pipeline import VisionPipeline
    pipe = VisionPipeline(prefer_tensorrt=False)
    err = pipe.set_object_enabled(True)
    if err:
        print('FAILED:', err)
    else:
        print('OK (object detector loaded)')
except Exception:
    traceback.print_exc()
"
