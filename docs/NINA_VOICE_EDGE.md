# Nina edge voice assistant

Runs the **sirena-repo voice stack** entirely on the Jetson (no `just_stream.js` cloud gateway, no vision).

## Architecture

```text
  Mic (arecord 16 kHz)
        │
        ▼
  ASR :6000  (Whisper + VAD WebSocket)
        │ transcript
        ▼
  LLM :4000  (Ollama via llm_app.py)
        │ reply text
        ▼
  TTS :2000  (Piper or espeak → MP3)
        │
        ▼
  Nina AudioPlayer (existing speaker path)
```

Services live under `nina/voice/servers/` (copied/adapted from sirena-repo). The kiosk UI starts a **VoiceAssistant** thread when enabled.

## Jetson setup

```bash
cd ~/Nvidia-jetson-platform
git pull
bash scripts/install-voice-edge.sh
```

Add to `/etc/nina-link/navigation.env`:

```bash
NINA_VOICE_EDGE_ENABLE=1
NINA_VOICE_ASSISTANT_ENABLE=1
NINA_VOICE_DEVICE_ID=nina-jetson
NINA_VOICE_MIC_DEVICE=default
LLM_PRIMARY_MODEL=gemma2:2b
ASR_MODEL_PATH=/home/jnx/Nvidia-jetson-platform/models/whisper-tiny
```

Terminal 1 — microservices:

```bash
bash scripts/start-voice-edge.sh
```

Terminal 2 — kiosk (loads assistant after services are up):

```bash
systemctl --user restart nina-ui-kiosk.service
```

## Health checks

```bash
curl -s http://127.0.0.1:2000/health
curl -s http://127.0.0.1:4000/health
curl -s http://127.0.0.1:6000/health
curl -s http://127.0.0.1:8787/v1/voice/status
```

## RAM notes (Orin Nano 8 GB)

| Component | Typical |
|-----------|---------|
| Ollama gemma2:2b | ~2–3 GB |
| Whisper tiny int8 | ~0.5–1 GB |
| TTS espeak | negligible |
| TTS Piper + torch | +1–2 GB |

Use `FORCE_CPU=1` and espeak TTS (default) if GPU/RAM is tight. Pull a smaller Ollama model if needed.

## Optional Piper TTS

```bash
export PIPER_BIN=/path/to/piper
export PIPER_MODEL=/path/to/en_US-amy-low.onnx
export PIPER_CONFIG=/path/to/en_US-amy-low.onnx.json
```

Restart `start-voice-edge.sh`.

## Not included (by design)

- `just_stream.js` HTTP/2 device gateway
- Vision / face / multimodal (`video_server`, embeddings optional in LLM)
- Cloud info APIs (weather/news) — LLM works offline; info queries need `appInfo.py` separately
- Wake word — speak after assistant shows “listening”; say “goodbye” to end

## Stop services

```bash
bash scripts/stop-voice-edge.sh
```
