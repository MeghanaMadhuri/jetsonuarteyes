#!/usr/bin/env bash
# Start local voice microservices on the Jetson (loopback only).
# ASR :6000  LLM :4000  TTS :2000
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

if [[ -x "${ROOT}/.venv-link/bin/python" ]]; then
  PYTHON="${ROOT}/.venv-link/bin/python"
elif [[ -x "${ROOT}/.venv/bin/python" ]]; then
  PYTHON="${ROOT}/.venv/bin/python"
else
  PYTHON="python3"
fi

export PYTHONPATH="${ROOT}${PYTHONPATH:+:${PYTHONPATH}}"
export TTS_SIG_DIR="${TTS_SIG_DIR:-${ROOT}/nina/data/voice/tts_signals}"
export NINA_VOICE_SPEECH_DIR="${NINA_VOICE_SPEECH_DIR:-${ROOT}/nina/data/voice/speech}"
export CONVERSATION_FOLDER="${CONVERSATION_FOLDER:-${ROOT}/nina/data/voice/conversations}"
export ASR_TTS_SIG_DIR="${ASR_TTS_SIG_DIR:-${TTS_SIG_DIR}}"
mkdir -p "${TTS_SIG_DIR}" "${NINA_VOICE_SPEECH_DIR}" "${CONVERSATION_FOLDER}"

export LLM_PRIMARY_BASE_URL="${LLM_PRIMARY_BASE_URL:-http://127.0.0.1:11434}"
export LLM_PRIMARY_MODEL="${LLM_PRIMARY_MODEL:-gemma2:2b}"
export LLM_FALLBACK_MODEL="${LLM_FALLBACK_MODEL:-llama3.2:3b-instruct-q4_K_M}"
export ASR_MODEL_PATH="${ASR_MODEL_PATH:-${ROOT}/models/whisper-tiny}"
export ASR_SINGLE_SESSION="${ASR_SINGLE_SESSION:-1}"
export FORCE_CPU="${FORCE_CPU:-1}"

# Optional Piper (higher quality than espeak):
# export PIPER_BIN=/path/to/piper
# export PIPER_MODEL=/path/to/en_US-amy-low.onnx
# export PIPER_CONFIG=/path/to/en_US-amy-low.onnx.json

log() { echo "[voice-edge] $*"; }

start_one() {
  local name="$1"
  shift
  log "starting ${name}..."
  "$@" &
  echo $! >> "${ROOT}/.voice-edge.pids"
}

: > "${ROOT}/.voice-edge.pids" 2>/dev/null || true

start_one tts \
  "${PYTHON}" -m uvicorn nina.voice.servers.tts_edge:app \
  --host 127.0.0.1 --port 2000 --log-level info

start_one llm \
  "${PYTHON}" -m uvicorn nina.voice.servers.llm_app:app \
  --host 127.0.0.1 --port 4000 --log-level info

start_one asr \
  "${PYTHON}" -m uvicorn nina.voice.servers.asr_app:app \
  --host 127.0.0.1 --port 6000 --log-level info

log "voice edge services started (PIDs in .voice-edge.pids)"
log "  TTS http://127.0.0.1:2000/health"
log "  LLM http://127.0.0.1:4000/health"
log "  ASR http://127.0.0.1:6000/health  ws://127.0.0.1:6000/ws/audio?mac=nina-jetson"
log "Enable Nina assistant: NINA_VOICE_EDGE_ENABLE=1 NINA_VOICE_ASSISTANT_ENABLE=1"

wait
