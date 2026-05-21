#!/usr/bin/env bash
# Install FastAPI / uvicorn + link deps into .venv-link (embedded tablet gateway).
#
# The kiosk runs ``python -m sirena_ui``, which imports the tablet gateway at
# startup. Without these packages the UI exits immediately with
# ``ModuleNotFoundError: No module named 'fastapi'``.
#
#   ./scripts/install-tablet-gateway-jetson.sh
#
# Full nina-link install (``install-nina-link-jetson.sh --all``) also installs
# requirements-link.txt; this script is for kiosk-only Jetsons.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
VENV_PATH="${REPO_ROOT}/.venv-link"
PY="${VENV_PATH}/bin/python"
PIP="${VENV_PATH}/bin/pip"
REQ="${REPO_ROOT}/requirements-link.txt"

say() { printf '\n\033[1m%s\033[0m\n' "$*"; }
ok()  { printf '  [\033[32mOK\033[0m] %s\n' "$*"; }
bad() { printf '  [\033[31m!!\033[0m] %s\n' "$*" >&2; }

if [[ ! -x "${PY}" ]]; then
    bad "No ${VENV_PATH} — run ./scripts/install-nina-link-jetson.sh --all first"
    exit 1
fi
if [[ ! -f "${REQ}" ]]; then
    bad "Missing ${REQ}"
    exit 1
fi

export PIP_USER=0
export PIP_BREAK_SYSTEM_PACKAGES="${PIP_BREAK_SYSTEM_PACKAGES:-1}"

cd "${REPO_ROOT}"
say "pip install -r requirements-link.txt (fastapi, uvicorn, pyserial, smbus2)"
"${PIP}" install -U pip setuptools wheel
"${PIP}" install -r "${REQ}"

say "import check"
export PYTHONNOUSERSITE=1
export PYTHONPATH="${REPO_ROOT}"
"${PY}" -c "
import fastapi, uvicorn, pydantic, serial, smbus2
print('fastapi', fastapi.__version__)
print('uvicorn', uvicorn.__version__)
print('tablet gateway deps OK')
"
ok "fastapi + uvicorn ready in ${PY}"

echo ""
echo "Restart kiosk: systemctl --user restart nina-ui-kiosk.service"
