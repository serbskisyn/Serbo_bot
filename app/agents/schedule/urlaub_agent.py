"""
UrlaubAgent — lädt Urlaubseinträge aus dem Google Sheet.
Gibt list[Abwesenheit] zurück.
"""
from __future__ import annotations
import logging
from app.services.schedule_builder import Abwesenheit
from app.config import SCHEDULE_URLAUB_SHEET_ID

logger = logging.getLogger(__name__)


class UrlaubAgent:
    """
    Liest Urlaubsdaten aus dem Sheet (Tab 'Urlaub_CLI').
    Gibt leere Liste + Fehlermeldung zurück wenn Sheet nicht erreichbar.
    """

    def run(self) -> tuple[list[Abwesenheit], str | None]:
        """
        Returns:
            abwesenheiten:  Liste aller Urlaubseinträge
            error:          Fehlermeldung bei Fehler, sonst None
        """
        try:
            from app.services.gspread_client import read_abwesenheiten
            ab_urlaub = read_abwesenheiten(SCHEDULE_URLAUB_SHEET_ID, "Urlaub_CLI")
            logger.info("UrlaubAgent: %d Einträge geladen", len(ab_urlaub))
            return ab_urlaub, None
        except Exception as e:
            logger.warning("UrlaubAgent: Sheet nicht ladbar: %s", e)
            return [], str(e)
