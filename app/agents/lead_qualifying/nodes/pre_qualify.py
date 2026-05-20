"""
pre_qualify.py — LangGraph node: fast pre-qualification on raw lead data only.

Uses a cheap, fast LLM call (GPT-4o-mini) on the raw Sheet fields to determine
whether a lead is worth enriching at all. No web search involved.

Labels:
  HIGH  — clear potential for ≥1 Atolls platform → full enrichment pipeline
  LOW   — ambiguous signal → enrich anyway, lower priority
  SKIP  — obvious non-fit → write as FILTERED to sheet, no enrichment, no Telegram
"""
from __future__ import annotations

import json
import logging
import re

from app.agents.lead_qualifying.prompts import PRE_QUALIFY_SYSTEM, PRE_QUALIFY_USER
from app.agents.lead_qualifying.state import LeadState
from app.services.openrouter_client import ask_llm

logger = logging.getLogger(__name__)

_JSON_RE = re.compile(r"\{.*\}", re.DOTALL)

# Fast, cheap model for the pre-qualify step — no need for a heavy model here
_PRE_QUALIFY_MODEL = "openai/gpt-4o-mini"

# Deterministische SKIP-Heuristiken — sparen LLM-Call bei offensichtlichem Müll
_SPAM_EMAIL_PATTERNS = ("test@", "asdf", "foo@", "bar@", "noreply", "no-reply",
                       "example.com", "tempmail", "mailinator")
_MIN_FIRMA_LEN  = 3   # "e", "co", "x" → SKIP
_MIN_NAME_LEN   = 2   # "e", "x" allein als Vorname → SKIP


def _deterministic_skip(vorname: str, nachname: str, firma: str, email: str) -> str | None:
    """Schnelle Heuristik vor dem LLM. Returns reason-string wenn SKIP, sonst None."""
    if len(firma) < _MIN_FIRMA_LEN and not nachname:
        return f"Firmenname und Nachname zu kurz ('{firma}', '{nachname}') — keine verwertbaren Daten"
    if len(firma) < _MIN_FIRMA_LEN and len(vorname) < _MIN_NAME_LEN:
        return f"Firma '{firma}' und Vorname '{vorname}' zu kurz — ungültiger Lead"
    if not firma and not email:
        return "Weder Firma noch E-Mail vorhanden"
    email_low = email.lower()
    for pat in _SPAM_EMAIL_PATTERNS:
        if pat in email_low:
            return f"E-Mail enthält Spam-/Test-Muster '{pat}'"
    # Identische Vor-/Nachname/Firma (z.B. alles "e") → Datenmüll
    if vorname and firma and vorname.lower() == firma.lower() and len(firma) <= 3:
        return f"Identische Müll-Daten ('{firma}'={vorname}) — kein echter Lead"
    return None


async def pre_qualify_node(state: LeadState) -> LeadState:
    """
    Quick assessment of the raw lead fields.

    1. Deterministische SKIP-Heuristiken (kein LLM-Call bei offensichtlichem Müll)
    2. LLM-Klassifikation für alles andere

    Reads:  state["current_lead"]
    Writes: state["pre_qualify_label"] ("HIGH" | "LOW" | "SKIP")
            state["pre_qualify_reason"] (1-sentence explanation)
    """
    lead = state.get("current_lead", {})
    vorname = str(lead.get("Vorname", "")).strip()
    nachname = str(lead.get("Nachname", "")).strip()
    firma = str(lead.get("Firma", "")).strip()
    email = str(lead.get("E-Mail", "")).strip()
    quelle = str(lead.get("Quelle", "")).strip()

    logger.info("pre_qualify: '%s %s' @ '%s'", vorname, nachname, firma)

    # 1. Schneller deterministischer SKIP-Check
    auto_skip = _deterministic_skip(vorname, nachname, firma, email)
    if auto_skip:
        logger.info("pre_qualify: '%s' → SKIP (deterministisch) | %s", firma, auto_skip)
        return {
            **state,
            "pre_qualify_label": "SKIP",
            "pre_qualify_reason": auto_skip,
        }

    label = "LOW"       # safe default: enrich anyway
    reason = ""

    try:
        prompt = PRE_QUALIFY_USER.format(
            vorname=vorname,
            nachname=nachname,
            firma=firma,
            email=email,
            quelle=quelle,
        )
        raw = await ask_llm(
            user_text=prompt,
            system_prompt=PRE_QUALIFY_SYSTEM,
        )
        match = _JSON_RE.search(raw)
        if match:
            data = json.loads(match.group())
            raw_label = str(data.get("label", "LOW")).upper().strip()
            label = raw_label if raw_label in ("HIGH", "LOW", "SKIP") else "LOW"
            reason = str(data.get("reason", "")).strip()
        else:
            logger.warning("pre_qualify: kein JSON in Antwort für '%s'", firma)

    except Exception as exc:
        logger.warning("pre_qualify: Fehler für '%s': %s", firma, exc)

    logger.info("pre_qualify: '%s' → %s | %s", firma, label, reason)

    return {
        **state,
        "pre_qualify_label": label,
        "pre_qualify_reason": reason,
    }


def route_after_pre_qualify(state: LeadState) -> str:
    """
    LangGraph conditional edge: decide which node to run after pre_qualify.

    SKIP  → collect_filtered_result (write to sheet as FILTERED, no enrichment)
    HIGH / LOW → enrich_contact (full pipeline)
    """
    label = state.get("pre_qualify_label", "LOW")
    if label == "SKIP":
        logger.info("pre_qualify router: SKIP → collect_filtered_result")
        return "collect_filtered_result"
    logger.info("pre_qualify router: %s → enrich_contact", label)
    return "enrich_contact"
