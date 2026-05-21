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
say "pip install -r requirements-link.txt into .venv-link (force — ignore ~/.local)"
# Pip treats ~/.local as \"already satisfied\" unless we force into the venv.
# Kiosk uses PYTHONNOUSERSITE=1, so packages must live under .venv-link.
"${PIP}" install -U pip wheel
"${PIP}" install 'setuptools>=70,<82'
"${PIP}" install --force-reinstall --no-cache-dir -r "${REQ}"

say "import check (PYTHONNOUSERSITE=1 — same as kiosk)"
export PYTHONNOUSERSITE=1
export PYTHONPATH="${REPO_ROOT}"
_venv_site="$("${PY}" -c "import site; print(site.getsitepackages()[0])")"
export PYTHONPATH="${REPO_ROOT}:${_venv_site}"
_sys_d="/usr/lib/python3/dist-packages"
if [[ -d "${_sys_d}/PyQt5" ]]; then
    export PYTHONPATH="${PYTHONPATH}:${_sys_d}"
fi
"${PY}" -c "
import fastapi, uvicorn, pydantic, serial, smbus2
assert 'site-packages' in fastapi.__file__, fastapi.__file__
print('fastapi', fastapi.__version__, fastapi.__file__)
print('uvicorn', uvicorn.__version__)
print('tablet gateway deps OK')
"
ok "fastapi + uvicorn ready in ${PY}"

echo ""
echo "Restart kiosk: systemctl --user restart nina-ui-kiosk.service"
