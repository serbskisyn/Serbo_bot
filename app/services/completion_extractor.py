"""
completion_extractor.py — Detect "I did X" in chat and auto-mark todos done.

Mirrors the todo_extractor pattern: LLM filters the conversation for
user-completed actions, then semantic-matches each against open todos.
A match above DIST_FUZZY_MATCH triggers `todos.mark_done`.

Fire-and-forget from handlers.py — the user will see the effect in
the next /todo list or the next morning briefing.
"""
from __future__ import annotations

import json
import logging
import re

import httpx

from app.config import OPENROUTER_API_KEY
from app.services import semantic, todos as todos_svc

logger = logging.getLogger(__name__)

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
EXTRACTOR_MODEL = "openai/gpt-4o-mini"


_PROMPT = """Du analysierst eine Konversation und identifizierst Aktionen, die der USER bereits ABGESCHLOSSEN hat.

Was ZÄHLT als Abschluss:
- "Habe X erledigt / gemacht / fertig"
- "X ist durch / abgeschickt / done"
- "X habe ich gerade geschafft"
- "Just sent X" / "Just finished Y"

Was NICHT zählt:
- Pläne ("Ich werde X machen", "muss noch X")
- Aussagen über andere Personen ("Andi hat X erledigt")
- Fragen ("Hast du X erledigt?")
- Allgemeine Diskussion ohne klares Abschluss-Signal
- Assistant-Aussagen

Antworte NUR mit validem JSON:
{
  "completions": [
    "<kurze imperative Phrase der erledigten Aktion>"
  ]
}

Bei nichts Erkennbarem: {"completions": []}
Maximal 3 pro Konversation."""


async def _call_llm(user_text: str, assistant_response: str) -> str:
    from app.services.llm_client import chat
    from app.config import LLM_CHEAP_MODEL
    return await chat(
        [{"role": "system", "content": _PROMPT},
         {"role": "user", "content": f"USER: {user_text}\nASSISTANT: {assistant_response}"}],
        model=LLM_CHEAP_MODEL, temperature=0.0, max_tokens=200, timeout=10.0,
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
    """End-to-end: detect completions → semantic-match → mark_done.

    Returns {detected, marked_done, ids: [(id, original_text, matched_phrase)]}.
    """
    try:
        raw = await _call_llm(user_text, assistant_response)
    except Exception as exc:
        logger.warning(f"completion_extractor: LLM call failed: {exc}")
        return {"detected": 0, "marked_done": 0, "ids": []}

    data = _extract_json(raw) or {}
    completions = data.get("completions") or []
    if not completions:
        return {"detected": 0, "marked_done": 0, "ids": []}

    marked: list[tuple[int, str, str]] = []
    for comp in completions[:3]:
        comp = str(comp).strip()
        if not comp or len(comp) < 4:
            continue

        # Semantic match against open todos. FUZZY threshold because the
        # user's completion phrasing ("habe X fertig") differs from the
        # todo phrasing ("X vorbereiten") more than a paraphrase would.
        try:
            hits = await semantic.find_similar(
                "todos", user_id, comp,
                threshold=semantic.DIST_FUZZY_MATCH, limit=3,
            )
        except Exception as exc:
            logger.debug("completion_extractor: semantic search skipped: %s", exc)
            continue

        for ref_id, hit_text, _dist in hits:
            todo = await todos_svc.get_todo(user_id, ref_id)
            if not todo or todo.get("status") != "open":
                continue
            if await todos_svc.mark_done(user_id, ref_id):
                marked.append((ref_id, hit_text, comp))
                break  # one completion → one todo at most

    summary = {
        "detected": len(completions),
        "marked_done": len(marked),
        "ids": marked,
    }
    if marked:
        logger.info(
            "completion_extractor: user=%s detected=%d marked_done=%d",
            user_id, summary["detected"], summary["marked_done"],
        )
    return summary
