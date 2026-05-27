#!/usr/bin/env bash
# Bootstrap edge voice on Jetson: deps, optional Whisper model, Ollama model.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

if [[ -x "${ROOT}/.venv-link/bin/pip" ]]; then
  PIP="${ROOT}/.venv-link/bin/pip"
  PYTHON="${ROOT}/.venv-link/bin/python"
else
  PIP="pip3"
  PYTHON="python3"
fi

echo "==> apt packages (may need sudo)"
if command -v apt-get >/dev/null 2>&1; then
  sudo apt-get update -qq
  sudo apt-get install -y -qq alsa-utils espeak-ng ffmpeg curl || true
fi

echo "==> Python voice-edge requirements"
"${PIP}" install -r "${ROOT}/requirements-voice-edge.txt"

if command -v ollama >/dev/null 2>&1; then
  echo "==> Ollama model (gemma2:2b)"
  ollama pull gemma2:2b || ollama pull llama3.2:3b-instruct-q4_K_M || true
else
  echo "WARN: install Ollama for local LLM — https://ollama.com"
fi

MODEL_DIR="${ROOT}/models/whisper-tiny"
if [[ ! -f "${MODEL_DIR}/model.bin" ]] && [[ -f "${ROOT}/scripts/download_whisper_model.py" ]]; then
  echo "==> Downloading whisper-tiny (Systran/faster-whisper-tiny, ~75 MB)"
  if ! "${PYTHON}" "${ROOT}/scripts/download_whisper_model.py" --size tiny --out "${MODEL_DIR}"; then
    echo "ERROR: Whisper download failed."
    echo "  rm -rf ${ROOT}/models/models--openai--whisper-large-v3-turbo"
    echo "  export HF_TOKEN=<token>   # optional, avoids HF rate limits"
    echo "  ${PYTHON} ${ROOT}/scripts/download_whisper_model.py --size tiny --out ${MODEL_DIR}"
    exit 1
  fi
elif [[ ! -f "${MODEL_DIR}/model.bin" ]]; then
  echo "WARN: place Whisper model at ${MODEL_DIR}/model.bin or set ASR_MODEL_PATH"
fi

mkdir -p "${ROOT}/nina/data/voice/tts_signals" "${ROOT}/nina/data/voice/speech"
chmod +x "${ROOT}/scripts/start-voice-edge.sh" "${ROOT}/scripts/stop-voice-edge.sh"

echo "Done. Add to /etc/nina-link/navigation.env:"
echo "  NINA_VOICE_EDGE_ENABLE=1"
echo "  NINA_VOICE_ASSISTANT_ENABLE=1"
echo "Then: bash scripts/start-voice-edge.sh  (separate terminal)"
echo "      systemctl --user restart nina-ui-kiosk.service"
