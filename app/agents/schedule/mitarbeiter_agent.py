"""
MitarbeiterAgent — lädt MA-Liste aus Sheet, erkennt Springer.
Gibt list[Mitarbeiter] zurück.
"""
from __future__ import annotations
import logging
from app.services.schedule_builder import Mitarbeiter
from app.config import SCHEDULE_OUTPUT_SHEET_ID

logger = logging.getLogger(__name__)

MITARBEITER_FALLBACK: dict[str, float] = {
    "Heike":     7.0,
    "Silke":     8.0,
    "Ariane":    7.0,
    "Jasmin":    7.0,
    "Maria":     7.0,
    "Linus":     7.0,
    "Celina":    6.0,
    "Geraldine": 8.0,
    "Svitlana":  7.0,
    "Elvira":    7.0,
    "Romy":      6.0,
    "Annika":    7.0,
}


class MitarbeiterAgent:
    """
    Lädt die Mitarbeiterliste aus dem Google Sheet (Tab 'Mitarbeiterübersicht').
    Fällt auf die Fallback-Liste zurück wenn das Sheet nicht erreichbar ist.
    """

    def run(self) -> tuple[list[Mitarbeiter], list[str], str | None]:
        """
        Returns:
            ma_liste:       Liste aller Mitarbeiter (inkl. Springer)
            springer_namen: Namen der Springer (tagesstunden == 0)
            error:          Fehlermeldung wenn Fallback aktiv, sonst None
        """
        try:
            from app.services.gspread_client import read_mitarbeiter
            ma_liste = read_mitarbeiter(SCHEDULE_OUTPUT_SHEET_ID, "Mitarbeiterübersicht")
            springer_namen = [ma.name for ma in ma_liste if ma.ist_springer]
            logger.info("MitarbeiterAgent: %d MA geladen", len(ma_liste))
            return ma_liste, springer_namen, None
        except Exception as e:
            logger.warning("MitarbeiterAgent: Sheet nicht ladbar (%s) — Fallback aktiv", e)
            ma_liste = [
                Mitarbeiter(name=name, tagesstunden=std)
                for name, std in MITARBEITER_FALLBACK.items()
            ]
            springer_namen = [ma.name for ma in ma_liste if ma.ist_springer]
            return ma_liste, springer_namen, str(e)
