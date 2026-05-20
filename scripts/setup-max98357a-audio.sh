#!/usr/bin/env bash
# Configure Nina audio playback for a MAX98357A I2S amplifier after the
# Jetson I2S sound card has been enabled by Jetson-IO / device tree.
#
# This script does NOT create the Jetson device-tree overlay. That part is
# board / JetPack / carrier specific. It verifies that ALSA can see a playback
# PCM, writes Nina's audio device env vars into /etc/nina-link/navigation.env,
# and optionally plays a short test tone through the chosen device.
#
# Usage:
#   ./scripts/setup-max98357a-audio.sh --device plughw:CARD=<card>,DEV=0
#   ./scripts/setup-max98357a-audio.sh --list
#
# Typical flow:
#   1. Enable the Jetson I2S pins / sound card (see docs/MAX98357A_JETSON_I2S.md)
#   2. Reboot
#   3. Run: aplay -l
#   4. Run this script with the matching ALSA device

set -euo pipefail

ENV_FILE="${NINA_NAV_ENV_FILE:-/etc/nina-link/navigation.env}"
DEVICE="${NINA_MAX98357A_ALSA_DEVICE:-}"
RATE="${NINA_MAX98357A_AUDIO_RATE:-48000}"
WARMUP_MS="${NINA_MAX98357A_WARMUP_MS:-100}"
RUN_TEST=1

usage() {
    cat <<EOF
Usage:
  $0 --device <alsa-pcm> [--rate 48000] [--env-file /etc/nina-link/navigation.env] [--no-test]
  $0 --list

Examples:
  $0 --device plughw:CARD=max98357a,DEV=0
  $0 --device hw:2,0 --rate 48000
  NINA_MAX98357A_ALSA_DEVICE=plughw:CARD=tegrasndt210ref,DEV=0 $0

This writes:
  NINA_GREET_APLAY_DEVICE=<alsa-pcm>
  NINA_AUDIO_MPG123_DEVICE=<alsa-pcm>
  NINA_AUDIO_MP3_VIA_APLAY=1
  NINA_AUDIO_APLAY_STEREO_MODE=left
  NINA_AUDIO_OUTPUT_RATE=<rate>
  NINA_AUDIO_OUTPUT_WARMUP_MS=<warmup-ms>
  NINA_AUDIO_PREROLL_MS=0
  NINA_AUDIO_MUTE_PREROLL_SEC=0
  NINA_AUDIO_SILENCE_KEEPALIVE=1
  NINA_AUDIO_SILENCE_KEEPALIVE_SEC=2
  NINA_AUDIO_EDGE_SILENCE_MS=80
  NINA_AUDIO_APE_ROUTE=1
  NINA_AUDIO_APE_CARD=APE
  NINA_AUDIO_APE_I2S=I2S5
  NINA_AUDIO_APE_MUX=ADMAIF1
  NINA_AUDIO_APE_MASTER_MODE=cbs-cfs
  NINA_AUDIO_APE_BCLK_RATIO=16
  NINA_AUDIO_APE_FSYNC_WIDTH=1

into:
  ${ENV_FILE}

Then restart:
  systemctl --user restart nina-ui-kiosk.service
EOF
}

list_devices() {
    echo "=== aplay -l (hardware playback devices) ==="
    if command -v aplay >/dev/null 2>&1; then
        aplay -l || true
        echo
        echo "=== aplay -L (PCM names) ==="
        aplay -L || true
    else
        echo "aplay not found. Install with: sudo apt install -y alsa-utils" >&2
        return 1
    fi
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --device|-D)
            DEVICE="${2:-}"
            shift 2
            ;;
        --rate)
            RATE="${2:-48000}"
            shift 2
            ;;
        --warmup-ms)
            WARMUP_MS="${2:-100}"
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
    echo "Installing alsa-utils (aplay) ..." >&2
    sudo apt-get update
    sudo apt-get install -y alsa-utils
fi

if ! command -v mpg123 >/dev/null 2>&1; then
    echo "Installing mpg123 for MP3 playback ..." >&2
    sudo apt-get update
    sudo apt-get install -y mpg123
fi

if [[ -z "${DEVICE}" ]]; then
    cat >&2 <<EOF
No ALSA device specified.

Run:
  $0 --list

Then pick the MAX98357A / Jetson I2S playback PCM and re-run, e.g.:
  $0 --device plughw:CARD=max98357a,DEV=0
  $0 --device hw:2,0

EOF
    exit 2
fi

if ! [[ "${RATE}" =~ ^[0-9]+$ ]] || [[ "${RATE}" -lt 8000 || "${RATE}" -gt 192000 ]]; then
    echo "--rate must be an integer in [8000, 192000], got: ${RATE}" >&2
    exit 2
fi

if ! [[ "${WARMUP_MS}" =~ ^[0-9]+$ ]] || [[ "${WARMUP_MS}" -gt 2000 ]]; then
    echo "--warmup-ms must be an integer in [0, 2000], got: ${WARMUP_MS}" >&2
    exit 2
fi

TMP_FILE="$(mktemp)"
cleanup() {
    rm -f "${TMP_FILE}" "${TMP_FILE}.wav"
}
trap cleanup EXIT

if [[ -f "${ENV_FILE}" ]]; then
    cp "${ENV_FILE}" "${TMP_FILE}"
else
    : > "${TMP_FILE}"
fi

# Remove any previous Nina audio output entries so the last occurrence cannot
# shadow the new values at systemd EnvironmentFile parse time.
sed -i -E \
    -e '/^NINA_GREET_APLAY_DEVICE=/d' \
    -e '/^NINA_AUDIO_MPG123_DEVICE=/d' \
    -e '/^NINA_AUDIO_MP3_VIA_APLAY=/d' \
    -e '/^NINA_AUDIO_APLAY_STEREO_MODE=/d' \
    -e '/^NINA_AUDIO_OUTPUT_RATE=/d' \
    -e '/^NINA_AUDIO_OUTPUT_WARMUP_MS=/d' \
    -e '/^NINA_AUDIO_PREROLL_MS=/d' \
    -e '/^NINA_AUDIO_MUTE_PREROLL_SEC=/d' \
    -e '/^NINA_AUDIO_SILENCE_KEEPALIVE=/d' \
    -e '/^NINA_AUDIO_SILENCE_KEEPALIVE_SEC=/d' \
    -e '/^NINA_AUDIO_EDGE_SILENCE_MS=/d' \
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

# MAX98357A I2S amplifier playback (written by setup-max98357a-audio.sh)
NINA_GREET_APLAY_DEVICE=${DEVICE}
NINA_AUDIO_MPG123_DEVICE=${DEVICE}
NINA_AUDIO_MP3_VIA_APLAY=1
NINA_AUDIO_APLAY_STEREO_MODE=left
NINA_AUDIO_OUTPUT_RATE=${RATE}
NINA_AUDIO_OUTPUT_WARMUP_MS=${WARMUP_MS}
NINA_AUDIO_PREROLL_MS=0
NINA_AUDIO_MUTE_PREROLL_SEC=0
NINA_AUDIO_SILENCE_KEEPALIVE=1
NINA_AUDIO_SILENCE_KEEPALIVE_SEC=2
NINA_AUDIO_EDGE_SILENCE_MS=80
NINA_AUDIO_APE_ROUTE=1
NINA_AUDIO_APE_CARD=APE
NINA_AUDIO_APE_I2S=I2S5
NINA_AUDIO_APE_MUX=ADMAIF1
NINA_AUDIO_APE_CHANNELS=2
NINA_AUDIO_APE_BITS=16
NINA_AUDIO_APE_FRAME_MODE=i2s
NINA_AUDIO_APE_MASTER_MODE=cbs-cfs
NINA_AUDIO_APE_BCLK_RATIO=16
NINA_AUDIO_APE_FSYNC_WIDTH=1
EOF

sudo mkdir -p "$(dirname "${ENV_FILE}")"
sudo install -m 0644 -o root -g root "${TMP_FILE}" "${ENV_FILE}"

echo
echo "Wrote MAX98357A audio config to ${ENV_FILE}:"
grep -E '^(NINA_GREET_APLAY_DEVICE|NINA_AUDIO_MPG123_DEVICE|NINA_AUDIO_MP3_VIA_APLAY|NINA_AUDIO_APLAY_STEREO_MODE|NINA_AUDIO_OUTPUT_RATE|NINA_AUDIO_OUTPUT_WARMUP_MS|NINA_AUDIO_PREROLL_MS|NINA_AUDIO_MUTE_PREROLL_SEC|NINA_AUDIO_SILENCE_KEEPALIVE|NINA_AUDIO_SILENCE_KEEPALIVE_SEC|NINA_AUDIO_EDGE_SILENCE_MS|NINA_AUDIO_APE_)=' "${ENV_FILE}" || true

echo
echo "Applying Orin Nano APE -> I2S5 route now ..."
amixer -c APE cset name='I2S5 Mux' ADMAIF1 >/dev/null
amixer -c APE cset name='I2S5 Sample Rate' "${RATE}" >/dev/null
amixer -c APE cset name='I2S5 Playback Audio Channels' 2 >/dev/null
amixer -c APE cset name='I2S5 Playback Audio Bit Format' 16 >/dev/null
amixer -c APE cset name='I2S5 Client Channels' 2 >/dev/null
amixer -c APE cset name='I2S5 Client Bit Format' 16 >/dev/null
amixer -c APE cset name='I2S5 codec frame mode' i2s >/dev/null
amixer -c APE cset name='I2S5 codec master mode' cbs-cfs >/dev/null
amixer -c APE cset name='I2S5 BCLK Ratio' 16 >/dev/null
amixer -c APE cset name='I2S5 FSYNC Width' 1 >/dev/null

if [[ "${RUN_TEST}" -eq 1 ]]; then
    echo
    echo "Playing a 1-second left-slot test tone through ${DEVICE} ..."
    python3 - <<'PY' "${TMP_FILE}.wav" "${RATE}"
import math
import sys
import wave

path = sys.argv[1]
rate = int(sys.argv[2])
duration = 1.0
freq = 440.0
amp = 0.25
frames = int(rate * duration)
with wave.open(path, "wb") as w:
    # MAX98357A breakout on the reference Orin Nano listens to the left I2S slot.
    w.setnchannels(2)
    w.setsampwidth(2)
    w.setframerate(rate)
    data = bytearray()
    for i in range(frames):
        sample = int(32767 * amp * math.sin(2 * math.pi * freq * i / rate))
        left = sample.to_bytes(2, "little", signed=True)
        right = (0).to_bytes(2, "little", signed=True)
        data.extend(left)
        data.extend(right)
    w.writeframes(bytes(data))
PY
    if ! aplay -D "${DEVICE}" -q "${TMP_FILE}.wav"; then
        cat >&2 <<EOF

Test playback failed.

This usually means one of:
  - the Jetson I2S sound card / pinmux is not enabled yet,
  - the ALSA PCM name is wrong,
  - the MAX98357A wiring / power / SD_MODE pin is wrong,
  - the selected PCM only accepts a different rate (try --rate 48000 or --rate 44100).

Run:
  aplay -l
  aplay -L
  speaker-test -D ${DEVICE} -c 2 -r ${RATE} -t sine -f 440
EOF
        exit 1
    fi
fi

cat <<EOF

Done.

Restart Nina so systemd reloads ${ENV_FILE}:
  systemctl --user restart nina-ui-kiosk.service

Verify runtime env:
  systemctl --user show nina-ui-kiosk.service --property=Environment | tr ' ' '\\n' | grep NINA_AUDIO

Verify playback:
  mpg123 -q -r '${RATE}' -w /tmp/nina-cant-move.wav nina/audio/alerts/cant_move.mp3
  aplay -D '${DEVICE}' /tmp/nina-cant-move.wav
EOF

