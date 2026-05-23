#!/usr/bin/env bash
# Collect forward/back straight-motion evidence from launch.log + navigation.env.
#
# Run on the Jetson after reproducing bad forward motion (D-pad FWD or Straight bench):
#   bash scripts/diagnose-forward-motion.sh
#
# Optional: analyze a copied log file:
#   bash scripts/diagnose-forward-motion.sh /path/to/launch.log
#
# Then reproduce once more and re-run — the "RECENT" section should show either:
#   drive: hover forward pulse          → drift-correct loop (good)
#   hover forward straight: cycle N     → 0.5 s legs + IMU checks (good)
#   drive from stop (straight fallback) → BLDC kick/cruise, NOT lean legs (bad on hoverboard)

set -euo pipefail

LOG="${1:-${HOME}/.cache/sirena/launch.log}"
ENV="${NINA_NAV_ENV:-/etc/nina-link/navigation.env}"

section() { printf '\n=== %s ===\n' "$1"; }

section "Forward motion diagnostic ($(date -Is 2>/dev/null || date))"
echo "Log file: ${LOG}"
echo "Env file: ${ENV}"

if [[ ! -f "${LOG}" ]]; then
  echo "ERROR: log not found. Start kiosk first or pass a log path."
  exit 1
fi

section "Deploy / code (repo checkout on this host)"
if command -v git >/dev/null 2>&1; then
  repo_root="$(git -C "$(dirname "$0")/.." rev-parse --show-toplevel 2>/dev/null || true)"
  if [[ -n "${repo_root}" ]]; then
    git -C "${repo_root}" log -1 --oneline 2>/dev/null || true
  fi
fi

section "Active hover + IMU env (uncommented lines only)"
if [[ -f "${ENV}" ]]; then
  grep -E '^[^#]*NINA_HOVER_(FWD|REV|STRAIGHT|IMU|PULSE|SWAP)' "${ENV}" 2>/dev/null || echo "(no active NINA_HOVER_* lines — code defaults apply)"
  grep -E '^[^#]*NINA_IMU_' "${ENV}" 2>/dev/null || echo "(no active NINA_IMU_* lines)"
else
  echo "WARNING: ${ENV} not found"
fi

section "Resolved lean defaults (from Python, same as kiosk)"
if [[ -f "${ENV}" ]]; then
  set -a
  # shellcheck disable=SC1090
  source "${ENV}" 2>/dev/null || true
  set +a
fi
repo_root="$(cd "$(dirname "$0")/.." && pwd)"
if [[ -d "${repo_root}/.venv-link" ]]; then
  PY="${repo_root}/.venv-link/bin/python3"
elif command -v python3 >/dev/null 2>&1; then
  PY=python3
else
  PY=""
fi
if [[ -n "${PY}" ]] && command -v "${PY}" >/dev/null 2>&1; then
  (cd "${repo_root}" && "${PY}" -c "
from pathlib import Path
from nina.config.settings import load_settings
s = load_settings(Path('.'))
h = s.hoverboard_axis
print(f'  FWD L/R = {h.forward_pos_left} / {h.forward_pos_right}')
print(f'  REV L/R = {h.backward_pos_left} / {h.backward_pos_right}')
print(f'  brake L/R = {h.brake_pos_left} / {h.brake_pos_right}')
print(f'  pulse_forward_enabled = {h.pulse_forward_enabled}')
") 2>/dev/null || echo "(python settings load failed)"
else
  echo "  (python not available — defaults: FWD 2022/2080, REV 2100/2000)"
fi

section "Which drive path ran recently (last 50 matches)"
grep -E 'drive: hover (forward|backward) pulse|drive from stop \(straight fallback\)|hover (forward|backward) straight:' "${LOG}" | tail -50 || echo "(no matches)"

section "Last forward straight loop startup (lean goals + IMU settle mode)"
grep -E 'hover forward straight loop:|hover forward straight: priming' "${LOG}" | tail -5 || echo "(none — drift-correct loop may not have started)"

section "Last forward cycles (drift / correction / abort)"
grep -E 'hover forward straight: cycle|no IMU sample|ABORT threshold|cumulative drift|cant_move|settle TIMEOUT' "${LOG}" | tail -40 || echo "(none)"

section "IMU hook wiring"
grep -E 'MPU-9250|imu.*hook|yaw_rate_fn|active_settle=(ON|OFF)' "${LOG}" | tail -15 || echo "(no IMU lines in log)"

section "Straight fallback warnings (should be empty on hoverboard FWD)"
grep -E 'straight fallback|wanted drift-correct|supports_pulse=' "${LOG}" | tail -20 || echo "(none — good)"

section "Kiosk service env snippet (if systemctl available)"
if systemctl --user show nina-ui-kiosk.service -p Environment 2>/dev/null | head -5; then
  :
else
  echo "(systemctl --user not available in this shell)"
fi

section "What to do next"
cat <<'EOF'
1. Reproduce: Drive screen → hold D-pad Forward (or Straight bench forward) for ~3 s, release.
2. Re-run this script immediately.
3. Good logs show:
     drive: hover forward pulse
     hover forward straight: cycle 1 — forward leg 0.50s
     hover forward straight loop: ... FWD L(id12)=... R(id13)=...
4. Bad logs show:
     drive from stop (straight fallback): kick 14% then cruise 5%
5. If drift-correct runs but motion curves/spins, paste the "Last forward cycles" section
   and check IMU (no IMU sample = correction skipped).
6. Enable verbose drive logging (optional), restart kiosk, reproduce:
     export NINA_HOVER_FORWARD_DEBUG=1
EOF
