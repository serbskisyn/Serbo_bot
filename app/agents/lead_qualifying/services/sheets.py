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
    """Read all rows from the Inbound tab as a list of dicts."""
    client = _get_client()
    sh = client.open_by_key(INBOUND_SHEET_ID)
    ws = sh.worksheet(INBOUND_TAB_NAME)
    rows = ws.get_all_records(default_blank="")
    logger.info("Inbound Tab: %d Zeilen gelesen", len(rows))
    return rows  # type: ignore[return-value]


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
