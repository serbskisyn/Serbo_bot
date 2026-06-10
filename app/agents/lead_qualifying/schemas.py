"""
schemas.py — Pydantic schemas for lead data validation and serialisation.
"""
from __future__ import annotations

from datetime import datetime
from typing import ClassVar, Literal

from pydantic import BaseModel, Field


class InboundLead(BaseModel):
    """Represents one row from the Inbound Google Sheet tab."""

    vorname: str = Field(default="", alias="Vorname")
    nachname: str = Field(default="", alias="Nachname")
    firma: str = Field(default="", alias="Firma")
    email: str = Field(default="", alias="E-Mail")
    quelle: str = Field(default="", alias="Quelle")

    model_config = {"populate_by_name": True}

    @property
    def display_name(self) -> str:
        parts = [self.vorname, self.nachname]
        return " ".join(p for p in parts if p).strip() or "(unbekannt)"

    @property
    def lead_key_raw(self) -> str:
        """Raw string used to compute the SHA-256 lead key."""
        return "|".join([self.vorname, self.nachname, self.firma, self.email])


class QualifiedLeadRow(BaseModel):
    """One fully-processed lead row ready to be written to the sheet."""

    # 'extra=allow' damit _row_index, pepper_summary etc. mitgespeichert werden können,
    # ohne dass to_sheet_row() sie ausgibt (das nutzt nur COLUMNS).
    model_config = {"extra": "allow"}

    lead_key: str
    processed_at: str = Field(default_factory=lambda: datetime.now(tz=__import__("zoneinfo").ZoneInfo("Europe/Berlin")).strftime("%Y-%m-%dT%H:%M:%S"))

    # Identity
    vorname: str = ""
    nachname: str = ""
    firma: str = ""
    email: str = ""
    quelle: str = ""

    # Enrichment
    contact_title: str = ""
    linkedin_url: str = ""
    company_website: str = ""

    # Pre-qualification (raw-data signal, set before enrichment)
    pre_qualify_label: str = ""    # HIGH / LOW / SKIP
    pre_qualify_reason: str = ""   # 1-sentence explanation

    # Qualification
    score_total: int = 0
    classification: Literal["HOT", "WARM", "COLD", "FILTERED", "AGENCY", ""] = ""
    recommended_action: str = ""

    # Bookkeeping
    telegram_notified: str = "nein"

    # Column order must match the sheet header exactly
    COLUMNS: ClassVar[list[str]] = [
        "lead_key", "processed_at", "vorname", "nachname", "firma", "email",
        "quelle", "pre_qualify_label", "pre_qualify_reason",
        "contact_title", "linkedin_url", "company_website",
        "score_total", "classification", "recommended_action", "telegram_notified",
    ]

    def to_sheet_row(self) -> list[str]:
        """Return values in the canonical column order for the sheet."""
        return [str(getattr(self, col, "")) for col in self.COLUMNS]
