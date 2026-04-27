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

# Vollständige Monatsnamen (für Zusammenfassungs-Labels)
_MONATE_LANG = ["",
    "Jan.", "Feb.", "März", "Apr.", "Mai", "Juni",
    "Juli", "Aug.", "Sep.", "Okt.", "Nov.", "Dez.",
]

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
# Vormonats-Tab ermitteln — robuste Varianten-Erkennung
# ---------------------------------------------------------------------------

# Kurze Monatsnamen in mehreren Schreibweisen (Umlaute + ASCII-Varianten)
_MONATE_KURZ_VARIANTEN: dict[int, list[str]] = {
    1:  ["Jan"],
    2:  ["Feb"],
    3:  ["Mär", "Mar", "Mrz"],
    4:  ["Apr"],
    5:  ["Mai", "May"],
    6:  ["Jun"],
    7:  ["Jul"],
    8:  ["Aug"],
    9:  ["Sep"],
    10: ["Okt", "Oct"],
    11: ["Nov"],
    12: ["Dez", "Dec"],
}

# Primäres Kürzel (für neue Tabs)
_MONATE_KURZ = ["", "Jan", "Feb", "Mär", "Apr", "Mai", "Jun",
                "Jul", "Aug", "Sep", "Okt", "Nov", "Dez"]


def _vormonat_prefixes(erster: date) -> list[str]:
    """
    Gibt alle möglichen Präfix-Varianten für den Vormonats-Tab zurück.
    Z.B. für Juni 2025 → ['Mai_2025', 'May_2025', 'Mai 2025', 'May 2025',
                           'Mai-2025', 'May-2025', '05_2025', '05-2025', '5_2025']
    """
    if erster.month == 1:
        pm, py = 12, erster.year - 1
    else:
        pm, py = erster.month - 1, erster.year

    varianten = _MONATE_KURZ_VARIANTEN.get(pm, [_MONATE_KURZ[pm]])
    separators = ["_", " ", "-"]
    prefixes: list[str] = []

    for kuerzel in varianten:
        for sep in separators:
            prefixes.append(f"{kuerzel}{sep}{py}")

    # Numerische Varianten: 05_2025, 5_2025
    for sep in separators:
        prefixes.append(f"{pm:02d}{sep}{py}")
        prefixes.append(f"{pm}{sep}{py}")

    return prefixes


def _find_previous_month_tabs(
    sh: gspread.Spreadsheet,
    erster: date,
) -> list[gspread.Worksheet]:
    """
    Gibt alle Worksheet-Objekte zurück, die dem Vormonat entsprechen.
    Erkennt alle Schreibweisen: 'Mär_2025', 'Mar_2025', 'Mär 2025',
    'Mai-2025', '05_2025', 'Mär_2025-1', 'Mär_2025-2' usw.
    Sortiert nach Versions-Suffix (höchste Nummer = letzter).
    """
    prefixes = _vormonat_prefixes(erster)
    all_ws   = sh.worksheets()
    matches: list[gspread.Worksheet] = []

    for ws in all_ws:
        title_lower = ws.title.lower()
        for prefix in prefixes:
            p_lower = prefix.lower()
            # Exakter Match oder Match mit Versions-Suffix (-1, -2, …)
            if title_lower == p_lower or title_lower.startswith(p_lower + "-"):
                matches.append(ws)
                break   # nächstes Worksheet

    def _sort_key(ws: gspread.Worksheet) -> int:
        title = ws.title
        for prefix in prefixes:
            if title.lower() == prefix.lower():
                return 0
            if title.lower().startswith(prefix.lower() + "-"):
                try:
                    return int(title.split("-")[-1])
                except ValueError:
                    return 0
        return 0

    matches.sort(key=_sort_key)
    logger.info(
        "Vormonat-Tabs für %s: %s",
        erster,
        [ws.title for ws in matches] or "keiner gefunden",
    )
    return matches


# ---------------------------------------------------------------------------
# Vormonats-Differenz (Carry-over) aus letztem Vormonats-Tab lesen
# ---------------------------------------------------------------------------

def read_vormonat_differenz(
    sh: gspread.Spreadsheet,
    erster_des_monats: date,
    mitarbeiter: list[str],
) -> dict[str, float]:
    """
    Liest die 'Differenz'-Zeile aus dem letzten Vormonats-Tab.
    Gibt dict[ma_name → differenz_stunden] zurück.

    Garantierter Fallback:
      - Kein Tab gefunden              → alle 0.0
      - Tab leer                       → alle 0.0
      - MA-Name nicht in Kopfzeile     → 0.0 für diesen MA
      - Zellwert nicht parsebar        → 0.0 für diesen MA
    """
    result = {ma: 0.0 for ma in mitarbeiter}

    tabs = _find_previous_month_tabs(sh, erster_des_monats)
    if not tabs:
        logger.info(
            "Kein Vormonats-Tab gefunden für %s — Differenz wird mit 0 initialisiert.",
            erster_des_monats,
        )
        return result

    ws = tabs[-1]
    logger.info("Lese Vormonats-Differenz aus Tab '%s'", ws.title)

    try:
        rows = ws.get_all_values()
    except Exception as e:
        logger.warning("Fehler beim Lesen von Tab '%s': %s — verwende 0", ws.title, e)
        return result

    if not rows:
        logger.info("Tab '%s' ist leer — Differenz wird mit 0 initialisiert.", ws.title)
        return result

    # Kopfzeile: MA-Namen ab Spalte B (Index 1)
    header = rows[0]
    ma_cols: dict[str, int] = {}
    for col_idx, cell in enumerate(header[1:], start=1):
        name = cell.strip()
        if name and name.lower() not in ("offen", "tag", ""):
            ma_cols[name] = col_idx

    if not ma_cols:
        logger.warning("Tab '%s': Keine MA-Namen in Kopfzeile — verwende 0", ws.title)
        return result

    # 'Differenz'-Zeile suchen (von unten, erste Übereinstimmung)
    differenz_row: list[str] | None = None
    for row in reversed(rows):
        if row and row[0].strip().lower() == "differenz":
            differenz_row = row
            break

    if differenz_row is None:
        logger.warning(
            "Tab '%s': Keine 'Differenz'-Zeile gefunden — verwende 0", ws.title
        )
        return result

    # Werte je MA auslesen
    for ma_name in mitarbeiter:
        col_idx = ma_cols.get(ma_name)
        if col_idx is None:
            logger.info(
                "MA '%s' nicht in Vormonat-Tab '%s' — Carry-over = 0",
                ma_name, ws.title,
            )
            continue   # bleibt 0.0
        raw = differenz_row[col_idx].strip() if col_idx < len(differenz_row) else ""
        if not raw:
            continue   # bleibt 0.0
        try:
            result[ma_name] = float(raw.replace(",", "."))
        except ValueError:
            logger.warning(
                "Tab '%s', MA '%s': Wert '%s' nicht parsebar — verwende 0",
                ws.title, ma_name, raw,
            )

    logger.info("Vormonat-Differenz gelesen aus Tab '%s': %s", ws.title, result)
    return result


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

        ws = tabs[-1]
        logger.info("Vormonats-Tab: '%s'", ws.title)
        rows = ws.get_all_values()
        if not rows:
            return {}

        # Kopfzeile: ["Tag", MA1, MA2, ..., "offen", "Tag"]
        header = rows[0]
        ma_names = header[1:]
        while ma_names and ma_names[-1].lower() in ("", "tag", "offen"):
            ma_names = ma_names[:-1]
        if ma_names and ma_names[-1].lower() == "offen":
            ma_names = ma_names[:-1]

        result: dict[str, dict[date, Dienst]] = {ma: {} for ma in ma_names}

        for row in rows[3:]:
            if not row or not row[0].strip():
                continue
            datum_raw = row[0].strip().lower()
            parsed_date: date | None = None
            for sep in [",", " "]:
                parts = datum_raw.split(sep, 1)
                if len(parts) == 2:
                    date_part = parts[1].strip().replace(".", "").strip()
                    tokens = date_part.split()
                    if len(tokens) >= 1:
                        try:
                            day = int(tokens[0])
                            if erster_des_monats.month == 1:
                                pm, py = 12, erster_des_monats.year - 1
                            else:
                                pm, py = erster_des_monats.month - 1, erster_des_monats.year
                            parsed_date = date(py, pm, day)
                            break
                        except ValueError:
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

    Zusammenfassungs-Block (wie im Original-Sheet):
      FREI | Früh | Spät | Nacht | Teamsitzung | BT | Urlaub
      [Leerzeile]
      Dienstplanstd. <Monat>   → Ist-Stunden
      Sollstd. <Monat>         → Soll-Stunden (aus ma_soll)
      Differenz                → Ist − Soll + Carry-over aus Vormonat

    Die Labels 'Dienstplanstd.' und 'Sollstd.' enthalten den
    variablen Monatsnamen (z.B. 'Dienstplanstd. Mai').
    Die Differenz-Zeile berücksichtigt die Differenz aus dem
    letzten Vormonats-Tab (Carry-over). Wird kein Vormonats-Tab
    gefunden, wird 0 eingesetzt — die Zeile erscheint immer.
    """
    erster     = tage[0]
    monat_name = _MONATE_LANG[erster.month]   # z.B. 'Apr.' / 'Mai'

    base_name = tab_name if tab_name else f"{_MONATE_KURZ[erster.month]}_{erster.year}"

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
    # Vormonat-Differenz (Carry-over) lesen — immer mit 0-Fallback
    # ---------------------------------------------------------------------------
    vormonat_diff: dict[str, float] = {ma: 0.0 for ma in mitarbeiter}
    try:
        vormonat_diff = read_vormonat_differenz(sh, erster, mitarbeiter)
    except Exception as e:
        logger.warning(
            "Vormonat-Differenz konnte nicht gelesen werden: %s — setze 0 für alle MA.", e
        )

    # ---------------------------------------------------------------------------
    # Zusammenfassungs-Block berechnen
    # ---------------------------------------------------------------------------
    n_cols = len(mitarbeiter) + 2  # MA-Spalten + offen + Tag

    count_labels = ["FREI", "Früh", "Spät", "Nacht", "Teamsitzung", "BT", "Urlaub"]
    dienst_keys  = ["Frei", "Früh", "Spät", "Nacht", "Team", "BT", "Urlaub"]

    summary_count_rows: list[list] = []
    for label, key in zip(count_labels, dienst_keys):
        row = [label]
        for ma_name in mitarbeiter:
            ma_plan = plan.get(ma_name, {})
            count = sum(
                1 for tag in tage
                if (ma_plan.get(tag) or _null_dienst()).value == key
            )
            row.append(count)
        row.append("")          # offen-Spalte leer
        row.append(label)       # rechtes Wiederholungs-Label
        summary_count_rows.append(row)

    # Leerzeile
    empty_row = [""] * (n_cols + 2)

    # Ist-Stunden pro MA — Label mit variablem Monatsnamen
    ist_label = f"Dienstplanstd. {monat_name}"
    ist_row = [ist_label]
    ist_values: dict[str, float] = {}
    for ma_name in mitarbeiter:
        ma_plan = plan.get(ma_name, {})
        ist = sum(
            _stunden_fuer(d.value)
            for tag in tage
            if (d := ma_plan.get(tag)) is not None
        )
        ist_values[ma_name] = round(ist, 1)
        ist_row.append(round(ist, 1) if ist > 0 else "")
    ist_row.append("")                  # offen
    ist_row.append("Dienstplanstd.")    # rechtes Label ohne Monat

    # Soll-Stunden pro MA — Label mit variablem Monatsnamen
    soll_label = f"Sollstd. {monat_name}"
    soll_dict  = ma_soll or {}
    soll_row   = [soll_label]
    soll_values: dict[str, float] = {}
    for ma_name in mitarbeiter:
        v = soll_dict.get(ma_name)
        soll_values[ma_name] = float(v) if v not in (None, "") else 0.0
        soll_row.append(v if v is not None else "")
    soll_row.append("")         # offen
    soll_row.append("Sollstd.") # rechtes Label

    # Differenz = Ist − Soll + Carry-over aus Vormonat
    # Erscheint immer — ohne Vormonat wird 0 eingesetzt
    diff_row = ["Differenz"]
    diff_values: dict[str, float | str] = {}
    for ma_name in mitarbeiter:
        ist_val   = ist_values.get(ma_name, 0.0)
        soll_val  = soll_values.get(ma_name, 0.0)
        carry     = vormonat_diff.get(ma_name, 0.0)  # 0.0 wenn kein Vormonat
        if soll_val != 0.0 or ist_val != 0.0:
            diff = round(ist_val - soll_val + carry, 1)
        else:
            diff = 0.0   # Zeile immer befüllen, auch wenn Ist+Soll = 0
        diff_values[ma_name] = diff
        diff_row.append(diff)
    diff_row.append("")           # offen
    diff_row.append("Differenz")  # rechtes Label

    # Zusammenfassung in Sheet schreiben
    data_end_row  = 4 + len(tage)
    summary_start = data_end_row + 2

    all_summary = summary_count_rows + [empty_row, ist_row, soll_row, diff_row]
    ws.update(f"A{summary_start}", all_summary)

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
            rgb = FARBEN_RGB.get(val) or FARBEN_RGB["Frei"]
            requests.append(_bg_request(ws.id, sheet_row - 1, col_idx - 1, *rgb))
            notiz = notiz_map.get((sheet_row, col_idx))
            if notiz:
                requests.append(_note_request(ws.id, sheet_row - 1, col_idx - 1, notiz))

        if tag.weekday() >= 5:
            for col in [0, len(mitarbeiter) + 2]:
                requests.append(_bg_request(ws.id, sheet_row - 1, col, 0.90, 0.90, 0.90))

    # Zusammenfassungs-Block einfärben
    for s_row_offset, (label, key) in enumerate(zip(count_labels, dienst_keys)):
        row_0 = summary_start + s_row_offset - 1  # 0-basiert

        requests.append(_bg_request(ws.id, row_0, 0, *_SUMMARY_LABEL_BG))
        requests.append(_bg_request(ws.id, row_0, len(mitarbeiter) + 2, *_SUMMARY_LABEL_BG))

        cell_rgb = FARBEN_RGB.get(key, _SUMMARY_VALUE_BG)
        for col_idx in range(1, len(mitarbeiter) + 2):
            requests.append(_bg_request(ws.id, row_0, col_idx, *cell_rgb))

    # Ist-/Soll-/Differenz-Zeilen einfärben
    ist_row_0  = summary_start + len(count_labels) + 1 - 1
    soll_row_0 = ist_row_0 + 1
    diff_row_0 = ist_row_0 + 2

    for col_idx in range(len(mitarbeiter) + 3):
        requests.append(_bg_request(ws.id, ist_row_0,  col_idx, *_SUMMARY_IST_BG))
        requests.append(_bg_request(ws.id, soll_row_0, col_idx, *_SUMMARY_SOLL_BG))

    # Differenz: Label-Spalten grau, Werte grün/rot je Vorzeichen
    requests.append(_bg_request(ws.id, diff_row_0, 0, *_SUMMARY_LABEL_BG))
    requests.append(_bg_request(ws.id, diff_row_0, len(mitarbeiter) + 2, *_SUMMARY_LABEL_BG))
    for i, ma_name in enumerate(mitarbeiter):
        diff_val = diff_values.get(ma_name, 0.0)
        if isinstance(diff_val, (int, float)):
            rgb = _SUMMARY_POS_BG if diff_val >= 0 else _SUMMARY_NEG_BG
        else:
            rgb = _SUMMARY_VALUE_BG
        requests.append(_bg_request(ws.id, diff_row_0, i + 1, *rgb))

    if requests:
        sh.batch_update({"requests": requests})

    logger.info(
        "Dienstplan '%s' geschrieben (%d Tage, Zusammenfassung ab Zeile %d)",
        final_name, len(tage), summary_start,
    )
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
