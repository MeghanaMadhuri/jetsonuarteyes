#!/usr/bin/env bash
# -----------------------------------------------------------------------------
# Jetson — single entry point for tablet companion + Sirena UI (embedded HTTP API)
#
# This script is a thin wrapper around install-sirena-companion-jetson.sh so
# operators have one memorable name. It:
#   - Creates/refreshes .venv-link (PyQt5 from distro + venv --system-site-packages)
#   - Installs pip deps from requirements-link.txt
#   - Registers nina-link.service (python -m sirena_ui) and enables HTTP bridges
#   - Optionally pulls in sirena_ui/requirements-headless.txt (SLAM / vision stack)
#
# From repo root on the Jetson:
#   chmod +x scripts/jetson-tablet-setup.sh
#   ./scripts/jetson-tablet-setup.sh
#   ./scripts/jetson-tablet-setup.sh --with-sirena-headless
#
# See also: docs/COMPANION_APP.md
# -----------------------------------------------------------------------------

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec "${SCRIPT_DIR}/install-sirena-companion-jetson.sh" "$@"
