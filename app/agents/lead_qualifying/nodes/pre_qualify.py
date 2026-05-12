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


async def pre_qualify_node(state: LeadState) -> LeadState:
    """
    Quick LLM assessment of the raw lead fields.

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
