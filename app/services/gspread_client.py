"""
gspread_client.py — Google Sheets lesen/schreiben für Dienstplan-Agent
"""
from __future__ import annotations

import json
import logging
import os
from datetime import date
from typing import TYPE_CHECKING

import gspread
from google.oauth2.service_account import Credentials

if TYPE_CHECKING:
    from app.services.schedule_builder import Abwesenheit, Dienst

logger = logging.getLogger(__name__)

SCOPES = [
    "https://spreadsheets.google.com/feeds",
    "https://www.googleapis.com/auth/drive",
]

# Pfad zur credentials.json Datei (Fallback wenn ENV-Variable kaputt)
_CREDENTIALS_FILE = os.path.join(
    os.path.dirname(__file__), "..", "..", "credentials.json"
)

# RGB-Farben für jede Schichtart (0.0–1.0)
FARBEN_RGB: dict[str, tuple[float, float, float]] = {
    "Früh":        (1.000, 1.000, 1.000),   # Weiß
    "Spät":        (0.698, 0.875, 0.604),   # Hellgrün
    "Nacht":       (0.773, 0.353, 0.067),   # Dunkelorange
    "Frei":        (0.847, 0.847, 0.847),   # Hellgrau
    "Urlaub":      (0.573, 0.816, 0.314),   # Grün
    "krank":       (0.918, 0.196, 0.196),   # Rot
    "BT":          (0.918, 0.820, 0.863),   # Rosa
    "Team":        (1.000, 0.851, 0.400),   # Gelb
    "Supervision": (1.000, 0.851, 0.400),   # Gelb
    "OFFEN-FD":    (1.000, 0.600, 0.000),   # Kräftiges Orange
    "OFFEN-SD":    (1.000, 0.600, 0.000),   # Kräftiges Orange
    "OFFEN-ND":    (0.800, 0.200, 0.000),   # Dunkelrot-Orange
}


def _get_client() -> gspread.Client:
    """Erstellt einen gspread-Client.
    Reihenfolge:
    1. GOOGLE_SERVICE_ACCOUNT_JSON Umgebungsvariable (JSON-String)
    2. credentials.json Datei im Projektroot
    """
    json_str = os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON", "").strip()

    if json_str:
        try:
            info = json.loads(json_str)
            if "private_key" in info:
                info["private_key"] = info["private_key"].replace("\\n", "\n")
            creds = Credentials.from_service_account_info(info, scopes=SCOPES)
            return gspread.authorize(creds)
        except Exception as e:
            logger.warning("GOOGLE_SERVICE_ACCOUNT_JSON ungueltig (%s), versuche credentials.json", e)

    creds_path = os.path.abspath(_CREDENTIALS_FILE)
    if os.path.exists(creds_path):
        logger.info("Nutze credentials.json: %s", creds_path)
        creds = Credentials.from_service_account_file(creds_path, scopes=SCOPES)
        return gspread.authorize(creds)

    raise EnvironmentError(
        "Kein Google-Credential gefunden. "
        "Setze GOOGLE_SERVICE_ACCOUNT_JSON oder lege credentials.json ins Projektroot."
    )


def _resolve_tab_name(sh: gspread.Spreadsheet, base_name: str) -> tuple[str, bool]:
    """Gibt den finalen Tab-Namen zurück und ob er neu angelegt werden muss.

    Logik:
    - Existiert base_name noch nicht  → (base_name, neu=True)
    - Existiert base_name bereits     → versuche base_name-1, base_name-2, …
      bis ein freier Name gefunden wird

    Beispiel:
      Mai_2026 existiert       → Mai_2026-1
      Mai_2026-1 existiert     → Mai_2026-2
      Mai_2026-2 existiert     → Mai_2026-3  usw.
    """
    vorhandene = {ws.title for ws in sh.worksheets()}

    if base_name not in vorhandene:
        return base_name, True

    counter = 1
    while True:
        candidate = f"{base_name}-{counter}"
        if candidate not in vorhandene:
            return candidate, True
        counter += 1


def read_abwesenheiten(spreadsheet_id: str, tab_name: str = "Urlaub_CLI") -> list["Abwesenheit"]:
    """
    Liest Abwesenheiten aus Google Sheets.
    Erwartet Spalten: Name | Art (U/F/K) | Datum (YYYY-MM-DD oder DD.MM.YYYY)
    """
    from datetime import datetime
    from app.services.schedule_builder import Abwesenheit

    client = _get_client()
    sh = client.open_by_key(spreadsheet_id)
    ws = sh.worksheet(tab_name)
    rows = ws.get_all_values()

    result = []
    for row in rows[1:]:
        if len(row) < 3 or not row[0].strip():
            continue
        name, art, datum_raw = row[0].strip(), row[1].strip().upper(), row[2].strip()
        if not datum_raw:
            continue
        datum = None
        for fmt in ("%Y-%m-%d", "%d.%m.%Y", "%d.%m.%y"):
            try:
                datum = datetime.strptime(datum_raw, fmt).date()
                break
            except ValueError:
                continue
        if datum is None:
            logger.warning("Unbekanntes Datumsformat: %s", datum_raw)
            continue
        result.append(Abwesenheit(name=name, art=art, datum=datum))

    logger.info("Abwesenheiten geladen: %d Einträge", len(result))
    return result


def write_dienstplan(
    spreadsheet_id: str,
    plan:           dict[str, dict[date, "Dienst"]],
    mitarbeiter:    list[str],
    tage:           list[date],
    tab_name:       str | None = None,
) -> str:
    monate_de = ["", "Jan", "Feb", "Mär", "Apr", "Mai", "Jun",
                 "Jul", "Aug", "Sep", "Okt", "Nov", "Dez"]
    erster = tage[0]

    base_name = tab_name if tab_name else f"{monate_de[erster.month]}_{erster.year}"

    client = _get_client()
    sh = client.open_by_key(spreadsheet_id)

    # Finalen Tab-Namen ermitteln: neues Blatt wenn Monat bereits existiert
    final_name, is_new = _resolve_tab_name(sh, base_name)

    if is_new:
        ws = sh.add_worksheet(title=final_name, rows=50, cols=35)
        logger.info("Neues Tabellenblatt angelegt: '%s'", final_name)
    else:
        # Sollte durch _resolve_tab_name nie eintreten, Fallback-Sicherheit
        ws = sh.worksheet(final_name)
        ws.clear()
        logger.info("Vorhandenes Tabellenblatt gecleart: '%s'", final_name)

    if final_name != base_name:
        logger.info(
            "Tab '%s' existierte bereits → neuer Plan als '%s' gespeichert",
            base_name, final_name,
        )

    # Kopfzeile
    header1 = ["Tag"] + mitarbeiter + ["offen", "Tag"]
    ws.update("A1", [header1])

    dienstart_row = [""] + ["Dienstart"] * len(mitarbeiter) + ["Dienstart", ""]
    ws.update("A3", [dienstart_row])

    wochentage_de = ["Mo", "Di", "Mi", "Do", "Fr", "Sa", "So"]
    data_rows = []
    for tag in tage:
        wt = wochentage_de[tag.weekday()]
        datum_str = f"{wt}, {tag.strftime('%d. %b.')}"
        row = [datum_str]
        for ma_name in mitarbeiter:
            d = plan.get(ma_name, {}).get(tag)
            row.append(d.value if d else "Frei")
        offen_val = plan.get("offen", {}).get(tag)
        row.append(offen_val.value if offen_val else "")
        row.append(datum_str)
        data_rows.append(row)

    ws.update("A4", data_rows)

    # Hintergrundfarben setzen
    requests = []
    ma_col_map = {ma: i + 2 for i, ma in enumerate(mitarbeiter)}
    ma_col_map["offen"] = len(mitarbeiter) + 2

    for row_idx, tag in enumerate(tage):
        sheet_row = 4 + row_idx
        for ma_name in list(mitarbeiter) + ["offen"]:
            col_idx = ma_col_map.get(ma_name)
            if col_idx is None:
                continue
            d = plan.get(ma_name, {}).get(tag)
            val = d.value if d else "Frei"
            rgb = FARBEN_RGB.get(val, (1.0, 1.0, 1.0))
            requests.append(_bg_request(ws.id, sheet_row - 1, col_idx - 1, *rgb))

        if tag.weekday() >= 5:
            for col in [0, len(mitarbeiter) + 2]:
                requests.append(_bg_request(ws.id, sheet_row - 1, col, 0.95, 0.95, 0.95))

    if requests:
        sh.batch_update({"requests": requests})

    logger.info("Dienstplan '%s' geschrieben (%d Tage)", final_name, len(tage))
    return final_name


def _bg_request(sheet_id: int, row: int, col: int, r: float, g: float, b: float) -> dict:
    return {
        "updateCells": {
            "range": {
                "sheetId": sheet_id,
                "startRowIndex": row,
                "endRowIndex": row + 1,
                "startColumnIndex": col,
                "endColumnIndex": col + 1,
            },
            "rows": [{"values": [{"userEnteredFormat": {
                "backgroundColor": {"red": r, "green": g, "blue": b}
            }}]}],
            "fields": "userEnteredFormat.backgroundColor",
        }
    }
