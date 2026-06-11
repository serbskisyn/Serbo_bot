"""
context_extractor.py — fire-and-forget extraction into the soft context layer.

Two extractors run after each message (gated by SOFT_LAYER_ENABLED):

  • extract_entities(user_msg, bot_response) — pulls person/place/event/task
    entities from the whole exchange, upserts them, and links every pair that
    co-occurs in the same turn (building the relationship graph).
  • extract_intents(user_msg) — pulls soft commitments ("ich sollte/wollte X")
    from the USER message only, stored as kind='intent'.

Both adapt FabBot's collector/intent_extractor prompts. Failures never
propagate — they are background tasks.
"""
from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timezone

import httpx

from app.config import OPENROUTER_API_KEY, SOFT_LAYER_ENABLED
from app.services import context_store

logger = logging.getLogger(__name__)

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
MODEL = "openai/gpt-4o-mini"

_ENTITY_PROMPT = """Analysiere die Konversation und extrahiere strukturierte Entitäten.
Heute ist {today}.

Extrahiere als JSON-Array. Jede Entität:
- "type": einer von ["person", "place", "event", "task"]
- "name": kanonischer Name (z.B. "Martin Gospodinov", "Clicky Migration", "Lissabon")
- "context": kurzer Kontext-Satz (max. 100 Zeichen)
- "due_date": ISO-Datum falls erkennbar, sonst weglassen

Regeln:
- Nur Entitäten mit klarer Bedeutung. Keine trivialen ("Bot", "Antwort").
- Keine Absichten/Verpflichtungen ("ich muss X") — die werden separat erfasst.
- Personen nur, wenn sie im echten Gesprächskontext vorkommen, nicht als
  reines Befehls-Argument ("schreib an X", "ruf Y an").
- Keine Entität → leeres Array [].

Antwort NUR als JSON-Array."""

_INTENT_PROMPT = """Analysiere die Nachricht auf Absichten/Verpflichtungen/Pläne des Users.
Heute ist {today}.

Muster: "ich muss/sollte/wollte/will X", "X noch nicht gemacht", "erinnere mich an X",
"nächste Woche/morgen/bald X".

JSON-Array, jeder Eintrag:
- "name": prägnanter Name (max. 60 Zeichen)
- "context": Original-Zitat (max. 100 Zeichen)
- "due_date": ISO-Datum falls erkennbar ("morgen" → +1 Tag), sonst weglassen

Nur echte Commitments — keine Wünsche/Hypothesen. Sonst [].
Antwort NUR als JSON-Array."""


def _today() -> str:
    return datetime.now(tz=timezone.utc).strftime("%Y-%m-%d")


async def _call_llm(system: str, user: str, timeout: float = 12.0) -> str:
    from app.services.llm_client import chat
    from app.config import LLM_CHEAP_MODEL
    return await chat(
        [{"role": "system", "content": system}, {"role": "user", "content": user}],
        model=LLM_CHEAP_MODEL, temperature=0.0, max_tokens=500, timeout=timeout,
    )


def _parse_array(raw: str) -> list[dict]:
    if not raw:
        return []
    raw = re.sub(r"```(?:json)?\s*", "", raw).strip().rstrip("`").strip()
    m = re.search(r"\[.*\]", raw, re.DOTALL)
    if not m:
        return []
    try:
        data = json.loads(m.group())
    except json.JSONDecodeError:
        return []
    return [d for d in data if isinstance(d, dict) and d.get("name")]


async def extract_entities(user_id: int, user_message: str, bot_response: str) -> None:
    if not SOFT_LAYER_ENABLED:
        return
    if not (user_message.strip() or bot_response.strip()):
        return
    try:
        raw = await _call_llm(
            _ENTITY_PROMPT.format(today=_today()),
            f"User: {user_message[:600]}\nBot: {bot_response[:600]}",
        )
        entities = _parse_array(raw)
        if not entities:
            return
        ids: list[int] = []
        for e in entities:
            etype = e.get("type") if e.get("type") in context_store.ENTITY_TYPES else "person"
            iid = await context_store.upsert_item(
                user_id, e["name"], kind="entity", entity_type=etype,
                context=str(e.get("context", ""))[:200], due_date=e.get("due_date"),
            )
            if iid:
                ids.append(iid)
        # Link every co-occurring pair → relationship graph
        for i in range(len(ids)):
            for j in range(i + 1, len(ids)):
                await context_store.add_link(user_id, ids[i], ids[j])
        if ids:
            logger.info("context: %d entit(ies), %d link(s) | user=%s",
                        len(ids), len(ids) * (len(ids) - 1) // 2, user_id)
    except Exception as exc:
        logger.debug("context: entity extraction failed: %s", exc)


async def extract_intents(user_id: int, user_message: str) -> None:
    if not SOFT_LAYER_ENABLED:
        return
    if not user_message.strip():
        return
    try:
        raw = await _call_llm(
            _INTENT_PROMPT.format(today=_today()), f"Nachricht: {user_message[:600]}"
        )
        intents = _parse_array(raw)
        for it in intents:
            await context_store.upsert_item(
                user_id, it["name"], kind="intent", entity_type="intent",
                context=str(it.get("context", ""))[:200], due_date=it.get("due_date"),
            )
        if intents:
            logger.info("context: %d intent(s) | user=%s", len(intents), user_id)
    except Exception as exc:
        logger.debug("context: intent extraction failed: %s", exc)
