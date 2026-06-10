"""
write_results.py — LangGraph node: write results to sheet and send Telegram summary.

Collects the current processed lead, appends it to the list, and at the
end of the run flushes all results to Google Sheets and sends a Telegram message.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

_BERLIN = ZoneInfo("Europe/Berlin")

from app.agents.lead_qualifying.schemas import QualifiedLeadRow
from app.agents.lead_qualifying.services.sheets import (
    INBOUND_SHEET_ID,
    append_qualified_leads,
    ensure_validation_columns,
    write_validation_for_row,
)
from app.agents.lead_qualifying.state import LeadState
from app.agents.lead_qualifying.prompts import (
    TELEGRAM_LEAD_BLOCK_TEMPLATE,
    TELEGRAM_SUMMARY_TEMPLATE,
)

logger = logging.getLogger(__name__)

_MAX_TELEGRAM_LEN = 4000


def _split_message(text: str, limit: int = _MAX_TELEGRAM_LEN) -> list[str]:
    """Split a long message into Telegram-safe chunks at line boundaries."""
    if len(text) <= limit:
        return [text]
    chunks: list[str] = []
    current_lines: list[str] = []
    current_len = 0
    for line in text.split("\n"):
        if current_len + len(line) + 1 > limit:
            chunks.append("\n".join(current_lines))
            current_lines, current_len = [line], len(line)
        else:
            current_lines.append(line)
            current_len += len(line) + 1
    if current_lines:
        chunks.append("\n".join(current_lines))
    return chunks


async def collect_filtered_result_node(state: LeadState) -> LeadState:
    """
    Build a minimal QualifiedLeadRow for a SKIP lead and append to processed_leads.

    Classification is set to FILTERED. No enrichment fields are populated.
    These leads are written to the sheet for traceability but NOT included in
    the Telegram summary.
    """
    lead = state.get("current_lead", {})

    row = QualifiedLeadRow(
        lead_key=str(lead.get("_lead_key", "")),
        processed_at=datetime.now(tz=_BERLIN).strftime("%Y-%m-%dT%H:%M:%S"),
        vorname=str(lead.get("Vorname", "")),
        nachname=str(lead.get("Nachname", "")),
        firma=str(lead.get("Firma", "")),
        email=str(lead.get("E-Mail", "")),
        quelle=str(lead.get("Quelle", "")),
        pre_qualify_label=state.get("pre_qualify_label", "SKIP"),
        pre_qualify_reason=state.get("pre_qualify_reason", ""),
        classification="AGENCY" if state.get("pre_qualify_label") == "AGENCY" else "FILTERED",
        recommended_action=state.get("pre_qualify_reason", ""),
        telegram_notified="nein",
    )

    processed = list(state.get("processed_leads", []))
    row_dict = row.model_dump()
    row_dict["_row_index"] = int(lead.get("_row_index", 0))
    row_dict["_pepper_summary"] = ""  # FILTERED-Leads kein Pepper-Lookup
    row_dict["_employee_count"] = ""
    processed.append(row_dict)
    logger.info(
        "collect_filtered_result: '%s %s' @ '%s' → FILTERED (%s)",
        row.vorname, row.nachname, row.firma, row.pre_qualify_reason,
    )
    return {**state, "processed_leads": processed}


async def collect_lead_result_node(state: LeadState) -> LeadState:
    """
    Build a QualifiedLeadRow from the enrichment/qualification data stored
    in the current state and append it to state["processed_leads"].

    This node runs once per lead, after all enrichment and qualification nodes.
    """
    lead = state.get("current_lead", {})

    row = QualifiedLeadRow(
        lead_key=str(lead.get("_lead_key", "")),
        processed_at=datetime.now(tz=_BERLIN).strftime("%Y-%m-%dT%H:%M:%S"),
        vorname=str(lead.get("Vorname", "")),
        nachname=str(lead.get("Nachname", "")),
        firma=str(lead.get("Firma", "")),
        email=str(lead.get("E-Mail", "")),
        quelle=str(lead.get("Quelle", "")),
        pre_qualify_label=state.get("pre_qualify_label", ""),
        pre_qualify_reason=state.get("pre_qualify_reason", ""),
        contact_title=state.get("contact_title", ""),
        linkedin_url=state.get("linkedin_url", ""),
        company_website=state.get("company_website", ""),
        score_total=state.get("score_total", 0),
        classification=state.get("classification", ""),
        recommended_action=state.get("recommended_action", ""),
        telegram_notified="nein",
    )

    processed = list(state.get("processed_leads", []))
    row_dict = row.model_dump()
    row_dict["_row_index"]      = int(lead.get("_row_index", 0))
    row_dict["_pepper_summary"] = state.get("pepper_summary", "")
    row_dict["_employee_count"] = state.get("_employee_count_estimate", "") or state.get("company_employees", "")

    # Neue Pipeline-Felder für Sheet-Spalten
    validated = state.get("validated_brands") or []
    seen: set[str] = set()
    unique_brand_names: list[str] = []
    for b in validated:
        if not isinstance(b, dict):
            continue
        n = (b.get("name") or "").strip()
        key = n.lower()
        if n and key not in seen:
            seen.add(key)
            unique_brand_names.append(n)
    row_dict["_brands"] = ", ".join(unique_brand_names)[:500] or "—"

    revenue   = (state.get("company_revenue", "") or "—")[:50]
    employees = (state.get("company_employees", "") or "—")[:50]
    hq        = (state.get("company_hq", "") or "")[:60]
    model     = (state.get("business_model", "") or "")[:30]
    facts = f"Revenue: {revenue} · Employees: {employees}"
    if hq:    facts += f" · HQ: {hq}"
    if model: facts += f" · Model: {model}"
    row_dict["_firmenfakten"] = facts

    # Contact details for the sheet (English)
    title    = (state.get("contact_title") or "").strip()
    auth     = (state.get("contact_authority") or "other").strip().lower()
    li       = (state.get("linkedin_url") or "").strip()
    role     = bool(state.get("contact_role_match", False))
    contact_parts = []
    if title:    contact_parts.append(f"Title: {title}")
    contact_parts.append(f"Authority: {auth}")
    contact_parts.append(f"Role-match: {'yes' if role else 'no'}")
    if li:       contact_parts.append("LinkedIn: yes")
    row_dict["_contact_info"] = " · ".join(contact_parts)[:500]

    row_dict["_sentiment_target"] = state.get("pepper_target_summary", "") or "—"
    row_dict["_sentiment_cross"]  = state.get("pepper_cross_summary", "") or "—"
    row_dict["_sales_signals"]    = state.get("sales_signals", "") or ""
    row_dict["_score_breakdown"]  = state.get("score_breakdown", "") or ""
    row_dict["_score_override"]   = state.get("score_override", "") or ""

    # Commercial intelligence fields
    comm_parts = []
    spend  = (state.get("marketing_spend_estimate") or "").strip()
    perf   = (state.get("perf_mktg_signals") or "").strip()
    affil  = (state.get("affiliate_likelihood") or "").strip()
    promo  = (state.get("promo_intensity") or "").strip()
    if spend: comm_parts.append(f"Spend: {spend}")
    if perf:  comm_parts.append(f"PerfMktg: {perf}")
    if affil: comm_parts.append(f"Affiliate: {affil}")
    if promo: comm_parts.append(f"Promo: {promo}")
    row_dict["_commercial_intel"] = " · ".join(comm_parts)[:500] or "—"
    row_dict["_priority_tier"]    = (state.get("priority_tier") or "—").strip()

    processed.append(row_dict)
    logger.info(
        "collect_lead_result: '%s %s' @ '%s' → %s (score=%d)",
        row.vorname, row.nachname, row.firma, row.classification, row.score_total,
    )

    return {**state, "processed_leads": processed}


async def write_results_node(state: LeadState) -> LeadState:
    """
    Flush all processed leads to Google Sheets and send a Telegram summary.

    This node runs once at the end of the pipeline (after all leads have been
    processed).
    """
    processed: list[dict] = state.get("processed_leads", [])
    if not processed:
        logger.info("write_results: Keine neuen Leads zu schreiben")
        return {**state, "telegram_notified": False}

    # ── 1a. Append in 'Qualified Leads' (Audit-Tab, behält History) ──────────
    rows_to_write: list[list[str]] = []
    for lead_dict in processed:
        # Privat-Felder vor QualifiedLeadRow rausnehmen, sonst rejected pydantic
        clean = {k: v for k, v in lead_dict.items() if not k.startswith("_")}
        row_obj = QualifiedLeadRow(**clean)
        rows_to_write.append(row_obj.to_sheet_row())

    try:
        await append_qualified_leads(rows_to_write)
        logger.info("write_results: %d Zeile(n) in 'Qualified Leads' geschrieben", len(rows_to_write))
    except Exception as exc:
        logger.error("write_results: Fehler beim Schreiben in Google Sheets: %s", exc)
        return {
            **state,
            "telegram_notified": False,
            "errors": [*state.get("errors", []), f"Sheets-Schreibfehler: {exc}"],
        }

    # ── 1b. Validierungsspalten im Inbound-Tab pro Lead-Zeile aktualisieren ──
    try:
        await ensure_validation_columns()
    except Exception as exc:
        logger.warning("write_results: Validierungsspalten konnten nicht angelegt werden: %s", exc)

    today_iso = datetime.now(tz=_BERLIN).strftime("%Y-%m-%d")
    val_errors = 0
    val_written = 0
    for lead_dict in processed:
        row_idx = int(lead_dict.get("_row_index", 0) or 0)
        if row_idx < 2:
            continue

        brands         = str(lead_dict.get("_brands", "")).strip() or "—"
        priority_tier  = str(lead_dict.get("_priority_tier", "")).strip() or "—"
        score          = lead_dict.get("score_total", 0)
        classification = lead_dict.get("classification", "")

        # Merge target + cross-country Pepper sentiment into one cell
        t = str(lead_dict.get("_sentiment_target", "")).strip()
        x = str(lead_dict.get("_sentiment_cross", "")).strip()
        pepper_parts = [p for p in [t, x] if p and p != "—"]
        pepper = "\n".join(pepper_parts) or "—"

        # Merge company facts + contact + commercial intel into one context cell
        ctx_parts = [
            p for p in [
                str(lead_dict.get("_firmenfakten", "")).strip(),
                str(lead_dict.get("_contact_info", "")).strip(),
                str(lead_dict.get("_commercial_intel", "")).strip(),
            ] if p and p not in ("—", "")
        ]
        context = " | ".join(ctx_parts) or "—"

        # Note: skip-reason for FILTERED, else action + override + breakdown + signals
        if classification in ("FILTERED", "AGENCY"):
            note = lead_dict.get("pre_qualify_reason", "")
        else:
            action   = str(lead_dict.get("recommended_action", "")).strip()
            override = str(lead_dict.get("_score_override", "")).strip()
            brk      = str(lead_dict.get("_score_breakdown", "")).strip()
            signals  = str(lead_dict.get("_sales_signals", "")).strip()
            note = " | ".join(p for p in [action, override, brk, signals] if p)

        try:
            await write_validation_for_row(row_idx, {
                "Validation_Brands":         brands[:500],
                "Validation_Pepper":         pepper[:800],
                "Validation_Context":        context[:800],
                "Validation_Score":          f"{score}/100" if classification not in ("FILTERED", "AGENCY") else "—",
                "Validation_Classification": classification,
                "Validation_Priority_Tier":  priority_tier[:100],
                "Validation_Note":           note[:600],
                "Validation_Date":           today_iso,
            })
            val_written += 1
        except Exception as exc:
            val_errors += 1
            logger.warning("write_results: Validierung-Write für Zeile %d fehlgeschlagen: %s", row_idx, exc)

    logger.info(
        "write_results: %d Validierungs-Zeilen in Inbound-Tab geschrieben (%d Fehler)",
        val_written, val_errors,
    )

    # ── 2. Send Telegram summary ─────────────────────────────────────────────
    telegram_notified = False
    try:
        from app.bot.bot_context import get_bot
        from app.config import ADMIN_CHAT_ID

        bot = get_bot()
        if bot is None:
            logger.warning("write_results: Bot nicht verfügbar (get_bot() → None)")
        elif not ADMIN_CHAT_ID:
            logger.warning("write_results: ADMIN_CHAT_ID nicht gesetzt — kein Telegram-Report")
        else:
            # Include HOT/WARM/COLD and AGENCY leads in Telegram — skip FILTERED (spam/fake)
            qualified = [d for d in processed if d.get("classification") != "FILTERED"]
            filtered_count = len(processed) - len(qualified)
            if filtered_count:
                logger.info("write_results: %d FILTERED Leads nicht im Telegram-Report", filtered_count)

            lead_blocks = []
            for i, lead_dict in enumerate(qualified, 1):
                name = f"{lead_dict.get('vorname', '')} {lead_dict.get('nachname', '')}".strip()
                classification = lead_dict.get("classification", "")
                action = lead_dict.get("recommended_action", "") or lead_dict.get("pre_qualify_reason", "")
                block = TELEGRAM_LEAD_BLOCK_TEMPLATE.format(
                    idx=i,
                    name=name or "(unbekannt)",
                    firma=lead_dict.get("firma", ""),
                    classification=classification,
                    action=action,
                )
                lead_blocks.append(block)

            if not lead_blocks:
                logger.info("write_results: Alle Leads gefiltert — kein Telegram-Report")
                return {**state, "telegram_notified": False}

            full_message = TELEGRAM_SUMMARY_TEMPLATE.format(
                count=len(qualified),
                lead_blocks="\n\n".join(lead_blocks),
                sheet_id=INBOUND_SHEET_ID,
            )

            for chunk in _split_message(full_message):
                await bot.send_message(
                    chat_id=ADMIN_CHAT_ID,
                    text=chunk,
                    parse_mode="Markdown",
                    disable_web_page_preview=True,
                )

            telegram_notified = True
            logger.info(
                "write_results: Telegram-Report gesendet an chat_id=%s (%d Leads)",
                ADMIN_CHAT_ID, len(processed),
            )

            # Mark rows as notified in the in-memory list
            for lead_dict in processed:
                lead_dict["telegram_notified"] = "ja"

    except Exception as exc:
        logger.error("write_results: Telegram-Benachrichtigung fehlgeschlagen: %s", exc)

    return {**state, "telegram_notified": telegram_notified, "processed_leads": processed}
