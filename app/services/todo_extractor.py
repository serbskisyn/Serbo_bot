"""
todo_extractor.py — Detect actionable commitments in chat & queue them as todos.

Mirrors the fire-and-forget profile-learner pattern but with a much
tighter prompt: only extract things the USER has committed to or
asked to be reminded of. Filters out:
  - assistant-suggested actions ("du könntest X tun")
  - past completions ("habe ich gemacht")
  - hypothetical scenarios
  - generic discussion

The extractor calls profile-aware `mention_existing()` first — if the
user has repeated a known commitment, mention_count goes up, not a
new row.
"""
from __future__ import annotations

import json
import logging
import re

import httpx

from app.config import OPENROUTER_API_KEY
from app.services import todos as todos_svc

logger = logging.getLogger(__name__)

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
EXTRACTOR_MODEL = "openai/gpt-4o-mini"


_PROMPT = """Du analysierst eine Konversation und identifizierst konkrete TODOs, die der USER zu erledigen hat.

Was IST ein Todo:
- "Ich muss noch X machen / vorbereiten / einreichen"
- "Erinnere mich an X"
- "Bis Freitag muss ich X"
- "Vergiss nicht, X zu tun"
- "Ich sollte X anrufen / mailen / fragen"

Was IST KEIN Todo:
- Aussagen, die der Assistent macht ("du könntest X")
- Vergangenheits-Erledigungen ("habe X erledigt")
- Hypothetische / spekulative Aussagen ("vielleicht würde ich X")
- Generelle Diskussion / Fragen ohne Handlung
- Wünsche ohne Verantwortung des Users ("wäre cool wenn X")

Antworte NUR mit validem JSON:
{
  "todos": [
    {"text": "<kompakte Beschreibung in Imperativform>", "due_hint": "<heute|morgen|freitag|2026-05-30|null>"}
  ]
}

Bei nichts Erkennbarem: {"todos": []}
Maximal 3 Todos pro Konversation."""


async def _call_llm(user_text: str, assistant_response: str) -> str:
    from app.services.llm_client import chat
    from app.config import LLM_CHEAP_MODEL
    return await chat(
        [{"role": "system", "content": _PROMPT},
         {"role": "user", "content": f"USER: {user_text}\nASSISTANT: {assistant_response}"}],
        model=LLM_CHEAP_MODEL, temperature=0.0, max_tokens=250, timeout=10.0,
    )


def _extract_json(text: str) -> dict | None:
    if not text:
        return None
    m = re.search(r"\{.*\}", text, re.DOTALL)
    if not m:
        return None
    try:
        return json.loads(m.group())
    except json.JSONDecodeError:
        return None


async def extract_from_chat(user_id: int, user_text: str, assistant_response: str) -> dict:
    """End-to-end: LLM-detect commitments → mention_existing or add_todo.

    Returns: {detected: N, added: N, mentioned: N}
    """
    try:
        raw = await _call_llm(user_text, assistant_response)
    except Exception as exc:
        logger.warning(f"todo_extractor: LLM-call failed: {exc}")
        return {"detected": 0, "added": 0, "mentioned": 0}

    data = _extract_json(raw) or {}
    candidates = data.get("todos") or []
    if not candidates:
        return {"detected": 0, "added": 0, "mentioned": 0}

    added = 0
    mentioned = 0
    for cand in candidates[:3]:
        text = str(cand.get("text", "")).strip()
        if not text or len(text) < 4:
            continue

        # Already tracked → bump mention_count
        existing = await todos_svc.mention_existing(user_id, text)
        if existing is not None:
            mentioned += 1
            continue

        due_hint = cand.get("due_hint")
        due_iso = todos_svc.parse_date(due_hint) if due_hint and due_hint != "null" else None

        await todos_svc.add_todo(user_id, text, source="chat", due_date=due_iso)
        added += 1

    summary = {"detected": len(candidates), "added": added, "mentioned": mentioned}
    if added or mentioned:
        logger.info(
            "todo_extractor: user=%s detected=%d added=%d mentioned=%d",
            user_id, summary["detected"], summary["added"], summary["mentioned"],
        )
    return summary
