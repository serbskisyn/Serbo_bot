"""
VormonatAgent — lädt den Plan des Vormonats aus Google Sheets.
Gibt dict[str, dict[date, Dienst]] zurück.
"""
from __future__ import annotations
import logging
from datetime import date
from app.services.schedule_builder import Dienst
from app.config import SCHEDULE_OUTPUT_SHEET_ID

logger = logging.getLogger(__name__)


class VormonatAgent:
    """
    Liest den letzten Monatstab aus dem Output-Sheet.
    Gibt leeres Dict zurück wenn kein Tab gefunden oder Sheet nicht erreichbar.
    """

    def run(
        self,
        jahr: int,
        monat: int,
    ) -> tuple[dict[str, dict[date, Dienst]], str | None]:
        """
        Args:
            jahr, monat:  Zielmonat (Vormonat wird automatisch berechnet)
        Returns:
            vormonat_plan:  Plan des Vormonats {ma_name: {date: Dienst}}
            info:           Info-/Fehlermeldung, None wenn alles ok
        """
        erster_des_monats = date(jahr, monat, 1)
        try:
            from app.services.gspread_client import read_vormonat_plan
            plan = read_vormonat_plan(SCHEDULE_OUTPUT_SHEET_ID, erster_des_monats)
            if plan:
                logger.info("VormonatAgent: Plan geladen (%d MA)", len(plan))
                return plan, None
            else:
                logger.info("VormonatAgent: Kein Vormonats-Tab gefunden")
                return {}, "Kein Vormonats-Tab gefunden — Plan ohne Vormonat."
        except Exception as e:
            logger.warning("VormonatAgent: Fehler: %s", e)
            return {}, str(e)
