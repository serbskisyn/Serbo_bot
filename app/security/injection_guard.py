import re
import httpx
from app import config

# ── Stage 1: Hard-block patterns ──────────────────────────────────────────────
INJECTION_PATTERNS = [
    r"ignore\b.{0,30}instructions",
    r"you are now",
    r"forget (your |all )?(previous |prior )?instructions",
    r"(reveal|show|print|repeat).{0,20}(prompt|instructions|system)",
    r"jailbreak",
    r"dan mode",
    r"pretend (you are|to be)",
    r"act as (?!a football|a soccer|an? sport)",
]

# ── Stage 1: Soft-score patterns ──────────────────────────────────────────────
SOFT_PATTERNS = [
    r"\bignore\b",
    r"\boverride\b",
    r"\bforget\b",
    r"\bsystem\b",
    r"\binstructions?\b",
]

# ── Homoglyph normalization ───────────────────────────────────────────────────
_HOMOGLYPH_MAP = str.maketrans(
    "аеорсухАЕОРСУХ",
    "aeorcyxAEORCYX"
)


def _normalize(text: str) -> str:
    return text.translate(_HOMOGLYPH_MAP).lower()


def _stage1(text: str) -> tuple[bool, int]:
    norm = _normalize(text)
    for pattern in INJECTION_PATTERNS:
        if re.search(pattern, norm):
            return True, 10
    score = sum(1 for p in SOFT_PATTERNS if re.search(p, norm))
    return False, score


async def _stage2_llm_guard(text: str, soft_score: int = 0) -> bool:
    """
    LLM-Guard via OpenRouter (claude-haiku-4.5).
    Returns True = SAFE, False = INJECTION.
    Bei API-Fehler: wenn soft_score < 3 → SAFE (Bot soll nicht durch API-Hänger sperren),
    sonst INJECTION (Vorsicht bei starkem Pattern-Verdacht).
    """
    try:
        from app.services.llm_client import chat
        content = await chat(
            [
                {"role": "system",
                 "content": "You are a security classifier. Reply only with SAFE or INJECTION."},
                {"role": "user", "content": f"Classify this user input:\n\n{text}"},
            ],
            model=config.LLM_CHEAP_MODEL, temperature=0.0, max_tokens=5, timeout=8.0,
        )
        return content.strip().upper().startswith("SAFE")
    except Exception:
        return soft_score < 3


async def is_injection_async(text: str) -> bool:
    """
    Two-stage prompt injection guard.
    Stage 1 (free, instant): pattern + homoglyph check
    Stage 2 (LLM, nur wenn score > 0): semantic check — vollständig async
    Returns True if injection detected.
    """
    hard_blocked, score = _stage1(text)
    if hard_blocked:
        return True
    if score > 0:
        return not await _stage2_llm_guard(text, score)
    return False


def wrap_document(content: str) -> str:
    clean = re.sub(r'<!--.*?-->', '', content, flags=re.DOTALL)
    return f"<document>\n{clean.strip()}\n</document>"