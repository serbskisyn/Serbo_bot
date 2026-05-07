"""
KrankAgent — merged Sheet-Krankenstand mit manuellen Eingaben.
Gibt list[Abwesenheit] zurück.
"""
from __future__ import annotations
import logging
from app.services.schedule_builder import Abwesenheit
from app.config import SCHEDULE_KRANK_SHEET_ID

logger = logging.getLogger(__name__)


class KrankAgent:
    """
    Lädt den Krankenstand aus dem Sheet und merged ihn mit manuellen
    Eingaben aus dem Telegram-Dialog (bereits als list[Abwesenheit]).
    Dedupliziert per (name, datum).
    """

    def run(
        self,
        manuell: list[Abwesenheit] | None = None,
    ) -> tuple[list[Abwesenheit], str | None]:
        """
        Args:
            manuell:  Manuelle Krankmeldungen aus dem Dialog (kann None/leer sein)
        Returns:
            abwesenheiten:  Deduplizierte Gesamtliste (Sheet + manuell)
            error:          Fehlermeldung bei Sheet-Fehler, sonst None
        """
        manuell = manuell or []
        sheet_krank: list[Abwesenheit] = []
        error: str | None = None

        if SCHEDULE_KRANK_SHEET_ID:
            try:
                from app.services.gspread_client import read_krankenstand
                sheet_krank = read_krankenstand(SCHEDULE_KRANK_SHEET_ID, "Krankenstand")
                logger.info("KrankAgent: %d Einträge aus Sheet", len(sheet_krank))
            except Exception as e:
                logger.warning("KrankAgent: Sheet nicht ladbar: %s", e)
                error = str(e)

        # Deduplizieren: (name, datum) als Key
        seen: set[tuple[str, object]] = set()
        merged: list[Abwesenheit] = []
        for ab in sheet_krank + manuell:
            key = (ab.name, ab.datum)
            if key not in seen:
                seen.add(key)
                merged.append(ab)

        logger.info(
            "KrankAgent: %d gesamt (%d Sheet, %d manuell)",
            len(merged), len(sheet_krank), len(manuell),
        )
        return merged, error
