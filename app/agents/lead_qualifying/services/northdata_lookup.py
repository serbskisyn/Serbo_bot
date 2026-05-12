"""
northdata_lookup.py — Northdata API stub.

The NORTHDATA_API_KEY is not yet available. This module logs a warning and
returns an empty summary. Implement the actual API call once the key arrives.

Northdata API docs: https://www.northdata.de/doc/api
"""
from __future__ import annotations

import logging

from app import config

logger = logging.getLogger(__name__)


async def get_company_summary(firma: str) -> str:
    """
    Query Northdata for company information.

    Currently a stub — returns an empty string and logs a warning when
    NORTHDATA_API_KEY is not configured.
    """
    api_key = config.NORTHDATA_API_KEY or None
    if not api_key:
        logger.warning(
            "NORTHDATA_API_KEY nicht gesetzt — Northdata-Lookup für '%s' übersprungen. "
            "Bitte NORTHDATA_API_KEY in .env eintragen, sobald der Key verfügbar ist.",
            firma,
        )
        return ""

    # TODO: Implement when API key is available.
    # Likely endpoint: GET https://www.northdata.de/data/v1/company
    # with query params: name=<firma>, apiKey=<api_key>
    # Returns JSON with financials, legal form, address, register data etc.
    logger.info("Northdata-Lookup für '%s' — API key vorhanden, aber Implementierung ausstehend", firma)
    return ""
