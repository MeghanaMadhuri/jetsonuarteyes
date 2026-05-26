import re
import unicodedata
from typing import Dict, List, Tuple

# ---------- Helpers ----------

def _matchcase(dst: str, src: str) -> str:
    if src.isupper():
        return dst.upper()
    if src.islower():
        return dst.lower()
    if src[:1].isupper() and src[1:].islower():
        return dst[:1].upper() + dst[1:].lower()
    return dst

def _normalize(s: str) -> str:
    return unicodedata.normalize("NFKC", s)

def build_replacer(variants_to_canonical: Dict[str, str]) -> List[Tuple[re.Pattern, str]]:
    compiled = []
    for variant, canonical in sorted(variants_to_canonical.items(), key=lambda kv: -len(kv[0])):
        v = re.escape(_normalize(variant))
        pat = re.compile(rf"(?<!\w){v}(?!\w)", re.IGNORECASE)
        compiled.append((pat, canonical))
    return compiled

def replace_terms_with_pairs(text: str, compiled_rules: List[Tuple[re.Pattern, str]]) -> Tuple[str, List[Tuple[str, str]]]:
    """
    Returns (new_text, pairs) where pairs lists (matched_src, replaced_dst) BEFORE case matching.
    We’ll report corrections using the actually seen source form and the final replaced form (case-matched).
    """
    pairs: List[Tuple[str, str]] = []
    out = text
    for pat, canon in compiled_rules:
        def _repl(m):
            src = m.group(0)
            dst = _matchcase(canon, src)
            if src != dst:
                pairs.append((src, dst))
            return dst
        out = pat.sub(_repl, out)
    return out, pairs

# ---------- Domain dictionary ----------

VARIANTS = {
    # Company / product
    "sirena technologies": "Sirena Technologies",
    "serena technologies": "Sirena Technologies",
    "sirena": "Sirena",
    "serena": "Sirena",
    "serina": "Sirena",
    "sarena": "Sirena",

    "nino": "Nino",

    # People
    "hari bojan": "Hari Bojan",
    "harry bojan": "Hari Bojan",
    "hariharan bojan": "Hariharan Bojan",
    "hari haran bojan": "Hariharan Bojan",
    "harry haran bojan": "Hariharan Bojan",
    "hari": "Hari",

    # Roles / domains
    "ceo": "CEO",
    "k-12": "K12",
    "k 12": "K12",
    "k12": "K12",
    "robotics": "Robotics",
    "education": "Education",
    "university": "University",
}

COMPILED_RULES = build_replacer(VARIANTS)

# For concise logs within one process lifetime
_last_seen_pairs: set[Tuple[str, str]] = set()

# ---------- Public API ----------

def fix_text(text: str) -> str:
    new_text, _ = replace_terms_with_pairs(text, COMPILED_RULES)
    return new_text

def fix_text_with_log(text: str) -> Tuple[str, List[Tuple[str, str]]]:
    new_text, pairs = replace_terms_with_pairs(text, COMPILED_RULES)
    # Deduplicate within this process to avoid spam
    global _last_seen_pairs
    fresh = []
    for p in pairs:
        if p not in _last_seen_pairs:
            _last_seen_pairs.add(p)
            fresh.append(p)
    return new_text, fresh

# add this utility at top-level
def segment_text(s) -> str:
    """Safely extract text from faster-whisper Segment, dict, or str."""
    if isinstance(s, dict):
        return s.get("text", "")
    if hasattr(s, "text"):
        return s.text
    if isinstance(s, str):
        return s
    return ""

def fix_segments(segments) -> list:
    fixed = []
    for s in segments:
        t = segment_text(s)
        t_fixed, _ = replace_terms_with_pairs(t, COMPILED_RULES)
        if isinstance(s, dict):
            s["text"] = t_fixed
            fixed.append(s)
        elif hasattr(s, "text"):
            s.text = t_fixed
            fixed.append(s)
        else:
            fixed.append(t_fixed)  # e.g., raw str
    return fixed


def diff_corrections(text: str) -> List[Tuple[str, str]]:
    """
    Convenience: compute (src -> dst) corrections without changing global dedupe state.
    Use this right after building your final text to log what changed.
    """
    _, pairs = replace_terms_with_pairs(text, COMPILED_RULES)
    # collapse duplicates within this call
    seen = set()
    uniq = []
    for a, b in pairs:
        key = (a.lower(), b.lower())
        if key not in seen:
            seen.add(key)
            uniq.append((a, b))
    return uniq
