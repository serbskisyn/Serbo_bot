"""
qualify_business_fit.py — LangGraph node: LLM-based business fit scoring.

Scores the current lead for all 4 platforms (Shoop.de, iGraal.de,
mydealz.de, mydealz.de/gutscheine) in a single LLM call, then classifies
HOT / WARM / COLD via the scorer service.
"""
from __future__ import annotations

import json
import logging
import re

from app.agents.lead_qualifying.prompts import (
    QUALIFICATION_SYSTEM,
    QUALIFICATION_USER,
)
from app.agents.lead_qualifying.services.scorer import classify, extract_score
from app.agents.lead_qualifying.state import LeadState
from app.services.openrouter_client import ask_llm

logger = logging.getLogger(__name__)

_JSON_RE = re.compile(r"\{.*\}", re.DOTALL)


def _fmt_score(score: int, rationale: str) -> str:
    """Format platform score as 'SCORE — rationale'."""
    return f"{score} — {rationale}" if rationale else str(score)


async def qualify_business_fit_node(state: LeadState) -> LeadState:
    """
    Score business fit for all 4 platforms via a single LLM call.

    Reads:  state["current_lead"], enrichment fields
    Writes: business_fit_*, score_total, classification, recommended_action
    """
    lead = state.get("current_lead", {})
    vorname = str(lead.get("Vorname", "")).strip()
    nachname = str(lead.get("Nachname", "")).strip()
    firma = str(lead.get("Firma", "")).strip()
    name = f"{vorname} {nachname}".strip()

    contact_title = state.get("contact_title", "")
    company_website = state.get("company_website", "")
    company_description = state.get("_company_description", "")
    industry = state.get("_industry", "")
    employee_count_estimate = state.get("_employee_count_estimate", "")
    news_summary = state.get("news_summary", "")

    logger.info("qualify_business_fit: '%s' @ '%s'", name, firma)

    prompt = QUALIFICATION_USER.format(
        name=name,
        contact_title=contact_title or "(unbekannt)",
        firma=firma,
        company_website=company_website or "(nicht gefunden)",
        company_description=company_description or "(keine Beschreibung)",
        industry=industry or "(unbekannt)",
        employee_count_estimate=employee_count_estimate or "(unbekannt)",
        news_summary=news_summary or "Keine News gefunden.",
    )

    # Defaults in case LLM fails
    shoop_score = igraal_score = mydealz_score = gutscheine_score = 0
    shoop_rationale = igraal_rationale = mydealz_rationale = gutscheine_rationale = ""
    recommended_action = ""
    contact_seniority = "mid"

    try:
        raw = await ask_llm(user_text=prompt, system_prompt=QUALIFICATION_SYSTEM)
        match = _JSON_RE.search(raw)
        if match:
            data = json.loads(match.group())
            shoop_score = int(data.get("shoop", {}).get("score", 0))
            shoop_rationale = data.get("shoop", {}).get("rationale", "")
            igraal_score = int(data.get("igraal", {}).get("score", 0))
            igraal_rationale = data.get("igraal", {}).get("rationale", "")
            mydealz_score = int(data.get("mydealz", {}).get("score", 0))
            mydealz_rationale = data.get("mydealz", {}).get("rationale", "")
            gutscheine_score = int(data.get("gutscheine", {}).get("score", 0))
            gutscheine_rationale = data.get("gutscheine", {}).get("rationale", "")
            recommended_action = data.get("recommended_action", "")
            contact_seniority = data.get("contact_seniority", "mid")
        else:
            logger.warning("qualify_business_fit: Kein JSON in LLM-Antwort für '%s'", name)
    except Exception as exc:
        logger.warning("qualify_business_fit: LLM Fehler für '%s': %s", name, exc)

    classification, score_total = classify(
        shoop_score, igraal_score, mydealz_score, gutscheine_score, contact_seniority
    )

    logger.info(
        "qualify_business_fit: %s | Shoop=%d iGraal=%d mydealz=%d Gutscheine=%d | Total=%d",
        classification, shoop_score, igraal_score, mydealz_score, gutscheine_score, score_total,
    )

    return {
        **state,
        "business_fit_shoop": _fmt_score(shoop_score, shoop_rationale),
        "business_fit_igraal": _fmt_score(igraal_score, igraal_rationale),
        "business_fit_mydealz": _fmt_score(mydealz_score, mydealz_rationale),
        "business_fit_gutscheine": _fmt_score(gutscheine_score, gutscheine_rationale),
        "score_total": score_total,
        "classification": classification,
        "recommended_action": recommended_action,
    }
