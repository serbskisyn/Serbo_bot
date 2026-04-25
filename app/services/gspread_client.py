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

FARBEN_RGB: dict[str, tuple[float, float, float]] = {
    "Früh":        (1.0,   1.0,   1.0),
    "Spät":        (0.886, 0.937, 0.851),
    "Nacht":       (0.773, 0.353, 0.067),
    "Frei":        (0.847, 0.847, 0.847),
    "Urlaub":      (0.573, 0.816, 0.314),
    "krank":       (1.0,   0.0,   0.0),
    "BT":          (0.918, 0.820, 0.863),
    "Team":        (1.0,   0.851, 0.400),
    "Supervision": (1.0,   0.851, 0.400),
    "OFFEN-FD":    (1.0,   0.753, 0.0),
    "OFFEN-SD":    (1.0,   0.753, 0.0),
    "OFFEN-ND":    (1.0,   0.753, 0.0),
}


def _get_client() -> gspread.Client:
    json_str = os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON", "")
    if not json_str:
        raise EnvironmentError("GOOGLE_SERVICE_ACCOUNT_JSON nicht gesetzt")
    info = json.loads(json_str)
    creds = Credentials.from_service_account_info(info, scopes=SCOPES)
    return gspread.authorize(creds)


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
        ws = sh.add_worksheet(title=tab_name, rows=50, cols=30)

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
