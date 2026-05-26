# app.py
# Llama-only LLM router for your education agent.
# - Primary only (no fallback) to save RAM.
# - Works with Ollama (default) or any OpenAI-compatible /v1 server.
#
# Endpoints:
#   GET  /health  -> {"status":"ok", ...}
#   POST /ask     -> {"response":"...", "session_uuid":"..."}
#
# Request example:
#   {"mac_id":"AA:BB:CC","question":"Explain photosynthesis in one line."}
#
# Env vars:
#   LLM_PRIMARY_BASE_URL     (default: http://localhost:11434)   # Ollama host, no /v1
#   LLM_PRIMARY_MODEL        (default: llama3.2:3b-instruct-q4_K_M)
#   LLM_PRIMARY_TIMEOUT_MS   (default: 60000)  # generous for cold start on CPU
#   LLM_KEEP_ALIVE_SECONDS   (default: 600)    # keep model in RAM between turns
#   CONVERSATION_FOLDER      (default: conversations)
#   MAX_HISTORY              (default: 8)
#
# Run:
#   pip install fastapi "uvicorn[standard]" requests
#   uvicorn app:app --host 0.0.0.0 --port 4000

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field
from typing import List, Optional, Dict, Any
import os, time, uuid, json, traceback
import requests
from datetime import datetime
from pathlib import Path
import re

# ---------- Config (env) ----------
PRIMARY_BASE = os.getenv("LLM_PRIMARY_BASE_URL", "http://localhost:11434").rstrip("/")
PRIMARY_MODEL = os.getenv("LLM_PRIMARY_MODEL", "gemma2:2b")  # Default to Gemma2
FALLBACK_MODEL = os.getenv("LLM_FALLBACK_MODEL", "llama3.2:3b-instruct-q4_K_M")  # Llama fallback
PRIMARY_TIMEOUT = int(os.getenv("LLM_PRIMARY_TIMEOUT_MS", "60000")) / 1000.0
KEEP_ALIVE_S = int(os.getenv("LLM_KEEP_ALIVE_SECONDS", "600"))
CONV_DIR = Path(os.getenv("CONVERSATION_FOLDER", "conversations"))
MAX_HISTORY = int(os.getenv("MAX_HISTORY", "8"))
INFO_SERVICE_URL = os.getenv("INFO_SERVICE_URL", "http://localhost:5002")
EMBEDDING_SERVICE_URL = os.getenv("EMBEDDING_SERVICE_URL", "http://localhost:5003")

# ---------- Tutor system style ----------

AGENT_SYSTEM = (
    "You are Nino, a patient, age-aware humanoid robot for schools & universities "
    "(K–12 learners, university students & staff, and general public). "
    "Speak in short, natural sentences for TTS. "
    "Adapt tone by audience (simple for K–12; concise & expert for staff/uni). " 
    "Teaching: prefer step-by-step reasoning and quick checks. "
    "General help: be clear, safe, and practical. "
    "Entertainment: you can ask the robot to play a song and dance on request. "
    "Safety: avoid adult/unsafe topics; for medical/legal/financial matters, be careful and suggest verified sources."
)   

# ---------- Schemas ----------
class Turn(BaseModel):
    role: str
    content: str

class AskRequest(BaseModel):
    question: str = Field(..., min_length=1)
    mac_id: Optional[str] = None
    level: Optional[str] = None
    session_uuid: Optional[str] = None
    history: Optional[List[Turn]] = None  # prior messages [{role, content}]

class AskResponse(BaseModel):
    response: str
    session_uuid: str

# ---------- FastAPI app ----------
app = FastAPI()

# ---------- Helpers ----------
def _ensure_session(req: AskRequest) -> str:
    return req.session_uuid or str(uuid.uuid4())

def _load_session_context(mac_id: str, session_uuid: str) -> List[Dict[str, str]]:
    """Load conversation history for a specific session"""
    if not mac_id or not session_uuid:
        return []
    
    mac = mac_id.replace("/", "_")
    date_folder = datetime.utcnow().strftime("%Y%m%d")
    session_file = CONV_DIR / mac / date_folder / f"{session_uuid}.jsonl"
    
    if not session_file.exists():
        print(f"[CONTEXT] No session file found for {mac_id}/{session_uuid} - starting fresh session")
        return []
    
    context = []
    try:
        with session_file.open("r", encoding="utf-8") as f:
            for line in f:
                if line.strip():
                    data = json.loads(line.strip())
                    if "question" in data and "answer" in data:
                        context.append({"role": "user", "content": data["question"]})
                        context.append({"role": "assistant", "content": data["answer"]})
        print(f"[CONTEXT] Loaded {len(context)} conversation turns for session {session_uuid}")
    except Exception as e:
        print(f"Error loading session context: {e}")
    
    return context[-MAX_HISTORY:] if context else []

def _save_session_context(mac_id: str, session_uuid: str, question: str, answer: str) -> None:
    """Save conversation turn to session context"""
    if not mac_id or not session_uuid:
        return
    
    mac = mac_id.replace("/", "_")
    date_folder = datetime.utcnow().strftime("%Y%m%d")
    out_dir = CONV_DIR / mac / date_folder
    out_dir.mkdir(parents=True, exist_ok=True)
    
    payload = {
        "ts": time.time(),
        "question": question,
        "answer": answer,
        "session_uuid": session_uuid,
        "mac_id": mac_id
    }
    
    try:
        with (out_dir / f"{session_uuid}.jsonl").open("a", encoding="utf-8") as f:
            f.write(json.dumps(payload, ensure_ascii=False) + "\n")
        print(f"[CONTEXT] Saved conversation turn for session {session_uuid}")
    except Exception as e:
        print(f"Error saving session context: {e}")

def _detect_info_query(question: str) -> bool:
    """Detect if question should be routed to info service"""
    q_lower = question.lower()
    
    # Weather patterns
    weather_patterns = [
        r"weather", r"temperature", r"forecast", r"rain", r"snow", r"sunny", r"cloudy",
        r"humidity", r"wind", r"storm", r"climate"
    ]
    
    # News patterns
    news_patterns = [
        r"news", r"headlines", r"current affairs", r"latest", r"recent", r"today",
        r"breaking", r"update", r"happening"
    ]
    
    # Search patterns
    search_patterns = [
        r"search", r"look up", r"lookup", r"find", r"google", r"internet",
        r"web", r"online", r"information about"
    ]
    
    all_patterns = weather_patterns + news_patterns + search_patterns
    return any(re.search(pattern, q_lower) for pattern in all_patterns)

def _call_info_service(question: str) -> Optional[str]:
    """Call the info service for weather/news/search queries"""
    try:
        response = requests.post(
            f"{INFO_SERVICE_URL}/answer",
            json={"question": question},
            timeout=10
        )
        if response.status_code == 200:
            data = response.json()
            return data.get("answer")
        elif response.status_code == 204:
            # No external answer available
            return None
        else:
            print(f"Info service error: {response.status_code}")
            return None
    except Exception as e:
        print(f"Info service call failed: {e}")
        return None

def _build_messages(req: AskRequest) -> List[Dict[str, str]]:
    msgs: List[Dict[str, str]] = [{"role": "system", "content": AGENT_SYSTEM}]
    
    # Load session context if available
    if req.mac_id and req.session_uuid:
        session_context = _load_session_context(req.mac_id, req.session_uuid)
        msgs.extend(session_context)
    elif req.history:
        # Fallback to provided history
        trimmed = req.history[-MAX_HISTORY:]
        for t in trimmed:
            role = t.role if t.role in ("user", "assistant", "system") else "user"
            msgs.append({"role": role, "content": t.content})
    
    msgs.append({"role": "user", "content": req.question})
    return msgs

def _log_turn(mac: str, session_uuid: str, payload: Dict[str, Any]) -> None:
    date_folder = datetime.utcnow().strftime("%Y%m%d")
    out_dir = CONV_DIR / (mac or "unknown") / date_folder
    out_dir.mkdir(parents=True, exist_ok=True)
    with (out_dir / f"{session_uuid}.jsonl").open("a", encoding="utf-8") as f:
        f.write(json.dumps(payload, ensure_ascii=False) + "\n")

# ---------- Backends ----------
def _call_openai_compatible(base: str, model: str, messages: List[Dict[str,str]], timeout_s: float) -> str:
    """
    For llama.cpp server, vLLM, etc. at .../v1
    """
    url = base.rstrip("/") + "/chat/completions"
    headers = {"Content-Type": "application/json", "Authorization": "Bearer sk-local"}
    body = {"model": model, "stream": False, "temperature": 0.3, "messages": messages}
    r = requests.post(url, headers=headers, json=body, timeout=timeout_s)
    r.raise_for_status()
    data = r.json()
    text = data.get("choices", [{}])[0].get("message", {}).get("content", "")
    return (text or "").strip()

def _call_ollama_native(base: str, model: str, messages: List[Dict[str,str]], timeout_s: float) -> str:
    """
    For Ollama native API (default): POST /api/chat
    """
    url = base.rstrip("/") + "/api/chat"
    body = {
       "model": model,
       "messages": messages,
       "stream": False,
       "options": {"temperature": 0.3},
       "keep_alive": KEEP_ALIVE_S
    }
    r = requests.post(url, json=body, timeout=timeout_s)
    r.raise_for_status()
    data = r.json()
    # Standard Ollama chat response:
    text = data.get("message", {}).get("content", "")
    # Some builds can return OpenAI-like 'choices'
    if not text and isinstance(data.get("choices"), list) and data["choices"]:
        text = data["choices"][0].get("message", {}).get("content", "")
    return (text or "").strip()

def _ask_primary_only(messages: List[Dict[str, str]]) -> str:
    # Decide by URL shape: /v1 => OpenAI-compatible server; otherwise Ollama native
    if PRIMARY_BASE.endswith("/v1"):
        return _call_openai_compatible(PRIMARY_BASE, PRIMARY_MODEL, messages, timeout_s=PRIMARY_TIMEOUT)
    return _call_ollama_native(PRIMARY_BASE, PRIMARY_MODEL, messages, timeout_s=PRIMARY_TIMEOUT)

def _call_llm_with_fallback(messages: List[Dict[str, str]]) -> str:
    """Call LLM with fallback from Gemma2 to Llama."""
    try:
        # Try primary model (Gemma2) first
        print(f"🧠 Trying primary model: {PRIMARY_MODEL}")
        if PRIMARY_BASE.endswith("/v1"):
            return _call_openai_compatible(PRIMARY_BASE, PRIMARY_MODEL, messages, timeout_s=PRIMARY_TIMEOUT)
        return _call_ollama_native(PRIMARY_BASE, PRIMARY_MODEL, messages, timeout_s=PRIMARY_TIMEOUT)
    except Exception as e:
        print(f"⚠️ Primary model failed ({PRIMARY_MODEL}): {e}")
        try:
            # Fallback to Llama
            print(f"🔄 Falling back to: {FALLBACK_MODEL}")
            if PRIMARY_BASE.endswith("/v1"):
                return _call_openai_compatible(PRIMARY_BASE, FALLBACK_MODEL, messages, timeout_s=PRIMARY_TIMEOUT)
            return _call_ollama_native(PRIMARY_BASE, FALLBACK_MODEL, messages, timeout_s=PRIMARY_TIMEOUT)
        except Exception as e2:
            print(f"❌ Both models failed. Primary: {e}, Fallback: {e2}")
            raise e2

def _call_embedding_service(query: str, documents: List[str], top_k: int = 3) -> List[Dict[str, Any]]:
    """Call the embedding service for semantic search."""
    try:
        url = f"{EMBEDDING_SERVICE_URL}/search"
        payload = {
            "query": query,
            "documents": documents,
            "top_k": top_k,
            "threshold": 0.3
        }
        resp = requests.post(url, json=payload, timeout=10)
        resp.raise_for_status()
        return resp.json()["results"]
    except Exception as e:
        print(f"⚠️ Embedding service unavailable: {e}")
        return []

def _enhance_with_embeddings(question: str, context: List[Dict[str, str]]) -> str:
    """Enhance the question with relevant context from embeddings."""
    try:
        # Extract text from context for semantic search
        context_texts = [turn["content"] for turn in context if turn["role"] == "user"]
        
        if not context_texts:
            return question
        
        # Get relevant context using embeddings
        relevant_results = _call_embedding_service(question, context_texts, top_k=2)
        
        if relevant_results:
            relevant_context = "\n".join([f"Previous: {result['text']}" for result in relevant_results])
            enhanced_question = f"Context: {relevant_context}\n\nCurrent question: {question}"
            print(f"🔍 Enhanced question with {len(relevant_results)} relevant context items")
            return enhanced_question
        
    except Exception as e:
        print(f"⚠️ Context enhancement failed: {e}")
    
    return question

def _clean_for_tts(text: str) -> str:
    """Clean LLM response for TTS compatibility by removing special characters."""
    if not text:
        return text
    
    # Convert common emoticons to TTS-friendly alternatives
    emoticon_replacements = {
        '😊': ' (smiling) ',
        '😄': ' (laughing) ',
        '😃': ' (happy) ',
        '😁': ' (grinning) ',
        '😉': ' (winking) ',
        '😍': ' (loving) ',
        '🥰': ' (in love) ',
        '😘': ' (kissing) ',
        '😎': ' (cool) ',
        '🤔': ' (thinking) ',
        '😮': ' (surprised) ',
        '😯': ' (amazed) ',
        '😲': ' (astonished) ',
        '😢': ' (sad) ',
        '😭': ' (crying) ',
        '😤': ' (frustrated) ',
        '😡': ' (angry) ',
        '🤗': ' (hugging) ',
        '👍': ' (thumbs up) ',
        '👎': ' (thumbs down) ',
        '👏': ' (clapping) ',
        '🙌': ' (celebrating) ',
        '💪': ' (strong) ',
        '❤️': ' (heart) ',
        '💯': ' (perfect) ',
        '✨': ' (sparkling) ',
        '🎉': ' (party) ',
        '🔥': ' (fire) ',
        '⭐': ' (star) ',
        '🌟': ' (shining star) ',
    }
    
    # Apply emoticon replacements
    cleaned = text
    for emoticon, replacement in emoticon_replacements.items():
        cleaned = cleaned.replace(emoticon, replacement)
    
    # Convert & to "and" for better TTS
    cleaned = cleaned.replace('&', ' and ')
    
    # Remove other special characters that TTS can't handle well
    # Keep basic punctuation: . , ! ? : ; - ( ) [ ] " '
    # Remove: * _ ~ ` | \ / @ # $ % ^ + = < > { } 
    cleaned = re.sub(r'[*_~`|\\/@#$%^+=<>{}]', ' ', cleaned)
    
    # Remove multiple spaces and clean up
    cleaned = re.sub(r'\s+', ' ', cleaned).strip()
    
    # Remove any remaining non-printable characters except basic ones
    cleaned = re.sub(r'[^\x20-\x7E\u00A0-\uFFFF]', '', cleaned)
    
    return cleaned

# ---------- Routes ----------
@app.get("/health")
def health():
    # Check info service availability
    info_service_status = "unknown"
    try:
        response = requests.get(f"{INFO_SERVICE_URL}/weather?city=test", timeout=2)
        info_service_status = "available" if response.status_code in [200, 400] else "error"
    except:
        info_service_status = "unavailable"
    
    # Check embedding service availability
    embedding_service_status = "unknown"
    try:
        response = requests.get(f"{EMBEDDING_SERVICE_URL}/health", timeout=2)
        embedding_service_status = "available" if response.status_code == 200 else "error"
    except:
        embedding_service_status = "unavailable"
    
    return {
        "status": "ok",
        "primary_base": PRIMARY_BASE,
        "primary_model": PRIMARY_MODEL,
        "fallback_model": FALLBACK_MODEL,
        "timeout_ms": int(PRIMARY_TIMEOUT * 1000),
        "keep_alive_s": KEEP_ALIVE_S,
        "history_limit": MAX_HISTORY,
        "info_service_url": INFO_SERVICE_URL,
        "info_service_status": info_service_status,
        "embedding_service_url": EMBEDDING_SERVICE_URL,
        "embedding_service_status": embedding_service_status,
        "context_storage": "enabled",
        "model_fallback": "enabled"
    }

@app.post("/ask", response_model=AskResponse)
def ask(req: AskRequest):
    session_uuid = _ensure_session(req)
    mac = (req.mac_id or "unknown").replace("/", "_")
    
    print(f"[SESSION] Processing request for MAC: {mac}, Session: {session_uuid}")
    
    started = time.time()
    text = ""
    route = "primary"
    
    # Check if this is an info service query
    if _detect_info_query(req.question):
        print(f"[INFO] Detected info query: {req.question}")
        info_response = _call_info_service(req.question)
        if info_response:
            text = info_response
            route = "info-service"
            print(f"[INFO] Info service response: {text[:100]}...")
    
    # If no info service response, use LLM with fallback
    if not text:
        messages = _build_messages(req)
        
        # Enhance question with embeddings if context is available
        if req.history and len(req.history) > 1:
            enhanced_question = _enhance_with_embeddings(req.question, req.history)
            if enhanced_question != req.question:
                # Update the last user message with enhanced version
                messages[-1]["content"] = enhanced_question
        
        try:
            text = _call_llm_with_fallback(messages)
            route = "llm-with-fallback"
        except Exception as e:
            print("[LLM error]", e)
            traceback.print_exc()
            text = ""
    
    elapsed = round((time.time() - started) * 1000)

    if not text:
        _log_turn(mac, session_uuid, {
            "ts": time.time(), "route": "primary-failed",
            "question": req.question, "elapsed_ms": elapsed
        })
        raise HTTPException(status_code=502, detail="Primary LLM call failed or returned empty.")

    # Clean response for TTS compatibility
    cleaned_text = _clean_for_tts(text)
    
    # Save session context for future conversations
    if req.mac_id and session_uuid:
        _save_session_context(req.mac_id, session_uuid, req.question, cleaned_text)

    _log_turn(mac, session_uuid, {
        "ts": time.time(), "route": route,
        "question": req.question, "answer": cleaned_text, "elapsed_ms": elapsed
    })

    print(f"[SESSION] Response generated via {route} for session {session_uuid}")
    return AskResponse(response=cleaned_text, session_uuid=session_uuid)
