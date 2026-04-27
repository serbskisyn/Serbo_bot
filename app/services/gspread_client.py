"""
gspread_client.py — Google Sheets lesen/schreiben für Dienstplan-Agent
"""
from __future__ import annotations

import json
import logging
import os
import traceback
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

_MONATE_MAP: dict[str, int] = {
    "januar": 1, "februar": 2, "märz": 3, "maerz": 3, "april": 4,
    "mai": 5, "juni": 6, "juli": 7, "august": 8, "september": 9,
    "oktober": 10, "november": 11, "dezember": 12,
}

_SCHICHT_MAP: dict[str, str] = {
    "fd": "Früh",        "frühdienst": "Früh",  "früh": "Früh",
    "frueh": "Früh",    "f": "Früh",
    "sd": "Spät",        "spätdienst": "Spät",  "spät": "Spät",
    "spaet": "Spät",    "s": "Spät",
    "nd": "Nacht",       "nachtdienst": "Nacht", "nacht": "Nacht", "n": "Nacht",
    "frei": "Frei",
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
            logger.warning(
                "GOOGLE_SERVICE_ACCOUNT_JSON ungueltig (%s), versuche credentials.json", e
            )

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


def _extract_vorname(full_name: str) -> str:
    return full_name.strip().split()[0].capitalize() if full_name.strip() else ""


# ---------------------------------------------------------------------------
# Debug-Helfer: zeigt rohe Sheet-Daten im Telegram-Chat
# ---------------------------------------------------------------------------

def debug_wunsch_sheet(
    spreadsheet_id: str,
    tab_name: str = "Form_Responses",
    max_rows: int = 4,
) -> str:
    """
    Gibt die ersten max_rows Zeilen des Tabs als lesbaren Text zurück.
    Aufruf via /debugwunsch im Bot.
    """
    try:
        client = _get_client()
        sh = client.open_by_key(spreadsheet_id)
        tab_titles = [ws.title for ws in sh.worksheets()]
        lines = [f"📋 Verfügbare Tabs: {', '.join(tab_titles)}", ""]

        if tab_name not in tab_titles:
            lines.append(f"❌ Tab '{tab_name}' NICHT gefunden!")
            return "\n".join(lines)

        ws = sh.worksheet(tab_name)
        rows = ws.get_all_values()
        lines.append(f"✅ Tab '{tab_name}' — {len(rows)} Zeilen gesamt")
        lines.append("")
        for i, row in enumerate(rows[:max_rows]):
            lines.append(f"--- Zeile {i} ---")
            for j, cell in enumerate(row):
                col_letter = chr(ord('A') + j) if j < 26 else f"COL{j}"
                lines.append(f"  {col_letter}({j}): '{cell}'")
        return "\n".join(lines)
    except Exception:
        return f"Fehler beim Debug:\n{traceback.format_exc()}"


# ---------------------------------------------------------------------------
# Abwesenheiten
# ---------------------------------------------------------------------------

def read_abwesenheiten(
    spreadsheet_id: str, tab_name: str = "Urlaub_CLI"
) -> list["Abwesenheit"]:
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


# ---------------------------------------------------------------------------
# Wunschschichten
# ---------------------------------------------------------------------------

def read_wunschschichten(
    spreadsheet_id: str,
    tab_name: str = "Form_Responses",
    monat: int | None = None,
    jahr: int | None = None,
) -> list["Wunschschicht"]:
    """
    Liest Wunschschichten aus dem Google-Formular-Tab.

    Spalten (0-basiert):
      A(0) Timestamp | B(1) Name | C(2) E-Mail | D(3) Monat
      E(4) Tag-1 | F(5) Schicht-1 | G(6) Tag-2 | H(7) Schicht-2 | I(8) Tag-3 | J(9) Schicht-3
    """
    from datetime import datetime
    from app.services.schedule_builder import Wunschschicht

    client = _get_client()
    sh = client.open_by_key(spreadsheet_id)

    # Tab-Suche: exakt → case-insensitive
    tab_titles = [t.title for t in sh.worksheets()]
    logger.info("Verfügbare Tabs in Spreadsheet: %s", tab_titles)

    ws = None
    if tab_name in tab_titles:
        ws = sh.worksheet(tab_name)
    else:
        for title in tab_titles:
            if title.lower() == tab_name.lower():
                ws = sh.worksheet(title)
                logger.info("Tab gefunden als '%s' (case-insensitive)", title)
                break

    if ws is None:
        raise ValueError(
            f"Tab '{tab_name}' nicht gefunden. "
            f"Vorhandene Tabs: {tab_titles}"
        )

    rows = ws.get_all_values()
    logger.info("Tab '%s': %d Zeilen", tab_name, len(rows))
    if rows:
        logger.info("Header: %s", rows[0])
    if len(rows) > 1:
        logger.info("Erste Datenzeile: %s", rows[1])

    seen_names: set[str] = set()
    kandidaten: list[tuple[str, list[tuple[int, str]]]] = []

    for row in reversed(rows[1:]):
        if len(row) <= 1 or not row[1].strip():
            continue

        full_name = row[1].strip()
        vorname   = _extract_vorname(full_name)
        if not vorname:
            continue

        # Monatsfilter via Spalte D
        monat_raw   = row[3].strip().lower() if len(row) > 3 else ""
        zeile_monat = _MONATE_MAP.get(monat_raw)
        if monat is not None and zeile_monat != monat:
            logger.debug(
                "'%s': Monat '%s'(%s) != Zielmonat %d – übersprungen",
                vorname, monat_raw, zeile_monat, monat,
            )
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

            # Schichtart: exakter Match → Teilmatch
            dienst_str = _SCHICHT_MAP.get(art_raw)
            if dienst_str is None:
                for key, val in _SCHICHT_MAP.items():
                    if len(key) > 1 and key in art_raw:
                        dienst_str = val
                        logger.info(
                            "Teilmatch Schicht '%s' → '%s' für %s",
                            art_raw, val, vorname,
                        )
                        break
            if dienst_str is None:
                logger.warning(
                    "Schichtart '%s' für %s nicht erkennbar – übersprungen",
                    art_raw, vorname,
                )
                continue

            # Tag parsen: reine Zahl ODER volles Datum
            tag_int: int | None = None
            try:
                tag_int = int(tag_raw)
            except ValueError:
                for fmt in ("%d.%m.%Y", "%d.%m.%y", "%Y-%m-%d", "%m/%d/%Y"):
                    try:
                        parsed = datetime.strptime(tag_raw, fmt)
                        if monat is not None and parsed.month != monat:
                            tag_int = None
                        else:
                            tag_int = parsed.day
                        break
                    except ValueError:
                        continue

            if tag_int is None:
                logger.warning(
                    "Tag '%s' für %s nicht parsebar – übersprungen", tag_raw, vorname
                )
                continue

            paare.append((tag_int, dienst_str))
            logger.info("Wunsch: %s Tag=%d Schicht=%s", vorname, tag_int, dienst_str)

        if paare:
            kandidaten.append((vorname, paare))

    result: list[Wunschschicht] = []
    for vorname, paare in kandidaten:
        for tag_int, dienst_str in paare:
            result.append(Wunschschicht(name=vorname, tag=tag_int, dienst_str=dienst_str))

    logger.info("%d Wunschschichten aus Tab '%s' geladen", len(result), tab_name)
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
        logger.info("Neues Tab angelegt: '%s'", final_name)
    else:
        ws = sh.worksheet(final_name)
        ws.clear()
        logger.info("Tab gecleart: '%s'", final_name)

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

    # Farben + Notizen
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
