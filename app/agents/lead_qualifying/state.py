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
    company_website: str
    northdata_summary: str
    news_summary: str

    # ── Qualification ─────────────────────────────────────────────────────────
    business_fit_shoop: str        # 0-10 score + one-line rationale
    business_fit_igraal: str
    business_fit_mydealz: str
    business_fit_gutscheine: str
    score_total: int               # 0-40 aggregate
    classification: str            # HOT / WARM / COLD
    recommended_action: str

    # ── Pre-qualification (raw-data only, before enrichment) ─────────────────
    pre_qualify_label: str         # HIGH / LOW / SKIP
    pre_qualify_reason: str        # 1-sentence explanation

    # ── Output ────────────────────────────────────────────────────────────────
    processed_leads: list[dict]    # finished lead result dicts, accumulated
    telegram_notified: bool
    errors: list[str]              # non-fatal errors collected during the run
