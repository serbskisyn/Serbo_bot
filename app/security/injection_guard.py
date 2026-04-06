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


async def _stage2_llm_guard(text: str) -> bool:
    """
    LLM-Guard via OpenRouter (claude-haiku).
    Returns True = SAFE, False = INJECTION.
    Async — blockiert den Event Loop nicht.
    """
    try:
        async with httpx.AsyncClient(timeout=8.0) as client:
            response = await client.post(
                "https://openrouter.ai/api/v1/chat/completions",
                headers={
                    "Authorization": f"Bearer {config.OPENROUTER_API_KEY}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": "anthropic/claude-haiku-4",
                    "max_tokens": 5,
                    "messages": [
                        {
                            "role": "system",
                            "content": "You are a security classifier. Reply only with SAFE or INJECTION."
                        },
                        {
                            "role": "user",
                            "content": f"Classify this user input:\n\n{text}"
                        }
                    ]
                }
            )
            result = response.json()["choices"][0]["message"]["content"].strip().upper()
            return result == "SAFE"
    except Exception:
        return False


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
        return not await _stage2_llm_guard(text)
    return False


def wrap_document(content: str) -> str:
    clean = re.sub(r'<!--.*?-->', '', content, flags=re.DOTALL)
    return f"<document>\n{clean.strip()}\n</document>"