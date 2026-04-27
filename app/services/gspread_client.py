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
    from app.services.schedule_builder import Abwesenheit, Dienst, Mitarbeiter, Wunschschicht

logger = logging.getLogger(__name__)

SCOPES = [
    "https://spreadsheets.google.com/feeds",
    "https://www.googleapis.com/auth/drive",
]

_CREDENTIALS_FILE = os.path.join(
    os.path.dirname(__file__), "..", "..", "credentials.json"
)

def _hex(h: str) -> tuple[float, float, float]:
    """Hex-String (#rrggbb) → (r, g, b) als float 0..1"""
    h = h.lstrip("#")
    return tuple(int(h[i:i+2], 16) / 255 for i in (0, 2, 4))  # type: ignore

# ---------------------------------------------------------------------------
# Farbcodierung — exakte Hex-Werte aus dem Original-Sheet
# ---------------------------------------------------------------------------
FARBEN_RGB: dict[str, tuple[float, float, float]] = {
    "Früh":        _hex("#75a0e5"),
    "Spät":        _hex("#ff9bd5"),
    "Nacht":       _hex("#dd7247"),
    "Frei":        _hex("#d8d8d8"),
    "Urlaub":      _hex("#8cc068"),
    "krank":       _hex("#ffffff"),
    "K":           _hex("#ffffff"),
    "BT":          _hex("#ead1dc"),
    "Team":        _hex("#ffd965"),
    "Supervision": _hex("#ffd965"),
    "OFFEN-FD":    _hex("#75a0e5"),
    "OFFEN-SD":    _hex("#ff9bd5"),
    "OFFEN-ND":    _hex("#dd7247"),
}

# Farben für Zusammenfassung
_SUMMARY_LABEL_BG  = _hex("#d8d8d8")   # Labels (FREI, Früh, …)
_SUMMARY_VALUE_BG  = _hex("#ffffff")   # Zahlenwerte
_SUMMARY_IST_BG    = _hex("#dce6f1")   # Dienstplanstunden (hellblau)
_SUMMARY_SOLL_BG   = _hex("#fce4d6")   # Sollstunden (lachsfarben)
_SUMMARY_NEG_BG    = _hex("#f4cccc")   # negative Differenz
_SUMMARY_POS_BG    = _hex("#d9ead3")   # positive/null Differenz
_URLAUB_BG         = FARBEN_RGB["Urlaub"]

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
    all_ws    = sh.worksheets()
    titles    = [ws.title for ws in all_ws]
    title_map = {ws.title: ws for ws in all_ws}

    if tab_name in title_map:
        return title_map[tab_name]
    for title, ws in title_map.items():
        if title.lower() == tab_name.lower():
            return ws
    for kw in _WUNSCH_TAB_KEYWORDS:
        for title, ws in title_map.items():
            if kw in title.lower():
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
# Hilfsfunktion: Stunden pro Dienst
# ---------------------------------------------------------------------------

def _stunden_fuer(dienst_val: str) -> float:
    return {"Früh": 7.5, "Spät": 7.0, "Nacht": 9.0}.get(dienst_val, 0.0)


# ---------------------------------------------------------------------------
# Vormonats-Tab ermitteln
# ---------------------------------------------------------------------------

_MONATE_KURZ = ["", "Jan", "Feb", "Mär", "Apr", "Mai", "Jun",
                "Jul", "Aug", "Sep", "Okt", "Nov", "Dez"]


def _find_previous_month_tabs(
    sh: gspread.Spreadsheet,
    erster: date,
) -> list[gspread.Worksheet]:
    """
    Gibt alle Worksheet-Objekte zurück, die dem Vormonat entsprechen.
    Erkennt Muster wie 'Mär_2025', 'Mär_2025-1', 'Mär_2025-2' usw.
    Gibt die Tabs nach Titel-Suffix sortiert zurück (höchste Nummer = letzter).
    """
    if erster.month == 1:
        prev_month, prev_year = 12, erster.year - 1
    else:
        prev_month, prev_year = erster.month - 1, erster.year

    prefix = f"{_MONATE_KURZ[prev_month]}_{prev_year}"
    matches = [
        ws for ws in sh.worksheets()
        if ws.title == prefix or ws.title.startswith(prefix + "-")
    ]

    def _sort_key(ws: gspread.Worksheet) -> int:
        if ws.title == prefix:
            return 0
        try:
            return int(ws.title.split("-")[-1])
        except ValueError:
            return 0

    matches.sort(key=_sort_key)
    return matches


# ---------------------------------------------------------------------------
# Vormonats-Plan aus Sheet lesen (für _init_aus_vormonat)
# ---------------------------------------------------------------------------

def read_vormonat_plan(
    spreadsheet_id: str,
    erster_des_monats: date,
) -> dict[str, dict[date, "Dienst"]]:
    """
    Liest den letzten Vormonats-Tab und gibt einen dict[ma_name → dict[date → Dienst]] zurück.
    Gibt {} zurück, wenn kein Tab gefunden wird.
    """
    from app.services.schedule_builder import Dienst

    _DIENST_MAP = {
        d.value.lower(): d for d in Dienst
    }

    try:
        client = _get_client()
        sh     = client.open_by_key(spreadsheet_id)
        tabs   = _find_previous_month_tabs(sh, erster_des_monats)
        if not tabs:
            logger.info("Kein Vormonats-Tab gefunden für %s", erster_des_monats)
            return {}

        ws = tabs[-1]  # letzter (= aktuellster) Vormonats-Tab
        logger.info("Vormonats-Tab: '%s'", ws.title)
        rows = ws.get_all_values()
        if not rows:
            return {}

        # Kopfzeile: ["Tag", MA1, MA2, ..., "offen", "Tag"]
        header = rows[0]
        ma_names = header[1:]  # Spalten ab Index 1
        # Letzten "Tag"-Doppel und "offen" überspringen
        while ma_names and ma_names[-1].lower() in ("", "tag", "offen"):
            ma_names = ma_names[:-1]
        if ma_names and ma_names[-1].lower() == "offen":
            ma_names = ma_names[:-1]

        result: dict[str, dict[date, Dienst]] = {ma: {} for ma in ma_names}

        wochentage_prefix = ["mo", "di", "mi", "do", "fr", "sa", "so"]
        for row in rows[3:]:  # Datenzeilen ab Zeile 4 (Index 3)
            if not row or not row[0].strip():
                continue
            datum_raw = row[0].strip().lower()
            # Format: "Mo, 01. Jan."
            parsed_date: date | None = None
            for sep in [",", " "]:
                parts = datum_raw.split(sep, 1)
                if len(parts) == 2:
                    date_part = parts[1].strip().replace(".", "").strip()
                    tokens = date_part.split()
                    if len(tokens) >= 1:
                        try:
                            day = int(tokens[0])
                            from app.services.schedule_builder import get_feiertage
                            if erster_des_monats.month == 1:
                                pm, py = 12, erster_des_monats.year - 1
                            else:
                                pm, py = erster_des_monats.month - 1, erster_des_monats.year
                            parsed_date = date(py, pm, day)
                            break
                        except (ValueError, ImportError):
                            continue

            if parsed_date is None:
                continue

            for col_idx, ma_name in enumerate(ma_names):
                cell_val = row[col_idx + 1].strip() if col_idx + 1 < len(row) else ""
                dienst = _DIENST_MAP.get(cell_val.lower())
                if dienst:
                    result[ma_name][parsed_date] = dienst

        logger.info("Vormonats-Plan gelesen: %d MA, Tab '%s'", len(result), ws.title)
        return result
    except Exception as e:
        logger.warning("Fehler beim Lesen des Vormonats-Plans: %s", e)
        return {}


# ---------------------------------------------------------------------------
# Mitarbeiter aus Sheet laden (Tab: Mitarbeiterübersicht)
# ---------------------------------------------------------------------------

def read_mitarbeiter(
    spreadsheet_id: str,
    tab_name: str = "Mitarbeiterübersicht",
) -> list["Mitarbeiter"]:
    from app.services.schedule_builder import Mitarbeiter

    client = _get_client()
    sh     = client.open_by_key(spreadsheet_id)
    ws     = sh.worksheet(tab_name)
    rows   = ws.get_all_values()

    result: list[Mitarbeiter] = []
    for row in rows[1:]:
        if not row or not row[0].strip():
            continue
        vorname = _extract_vorname(row[0].strip())
        if not vorname:
            continue
        std_raw = row[1].strip() if len(row) > 1 else ""
        try:
            wochenstunden = float(std_raw.replace(",", "."))
        except ValueError:
            logger.warning("Stundenwert für '%s' nicht lesbar: '%s'", vorname, std_raw)
            continue
        tagesstunden = round(wochenstunden / 5, 1)
        result.append(Mitarbeiter(name=vorname, tagesstunden=tagesstunden))
        logger.info("Mitarbeiter geladen: %s (%.1fh/Tag)", vorname, tagesstunden)

    logger.info("Mitarbeiterliste: %d Personen aus Tab '%s'", len(result), tab_name)
    return result


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
        all_ws = sh.worksheets()
        titles = [ws.title for ws in all_ws]
        lines  = [
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
        name  = row[0].strip()
        art   = row[1].strip().upper()
        datum = _parse_date(row[2])
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
    from app.services.schedule_builder import Abwesenheit

    client = _get_client()
    sh     = client.open_by_key(spreadsheet_id)
    ws     = sh.worksheet(tab_name)
    rows   = ws.get_all_values()

    result: list[Abwesenheit] = []
    for row in rows[1:]:
        if len(row) < 3 or not row[0].strip():
            continue
        vorname = _extract_vorname(row[0])
        beginn  = _parse_date(row[1])
        ende    = _parse_date(row[2])
        if not vorname or beginn is None or ende is None:
            logger.warning("Krankenstand-Zeile übersprungen: %s", row)
            continue
        if ende < beginn:
            continue
        current = beginn
        while current <= ende:
            result.append(Abwesenheit(name=vorname, art="K", datum=current))
            current += timedelta(days=1)

    logger.info("Krankenstand geladen: %d Tage", len(result))
    return result


# ---------------------------------------------------------------------------
# Wunschschichten
# ---------------------------------------------------------------------------

def read_wunschschichten(
    spreadsheet_id: str,
    tab_name: str = "Form_Responses",
    monat: int | None = None,
    jahr: int | None = None,
    bekannte_namen: set[str] | None = None,
) -> list["Wunschschicht"]:
    from datetime import datetime
    from app.services.schedule_builder import Wunschschicht

    client = _get_client()
    sh     = client.open_by_key(spreadsheet_id)
    ws     = _find_wunsch_worksheet(sh, tab_name)
    logger.info("Lese Wunsch-Tab: '%s'", ws.title)

    rows = ws.get_all_values()
    logger.info("Tab '%s': %d Zeilen", ws.title, len(rows))

    seen_names: set[str] = set()
    result: list[Wunschschicht] = []

    for row in reversed(rows[1:]):
        if len(row) <= 1 or not row[1].strip():
            continue

        full_name = row[1].strip()
        vorname   = _extract_vorname(full_name)
        if not vorname:
            continue

        monat_raw   = row[3].strip().lower() if len(row) > 3 else ""
        monat_wort  = monat_raw.split()[0] if monat_raw else ""
        zeile_monat = _MONATE_MAP.get(monat_wort)

        if monat is not None and zeile_monat != monat:
            continue

        dedup_key = f"{vorname}_{zeile_monat}"
        if dedup_key in seen_names:
            continue
        seen_names.add(dedup_key)

        if bekannte_namen and vorname not in bekannte_namen:
            logger.warning("Wunsch von '%s' nicht in Mitarbeiterliste — ignoriert", vorname)
            continue

        rohe_paare = [
            (row[4] if len(row) > 4 else "", row[5] if len(row) > 5 else ""),
            (row[6] if len(row) > 6 else "", row[7] if len(row) > 7 else ""),
            (row[8] if len(row) > 8 else "", row[9] if len(row) > 9 else ""),
        ]

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
                for fmt in ("%d.%m.%Y", "%d.%m.%y", "%Y-%m-%d"):
                    try:
                        parsed = datetime.strptime(tag_raw, fmt)
                        if monat is None or parsed.month == monat:
                            tag_int = parsed.day
                        break
                    except ValueError:
                        continue

            if tag_int is None:
                logger.warning("Tag '%s' für %s nicht parsebar", tag_raw, vorname)
                continue

            result.append(Wunschschicht(name=vorname, tag=tag_int, dienst_str=dienst_str))
            logger.info("Wunsch: %s Tag=%d Schicht=%s", vorname, tag_int, dienst_str)

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
    ma_soll:        dict[str, float] | None = None,
) -> str:
    """
    Schreibt den Dienstplan ins Google Sheet.

    Zusätzlich wird ein Zusammenfassungs-Block (wie im Original-Sheet)
    unterhalb der Tagesdaten eingefügt:
      - Zeile: FREI-Zählung pro MA
      - Zeile: Früh-Zählung
      - Zeile: Spät-Zählung
      - Zeile: Nacht-Zählung
      - Zeile: Teamsitzung
      - Zeile: BT
      - Zeile: Urlaub
      - Leerzeile
      - Zeile: Dienstplan Ist-Stunden
      - Zeile: Soll-Stunden
      - Zeile: Differenz (Ist - Soll)

    ma_soll: dict[ma_name → soll_stunden] aus dem Generator.
    Fehlende Soll-Stunden werden als '' geschrieben.
    """
    monate_de = ["", "Jan", "Feb", "Mär", "Apr", "Mai", "Jun",
                 "Jul", "Aug", "Sep", "Okt", "Nov", "Dez"]
    erster    = tage[0]
    base_name = tab_name if tab_name else f"{monate_de[erster.month]}_{erster.year}"

    client = _get_client()
    sh     = client.open_by_key(spreadsheet_id)

    final_name, is_new = _resolve_tab_name(sh, base_name)

    if is_new:
        ws = sh.add_worksheet(title=final_name, rows=60, cols=35)
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

    # ---------------------------------------------------------------------------
    # Zusammenfassungs-Block berechnen
    # ---------------------------------------------------------------------------
    n_cols = len(mitarbeiter) + 2  # MA-Spalten + offen

    # Zähl-Kategorien
    count_labels = ["FREI", "Früh", "Spät", "Nacht", "Teamsitzung", "BT", "Urlaub"]
    dienst_keys  = ["Frei", "Früh", "Spät", "Nacht", "Team", "BT", "Urlaub"]

    summary_count_rows: list[list] = []
    for label, key in zip(count_labels, dienst_keys):
        row = [label]
        for ma_name in mitarbeiter:
            count = sum(
                1 for tag in tage
                if (plan.get(ma_name, {}).get(tag) or _null_dienst()).value == key
                or (plan.get(ma_name, {}).get(tag) is not None
                    and plan[ma_name][tag].value == key)
            )
            row.append(count)
        # Gesamt (offen-Spalte leer für Zähler)
        row.append("")
        row.append(label)
        summary_count_rows.append(row)

    # Leerzeile
    empty_row = [""] * (n_cols + 2)

    # Ist-Stunden pro MA
    ist_row = ["Dienstplanstd. Apr."]
    for ma_name in mitarbeiter:
        ist = sum(
            _stunden_fuer(plan[ma_name][tag].value)
            for tag in tage
            if plan.get(ma_name, {}).get(tag) is not None
        )
        ist_row.append(round(ist, 1) if ist > 0 else "")
    ist_row.append("")
    ist_row.append("Dienstplanstd.")

    # Soll-Stunden pro MA
    soll_dict = ma_soll or {}
    soll_row = ["Sollstd. Apr."]
    for ma_name in mitarbeiter:
        soll_row.append(soll_dict.get(ma_name, ""))
    soll_row.append("")
    soll_row.append("Sollstd.")

    # Differenz
    diff_row = ["Differenz"]
    for ma_name in mitarbeiter:
        ist_val  = ist_row[mitarbeiter.index(ma_name) + 1]
        soll_val = soll_row[mitarbeiter.index(ma_name) + 1]
        if isinstance(ist_val, (int, float)) and isinstance(soll_val, (int, float)):
            diff = round(float(ist_val) - float(soll_val), 1)
            diff_row.append(diff)
        else:
            diff_row.append("")
    diff_row.append("")
    diff_row.append("Differenz")

    # Zusammenfassung in Sheet schreiben
    # Leerzeile nach den Tagen
    data_end_row = 4 + len(tage)  # 1-basierte Zeilennummer der ersten Zeile nach den Tagen
    summary_start = data_end_row + 2  # zwei Leerzeilen Abstand

    all_summary = summary_count_rows + [empty_row, ist_row, soll_row, diff_row]
    summary_range = f"A{summary_start}"
    ws.update(summary_range, all_summary)

    # ---------------------------------------------------------------------------
    # Formatierung (Farben)
    # ---------------------------------------------------------------------------
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

    # Tagesdaten einfärben
    for row_idx, tag in enumerate(tage):
        sheet_row = 4 + row_idx
        for ma_name in list(mitarbeiter) + ["offen"]:
            col_idx = ma_col_map.get(ma_name)
            if col_idx is None:
                continue
            d   = plan.get(ma_name, {}).get(tag)
            val = d.value if d else "Frei"
            rgb = FARBEN_RGB.get(val) or FARBEN_RGB.get("Frei")
            requests.append(_bg_request(ws.id, sheet_row - 1, col_idx - 1, *rgb))
            notiz = notiz_map.get((sheet_row, col_idx))
            if notiz:
                requests.append(_note_request(ws.id, sheet_row - 1, col_idx - 1, notiz))

        if tag.weekday() >= 5:
            for col in [0, len(mitarbeiter) + 2]:
                requests.append(_bg_request(ws.id, sheet_row - 1, col, 0.90, 0.90, 0.90))

    # Zusammenfassungs-Block einfärben
    for s_row_offset, (label, key) in enumerate(zip(count_labels, dienst_keys)):
        sheet_row = summary_start + s_row_offset  # 1-basiert
        row_0     = sheet_row - 1                 # 0-basiert für API

        # Label-Zelle (Spalte A)
        requests.append(_bg_request(ws.id, row_0, 0, *_SUMMARY_LABEL_BG))
        # Wiederhol-Label rechts
        requests.append(_bg_request(ws.id, row_0, len(mitarbeiter) + 2, *_SUMMARY_LABEL_BG))

        # Farbe des Dienstes oder neutral
        cell_rgb = FARBEN_RGB.get(key, _SUMMARY_VALUE_BG)
        for col_idx in range(1, len(mitarbeiter) + 2):
            requests.append(_bg_request(ws.id, row_0, col_idx, *cell_rgb))

    # Urlaub-Zeile extra grün
    urlaub_row_0 = summary_start + count_labels.index("Urlaub") - 1
    for col_idx in range(1, len(mitarbeiter) + 2):
        requests.append(_bg_request(ws.id, urlaub_row_0, col_idx, *_URLAUB_BG))

    # Ist-/Soll-/Differenz-Zeilen einfärben
    ist_row_0  = summary_start + len(count_labels) + 1 - 1   # +1 für Leerzeile
    soll_row_0 = ist_row_0 + 1
    diff_row_0 = ist_row_0 + 2

    for col_idx in range(len(mitarbeiter) + 2):
        requests.append(_bg_request(ws.id, ist_row_0,  col_idx, *_SUMMARY_IST_BG))
        requests.append(_bg_request(ws.id, soll_row_0, col_idx, *_SUMMARY_SOLL_BG))

    # Differenz: grün wenn ≥ 0, rot wenn < 0
    for i, ma_name in enumerate(mitarbeiter):
        diff_val = diff_row[i + 1]
        if isinstance(diff_val, (int, float)):
            rgb = _SUMMARY_POS_BG if diff_val >= 0 else _SUMMARY_NEG_BG
        else:
            rgb = _SUMMARY_VALUE_BG
        requests.append(_bg_request(ws.id, diff_row_0, i + 1, *rgb))
    requests.append(_bg_request(ws.id, diff_row_0, 0, *_SUMMARY_LABEL_BG))
    requests.append(_bg_request(ws.id, diff_row_0, len(mitarbeiter) + 2, *_SUMMARY_LABEL_BG))

    if requests:
        sh.batch_update({"requests": requests})

    logger.info("Dienstplan '%s' geschrieben (%d Tage, Zusammenfassung ab Zeile %d)",
                final_name, len(tage), summary_start)
    return final_name


def _null_dienst():
    """Dummy-Dienst mit value 'Frei' für None-Fälle."""
    class _D:
        value = "Frei"
    return _D()


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
