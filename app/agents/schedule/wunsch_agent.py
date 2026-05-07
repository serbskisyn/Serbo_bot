"""
WunschAgent — lädt und validiert Wunschschichten aus Google Sheets.
Gibt list[Wunschschicht] zurück.
"""
from __future__ import annotations
import logging
from app.services.schedule_builder import Wunschschicht
from app.config import SCHEDULE_WUNSCH_SHEET_ID

logger = logging.getLogger(__name__)


class WunschAgent:
    """
    Liest Wunschschichten aus dem Formular-Sheet und filtert
    nach Monat/Jahr sowie bekannten MA-Namen.
    """

    def run(
        self,
        monat: int,
        jahr: int,
        bekannte_namen: set[str],
    ) -> tuple[list[Wunschschicht], str | None]:
        """
        Args:
            monat, jahr:      Zielmonat
            bekannte_namen:   Set der MA-Namen (für Validierung)
        Returns:
            wunschschichten:  Gefilterte Wunschschichten
            error:            Fehlermeldung bei Fehler, sonst None
        """
        try:
            from app.services.gspread_client import read_wunschschichten
            wuensche = read_wunschschichten(
                spreadsheet_id=SCHEDULE_WUNSCH_SHEET_ID,
                tab_name="Formularantworten 1",
                monat=monat,
                jahr=jahr,
                bekannte_namen=bekannte_namen,
            )
            logger.info("WunschAgent: %d Wünsche geladen", len(wuensche))
            return wuensche, None
        except Exception as e:
            logger.warning("WunschAgent: Sheet nicht ladbar: %s", e)
            return [], str(e)
