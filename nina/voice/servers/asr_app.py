import os, io, wave, time, struct, asyncio, json, re
from typing import Dict, Optional, Tuple

from fastapi import FastAPI, WebSocket
from starlette.websockets import WebSocketDisconnect
from faster_whisper import WhisperModel

# --- Post-processing helpers (your autocorrect utilities) ---
from nina.voice.servers.asr_post import diff_corrections, fix_text, segment_text

# =========================
# FastAPI app
# =========================
app = FastAPI()

# =========================
# Config (env-tunable)
# =========================
GOODBYE_IDLE_SEC       = float(os.getenv("ASR_GOODBYE_IDLE_SEC", "10.0"))   # kept for compatibility (no longer used to send Goodbye)
CONNECT_GRACE_SEC      = float(os.getenv("ASR_CONNECT_GRACE_SEC", "2.0"))   # kept for compatibility
IDLE_TICK_SEC          = float(os.getenv("ASR_IDLE_TICK_SEC", "0.5"))       # polling for TTS signal watcher
MIN_NO_AUDIO_SEC       = float(os.getenv("ASR_MIN_NO_AUDIO_SEC", "2.0"))    # kept for compatibility

# TTS duration signaling (filesystem) — ASR watches this dir for <mac>.json
TTS_SIG_DIR            = os.path.abspath(os.getenv("ASR_TTS_SIG_DIR", "./tmp/tts_signals"))
TTS_PAD_END_SEC        = float(os.getenv("ASR_TTS_PAD_END_SEC", "0.25"))    # cushion after declared duration
TTS_FILE_TIMEOUT_S     = float(os.getenv("ASR_TTS_FILE_TIMEOUT_S", "60.0")) # ignore stale files older than this
SUPPRESS_DURING_TTS    = os.getenv("ASR_SUPPRESS_DURING_TTS", "1").lower() not in ("0","false","no")
TTS_SUPPRESS_PAD_START = float(os.getenv("ASR_TTS_SUPPRESS_PAD_START", "0.10"))  # guard at the start of TTS
# Accounts for device fetch/buffer/startup lag so we don’t time out too soon
TTS_DEVICE_LATENCY_SEC = float(os.getenv("ASR_TTS_DEVICE_LATENCY_SEC", "1.0"))

ENFORCE_SINGLE_SESSION = os.getenv("ASR_SINGLE_SESSION", "0").lower() not in ("0","false","no")
MODEL_PATH             = os.getenv("ASR_MODEL_PATH", "models/whisper-tiny")

DEBUG_MODE = os.getenv("ASR_DEBUG", "0").lower() in ("1","true","yes")

# Ensure the TTS signal dir exists
try:
    os.makedirs(TTS_SIG_DIR, exist_ok=True)
except Exception:
    pass

# =========================
# Whisper setup
# =========================
import torch
device = "cuda" if torch.cuda.is_available() else "cpu"
compute_type = "float16" if device == "cuda" else "int8"
model = WhisperModel(MODEL_PATH, device=device, compute_type=compute_type)

# =========================
# Audio / VAD parameters
# =========================
SAMPLE_RATE   = 16000
CHANNELS      = 1
SAMPLE_WIDTH  = 2  # bytes per sample (s16le)

FRAME_MS      = 30
FRAME_SAMPLES = int(SAMPLE_RATE * FRAME_MS / 1000)        # 480
FRAME_BYTES   = FRAME_SAMPLES * CHANNELS * SAMPLE_WIDTH   # 960

TAIL_SIL_MS       = float(os.getenv("ASR_TAIL_SIL_MS", "600"))         # silence detection threshold
TAIL_SIL_FRAMES   = max(1, int(TAIL_SIL_MS // FRAME_MS))
PRE_ROLL_MS       = float(os.getenv("ASR_PRE_ROLL_MS", "300"))         # pre-roll buffer
PRE_ROLL_FRAMES   = max(1, int(PRE_ROLL_MS // FRAME_MS))
MAX_UTTERANCE_SEC = 15.0
MAX_UTTERANCE_BYTES = int(SAMPLE_RATE * CHANNELS * SAMPLE_WIDTH * MAX_UTTERANCE_SEC)

# Energy-based VAD thresholds (with hysteresis) - Tighter settings for >45dB threshold
EMA_ALPHA     = float(os.getenv("ASR_EMA_ALPHA", "0.96"))          # noise floor adaptation rate
INIT_NOISE_RMS= float(os.getenv("ASR_INIT_NOISE_RMS", "200.0"))    # initial noise floor estimate
UP_RATIO      = float(os.getenv("ASR_UP_RATIO", "2.5"))            # threshold to start speech
DOWN_RATIO    = float(os.getenv("ASR_DOWN_RATIO", "1.5"))          # threshold to end speech
ABS_MIN_RMS   = float(os.getenv("ASR_ABS_MIN_RMS", "90.0"))       # minimum RMS to consider (>45dB threshold)

GOODBYE_RE = re.compile(r"\b(good\s*bye|goodbye|bye|see you|see ya|stop listening)\b", re.I)

# False positive filters
FALSE_POSITIVE_PHRASES = [
    r"\b(thank you|thanks)\b",
    r"\b(thanks for watching|thank you for watching)\b", 
    r"\b(subscribe|comment)\b",
    r"\b(click the bell|notification)\b",
    r"\b(see you next time|until next time)\b",
    r"\b(that's all|that's it)\b",
    r"^\s*(okay|ok|alright)\s*$",  # Only if it's the entire phrase
    r"^\s*(um|uh|ah|eh)\s*$",  # Filler words only
    r"^\s*(yes|no|yeah|nope)\s*$",  # Single word responses
    r"^\s*(hello|hi|hey)\s*$",  # Greetings only (not in context)
    r"^\s*[.!?]+\s*$",  # Only punctuation
]

# Compile false positive patterns
FALSE_POSITIVE_PATTERNS = [re.compile(pattern, re.I) for pattern in FALSE_POSITIVE_PHRASES]

# Confidence and quality thresholds
MIN_CONFIDENCE = float(os.getenv("ASR_MIN_CONFIDENCE", "0.1"))   # Minimum confidence score (0.1 = very lenient)
ENABLE_CONFIDENCE_FILTER = os.getenv("ASR_ENABLE_CONFIDENCE_FILTER", "true").lower() == "true"
MIN_TEXT_LENGTH = int(os.getenv("ASR_MIN_TEXT_LENGTH", "3"))     # Minimum character length
MAX_TEXT_LENGTH = int(os.getenv("ASR_MAX_TEXT_LENGTH", "200"))   # Maximum character length
MIN_WORD_COUNT = int(os.getenv("ASR_MIN_WORD_COUNT", "1"))       # Minimum word count

# Auto-goodbye is handled by the gateway service

# =========================
# Utils
# =========================
def is_false_positive(text: str, segments: list) -> Tuple[bool, str]:
    """
    Check if transcription is likely a false positive.
    Returns (is_false_positive, reason)
    """
    if not text or not text.strip():
        return True, "empty_text"
    
    text = text.strip()
    
    # Check length filters
    if len(text) < MIN_TEXT_LENGTH:
        return True, f"too_short_{len(text)}_chars"
    
    if len(text) > MAX_TEXT_LENGTH:
        return True, f"too_long_{len(text)}_chars"
    
    # Check word count
    word_count = len(text.split())
    if word_count < MIN_WORD_COUNT:
        return True, f"too_few_words_{word_count}"
    
    # Check for false positive phrases
    for pattern in FALSE_POSITIVE_PATTERNS:
        if pattern.search(text):
            return True, f"false_positive_phrase_{pattern.pattern}"
    
    # Check confidence scores if available and enabled
    if ENABLE_CONFIDENCE_FILTER and segments:
        # Get average log probability from segments
        log_probs = [getattr(seg, 'avg_logprob', -1.0) for seg in segments if hasattr(seg, 'avg_logprob')]
        if log_probs:
            avg_logprob = sum(log_probs) / len(log_probs)
            # Convert log probability to confidence (more accurate conversion)
            # Log probs are typically between -1.0 (high confidence) and -2.0 (low confidence)
            # Convert to 0-1 scale where 0.5 = -1.0 logprob, 0.0 = -2.0 logprob
            confidence = max(0.0, min(1.0, (avg_logprob + 2.0) / 1.0))
            
            if confidence < MIN_CONFIDENCE:
                return True, f"low_confidence_{confidence:.2f}"
    
    # Check for repetitive patterns (like "thank you thank you")
    words = text.lower().split()
    if len(words) > 1:
        # Check if more than 50% of words are the same
        word_counts = {}
        for word in words:
            word_counts[word] = word_counts.get(word, 0) + 1
        max_count = max(word_counts.values())
        if max_count > len(words) * 0.5:
            return True, f"repetitive_pattern_{max_count}/{len(words)}"
    
    return False, "valid"

def bytes_to_wav_io(audio_bytes: bytes) -> io.BytesIO:
    """Wrap raw PCM s16le bytes into an in-memory WAV for Whisper."""
    buf = io.BytesIO()
    with wave.open(buf, "wb") as f:
        f.setnchannels(CHANNELS)
        f.setsampwidth(SAMPLE_WIDTH)
        f.setframerate(SAMPLE_RATE)
        f.writeframes(audio_bytes)
    buf.seek(0)
    return buf

def frame_rms_int16(frame_bytes: bytes) -> float:
    """RMS for a 16-bit PCM mono frame; no numpy dependency."""
    if not frame_bytes:
        return 0.0
    it = struct.iter_unpack("<h", frame_bytes)  # little-endian s16
    s = 0
    n = 0
    for (x,) in it:
        s += x * x
        n += 1
    return (s / n) ** 0.5 if n else 0.0

def sanitize_mac(mac: str) -> str:
    return "".join(ch for ch in (mac or "").lower() if ch.isalnum())

def tts_signal_path(mac: str) -> str:
    mac_s = sanitize_mac(mac)
    return os.path.join(TTS_SIG_DIR, f"{mac_s}.json") if mac_s else ""

def read_tts_signal(path: str) -> Optional[Tuple[float, float, int, str]]:
    """
    Returns (duration_seconds, mtime, created_at_ms, file_name) if a fresh/valid signal exists, else None.
    Signal JSON: {"duration_ms": <int>, "created_at": <ms>, "file": "speech_*.mp3"}
    """
    if not path:
        return None
    try:
        if not os.path.isdir(os.path.dirname(path)):
            if DEBUG_MODE:
                print(f"[TTS] Signal dir missing: {os.path.dirname(path)}")
            return None
        st = os.stat(path)
        if (time.time() - st.st_mtime) > TTS_FILE_TIMEOUT_S:
            return None
        with open(path, "r", encoding="utf-8") as f:
            obj = json.load(f)
        dur_ms = float(obj.get("duration_ms", 0.0))
        created_ms = int(obj.get("created_at", 0))
        file_name = str(obj.get("file", "") or "")
        if dur_ms <= 0 or created_ms <= 0:
            return None
        return dur_ms / 1000.0, st.st_mtime, created_ms, file_name
    except FileNotFoundError:
        return None
    except Exception as e:
        if DEBUG_MODE:
            print(f"[TTS] Read error for {path}: {e}")
        return None

# =========================
# Health
# =========================
@app.get("/health")
def health():
    return {
        "status": "ok",
        "model": MODEL_PATH,
        "device": device,
        "goodbye_idle_sec": GOODBYE_IDLE_SEC,
        "tts_sig_dir": TTS_SIG_DIR,
        "single_session": ENFORCE_SINGLE_SESSION,
        "debug": DEBUG_MODE,
        "suppress_during_tts": SUPPRESS_DURING_TTS,
        "tts_device_latency_sec": TTS_DEVICE_LATENCY_SEC,
        "vad_params": {
            "ema_alpha": EMA_ALPHA,
            "init_noise_rms": INIT_NOISE_RMS,
            "up_ratio": UP_RATIO,
            "down_ratio": DOWN_RATIO,
            "abs_min_rms": ABS_MIN_RMS,
            "tail_sil_ms": TAIL_SIL_MS,
            "pre_roll_ms": PRE_ROLL_MS
        }
    }

# =========================
# ASR Transcription Session Management
# Note: This is NOT the conversation session - that's managed by the Node.js gateway
# This only manages ASR WebSocket connections for individual transcriptions
# =========================
_sessions: Dict[str, WebSocket] = {}
_sessions_lock = asyncio.Lock()

async def register_session(mac: str, ws: WebSocket):
    if not ENFORCE_SINGLE_SESSION or not mac:
        return
    async with _sessions_lock:
        old = _sessions.get(mac)
        _sessions[mac] = ws
    if old and old is not ws:
        try:
            print(f"🔄 [ASR] Closing old WebSocket for MAC {mac} (new session starting)")
            await old.close()
        except Exception as e:
            print(f"⚠️ [ASR] Error closing old WebSocket: {e}")
            pass

async def unregister_session(mac: str, ws: WebSocket):
    if not ENFORCE_SINGLE_SESSION or not mac:
        return
    async with _sessions_lock:
        if _sessions.get(mac) is ws:
            _sessions.pop(mac, None)
            print(f"🎤 [ASR] Session unregistered for MAC {mac}")
        else:
            print(f"🎤 [ASR] Session not unregistered for MAC {mac} (different WebSocket)")

# =========================
# WebSocket endpoint
# =========================
@app.websocket("/ws/audio")
async def audio_ws(websocket: WebSocket):
    await websocket.accept()
    mac = websocket.query_params.get("mac", "")
    print(f"🎤 [ASR] WebSocket connection accepted mac={mac or '<unknown>'}  (watching {TTS_SIG_DIR})")
    await register_session(mac, websocket)
    print(f"🎤 [ASR] Session registered for MAC {mac}")

    # --- State machine
    STATE_IDLE, STATE_SPEAKING = 0, 1
    state = STATE_IDLE

    # --- Buffers
    frame_accum = bytearray()
    pre_roll = bytearray()
    utter_buf = bytearray()

    # --- VAD & timers
    noise_rms = INIT_NOISE_RMS
    consec_silence = 0

    now = time.monotonic()
    conn_start_ts = now
    last_non_silence_ts = now
    last_voice_ts = now
    last_frame_ts = now
    utterances_completed = 0

    # TTS coordination via filesystem (suppression only)
    tts_active_until = 0.0
    tts_sig_file = tts_signal_path(mac)
    last_tts_sig_mtime = 0.0
    last_tts_created_ms = 0
    last_tts_file_seen = ""

    # Timing for transcription
    utterance_start_time = None

    # --- Idle watcher (TTS watcher ONLY; no auto-Goodbye here)
    stop_idle_watcher = False

    async def idle_watcher():
        nonlocal tts_active_until, last_tts_sig_mtime, last_tts_created_ms, last_tts_file_seen
        while not stop_idle_watcher:
            await asyncio.sleep(IDLE_TICK_SEC)

            # Pull fresh TTS signal → extend suppression window; DO NOT send "Goodbye"
            if not tts_sig_file:
                continue

            sig = read_tts_signal(tts_sig_file)
            if sig is None:
                if DEBUG_MODE:
                    d_exists = os.path.isdir(os.path.dirname(tts_sig_file))
                    print(f"[TTS] No fresh signal at {tts_sig_file} (dir exists={d_exists})")
                continue

            duration_s, mtime, created_ms, fname = sig
            if (mtime > last_tts_sig_mtime) or (created_ms > last_tts_created_ms):
                nowm = time.monotonic()
                tts_active_until = max(
                    tts_active_until,
                    nowm + TTS_SUPPRESS_PAD_START + TTS_DEVICE_LATENCY_SEC + duration_s + TTS_PAD_END_SEC
                )
                last_tts_sig_mtime = mtime
                last_tts_created_ms = created_ms
                last_tts_file_seen = fname
                if DEBUG_MODE:
                    left = max(0.0, tts_active_until - nowm)
                    print(f"[TTS] Signal for {os.path.basename(tts_sig_file)}: dur={duration_s:.2f}s; active ~{left:.2f}s (created={created_ms})")

    idle_task = asyncio.create_task(idle_watcher())

    try:
        while True:
            try:
                msg = await websocket.receive()
            except Exception as e:
                if "disconnect" in str(e).lower():
                    print(f"🔌 [ASR] WebSocket disconnected gracefully: {e}")
                    break
                else:
                    print(f"❌ [ASR] WebSocket receive error: {e}")
                    break
            now = time.monotonic()

            if "bytes" in msg and msg["bytes"] is not None:
                chunk = msg["bytes"]
                last_frame_ts = now
                frame_accum.extend(chunk)

                # Process whole frames
                while len(frame_accum) >= FRAME_BYTES:
                    frame = bytes(frame_accum[:FRAME_BYTES])
                    del frame_accum[:FRAME_BYTES]

                    rms = frame_rms_int16(frame)
                    is_silence = rms <= max(noise_rms * DOWN_RATIO, ABS_MIN_RMS)
                    is_speech  = rms >= max(noise_rms * UP_RATIO, ABS_MIN_RMS * UP_RATIO / DOWN_RATIO)
                    now = time.monotonic()

                    if DEBUG_MODE:
                        remain = max(0.0, tts_active_until - now)
                        print(f"RMS={rms:.1f} noise={noise_rms:.1f} state={state} TTSremain={remain:.2f}s")

                    # Optional suppression: don't start new utterances while TTS is speaking
                    tts_blocked = SUPPRESS_DURING_TTS and (now < tts_active_until)

                    if state == STATE_IDLE:
                        noise_rms = EMA_ALPHA * noise_rms + (1.0 - EMA_ALPHA) * max(rms, ABS_MIN_RMS)

                        if not is_silence and not tts_blocked:
                            # Speech start
                            last_non_silence_ts = now
                            last_voice_ts = now
                            state = STATE_SPEAKING
                            consec_silence = 0
                            utterance_start_time = time.time()
                            print(f"🚀 [ASR] Starting new utterance at {time.strftime('%H:%M:%S')}")
                            utter_buf.extend(pre_roll)
                            pre_roll.clear()
                            utter_buf.extend(frame)
                        else:
                            pre_roll.extend(frame)
                            max_pre = PRE_ROLL_FRAMES * FRAME_BYTES
                            if len(pre_roll) > max_pre:
                                del pre_roll[:len(pre_roll) - max_pre]

                    else:
                        # SPEAKING
                        if is_speech:
                            last_non_silence_ts = now
                            last_voice_ts = now
                            consec_silence = 0
                            utter_buf.extend(frame)
                        else:
                            consec_silence += 1
                            noise_rms = EMA_ALPHA * noise_rms + (1.0 - EMA_ALPHA) * max(rms, ABS_MIN_RMS)
                            utter_buf.extend(frame)

                            # utterance end?
                            if consec_silence >= TAIL_SIL_FRAMES or len(utter_buf) >= MAX_UTTERANCE_BYTES:
                                # finalize
                                utterance_end_time = time.time()
                                if utterance_start_time:
                                    total_ms = (utterance_end_time - utterance_start_time) * 1000
                                    audio_ms = int(len(utter_buf) / SAMPLE_WIDTH / SAMPLE_RATE * 1000)
                                    rtf = total_ms / max(1, audio_ms)
                                    print(f"⏱️ [ASR] Processing complete: {total_ms:.2f}ms for {audio_ms}ms audio ({rtf:.2f}x)")

                                wav_io = bytes_to_wav_io(utter_buf)
                                whisper_start_time = time.time()

                                custom_prompt = (
                                    "This audio is in Indian English. Common terms include Nino, Robotics, "
                                    "Sirena, Sirena Technologies, CEO, Hari Bojan, education, K12, university, "
                                    "Hariharan Bojan, Hari."
                                )

                                segments, _ = model.transcribe(
                                    wav_io,
                                    language="en",
                                    beam_size=5,
                                    patience=1.1,
                                    vad_filter=True,
                                    temperature=0.0,
                                    condition_on_previous_text=False,
                                    initial_prompt=custom_prompt,
                                    word_timestamps=True
                                )

                                segments = list(segments)

                                if DEBUG_MODE:
                                    print(f"⏱️ [ASR] Whisper transcription: {(time.time() - whisper_start_time)*1000:.2f}ms")

                                raw_text = " ".join(segment_text(s) for s in segments)
                                corrections = diff_corrections(raw_text)
                                if corrections:
                                    print("🧭 [ASR] Corrections:", "; ".join(f"{a} → {b}" for a, b in corrections))

                                fixed_texts = [fix_text(segment_text(s)) for s in segments]
                                final_text = " ".join(fixed_texts).strip()

                                if final_text:
                                    # Check for false positives
                                    is_false, reason = is_false_positive(final_text, segments)
                                    
                                    if is_false:
                                        print(f"🚫 [ASR] FILTERED: '{final_text}' (reason: {reason})")
                                        # Debug: Show confidence details if it's a confidence issue
                                        if "low_confidence" in reason and segments:
                                            log_probs = [getattr(seg, 'avg_logprob', -1.0) for seg in segments if hasattr(seg, 'avg_logprob')]
                                            if log_probs:
                                                avg_logprob = sum(log_probs) / len(log_probs)
                                                print(f"🔍 [ASR] Debug - Avg logprob: {avg_logprob:.3f}, Segments: {len(segments)}")
                                    else:
                                        print(f"\033[91m📝 [ASR] TRANSCRIPTION: {final_text}\033[0m")
                                        # Only ever send transcript text — NEVER auto-send "Goodbye"
                                        await websocket.send_text(final_text)
                                else:
                                    print("ℹ️ Empty transcription")

                                # reset for next turn
                                utter_buf.clear()
                                pre_roll.clear()
                                state = STATE_IDLE
                                consec_silence = 0
                                last_non_silence_ts = now
                                last_voice_ts = now
                                utterance_start_time = None

            else:
                await asyncio.sleep(0)

    except WebSocketDisconnect:
        print(f"🔌 [ASR] WebSocket disconnected mac={mac or '<unknown>'}")
    except Exception as e:
        print(f"❌ [ASR] WebSocket error: {e}")
    finally:
        try:
            stop_idle_watcher = True
            if 'idle_task' in locals():
                idle_task.cancel()
        except Exception:
            pass
        try:
            await unregister_session(mac, websocket)
        except Exception:
            pass
        try:
            await websocket.close()
        except Exception:
            pass
