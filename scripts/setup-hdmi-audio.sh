#!/usr/bin/env bash
# Configure Nina audio for HDMI/DP (tegra HDA) on Jetson Orin Nano.
#
# Desktop Ubuntu often runs PipeWire/Pulse on the HDMI sink. Opening
# plughw:CARD=HDA,DEV=3 exclusively then fails with "Device or resource busy".
# This script prefers the shared ALSA name "default" (routes through Pulse)
# and disables I2S-only keepalive flags (persistent pipe / silence loop).
#
# Usage:
#   ./scripts/setup-hdmi-audio.sh
#   ./scripts/setup-hdmi-audio.sh --device default
#   ./scripts/setup-hdmi-audio.sh --device plughw:CARD=HDA,DEV=3 --no-test
#   ./scripts/setup-hdmi-audio.sh --list
#
# After writing /etc/nina-link/navigation.env:
#   systemctl --user restart nina-ui-kiosk.service

set -euo pipefail

ENV_FILE="${NINA_NAV_ENV_FILE:-/etc/nina-link/navigation.env}"
DEVICE="${NINA_HDMI_ALSA_DEVICE:-}"
RATE="${NINA_HDMI_AUDIO_RATE:-24000}"
RUN_TEST=1
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TEST_MP3="${REPO_ROOT}/nina/audio/alerts/touch.mp3"

usage() {
    cat <<EOF
Usage:
  $0 [--device <alsa-pcm>] [--rate 24000] [--env-file PATH] [--no-test]
  $0 --list

Writes HDMI-friendly Nina audio env (no APE/I2S route, no persistent aplay hold):
  NINA_GREET_APLAY_DEVICE=<pcm>
  NINA_AUDIO_MPG123_DEVICE=<pcm>
  NINA_AUDIO_MP3_VIA_APLAY=0
  NINA_AUDIO_OUTPUT_RATE=24000
  NINA_AUDIO_PERSISTENT_PIPE=0
  NINA_AUDIO_SILENCE_KEEPALIVE=0

Default device: plughw:CARD=HDA,DEV=3 (kiosk unit stops Pulse before start).
Use "default" only if you keep PipeWire running (see --list).

Re-run install-nina-ui-kiosk.sh after git pull to refresh the unit ExecStartPre.

Then: systemctl --user restart nina-ui-kiosk.service
EOF
}

list_devices() {
    echo "=== aplay -l ==="
    aplay -l 2>/dev/null || true
    echo
    echo "=== aplay -L (first 40 lines) ==="
    aplay -L 2>/dev/null | head -40 || true
    echo
    echo "=== pactl sinks (if Pulse/PipeWire) ==="
    pactl list short sinks 2>/dev/null || echo "(pactl not available)"
}

pick_default_device() {
    if [[ -n "${DEVICE}" ]]; then
        return 0
    fi
    # Fleet default: direct tegra-HDA (kiosk ExecStartPre stops Pulse first).
    DEVICE="plughw:CARD=HDA,DEV=3"
    local wav="/usr/share/sounds/alsa/Front_Center.wav"
    if [[ ! -f "${wav}" ]]; then
        return 0
    fi
    if aplay -q -D "${DEVICE}" "${wav}" 2>/dev/null; then
        echo "[hdmi-audio] using direct HDA PCM: ${DEVICE}"
        return 0
    fi
    if aplay -q -D default "${wav}" 2>/dev/null; then
        DEVICE="default"
        echo "[hdmi-audio] using ALSA device: ${DEVICE} (Pulse/shared)"
        return 0
    fi
    echo "[hdmi-audio] WARNING: could not probe HDA or default; still writing DEVICE=${DEVICE}" >&2
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --device|-D)
            DEVICE="${2:-}"
            shift 2
            ;;
        --rate)
            RATE="${2:-24000}"
            shift 2
            ;;
        --env-file)
            ENV_FILE="${2:-/etc/nina-link/navigation.env}"
            shift 2
            ;;
        --no-test)
            RUN_TEST=0
            shift
            ;;
        --list)
            list_devices
            exit 0
            ;;
        --help|-h)
            usage
            exit 0
            ;;
        *)
            echo "Unknown argument: $1" >&2
            usage >&2
            exit 2
            ;;
    esac
done

if ! command -v aplay >/dev/null 2>&1; then
    echo "Installing alsa-utils ..." >&2
    sudo apt-get update
    sudo apt-get install -y alsa-utils
fi
if ! command -v mpg123 >/dev/null 2>&1; then
    echo "Installing mpg123 ..." >&2
    sudo apt-get install -y mpg123
fi

pick_default_device

TMP_FILE="$(mktemp)"
cleanup() { rm -f "${TMP_FILE}"; }
trap cleanup EXIT

if [[ -f "${ENV_FILE}" ]]; then
    cp "${ENV_FILE}" "${TMP_FILE}"
else
    : > "${TMP_FILE}"
fi

sed -i -E \
    -e '/^NINA_GREET_APLAY_DEVICE=/d' \
    -e '/^NINA_AUDIO_MPG123_DEVICE=/d' \
    -e '/^NINA_AUDIO_MP3_VIA_APLAY=/d' \
    -e '/^NINA_AUDIO_APLAY_STEREO_MODE=/d' \
    -e '/^NINA_AUDIO_OUTPUT_RATE=/d' \
    -e '/^NINA_AUDIO_OUTPUT_WARMUP_MS=/d' \
    -e '/^NINA_AUDIO_PREROLL_MS=/d' \
    -e '/^NINA_AUDIO_MUTE_PREROLL_SEC=/d' \
    -e '/^NINA_AUDIO_PERSISTENT_PIPE=/d' \
    -e '/^NINA_AUDIO_SILENCE_KEEPALIVE=/d' \
    -e '/^NINA_AUDIO_SILENCE_KEEPALIVE_SEC=/d' \
    -e '/^NINA_AUDIO_EDGE_SILENCE_MS=/d' \
    -e '/^NINA_AUDIO_MIXER_CARD=/d' \
    -e '/^NINA_AUDIO_APE_ROUTE=/d' \
    -e '/^NINA_AUDIO_APE_CARD=/d' \
    -e '/^NINA_AUDIO_APE_I2S=/d' \
    -e '/^NINA_AUDIO_APE_MUX=/d' \
    -e '/^NINA_AUDIO_APE_CHANNELS=/d' \
    -e '/^NINA_AUDIO_APE_BITS=/d' \
    -e '/^NINA_AUDIO_APE_FRAME_MODE=/d' \
    -e '/^NINA_AUDIO_APE_MASTER_MODE=/d' \
    -e '/^NINA_AUDIO_APE_BCLK_RATIO=/d' \
    -e '/^NINA_AUDIO_APE_FSYNC_WIDTH=/d' \
    "${TMP_FILE}"

cat >> "${TMP_FILE}" <<EOF

# HDMI/DP audio (written by setup-hdmi-audio.sh) — use default with Pulse; not I2S keepalive.
NINA_GREET_APLAY_DEVICE=${DEVICE}
NINA_AUDIO_MPG123_DEVICE=${DEVICE}
NINA_AUDIO_MP3_VIA_APLAY=0
NINA_AUDIO_APLAY_STEREO_MODE=none
NINA_AUDIO_OUTPUT_RATE=${RATE}
NINA_AUDIO_OUTPUT_WARMUP_MS=100
NINA_AUDIO_PREROLL_MS=0
NINA_AUDIO_MUTE_PREROLL_SEC=0
NINA_AUDIO_PERSISTENT_PIPE=0
NINA_AUDIO_SILENCE_KEEPALIVE=0
EOF

sudo mkdir -p "$(dirname "${ENV_FILE}")"
sudo install -m 0644 -o root -g root "${TMP_FILE}" "${ENV_FILE}"

echo
echo "Wrote HDMI audio config to ${ENV_FILE}:"
grep -E '^NINA_(GREET_APLAY|AUDIO_)' "${ENV_FILE}" || true

if [[ "${RUN_TEST}" -eq 1 && -f "${TEST_MP3}" ]]; then
    echo
    echo "Playing test MP3 via mpg123 -> ${DEVICE} ..."
    if mpg123 -q -o alsa -a "${DEVICE}" "${TEST_MP3}"; then
        echo "[hdmi-audio] test OK"
    else
        echo "[hdmi-audio] test FAILED — try: $0 --list" >&2
        echo "  Or stop Pulse: systemctl --user stop pipewire pipewire-pulse wireplumber" >&2
        exit 1
    fi
fi

echo
echo "Restart Nina:"
echo "  systemctl --user restart nina-ui-kiosk.service"
