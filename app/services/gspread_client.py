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
# Früh  = Weiß        | Spät   = Hellgrün   | Nacht = Dunkelorange
# Frei  = Hellgrau    | Urlaub = Grün        | krank = Rot
# BT    = Rosa        | Team   = Gelb         | OFFEN = Kräftiges Orange
FARBEN_RGB: dict[str, tuple[float, float, float]] = {
    "Früh":        (1.000, 1.000, 1.000),   # Weiß
    "Spät":        (0.698, 0.875, 0.604),   # Hellgrün (kräftiger als vorher)
    "Nacht":       (0.773, 0.353, 0.067),   # Dunkelorange
    "Frei":        (0.847, 0.847, 0.847),   # Hellgrau
    "Urlaub":      (0.573, 0.816, 0.314),   # Grün
    "krank":       (0.918, 0.196, 0.196),   # Rot (etwas weicher)
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
            # Sicherstellen dass private_key korrekte Newlines hat
            if "private_key" in info:
                info["private_key"] = info["private_key"].replace("\\n", "\n")
            creds = Credentials.from_service_account_info(info, scopes=SCOPES)
            return gspread.authorize(creds)
        except Exception as e:
            logger.warning("GOOGLE_SERVICE_ACCOUNT_JSON ungueltig (%s), versuche credentials.json", e)

    # Fallback: direkt credentials.json lesen
    creds_path = os.path.abspath(_CREDENTIALS_FILE)
    if os.path.exists(creds_path):
        logger.info("Nutze credentials.json: %s", creds_path)
        creds = Credentials.from_service_account_file(creds_path, scopes=SCOPES)
        return gspread.authorize(creds)

    raise EnvironmentError(
        "Kein Google-Credential gefunden. "
        "Setze GOOGLE_SERVICE_ACCOUNT_JSON oder lege credentials.json ins Projektroot."
    )


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
    if tab_name is None:
        monate_de = ["", "Jan", "Feb", "Mär", "Apr", "Mai", "Jun",
                     "Jul", "Aug", "Sep", "Okt", "Nov", "Dez"]
        erster = tage[0]
        tab_name = f"{monate_de[erster.month]}_{erster.year}"

    client = _get_client()
    sh = client.open_by_key(spreadsheet_id)

    try:
        ws = sh.worksheet(tab_name)
        ws.clear()
    except gspread.WorksheetNotFound:
        ws = sh.add_worksheet(title=tab_name, rows=50, cols=35)

    # Kopfzeile: Namen der Mitarbeiter
    header1 = ["Tag"] + mitarbeiter + ["offen", "Tag"]
    ws.update("A1", [header1])

    # Leerzeile 2, Dienstart-Label in Zeile 3
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

        # Wochenende-Zeilen leicht grau hinterlegen (Spalte A und letzte Spalte)
        if tag.weekday() >= 5:
            for col in [0, len(mitarbeiter) + 2]:  # Spalte A und offen+1
                requests.append(_bg_request(ws.id, sheet_row - 1, col, 0.95, 0.95, 0.95))

    if requests:
        sh.batch_update({"requests": requests})

    logger.info("Dienstplan '%s' geschrieben (%d Tage)", tab_name, len(tage))
    return tab_name


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
