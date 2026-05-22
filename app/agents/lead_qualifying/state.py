"""
state.py — LeadState TypedDict for the Lead Qualifying LangGraph pipeline.
"""
from __future__ import annotations

from typing import TypedDict


class LeadState(TypedDict, total=False):
    # ── Input ─────────────────────────────────────────────────────────────────
    raw_leads: list[dict]          # rows read from the Inbound tab
    new_leads: list[dict]          # raw_leads filtered by idempotency check

    # ── Current lead being processed (populated per-iteration) ───────────────
    current_lead: dict             # single lead row in flight

    # ── Enrichment ────────────────────────────────────────────────────────────
    contact_title: str
    linkedin_url: str
    contact_authority: str         # decision_maker | influencer | other
    contact_role_match: bool       # True if role is marketing/sales/eCom-relevant
    company_website: str
    northdata_summary: str
    news_summary: str

    # Intermediate-Felder aus enrich_company, verwendet von qualify_business_fit_node
    # UND write_results (Größe-Spalte). LangGraph strippt sonst undeklarierte Keys.
    _company_description: str
    _industry: str
    _employee_count_estimate: str

    # ── Neue Pipeline (2026-05-20 Refactor): Brand-Discovery + Sales-Validation +
    #     Multi-Country-Pepper-Lookup pro Brand ────────────────────────────────
    discovered_brands:   list                  # aus discover_ecommerce_brands
    is_holding:          bool
    validated_brands:    list                  # aus validate_company_sales
    company_revenue:     str                   # Umsatz-Schätzung
    company_employees:   str                   # Mitarbeiterzahl
    company_hq:          str                   # Headquarters-Standort
    primary_markets:     list                  # Liste ISO-Codes
    business_model:      str                   # B2C/B2B/Marketplace/Hybrid
    sales_signals:       str                   # 2-3 Sätze Sales-Kontext

    # Zielland aus Inbound-Spalte "Target country" → Pepper-ISO-Code
    target_country_iso:  str

    # Pepper-Multi-Brand-Multi-Country-Output (komplette Datenstruktur)
    pepper_by_brand:           dict
    pepper_brands_found:       int
    pepper_total_mentions_all: int

    # Kompakte Strings für Sheet-Spalten (zur Performance, einmal berechnet)
    pepper_target_summary:     str             # 1 Zeile für Zielland
    pepper_cross_summary:      str             # 1 Zeile für Top-Cross-Country

    # ── Pepper Sentiment (community mentions, 90-Tage-Lookback) ─────────────
    pepper_found: bool             # True wenn matched
    pepper_matched_name: str       # canonical_retailer_name aus Pepper
    pepper_total_mentions: int
    pepper_pos: int
    pepper_neg: int
    pepper_neu: int
    pepper_pos_rate: float         # 0.0–1.0, None → -1.0 markiert "kein Signal"
    pepper_top_country: str
    pepper_summary: str            # 1-Zeilen-DE-Summary für Sheet/Telegram

    # ── Commercial Intelligence (enrich_commercial_intelligence node) ────────
    marketing_spend_estimate: str  # e.g. "~2-5M EUR/year" or "unknown"
    perf_mktg_signals: str         # Google Shopping / Meta / TikTok / Amazon signals
    affiliate_likelihood: str      # high / medium / low + short rationale
    promo_intensity: str           # coupon/deal/promo frequency summary
    commercial_intel_summary: str  # 2-3 sentence structured summary for LLM context
    priority_tier: str             # LOW / MEDIUM / HIGH / STRATEGIC

    # ── Qualification ─────────────────────────────────────────────────────────
    business_fit_shoop: str        # Legacy (jetzt leer)
    business_fit_igraal: str
    business_fit_mydealz: str
    business_fit_gutscheine: str
    score_total: int               # 0-100 (neuer deterministischer Score)
    classification: str            # HOT / WARM / COLD
    recommended_action: str
    contact_seniority: str         # junior/mid/senior (vom LLM)
    score_breakdown: str           # 1-Zeilen-Audit "Biz X · Pepper Y · Ctx Z"
    score_override: str            # falls Auto-HOT/COLD getriggert

    # ── Pre-qualification (raw-data only, before enrichment) ─────────────────
    pre_qualify_label: str         # HIGH / LOW / SKIP
    pre_qualify_reason: str        # 1-sentence explanation

    # ── Runtime overrides ────────────────────────────────────────────────────
    max_leads_override: int        # if set, overrides LEAD_QUALIFYING_MAX_PER_RUN

    # ── Output ────────────────────────────────────────────────────────────────
    processed_leads: list[dict]    # finished lead result dicts, accumulated
    telegram_notified: bool
    errors: list[str]              # non-fatal errors collected during the run
