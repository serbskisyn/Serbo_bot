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
    from app.services.schedule_builder import Abwesenheit, Dienst, Wunschschicht

logger = logging.getLogger(__name__)

SCOPES = [
    "https://spreadsheets.google.com/feeds",
    "https://www.googleapis.com/auth/drive",
]

_CREDENTIALS_FILE = os.path.join(
    os.path.dirname(__file__), "..", "..", "credentials.json"
)

FARBEN_RGB: dict[str, tuple[float, float, float]] = {
    "Früh":        (1.000, 1.000, 1.000),
    "Spät":        (0.698, 0.875, 0.604),
    "Nacht":       (0.773, 0.353, 0.067),
    "Frei":        (0.847, 0.847, 0.847),
    "Urlaub":      (0.573, 0.816, 0.314),
    "krank":       (0.918, 0.196, 0.196),
    "BT":          (0.918, 0.820, 0.863),
    "Team":        (1.000, 0.851, 0.400),
    "Supervision": (1.000, 0.851, 0.400),
    "OFFEN-FD":    (1.000, 0.600, 0.000),
    "OFFEN-SD":    (1.000, 0.600, 0.000),
    "OFFEN-ND":    (0.800, 0.200, 0.000),
}


def _get_client() -> gspread.Client:
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


def read_wunschschichten(
    spreadsheet_id: str,
    tab_name: str = "Wunschschichten",
) -> list["Wunschschicht"]:
    """
    Liest Wunschschichten aus Google Sheets.

    Spalten-Layout (1-basiert):
      B  = Name des Mitarbeiters
      E  = Wunschtag 1  (Zahl: Tag des Monats, z.B. "5")
      F  = Schichtart 1 ("FD", "SD", "ND" oder "Früh", "Spät", "Nacht")
      G  = Wunschtag 2
      H  = Schichtart 2
      I  = Wunschtag 3
      J  = Schichtart 3

    Leere Zellen werden übersprungen.
    Gibt eine Liste von Wunschschicht-Objekten zurück.
    """
    from datetime import datetime
    from app.services.schedule_builder import Wunschschicht

    # Normierung: verschiedene Schreibweisen → interner Dienst-String
    SCHICHT_MAP = {
        "fd": "Früh", "früh": "Früh", "frueh": "Früh", "f": "Früh",
        "sd": "Spät", "spät": "Spät", "spaet": "Spät", "s": "Spät",
        "nd": "Nacht", "nacht": "Nacht", "n": "Nacht",
    }

    client = _get_client()
    sh = client.open_by_key(spreadsheet_id)
    ws = sh.worksheet(tab_name)
    rows = ws.get_all_values()

    result: list[Wunschschicht] = []

    # Spalten-Indizes (0-basiert): B=1, E=4, F=5, G=6, H=7, I=8, J=9
    COL_NAME  = 1
    WUNSCH_PAARE = [(4, 5), (6, 7), (8, 9)]  # (tag_col, art_col)

    for row_num, row in enumerate(rows[1:], start=2):  # Zeile 1 = Header
        if len(row) <= COL_NAME:
            continue
        name = row[COL_NAME].strip()
        if not name:
            continue

        for tag_col, art_col in WUNSCH_PAARE:
            if len(row) <= art_col:
                continue
            tag_raw = row[tag_col].strip()
            art_raw = row[art_col].strip().lower()
            if not tag_raw or not art_raw:
                continue

            # Tag parsen: entweder reine Zahl ("5") oder Datum ("05.05.2026")
            tag_int: int | None = None
            try:
                tag_int = int(tag_raw)
            except ValueError:
                for fmt in ("%d.%m.%Y", "%d.%m.%y", "%Y-%m-%d"):
                    try:
                        tag_int = datetime.strptime(tag_raw, fmt).day
                        break
                    except ValueError:
                        continue

            if tag_int is None:
                logger.warning(
                    "Wunschschicht Zeile %d: Ungültiger Tag '%s' für %s – übersprungen",
                    row_num, tag_raw, name,
                )
                continue

            dienst_str = SCHICHT_MAP.get(art_raw)
            if dienst_str is None:
                logger.warning(
                    "Wunschschicht Zeile %d: Unbekannte Schichtart '%s' für %s – übersprungen",
                    row_num, art_raw, name,
                )
                continue

            result.append(Wunschschicht(name=name, tag=tag_int, dienst_str=dienst_str))

    logger.info(
        "Wunschschichten geladen: %d Einträge aus Tab '%s'",
        len(result), tab_name,
    )
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

    final_name, is_new = _resolve_tab_name(sh, base_name)

    if is_new:
        ws = sh.add_worksheet(title=final_name, rows=50, cols=35)
        logger.info("Neues Tabellenblatt angelegt: '%s'", final_name)
    else:
        ws = sh.worksheet(final_name)
        ws.clear()
        logger.info("Vorhandenes Tabellenblatt gecleart: '%s'", final_name)

    if final_name != base_name:
        logger.info(
            "Tab '%s' existierte bereits → neuer Plan als '%s' gespeichert",
            base_name, final_name,
        )

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
