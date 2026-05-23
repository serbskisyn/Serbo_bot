"""
sheets.py — Google Sheets access for the Lead Qualifying Agent.

Reads the Inbound tab and writes to the Qualified Leads tab in the same
spreadsheet. Uses the same _get_client() pattern from app/services/gspread_client.py.
"""
from __future__ import annotations

import asyncio
import logging
from functools import partial
from typing import Any

import gspread

from app.agents.lead_qualifying.schemas import QualifiedLeadRow

logger = logging.getLogger(__name__)

INBOUND_SHEET_ID = "1U_SRfQG-5NqnAHbZt6g2RMKCTtazs99VHEHwO_G15Fk"
INBOUND_TAB_NAME = "Inbound"
QUALIFIED_TAB_NAME = "Qualified Leads"

# Canonical column headers for the Qualified Leads tab
QUALIFIED_COLUMNS = QualifiedLeadRow.COLUMNS

# Validation columns appended to the Inbound tab (header names).
# Neue Pipeline (2026-05-20 Refactor): Brand-Discovery + Multi-Country-Pepper.
# Bestehende Spalten werden beibehalten, neue ergänzt.
VALIDATION_COLUMNS: list[str] = [
    "Validation_Company_Employees",          # Employee-count estimate
    "Validation_Brands",                    # eCommerce brands (comma-sep)
    "Validation_Company_Facts",             # Revenue, employees, HQ, model
    "Validation_Contact",                   # Title, authority, role-match, LinkedIn
    "Validation_Sentiment_Target",          # Pepper RAG-compact for target country
    "Validation_Sentiment_Cross",           # All countries with Pepper activity
    "Validation_Sentiment",                 # Legacy: overall Pepper summary
    "Validation_Commercial_Intel",          # Marketing spend, perf-mktg, affiliate signals
    "Validation_Priority_Tier",             # LOW / MEDIUM / HIGH / STRATEGIC
    "Validation_Score",                     # Score 0-100
    "Validation_Classification",            # HOT / WARM / COLD / FILTERED
    "Validation_Note",                      # Recommended action + breakdown + signals
    "Validation_Date",                      # ISO date, also idempotency marker
]


# Maps real Inbound headers → canonical fields the pipeline uses.
# "Name" wird in Vorname/Nachname gesplittet (siehe _map_inbound_row).
_FIELD_MAP: dict[str, str] = {
    "Email Address": "E-Mail",
    "Company Name":  "Firma",
    "Company website":          "company_website_raw",
    "Company industry/category": "Industry",
    "Partnership goals":        "Partnership_Goals",
    "Target country":           "Quelle",     # genutzt als grobe Quelle/Region
    "Phone number":             "Telefon",
    "Message":                  "Message",
    "Date":                     "Datum",
    "Status":                   "Status",
}


def _split_name(full: str) -> tuple[str, str]:
    full = full.strip()
    if not full:
        return "", ""
    parts = full.split(maxsplit=1)
    return (parts[0], parts[1] if len(parts) > 1 else "")


def _map_inbound_row(row: dict[str, str], row_index: int) -> dict[str, str]:
    """Map real Inbound column names to the pipeline-internal field names."""
    vorname, nachname = _split_name(str(row.get("Name", "")))
    # Idempotency: prefer the new Validation_Date column, fall back to the legacy
    # Validierung_Datum for already-processed leads from earlier runs.
    val_date = (str(row.get("Validation_Date", "")).strip()
                or str(row.get("Validierung_Datum", "")).strip())
    mapped: dict[str, str] = {
        "Vorname":  vorname,
        "Nachname": nachname,
        "_row_index": row_index,            # 1-based (header = 1, first data row = 2)
        "_validierung_datum": val_date,
    }
    for src, dst in _FIELD_MAP.items():
        if src in row:
            mapped[dst] = str(row.get(src, "")).strip()
    return mapped


def _get_client() -> gspread.Client:
    """Re-uses the same credential loading logic as gspread_client.py."""
    from app.services.gspread_client import _get_client as _base_get_client
    return _base_get_client()


def _ensure_qualified_tab(sh: gspread.Spreadsheet) -> gspread.Worksheet:
    """Return the Qualified Leads worksheet, creating it with a header row if absent."""
    titles = {ws.title for ws in sh.worksheets()}
    if QUALIFIED_TAB_NAME not in titles:
        logger.info("Erstelle Tab '%s'", QUALIFIED_TAB_NAME)
        ws = sh.add_worksheet(title=QUALIFIED_TAB_NAME, rows=1000, cols=len(QUALIFIED_COLUMNS) + 2)
        ws.update("A1", [QUALIFIED_COLUMNS])
        logger.info("Header-Zeile geschrieben in '%s'", QUALIFIED_TAB_NAME)
    else:
        ws = sh.worksheet(QUALIFIED_TAB_NAME)
        # Ensure header is present; if the sheet is empty, write it
        existing = ws.row_values(1)
        if not existing or existing[0] != "lead_key":
            ws.update("A1", [QUALIFIED_COLUMNS])
            logger.info("Header-Zeile nachgetragen in '%s'", QUALIFIED_TAB_NAME)
    return ws


def _sync_read_inbound() -> list[dict[str, str]]:
    """Read all rows from the Inbound tab as a list of dicts with canonical field names.

    - Mappt 'Name' → Vorname+Nachname-Split.
    - Mappt reale Header → 'Firma', 'E-Mail', etc. (siehe _FIELD_MAP).
    - Hängt '_row_index' an jede Zeile (1-basiert, Header = Zeile 1).
    """
    client = _get_client()
    sh = client.open_by_key(INBOUND_SHEET_ID)
    ws = sh.worksheet(INBOUND_TAB_NAME)
    raw_rows = ws.get_all_records(default_blank="")
    mapped = [_map_inbound_row(row, idx) for idx, row in enumerate(raw_rows, start=2)]
    logger.info("Inbound Tab: %d Zeilen gelesen (gemappt)", len(mapped))
    return mapped


def _sync_ensure_validation_columns() -> dict[str, int]:
    """Stellt sicher, dass alle Validierungsspalten im Inbound-Header existieren.

    Returns: Dict {VALIDATION_COLUMN_NAME: 1-basierte_spalten_id}.
    """
    client = _get_client()
    sh = client.open_by_key(INBOUND_SHEET_ID)
    ws = sh.worksheet(INBOUND_TAB_NAME)
    header = ws.row_values(1)

    existing: dict[str, int] = {h.strip(): i + 1 for i, h in enumerate(header) if h.strip()}
    missing = [c for c in VALIDATION_COLUMNS if c not in existing]

    if missing:
        from gspread.utils import rowcol_to_a1
        start_col = len(header) + 1
        needed_cols = start_col + len(missing) - 1
        if needed_cols > ws.col_count:
            ws.resize(rows=ws.row_count, cols=needed_cols + 4)  # +4 buffer
            logger.info("Inbound-Tab: Grid auf %d Spalten erweitert", needed_cols + 4)
        ws.update(rowcol_to_a1(1, start_col), [missing])
        for offset, name in enumerate(missing):
            existing[name] = start_col + offset
        logger.info("Inbound-Tab: %d Validierungsspalte(n) angelegt (%s)", len(missing), missing)

    return {c: existing[c] for c in VALIDATION_COLUMNS}


def _sync_write_validation_for_row(row_index: int, values: dict[str, str]) -> None:
    """Schreibt Validierungs-Felder in eine bestimmte Inbound-Zeile (row_index 1-basiert)."""
    if row_index < 2:
        logger.warning("write_validation_for_row: ungültiger row_index=%s", row_index)
        return
    col_map = _sync_ensure_validation_columns()
    from gspread.utils import rowcol_to_a1
    client = _get_client()
    sh = client.open_by_key(INBOUND_SHEET_ID)
    ws = sh.worksheet(INBOUND_TAB_NAME)

    # Zellweise statt batch_update, weil Spalten meist nicht zusammenhängen.
    updates = []
    for col_name, value in values.items():
        col_idx = col_map.get(col_name)
        if col_idx is None:
            logger.warning("write_validation_for_row: unbekannte Spalte '%s' ignoriert", col_name)
            continue
        updates.append({
            "range": rowcol_to_a1(row_index, col_idx),
            "values": [[str(value)]],
        })
    if updates:
        ws.batch_update(updates, value_input_option="USER_ENTERED")
        logger.debug("Inbound-Zeile %d: %d Validierungswerte geschrieben", row_index, len(updates))


def _sync_read_existing_keys() -> set[str]:
    """Return all lead_key values already present in the Qualified Leads tab."""
    client = _get_client()
    sh = client.open_by_key(INBOUND_SHEET_ID)
    ws = _ensure_qualified_tab(sh)
    all_values = ws.get_all_values()
    if len(all_values) <= 1:
        return set()
    # lead_key is column 0
    return {row[0].strip() for row in all_values[1:] if row and row[0].strip()}


def _sync_append_rows(rows: list[list[str]]) -> None:
    """Append processed lead rows to the Qualified Leads tab."""
    if not rows:
        return
    client = _get_client()
    sh = client.open_by_key(INBOUND_SHEET_ID)
    ws = _ensure_qualified_tab(sh)
    ws.append_rows(rows, value_input_option="USER_ENTERED")
    logger.info("Qualified Leads: %d Zeile(n) geschrieben", len(rows))


# ---------------------------------------------------------------------------
# Async wrappers — gspread is synchronous; we run in a thread executor.
# ---------------------------------------------------------------------------

async def read_inbound_leads() -> list[dict[str, str]]:
    """Async wrapper: read all rows from the Inbound tab."""
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, _sync_read_inbound)


async def read_existing_lead_keys() -> set[str]:
    """Async wrapper: return existing lead keys from Qualified Leads tab."""
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, _sync_read_existing_keys)


async def append_qualified_leads(rows: list[list[str]]) -> None:
    """Async wrapper: append rows to Qualified Leads tab."""
    loop = asyncio.get_running_loop()
    await loop.run_in_executor(None, partial(_sync_append_rows, rows))


async def ensure_validation_columns() -> dict[str, int]:
    """Async wrapper: stellt sicher, dass Validierungsspalten im Inbound-Header existieren."""
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, _sync_ensure_validation_columns)


async def write_validation_for_row(row_index: int, values: dict[str, str]) -> None:
    """Async wrapper: schreibt Validierungswerte in eine spezifische Inbound-Zeile."""
    loop = asyncio.get_running_loop()
    await loop.run_in_executor(None, partial(_sync_write_validation_for_row, row_index, values))
