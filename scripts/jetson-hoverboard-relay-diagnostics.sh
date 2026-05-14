#!/usr/bin/env bash
# Print hoverboard pack relay-related env as seen by systemd + optional
# navigation.env overrides. Run on the Jetson after install.
#
# Usage:
#   ./scripts/jetson-hoverboard-relay-diagnostics.sh
#
# Relay is enabled in the shipped desktop/nina-ui-kiosk.service via:
#   NINA_HOVER_RELAY_HEADER_PIN=37
#   NINA_HOVER_RELAY_POWER_ON_LEVEL=1
#   NINA_HOVER_RELAY_SHUTDOWN_ALLOWS_POWER=0
#
# If /etc/nina-link/navigation.env redefines the same variables *after* those
# lines in the unit, it can override them — this script helps spot that.

set -euo pipefail

echo "=== nina-ui-kiosk.service (installed user unit) — HOVER_RELAY / HOVER_POWER lines ==="
UNIT="${HOME}/.config/systemd/user/nina-ui-kiosk.service"
if [[ -f "${UNIT}" ]]; then
  grep -E 'NINA_HOVER_RELAY|NINA_HOVER_POWER_RELAY|EnvironmentFile' "${UNIT}" || true
else
  echo "(missing ${UNIT} — run scripts/install-nina-ui-kiosk.sh from the repo)"
fi

echo ""
echo "=== /etc/nina-link/navigation.env (if present) — same keys ==="
NAV_ENV="/etc/nina-link/navigation.env"
if [[ -f "${NAV_ENV}" ]]; then
  grep -E 'NINA_HOVER_RELAY|NINA_HOVER_POWER_RELAY' "${NAV_ENV}" || echo "(no matching keys)"
else
  echo "(no file — unit defaults apply unless you add a drop-in)"
fi

echo ""
echo "=== Tick test (stop kiosk first) ==="
echo "  systemctl --user stop nina-ui-kiosk.service"
echo "  python3 $(dirname "$0")/hoverboard_relay_tick_test.py --bcm 26"
echo ""
echo "=== After tick test, restart kiosk ==="
echo "  systemctl --user start nina-ui-kiosk.service"
