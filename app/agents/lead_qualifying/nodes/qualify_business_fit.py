"""
qualify_business_fit.py — LangGraph node: deterministischer Score + LLM-Sales-Action.

Score wird deterministisch via scorer_v2.compute_score() berechnet — transparent
und debuggbar. Der LLM-Call macht nur noch zwei kompakte Aufgaben:
  - contact_seniority (Junior/Mid/Senior) aus Name + Position einschätzen
  - recommended_action (1-2 Sätze) für die Sales-Person ableiten

Reads:  state[*] (alle Pipeline-Felder)
Writes: state["score_total"], state["classification"], state["recommended_action"],
        state["contact_seniority"], state["score_breakdown"], state["score_override"]
        sowie Legacy business_fit_* (leer).
"""
from __future__ import annotations

import json
import logging
import re

from app.agents.lead_qualifying.services.scorer_v2 import (
    compute_score,
    format_breakdown,
)
from app.agents.lead_qualifying.state import LeadState
from app.services.openrouter_client import ask_llm

logger = logging.getLogger(__name__)

_JSON_RE = re.compile(r"\{.*\}", re.DOTALL)


_SALES_ACTION_SYSTEM = (
    "You are a B2B sales analyst for Atolls (Shoop, iGraal, mydealz). "
    "You write short, concrete next-steps for the salesperson. "
    "Reply ONLY with the requested JSON object — no surrounding text. "
    "Always answer in English, regardless of the input language."
)

_SALES_ACTION_USER = """Lead profile:
- Name: {name}
- Company: {firma}
- Business model: {business_model}
- Markets: {markets}
- Validated eCom brands: {brands_text}
- Pepper sentiment (target country): {pepper_target}
- Pepper cross-country signals: {pepper_cross}
- Sales signals: {sales_signals}
- Score classification: {classification} ({score}/100)

Reply with this JSON in English:
{{
  "contact_seniority": "junior|mid|senior",
  "recommended_action": "1-2 concise sentences with the next step for sales — in English"
}}"""


async def qualify_business_fit_node(state: LeadState) -> LeadState:
    lead     = state.get("current_lead", {})
    vorname  = str(lead.get("Vorname", "")).strip()
    nachname = str(lead.get("Nachname", "")).strip()
    firma    = str(lead.get("Firma", "")).strip()
    name     = f"{vorname} {nachname}".strip()

    logger.info("qualify_business_fit: '%s' @ '%s'", name, firma)

    # ── 1. Deterministischer Score (erstmal ohne contact_seniority, das kommt vom LLM) ──
    score_result = compute_score(state)
    classification = score_result["classification"]
    score_total    = score_result["score_total"]

    # ── 2. Kompakter LLM-Call für recommended_action + contact_seniority ──
    validated = state.get("validated_brands") or []
    brands_text = ", ".join(
        b.get("name", "") for b in validated if isinstance(b, dict) and b.get("name")
    ) or "(keine identifiziert)"

    prompt = _SALES_ACTION_USER.format(
        name=name,
        firma=firma,
        business_model=state.get("business_model", "") or "(unbekannt)",
        markets=", ".join(state.get("primary_markets") or []) or "(unbekannt)",
        brands_text=brands_text,
        pepper_target=state.get("pepper_target_summary", "") or "(keine Daten)",
        pepper_cross=state.get("pepper_cross_summary", "") or "(keine Daten)",
        sales_signals=state.get("sales_signals", "") or "(keine)",
        classification=classification,
        score=score_total,
    )

    contact_seniority  = "mid"
    recommended_action = ""
    try:
        raw = await ask_llm(user_text=prompt, system_prompt=_SALES_ACTION_SYSTEM)
        match = _JSON_RE.search(raw)
        if match:
            data = json.loads(match.group())
            contact_seniority  = str(data.get("contact_seniority", "mid")).lower().strip()
            recommended_action = str(data.get("recommended_action", "")).strip()
    except Exception as exc:
        logger.warning("qualify_business_fit: LLM-Action-Fehler für '%s': %s", name, exc)

    # Score nochmal NEU berechnen mit der ermittelten Seniority (kann +5 Punkte geben)
    # und Klassifikation re-evaluieren — wichtig wenn Senior die Schwelle bricht.
    state_with_seniority = {**state, "contact_seniority": contact_seniority}
    score_result    = compute_score(state_with_seniority)
    classification  = score_result["classification"]
    score_total     = score_result["score_total"]
    breakdown_str   = format_breakdown(score_result)

    logger.info(
        "qualify_business_fit: %s | %d/100 | seniority=%s | %s",
        classification, score_total, contact_seniority, breakdown_str,
    )

    return {
        **state,
        # Neuer deterministischer Score
        "score_total":         score_total,
        "classification":      classification,
        "contact_seniority":   contact_seniority,
        "recommended_action":  recommended_action,
        # Score-Audit für Sheet/Logs
        "score_breakdown":     breakdown_str,
        "score_override":      score_result.get("override_reason", ""),
        # Legacy-Felder leer halten (QualifiedLeadRow erwartet sie)
        "business_fit_shoop":      "",
        "business_fit_igraal":     "",
        "business_fit_mydealz":    "",
        "business_fit_gutscheine": "",
    }
