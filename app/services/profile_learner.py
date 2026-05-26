"""
profile_learner.py — 3-stage extraction pipeline for user-profile learning.

Stage 1 — Detector
    "Does this message contain anything worth remembering about the USER?"
    Fast LLM call. Filters out weather data, dates, generic chit-chat, and
    any statements that describe the assistant rather than the user.
    Output: {has_facts: bool, candidates: [str]}

Stage 2 — Writer
    Takes Detector candidates + a compact view of the current profile and
    maps each candidate to a structured operation against the profile schema.
    Output: {ops: [{section, op, key, value, confidence}]}

Stage 3 — Reviewer
    Validates the proposed ops against the current profile. Rejects:
      - duplicates already present
      - overwrites of stronger existing facts with weaker confidence
      - ops with confidence < threshold
      - anything that looks like transient/ephemeral state
    Output: {approved: [...], rejected: [{op, reason}]}

The final approved ops are applied via profile.apply_ops().

All three stages use OpenRouter with gpt-4o-mini (cheap + fast). Failures
in any stage degrade gracefully — no exception escapes to the caller.
"""
from __future__ import annotations

import json
import logging
import re
from typing import Any

import httpx

from app.bot import profile
from app.config import OPENROUTER_API_KEY

logger = logging.getLogger(__name__)

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
LEARNER_MODEL = "openai/gpt-4o-mini"

MIN_CONFIDENCE_TO_STORE = 0.6
MIN_CONFIDENCE_AS_PENDING = 0.35

_DETECTOR_PROMPT = """Du bist ein Memory-Detektor für einen persönlichen Assistenten.
Deine Aufgabe: Identifiziere persönliche Fakten über den USER aus der Konversation.

WAS ZÄHLT als Fakt:
- Identität: Name, Wohnort, Alter, Geburtsort, Beruf
- Arbeit: Firma, Team, Branche, Rolle
- Interessen: Hobbys, Lieblingsthemen, Lieblingsclubs/Marken (KEINE einmaligen Themen)
- Präferenzen: Kommunikationsstil, Vorlieben/Abneigungen
- Personen: andere Menschen, die der User namentlich erwähnt
- Ziele: erklärte Vorhaben oder Projekte

WAS NICHT ZÄHLT (ignoriere strikt):
- Wetterdaten, Datum, Uhrzeit, Wochentag
- Generische Fragen oder Aussagen des Assistenten
- "User hat einen Namen", "user benutzt API" — solche Meta-Aussagen
- Einmalige Themen ohne erkennbare persönliche Relevanz
- Trivia, die der Assistent dem User erzählt hat
- Zeitlich begrenzte Zustände ("ich bin heute müde")

Antworte NUR mit validem JSON:
{
  "has_facts": <bool>,
  "candidates": [
    {"text": "<kompakte Fakt-Beschreibung auf Deutsch>", "type": "<identity|work|interest|preference|person|goal>", "confidence": <0.0-1.0>}
  ]
}

Bei nichts Erkennbarem: {"has_facts": false, "candidates": []}"""


_WRITER_PROMPT = """Du bist ein Memory-Writer. Du bekommst Kandidaten-Fakten und das aktuelle Profil.
Wandle die Kandidaten in strukturierte Update-Operationen um.

Erlaubte Sections:
- identity   (set-ops mit key ∈ {name, location, age, birthplace, role})
- work       (set-ops mit key ∈ {company, team, industry, position})
- preferences (set-ops mit key ∈ {communication_style})
- interests  (append-ops, value = string)
- people     (add_dict-ops, value = {name, relation, notes})
- goals      (add_dict-ops, value = {text, timeframe})
- facts      (set-ops mit beliebigem key — Catch-all für unstrukturierte Fakten)

Regeln:
- Wenn ein identity/work-Feld bereits gesetzt ist und stark abweicht → schlage UPDATE nur vor wenn confidence ≥ 0.8
- Bei Mehrfachnennungen (z.B. mehrere Lieblingsclubs) immer "append" nach interests
- Wenn confidence < 0.6: stattdessen op="add_pending"
- Antworte NUR mit validem JSON:

{
  "ops": [
    {"section": "<section>", "op": "<set|append|add_dict|add_pending>", "key": "<key|null>", "value": <value>, "confidence": <0-1>, "reason": "<short>"}
  ]
}"""


_REVIEWER_PROMPT = """Du bist ein Memory-Reviewer. Prüfe die vorgeschlagenen Operations gegen das aktuelle Profil.

Lehne ab (rejected) wenn:
- Op widerspricht einem bereits bestätigten Fakt mit höherer Sicherheit
- Op duplikat (Wert/Key bereits exakt vorhanden)
- Wert sieht aus wie transient/Wetter/Datum/temporäre Befindlichkeit
- confidence < 0.5 (außer add_pending)
- Section/Op-Kombination ungültig

Akzeptiere (approved) ansonsten.

Antworte NUR mit validem JSON:
{
  "approved": [<ops verbatim>],
  "rejected": [{"op": <op>, "reason": "<short>"}]
}"""


# ──────────────────────────────────────────────────────────────────────────────


async def _call_llm(system: str, user: str, timeout: float = 12.0) -> str:
    """Single OpenRouter call returning the raw assistant content."""
    payload = {
        "model": LEARNER_MODEL,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "temperature": 0.0,
        "max_tokens": 400,
    }
    headers = {
        "Authorization": f"Bearer {OPENROUTER_API_KEY}",
        "Content-Type": "application/json",
    }
    async with httpx.AsyncClient(timeout=timeout) as client:
        r = await client.post(OPENROUTER_URL, json=payload, headers=headers)
        r.raise_for_status()
        return r.json()["choices"][0]["message"]["content"]


def _extract_json(text: str) -> dict | None:
    """Pull the first JSON object out of the response text."""
    if not text:
        return None
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if not match:
        return None
    try:
        return json.loads(match.group())
    except json.JSONDecodeError:
        return None


def _profile_summary(user_id: int) -> str:
    """Tight summary of the current profile for prompt context."""
    p = profile.get_profile(user_id)
    lines = []
    if p.get("identity"):
        ident = {k: v for k, v in p["identity"].items() if v}
        if ident:
            lines.append(f"identity: {ident}")
    if p.get("work"):
        w = {k: v for k, v in p["work"].items() if v}
        if w:
            lines.append(f"work: {w}")
    if p.get("interests"):
        lines.append(f"interests: {p['interests']}")
    if p.get("preferences"):
        prefs = {k: v for k, v in p["preferences"].items() if v}
        if prefs:
            lines.append(f"preferences: {prefs}")
    if p.get("people"):
        names = [x.get("name") for x in p["people"] if isinstance(x, dict) and x.get("name")]
        if names:
            lines.append(f"people: {names}")
    if p.get("facts"):
        lines.append(f"facts: {p['facts']}")
    return "\n".join(lines) if lines else "(profile is empty)"


# ──────────────────────────────────────────────────────────────────────────────
# Stage 1 — Detector


async def detect(user_text: str, assistant_response: str) -> dict:
    """Returns {has_facts: bool, candidates: [{text, type, confidence}]}."""
    user_msg = (
        f"USER: {user_text}\n"
        f"ASSISTANT: {assistant_response}\n\n"
        f"Identifiziere persönliche Fakten über den USER (nicht über den Assistenten)."
    )
    try:
        raw = await _call_llm(_DETECTOR_PROMPT, user_msg, timeout=10.0)
    except Exception as exc:
        logger.warning(f"detector: LLM-call failed: {exc}")
        return {"has_facts": False, "candidates": []}
    data = _extract_json(raw) or {}
    return {
        "has_facts": bool(data.get("has_facts")),
        "candidates": data.get("candidates") or [],
    }


# Stage 2 — Writer


async def write(user_id: int, candidates: list[dict]) -> list[dict]:
    """Map candidates to structured update operations."""
    if not candidates:
        return []
    user_msg = (
        f"AKTUELLES PROFIL:\n{_profile_summary(user_id)}\n\n"
        f"KANDIDATEN:\n{json.dumps(candidates, ensure_ascii=False)}"
    )
    try:
        raw = await _call_llm(_WRITER_PROMPT, user_msg, timeout=12.0)
    except Exception as exc:
        logger.warning(f"writer: LLM-call failed: {exc}")
        return []
    data = _extract_json(raw) or {}
    ops = data.get("ops") or []
    return [op for op in ops if isinstance(op, dict)]


# Stage 3 — Reviewer


async def review(user_id: int, ops: list[dict]) -> list[dict]:
    """Filter ops against current profile. Returns the approved ops."""
    if not ops:
        return []
    user_msg = (
        f"AKTUELLES PROFIL:\n{_profile_summary(user_id)}\n\n"
        f"VORGESCHLAGENE OPS:\n{json.dumps(ops, ensure_ascii=False)}"
    )
    try:
        raw = await _call_llm(_REVIEWER_PROMPT, user_msg, timeout=10.0)
    except Exception as exc:
        logger.warning(f"reviewer: LLM-call failed: {exc}")
        # On reviewer failure, fall back to client-side confidence threshold
        return [op for op in ops if float(op.get("confidence", 0)) >= MIN_CONFIDENCE_TO_STORE]
    data = _extract_json(raw) or {}
    approved = data.get("approved") or []
    rejected = data.get("rejected") or []
    if rejected:
        for r in rejected[:5]:
            logger.info(f"reviewer rejected: {r.get('reason', '?')} — {r.get('op', {})}")
    return [op for op in approved if isinstance(op, dict)]


# Full pipeline


async def learn(user_id: int, user_text: str, assistant_response: str) -> dict:
    """End-to-end: detect → write → review → apply.

    Returns: {detected: N, proposed: N, applied: N, skipped: N}
    """
    detection = await detect(user_text, assistant_response)
    if not detection.get("has_facts"):
        return {"detected": 0, "proposed": 0, "applied": 0, "skipped": 0}

    candidates = detection.get("candidates", [])
    if not candidates:
        return {"detected": 0, "proposed": 0, "applied": 0, "skipped": 0}

    ops = await write(user_id, candidates)
    approved = await review(user_id, ops)

    result = await profile.apply_ops(user_id, approved)
    summary = {
        "detected": len(candidates),
        "proposed": len(ops),
        "applied": result.get("applied", 0),
        "skipped": result.get("skipped", 0),
    }
    if summary["applied"] > 0:
        logger.info(
            "profile_learner: user=%s detected=%d proposed=%d applied=%d",
            user_id, summary["detected"], summary["proposed"], summary["applied"],
        )
    return summary
