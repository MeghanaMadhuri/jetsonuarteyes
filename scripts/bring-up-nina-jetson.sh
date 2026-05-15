#!/usr/bin/env bash
# bring-up-nina-jetson.sh
# ---------------------------------------------------------------------------
# One-shot Jetson bring-up after a fresh `git clone` of feature/nina-app.
# Chains the four idempotent installers in the right order and prints a
# clear final banner with the LAN IP/port the Android companion app
# should be pointed at.
#
# What you get after this finishes successfully:
#   * Slamtec RPLIDAR S2E (Ethernet/UDP) reachable + pyrplidarsdk installed
#     (``--user`` for smoke tests + into ``.venv-link`` when that venv exists
#     or via install-sirena-companion step 1b so the kiosk interpreter sees it).
#   * Sirena UI companion gateway venv (`.venv-link`) populated with
#     vision/SLAM/sensor deps and the embedded HTTP gateway service
#     enabled.
#   * `/etc/sudoers.d/nina-host-power` rule so the in-app "Shutdown
#     Jetson" / "Reboot Jetson" buttons (and the Android
#     `/v1/system/{poweroff,reboot}` endpoints) actually fire.
#   * `nina-ui-kiosk.service` systemd USER unit enabled and running,
#     so the GUI auto-starts fullscreen on every boot. The kiosk
#     process embeds the Android companion gateway — there is no
#     separate `nina-link` daemon to keep alive.
#
# Re-run-safe: every underlying script is idempotent. Pass `--no-apt` to
# skip the apt step on hosts where you've already done it. Pass
# `--skip-lidar` if you're bringing up a tablet/headless dev box that
# doesn't have the S2E plugged in.
#
# Usage:
#   ./scripts/bring-up-nina-jetson.sh                 # full bring-up
#   ./scripts/bring-up-nina-jetson.sh --no-apt        # skip apt step
#   ./scripts/bring-up-nina-jetson.sh --skip-lidar    # skip S2E install
#
# Logs: each underlying installer logs to stdout/stderr; the final
# banner reproduces the most actionable bits. To see kiosk runtime
# logs after this finishes:
#   journalctl --user -u nina-ui-kiosk -f
# If that prints "No journal files were found", use:
#   tail -f ~/.cache/sirena/launch.log
# (user journal needs a login session or `sudo loginctl enable-linger "$USER"`).
# ---------------------------------------------------------------------------

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "${REPO_ROOT}"

# --- arg parsing ------------------------------------------------------------
DO_APT=1
DO_LIDAR=1
for arg in "$@"; do
  case "${arg}" in
    --no-apt) DO_APT=0 ;;
    --skip-lidar) DO_LIDAR=0 ;;
    -h|--help)
      sed -n '2,40p' "$0"
      exit 0
      ;;
    *)
      echo "[bring-up] unknown arg: ${arg}" >&2
      echo "[bring-up] try --help" >&2
      exit 2
      ;;
  esac
done

# --- helpers ----------------------------------------------------------------
BLUE="\033[1;34m"; GREEN="\033[1;32m"; YELLOW="\033[1;33m"; RED="\033[1;31m"; RESET="\033[0m"

step() { printf "\n${BLUE}==>${RESET} %s\n" "$*"; }
ok()   { printf "${GREEN}OK${RESET}   %s\n" "$*"; }
warn() { printf "${YELLOW}WARN${RESET} %s\n" "$*"; }
fail() { printf "${RED}FAIL${RESET} %s\n" "$*" >&2; }

# Bail out fast on non-Linux hosts — the underlying installers are all bash + apt.
case "$(uname -s)" in
  Linux) ;;
  *)
    fail "bring-up-nina-jetson.sh must run on the Jetson (Linux)."
    fail "Detected: $(uname -srm). Use scripts/build-companion-apk.sh on dev hosts."
    exit 1
    ;;
esac

# --- 1) apt deps ------------------------------------------------------------
if [[ "${DO_APT}" == "1" ]]; then
  step "1/6 apt deps (build tools + network helpers + onboard OSK)"
  sudo apt-get update -y
  sudo apt-get install -y \
    build-essential python3-dev cmake git \
    iputils-ping iproute2 net-tools \
    onboard
  ok "apt deps installed"
else
  warn "skipping apt deps (--no-apt)"
fi

# --- 2) Slamtec S2E lidar ---------------------------------------------------
if [[ "${DO_LIDAR}" == "1" ]]; then
  step "2/6 Slamtec RPLIDAR S2E (Ethernet/UDP + pyrplidarsdk + UDP buffer tuning)"
  bash "${REPO_ROOT}/scripts/install-slamtec-s2e-jetson.sh"
  ok "S2E lidar bring-up complete"
else
  warn "skipping lidar install (--skip-lidar) — kiosk will still boot, map screen will show 'Lidar (disabled)' until you set NINA_LIDAR_MODEL=disabled or re-run with the S2E"
fi

# --- 3) sirena_ui / android_gateway venv + service --------------------------
step "3/6 Sirena UI + embedded Android companion gateway (.venv-link)"
bash "${REPO_ROOT}/scripts/install-sirena-companion-jetson.sh"
ok "companion gateway installed"

step "4/6 Vision / YOLO (ultralytics into .venv-link for Object detection)"
bash "${REPO_ROOT}/scripts/install-vision-jetson.sh"
ok "vision / YOLO deps installed"

# --- 4) passwordless sudo for in-app shutdown/reboot ------------------------
step "5/6 sudoers rule for in-app Shutdown / Reboot Jetson"
bash "${REPO_ROOT}/scripts/install-nina-host-power.sh"
ok "host-power sudoers rule installed"

# --- 5) kiosk autostart -----------------------------------------------------
step "6/6 nina-ui-kiosk systemd USER unit (autostart fullscreen GUI)"
bash "${REPO_ROOT}/scripts/install-nina-ui-kiosk.sh"
ok "kiosk autostart enabled"

# --- final verification -----------------------------------------------------
step "post-install readiness check"

# kiosk unit healthy?
if systemctl --user is-active --quiet nina-ui-kiosk.service; then
  ok "systemctl --user is-active nina-ui-kiosk  -> active"
else
  warn "kiosk service is not active yet (often takes ~5s); status:"
  systemctl --user status nina-ui-kiosk.service --no-pager --lines 8 || true
fi

# gateway HTTP port up? capabilities endpoint as a smoke probe.
GATEWAY_PORT="${NINA_LINK_PORT:-8787}"
sleep 2
HTTP_OK=0
for i in 1 2 3 4 5; do
  if curl -fsS -m 2 "http://127.0.0.1:${GATEWAY_PORT}/v1/robot/capabilities" >/dev/null 2>&1; then
    HTTP_OK=1
    break
  fi
  sleep 2
done
if [[ "${HTTP_OK}" == "1" ]]; then
  ok "GET http://127.0.0.1:${GATEWAY_PORT}/v1/robot/capabilities  -> 200"
else
  warn "gateway didn't respond on :${GATEWAY_PORT} within ~12s. Check: journalctl --user -u nina-ui-kiosk -n 80"
fi

# Discover the LAN IP that the tablet should target. Prefer the Wi-Fi
# AP-mode addr (10.42.0.1) if it's up, else first non-loopback IPv4.
JETSON_IP=""
if ip -4 addr show 2>/dev/null | grep -q "10.42.0.1/"; then
  JETSON_IP="10.42.0.1"
else
  JETSON_IP="$(ip -4 -o addr show scope global 2>/dev/null | awk '{print $4}' | cut -d/ -f1 | head -n1 || true)"
fi
[[ -z "${JETSON_IP}" ]] && JETSON_IP="<jetson-ip>"

# Final banner.
echo ""
echo "==========================================================================="
printf "${GREEN}READY${RESET}: Nina Jetson bring-up complete.\n"
echo ""
printf "  Pair the companion app at:  ${GREEN}http://%s:%s${RESET}\n" "${JETSON_IP}" "${GATEWAY_PORT}"
echo ""
echo "  Next steps:"
echo "    * Open Sirena Companion on the tablet, Setup -> use that URL."
echo "    * Hard reboot the Jetson once to confirm the kiosk autostarts."
echo "    * Tail logs: journalctl --user -u nina-ui-kiosk -f"
echo "      (if 'No journal files': loginctl enable-linger, log in graphically once,"
echo "       or: tail -f ~/.cache/sirena/launch.log)"
echo ""
echo "  If the Drive screen says 'BLDC checking...' for >30s, see:"
echo "    journalctl --user -u nina-ui-kiosk -n 200 | grep -i hover"
echo "==========================================================================="
