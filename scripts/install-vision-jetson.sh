#!/usr/bin/env bash
# -----------------------------------------------------------------------------
# Install Vision / YOLO (ultralytics) into .venv-link — same Python as the kiosk.
#
# bring-up-nina-jetson.sh does NOT install ultralytics by default; object
# detection in the Vision tab needs this step once per Jetson.
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

if [[ "${FULL}" -eq 1 ]]; then
    HEADLESS="${REPO_ROOT}/sirena_ui/requirements-headless.txt"
    if [[ ! -f "${HEADLESS}" ]]; then
        bad "Missing ${HEADLESS}"
        exit 1
    fi
    say "pip install -r sirena_ui/requirements-headless.txt (SLAM + vision + sensors)"
    warn "On Jetson: if torch/ultralytics fails, install JetPack-matching PyTorch first,"
    warn "then: ${PIP} install --no-deps ultralytics"
    "${PIP}" install -U pip setuptools wheel
    "${PIP}" install -r "${HEADLESS}"
else
    say "pip install OpenCV + ultralytics (YOLO object detection only)"
    "${PIP}" install -U pip setuptools wheel
    "${PIP}" install 'opencv-python-headless>=4.5.4' 'numpy>=1.20'
    if ! "${PIP}" install 'ultralytics>=8.0.0'; then
        bad "ultralytics install failed (often wrong/missing PyTorch on Jetson)."
        echo ""
        echo "  1) Install NVIDIA's PyTorch wheel for your JetPack (see REQUIREMENTS.md)."
        echo "  2) Retry: ${PIP} install --no-deps ultralytics"
        exit 1
    fi
fi

say "import check (same interpreter as nina-ui-kiosk)"
export PYTHONPATH="${REPO_ROOT}"
if ! "${PY}" -c "from ultralytics import YOLO; print('ultralytics', YOLO)"; then
    bad "import ultralytics failed — run the kiosk from a terminal for the traceback:"
    bad "  cd ${REPO_ROOT} && ./scripts/launch-sirena.sh"
    exit 1
fi
ok "ultralytics imports in ${PY}"

echo ""
ok "Vision/YOLO deps ready. Restart the kiosk:"
echo "  systemctl --user restart nina-ui-kiosk.service"
echo ""
echo "First time you enable Object detection, TensorRT export can take 2–3 min"
echo "and needs ~4 GB swap on Nano — see sirena_ui/README.md."
