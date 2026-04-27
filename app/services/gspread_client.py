"""
gspread_client.py — Google Sheets lesen/schreiben für Dienstplan-Agent
"""
from __future__ import annotations

import json
import logging
import os
import traceback
from datetime import date, timedelta
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

_MONATE_MAP: dict[str, int] = {
    "januar": 1, "februar": 2, "märz": 3, "maerz": 3, "april": 4,
    "mai": 5, "juni": 6, "juli": 7, "august": 8, "september": 9,
    "oktober": 10, "november": 11, "dezember": 12,
}

_SCHICHT_MAP: dict[str, str] = {
    "fd": "Früh",        "früdienst": "Früh",  "früh": "Früh",
    "frueh": "Früh",    "f": "Früh",
    "sd": "Spät",        "spätdienst": "Spät",  "spät": "Spät",
    "spaet": "Spät",    "s": "Spät",
    "nd": "Nacht",       "nachtdienst": "Nacht", "nacht": "Nacht", "n": "Nacht",
    "frei": "Frei",
}

_WUNSCH_TAB_KEYWORDS = ["form", "wunsch", "response", "antwort"]


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
        creds = Credentials.from_service_account_file(creds_path, scopes=SCOPES)
        return gspread.authorize(creds)

    raise EnvironmentError(
        "Kein Google-Credential gefunden. "
        "Setze GOOGLE_SERVICE_ACCOUNT_JSON oder lege credentials.json ins Projektroot."
    )


def _find_wunsch_worksheet(sh: gspread.Spreadsheet, tab_name: str) -> gspread.Worksheet:
    all_ws   = sh.worksheets()
    titles   = [ws.title for ws in all_ws]
    title_map = {ws.title: ws for ws in all_ws}

    if tab_name in title_map:
        logger.info("Tab exakt gefunden: '%s'", tab_name)
        return title_map[tab_name]

    for title, ws in title_map.items():
        if title.lower() == tab_name.lower():
            logger.info("Tab case-insensitiv gefunden: '%s'", title)
            return ws

    for kw in _WUNSCH_TAB_KEYWORDS:
        for title, ws in title_map.items():
            if kw in title.lower():
                logger.info("Tab via Keyword '%s' gefunden: '%s'", kw, title)
                return ws

    raise ValueError(
        f"Kein passendes Wunsch-Tab gefunden (gesucht: '{tab_name}'). "
        f"Vorhandene Tabs: {titles}"
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


def _extract_vorname(full_name: str) -> str:
    return full_name.strip().split()[0].capitalize() if full_name.strip() else ""


def _parse_date(raw: str) -> date | None:
    from datetime import datetime
    for fmt in ("%d.%m.%Y", "%d.%m.%y", "%Y-%m-%d", "%m/%d/%Y"):
        try:
            return datetime.strptime(raw.strip(), fmt).date()
        except ValueError:
            continue
    return None


# ---------------------------------------------------------------------------
# Debug
# ---------------------------------------------------------------------------

def debug_wunsch_sheet(
    spreadsheet_id: str,
    tab_name: str = "Form_Responses",
    max_rows: int = 3,
) -> str:
    try:
        client = _get_client()
        sh = client.open_by_key(spreadsheet_id)
        all_ws  = sh.worksheets()
        titles  = [ws.title for ws in all_ws]
        lines   = [
            f"📋 Spreadsheet-ID: ...{spreadsheet_id[-6:]}",
            f"📂 Alle Tabs ({len(titles)}): {', '.join(titles)}",
            "",
        ]
        try:
            ws = _find_wunsch_worksheet(sh, tab_name)
            lines.append(f"✅ Genutzter Tab: '{ws.title}'")
        except ValueError as e:
            lines.append(f"❌ {e}")
            return "\n".join(lines)

        rows = ws.get_all_values()
        lines.append(f"📊 {len(rows)} Zeilen im Tab")
        lines.append("")
        for i, row in enumerate(rows[:max_rows + 1]):
            prefix = "HEADER" if i == 0 else f"Zeile {i}"
            lines.append(f"--- {prefix} ---")
            for j, cell in enumerate(row[:10]):
                col = chr(ord('A') + j)
                lines.append(f"  {col}({j}): '{cell}'")
        return "\n".join(lines)
    except Exception:
        return f"Fehler beim Debug:\n{traceback.format_exc()}"


# ---------------------------------------------------------------------------
# Urlaub (Urlaub_CLI-Tab: Name | Art | Datum)
# ---------------------------------------------------------------------------

def read_abwesenheiten(
    spreadsheet_id: str, tab_name: str = "Urlaub_CLI"
) -> list["Abwesenheit"]:
    from app.services.schedule_builder import Abwesenheit

    client = _get_client()
    sh = client.open_by_key(spreadsheet_id)
    ws = sh.worksheet(tab_name)
    rows = ws.get_all_values()

    result = []
    for row in rows[1:]:
        if len(row) < 3 or not row[0].strip():
            continue
        name     = row[0].strip()
        art      = row[1].strip().upper()
        datum    = _parse_date(row[2])
        if datum is None:
            logger.warning("Unbekanntes Datumsformat: %s", row[2])
            continue
        result.append(Abwesenheit(name=name, art=art, datum=datum))

    logger.info("Urlaub geladen: %d Einträge", len(result))
    return result


# ---------------------------------------------------------------------------
# Krankenstand (Tab: Krankenstand — Spalten: Name | Beginn | Ende)
# ---------------------------------------------------------------------------

def read_krankenstand(
    spreadsheet_id: str, tab_name: str = "Krankenstand"
) -> list["Abwesenheit"]:
    """
    Liest den Krankenstand aus einem Sheet mit den Spalten:
      A: Name (Vorname reicht, Nachname wird ignoriert)
      B: Beginn  (dd.mm.yyyy)
      C: Ende    (dd.mm.yyyy)

    Gibt eine Abwesenheit pro Krankheitstag (Art='K') zurück.
    """
    from app.services.schedule_builder import Abwesenheit

    client = _get_client()
    sh     = client.open_by_key(spreadsheet_id)
    ws     = sh.worksheet(tab_name)
    rows   = ws.get_all_values()

    result: list[Abwesenheit] = []
    for row in rows[1:]:          # Header überspringen
        if len(row) < 3 or not row[0].strip():
            continue
        vorname = _extract_vorname(row[0])   # nur Vorname
        beginn  = _parse_date(row[1])
        ende    = _parse_date(row[2])
        if not vorname or beginn is None or ende is None:
            logger.warning("Krankenstand-Zeile übersprungen (Parse-Fehler): %s", row)
            continue
        if ende < beginn:
            logger.warning("Krankenstand Ende < Beginn übersprungen: %s", row)
            continue
        current = beginn
        while current <= ende:
            result.append(Abwesenheit(name=vorname, art="K", datum=current))
            current += timedelta(days=1)

    logger.info("Krankenstand geladen: %d Tage aus '%s'", len(result), tab_name)
    return result


# ---------------------------------------------------------------------------
# Wunschschichten
# ---------------------------------------------------------------------------

def read_wunschschichten(
    spreadsheet_id: str,
    tab_name: str = "Form_Responses",
    monat: int | None = None,
    jahr: int | None = None,
) -> list["Wunschschicht"]:
    from datetime import datetime
    from app.services.schedule_builder import Wunschschicht

    client = _get_client()
    sh     = client.open_by_key(spreadsheet_id)
    ws     = _find_wunsch_worksheet(sh, tab_name)
    logger.info("Lese Wunsch-Tab: '%s'", ws.title)

    rows = ws.get_all_values()
    logger.info("Tab '%s': %d Zeilen", ws.title, len(rows))
    if rows:
        logger.info("Header: %s", rows[0])
    if len(rows) > 1:
        logger.info("Erste Datenzeile (roh): %s", rows[1])

    seen_names: set[str] = set()
    kandidaten: list[tuple[str, list[tuple[int, str]]]] = []

    for row in reversed(rows[1:]):
        if len(row) <= 1 or not row[1].strip():
            continue

        full_name = row[1].strip()
        vorname   = _extract_vorname(full_name)
        if not vorname:
            continue

        monat_raw   = row[3].strip().lower() if len(row) > 3 else ""
        zeile_monat = _MONATE_MAP.get(monat_raw)
        if monat is not None and zeile_monat != monat:
            continue

        if vorname in seen_names:
            continue
        seen_names.add(vorname)

        rohe_paare = [
            (row[4] if len(row) > 4 else "", row[5] if len(row) > 5 else ""),
            (row[6] if len(row) > 6 else "", row[7] if len(row) > 7 else ""),
            (row[8] if len(row) > 8 else "", row[9] if len(row) > 9 else ""),
        ]

        paare: list[tuple[int, str]] = []
        for tag_raw, art_raw in rohe_paare:
            tag_raw = tag_raw.strip()
            art_raw = art_raw.strip().lower()
            if not tag_raw:
                continue

            dienst_str = _SCHICHT_MAP.get(art_raw)
            if dienst_str is None:
                for key, val in _SCHICHT_MAP.items():
                    if len(key) > 1 and key in art_raw:
                        dienst_str = val
                        break
            if dienst_str is None:
                logger.warning("Schichtart '%s' für %s unbekannt", art_raw, vorname)
                continue

            tag_int: int | None = None
            try:
                tag_int = int(tag_raw)
            except ValueError:
                for fmt in ("%d.%m.%Y", "%d.%m.%y", "%Y-%m-%d", "%m/%d/%Y"):
                    try:
                        parsed = datetime.strptime(tag_raw, fmt)
                        tag_int = parsed.day if (monat is None or parsed.month == monat) else None
                        break
                    except ValueError:
                        continue

            if tag_int is None:
                logger.warning("Tag '%s' für %s nicht parsebar", tag_raw, vorname)
                continue

            paare.append((tag_int, dienst_str))
            logger.info("Wunsch: %s Tag=%d Schicht=%s", vorname, tag_int, dienst_str)

        if paare:
            kandidaten.append((vorname, paare))

    result: list[Wunschschicht] = []
    for vorname, paare in kandidaten:
        for tag_int, dienst_str in paare:
            result.append(Wunschschicht(name=vorname, tag=tag_int, dienst_str=dienst_str))

    logger.info("%d Wunschschichten geladen (Tab: '%s')", len(result), ws.title)
    return result


# ---------------------------------------------------------------------------
# Dienstplan schreiben
# ---------------------------------------------------------------------------

def write_dienstplan(
    spreadsheet_id: str,
    plan:           dict[str, dict[date, "Dienst"]],
    mitarbeiter:    list[str],
    tage:           list[date],
    tab_name:       str | None = None,
    wunsch_notizen: dict[str, list[tuple[date, str, bool]]] | None = None,
) -> str:
    monate_de = ["", "Jan", "Feb", "Mär", "Apr", "Mai", "Jun",
                 "Jul", "Aug", "Sep", "Okt", "Nov", "Dez"]
    erster    = tage[0]
    base_name = tab_name if tab_name else f"{monate_de[erster.month]}_{erster.year}"

    client = _get_client()
    sh     = client.open_by_key(spreadsheet_id)

    final_name, is_new = _resolve_tab_name(sh, base_name)

    if is_new:
        ws = sh.add_worksheet(title=final_name, rows=50, cols=35)
    else:
        ws = sh.worksheet(final_name)
        ws.clear()

    header1 = ["Tag"] + mitarbeiter + ["offen", "Tag"]
    ws.update("A1", [header1])
    dienstart_row = [""] + ["Dienstart"] * len(mitarbeiter) + ["Dienstart", ""]
    ws.update("A3", [dienstart_row])

    wochentage_de = ["Mo", "Di", "Mi", "Do", "Fr", "Sa", "So"]
    data_rows = []
    for tag in tage:
        wt        = wochentage_de[tag.weekday()]
        datum_str = f"{wt}, {tag.strftime('%d. %b.')}"
        row       = [datum_str]
        for ma_name in mitarbeiter:
            d = plan.get(ma_name, {}).get(tag)
            row.append(d.value if d else "Frei")
        offen_val = plan.get("offen", {}).get(tag)
        row.append(offen_val.value if offen_val else "")
        row.append(datum_str)
        data_rows.append(row)

    ws.update("A4", data_rows)

    requests   = []
    ma_col_map = {ma: i + 2 for i, ma in enumerate(mitarbeiter)}
    ma_col_map["offen"] = len(mitarbeiter) + 2

    notiz_map: dict[tuple[int, int], str] = {}
    if wunsch_notizen:
        tage_idx = {t: i for i, t in enumerate(tage)}
        for ma_name, eintraege in wunsch_notizen.items():
            col_idx = ma_col_map.get(ma_name)
            if col_idx is None:
                continue
            for datum, dienst_str, erfuellt in eintraege:
                row_offset = tage_idx.get(datum)
                if row_offset is None:
                    continue
                sheet_row = 4 + row_offset
                symbol = "✅" if erfuellt else "⚠️"
                notiz_map[(sheet_row, col_idx)] = (
                    f"{symbol} Wunsch: {dienst_str}"
                    + (" (erfüllt)" if erfuellt else " (nicht erfüllt)")
                )

    for row_idx, tag in enumerate(tage):
        sheet_row = 4 + row_idx
        for ma_name in list(mitarbeiter) + ["offen"]:
            col_idx = ma_col_map.get(ma_name)
            if col_idx is None:
                continue
            d   = plan.get(ma_name, {}).get(tag)
            val = d.value if d else "Frei"
            rgb = FARBEN_RGB.get(val, (1.0, 1.0, 1.0))
            requests.append(_bg_request(ws.id, sheet_row - 1, col_idx - 1, *rgb))
            notiz = notiz_map.get((sheet_row, col_idx))
            if notiz:
                requests.append(_note_request(ws.id, sheet_row - 1, col_idx - 1, notiz))

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
                "startRowIndex": row, "endRowIndex": row + 1,
                "startColumnIndex": col, "endColumnIndex": col + 1,
            },
            "rows": [{"values": [{"userEnteredFormat": {
                "backgroundColor": {"red": r, "green": g, "blue": b}
            }}]}],
            "fields": "userEnteredFormat.backgroundColor",
        }
    }


def _note_request(sheet_id: int, row: int, col: int, note: str) -> dict:
    return {
        "updateCells": {
            "range": {
                "sheetId": sheet_id,
                "startRowIndex": row, "endRowIndex": row + 1,
                "startColumnIndex": col, "endColumnIndex": col + 1,
            },
            "rows": [{"values": [{"note": note}]}],
            "fields": "note",
        }
    }
