"""
todo_drop_extractor.py — Detect "remove from list" intents in chat → drop_todo.

Sibling to completion_extractor.py. The completion extractor handles "I did X"
phrases and marks the matching todo done. This one handles the OTHER common
case: "Take X off the list" / "Forget X" / "Delete X" — where the user does
NOT claim to have done the task, just wants it gone.

Triggers on phrases like:
  • "Nimm X von der Liste"     /  "Take X off the list"
  • "Vergiss X"                 /  "Forget about X"
  • "Lösch X"                   /  "Delete X"
  • "Entferne X"                /  "Remove X"
  • "Brauche X nicht mehr"      /  "Don't need X anymore"

Each detected drop intent → semantic.find_similar against open todos →
todos.drop_todo on the closest hit. Fire-and-forget from handlers.py.
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


_PROMPT = """Du analysierst eine Konversation und identifizierst Aktionen, die der USER von seiner ToDo-Liste ENTFERNEN möchte — OHNE dass er sie erledigt hat.

Was ZÄHLT als Drop-Intent:
- "Nimm X von der Liste"
- "Lösch / Entferne X"
- "Vergiss X"
- "X brauche ich nicht mehr"
- "X ist erledigt durch jemand anderen / nicht mehr relevant"
- "Take X off the list"

Was IST KEIN Drop-Intent (NICHT erfassen):
- "Habe X erledigt" → das ist eine Completion, nicht ein Drop
- "Verschiebe X auf morgen" → das ist Snooze, nicht Drop
- Fragen ("soll ich X löschen?")
- Aussagen des Assistenten

Antworte NUR mit validem JSON:
{
  "drops": [
    "<kompakte Beschreibung des zu entfernenden Items, z.B. 'Zahnbürsten besorgen'>"
  ]
}

Bei nichts Erkennbarem: {"drops": []}
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
    """LLM-detect drop intents → semantic-match → drop_todo.

    Returns {detected, dropped, ids: [(id, original_text, matched_phrase)]}.
    """
    try:
        raw = await _call_llm(user_text, assistant_response)
    except Exception as exc:
        logger.warning(f"todo_drop_extractor: LLM call failed: {exc}")
        return {"detected": 0, "dropped": 0, "ids": []}

    data = _extract_json(raw) or {}
    drops = data.get("drops") or []
    if not drops:
        return {"detected": 0, "dropped": 0, "ids": []}

    dropped: list[tuple[int, str, str]] = []
    for phrase in drops[:3]:
        phrase = str(phrase).strip()
        if not phrase or len(phrase) < 4:
            continue

        # Semantic match against open todos. Use FUZZY threshold — the drop
        # phrase ("Zahnbürsten") will be a fragment of the todo text
        # ("Zahnbürsten für Hilda und Martha mitbringen"), so we need more
        # tolerance than a strict same-phrase match.
        try:
            hits = await semantic.find_similar(
                "todos", user_id, phrase,
                threshold=semantic.DIST_FUZZY_MATCH, limit=3,
            )
        except Exception as exc:
            logger.debug("todo_drop_extractor: semantic search skipped: %s", exc)
            continue

        for ref_id, hit_text, _dist in hits:
            todo = await todos_svc.get_todo(user_id, ref_id)
            if not todo or todo.get("status") != "open":
                continue
            if await todos_svc.drop_todo(user_id, ref_id):
                dropped.append((ref_id, hit_text, phrase))
                break

    summary = {
        "detected": len(drops),
        "dropped": len(dropped),
        "ids": dropped,
    }
    if dropped:
        logger.info(
            "todo_drop_extractor: user=%s detected=%d dropped=%d",
            user_id, summary["detected"], summary["dropped"],
        )
    return summary
