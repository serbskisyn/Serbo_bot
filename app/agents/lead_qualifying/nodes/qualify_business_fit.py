"""
qualify_business_fit.py — LangGraph node: deterministischer Score + LLM-Sales-Action.

Score wird deterministisch via scorer_v2.compute_score() berechnet — transparent
und debuggbar. Der LLM-Call macht nur noch zwei kompakte Aufgaben:
  - contact_seniority (Junior/Mid/Senior) aus Name + Position einschätzen
  - recommended_action (1-2 Sätze) für die Sales-Person ableiten

Reads:  state[*] (alle Pipeline-Felder)
Writes: state["score_total"], state["classification"], state["recommended_action"],
        state["contact_seniority"], state["score_breakdown"], state["score_override"]
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
    "You are a senior commercial intelligence analyst for Atolls. "
    "Atolls operates Pepper Community platforms (mydealz.de, dealabs.fr, pepper.com etc.) as PRIMARY business. "
    "Shoop (cashback), iGraal (cashback/coupons), and Gutscheine.de (vouchers) are secondary products. "
    "Leads arrive via the Pepper Contact-Us form — always prioritise Pepper Community Brand fit. "
    "Reply ONLY with the requested JSON object — no surrounding text. Answer in English."
)

_SALES_ACTION_USER = """Lead profile:
- Name: {name}
- Company: {firma}
- Business model: {business_model}
- Markets: {markets}
- Validated eCom brands: {brands_text}
- Revenue estimate: {revenue}
- Pepper sentiment — domestic: {pepper_target}
- Pepper sentiment — cross-country: {pepper_cross}
- Sales signals: {sales_signals}
- Performance marketing signals: {perf_mktg_signals}
- Affiliate likelihood: {affiliate_likelihood}
- Promo / deal activity: {promo_intensity}
- Commercial intelligence: {commercial_intel}
- Score: {classification} ({score}/100)

Evaluate in this order:
1. **Pepper Community Brands fit** — does the brand have an active Pepper community presence?
   High deal volume on mydealz/dealabs/pepper.com = strong signal. This is the primary criterion.
2. **Deal virality** — are deals likely to generate community engagement and organic reach?
3. **Performance marketing maturity** — does the brand invest in paid channels (PPC, affiliates)?
4. **Secondary Atolls products** — mention Shoop/iGraal/Gutscheine ONLY if Perplexity signals
   suggest clear cashback/voucher fit AND primary Pepper fit is strong.

Reply with this JSON in English:
{{
  "contact_seniority": "junior|mid|senior",
  "priority_tier": "LOW|MEDIUM|HIGH|STRATEGIC",
  "priority_reason": "1 sentence — why this tier, anchored to Pepper Community fit",
  "recommended_action": "2-3 concise sentences: primary Pepper Brand pitch angle, then deal/promo approach, and optionally Shoop/iGraal if strong secondary fit"
}}

Priority tier guidance:
- STRATEGIC: strong Pepper community presence (>500m target or cross-country), deal-viral brand, active perf-mktg
- HIGH: moderate Pepper mentions (100-500m) or clear affiliate/promo signals with Atolls market overlap
- MEDIUM: some Pepper activity or affiliate signals but limited reach / narrow markets
- LOW: weak or no Pepper signals, B2B-leaning, no affiliate signals, or no Atolls market overlap"""


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
    # (LOW-Leads erreichen diesen Knoten nicht — sie werden schon nach
    # pre_qualify ausgesteuert, siehe route_after_pre_qualify.)
    validated = state.get("validated_brands") or []
    brands_text = ", ".join(
        b.get("name", "") for b in validated if isinstance(b, dict) and b.get("name")
    ) or "(keine identifiziert)"

    prompt = _SALES_ACTION_USER.format(
        name=name,
        firma=firma,
        business_model=state.get("business_model", "") or "(unknown)",
        markets=", ".join(state.get("primary_markets") or []) or "(unknown)",
        brands_text=brands_text,
        revenue=state.get("company_revenue", "") or "(unknown)",
        pepper_target=state.get("pepper_target_summary", "") or "(no data)",
        pepper_cross=state.get("pepper_cross_summary", "") or "(no data)",
        sales_signals=state.get("sales_signals", "") or "(none)",
        perf_mktg_signals=state.get("perf_mktg_signals", "") or "(none)",
        affiliate_likelihood=state.get("affiliate_likelihood", "") or "(none)",
        promo_intensity=state.get("promo_intensity", "") or "(none)",
        commercial_intel=state.get("commercial_intel_summary", "") or "(none)",
        classification=classification,
        score=score_total,
    )

    contact_seniority  = "mid"
    recommended_action = ""
    priority_tier      = ""
    try:
        raw = await ask_llm(user_text=prompt, system_prompt=_SALES_ACTION_SYSTEM)
        match = _JSON_RE.search(raw)
        if match:
            data = json.loads(match.group())
            contact_seniority  = str(data.get("contact_seniority", "mid")).lower().strip()
            recommended_action = str(data.get("recommended_action", "")).strip()
            priority_tier      = str(data.get("priority_tier", "")).upper().strip()
            priority_reason    = str(data.get("priority_reason", "")).strip()
            if priority_reason and priority_tier:
                priority_tier = f"{priority_tier} — {priority_reason}"
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
        "priority_tier":       priority_tier,
        # Score-Audit für Sheet/Logs
        "score_breakdown":     breakdown_str,
        "score_override":      score_result.get("override_reason", ""),
    }
