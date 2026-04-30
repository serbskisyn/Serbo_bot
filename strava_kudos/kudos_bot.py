#!/usr/bin/env python3
"""
Strava Kudos Bot – Web-Session via gespeichertem Browser-Cookie

Setup (einmalig, wenn Session abläuft):
  1. Im Browser bei Strava einloggen
  2. DevTools öffnen (F12) → Application → Cookies → https://www.strava.com
  3. Wert von '_strava4_session' kopieren
  4. python kudos_bot.py --set-session WERT_HIER_EINFÜGEN

Cronjob (täglich 08:00 Uhr):
  0 8 * * * cd /home/pi/Serbo_bot/strava_kudos && \\
    /home/pi/Serbo_bot/.venv/bin/python kudos_bot.py >> kudos.log 2>&1
"""

import os
import re
import sys
import json
import time
import logging
from pathlib import Path
from datetime import datetime

import requests
from dotenv import load_dotenv

load_dotenv()

BASE_DIR      = Path(__file__).parent
LOG_FILE      = BASE_DIR / "kudos.log"
SESSION_FILE  = BASE_DIR / "session.json"

BASE_URL   = "https://www.strava.com"
FEED_LIMIT = 30
DELAY      = 2.0

# Telegram
TG_TOKEN   = os.getenv("TELEGRAM_BOT_TOKEN")
TG_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "de-DE,de;q=0.9,en;q=0.8",
}

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(LOG_FILE),
        logging.StreamHandler(sys.stdout),
    ],
)
log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Telegram
# ---------------------------------------------------------------------------

def send_telegram(text: str):
    """Sendet eine Nachricht an den konfigurierten Telegram-Chat."""
    if not TG_TOKEN or not TG_CHAT_ID:
        log.debug("Telegram nicht konfiguriert – überspringe Benachrichtigung.")
        return
    try:
        r = requests.post(
            f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage",
            json={"chat_id": TG_CHAT_ID, "text": text},
            timeout=10,
        )
        if r.ok:
            log.info("Telegram-Nachricht gesendet.")
        else:
            log.warning("Telegram Fehler: %s", r.text[:200])
    except Exception as e:
        log.warning("Telegram nicht erreichbar: %s", e)


# ---------------------------------------------------------------------------
# Session management
# ---------------------------------------------------------------------------

def load_session_cookie() -> str:
    if SESSION_FILE.exists():
        data = json.loads(SESSION_FILE.read_text())
        cookie = data.get("_strava4_session", "")
        if cookie:
            return cookie
    return os.getenv("STRAVA_SESSION_COOKIE", "")


def save_session_cookie(value: str):
    SESSION_FILE.write_text(json.dumps({"_strava4_session": value}, indent=2))
    log.info("Session-Cookie gespeichert in %s", SESSION_FILE)


def build_session(cookie_value: str) -> requests.Session:
    session = requests.Session()
    session.headers.update(HEADERS)
    session.cookies.set("_strava4_session", cookie_value, domain="www.strava.com")
    return session


# ---------------------------------------------------------------------------
# CSRF
# ---------------------------------------------------------------------------

def _get_csrf(html: str) -> str:
    for pat in [
        r'<meta\s+name=["\'"]csrf-token["\']\s+content=["\'"]([^"\']+)["\'"]',
        r'<meta\s+content=["\'"]([^"\']+)["\']\s+name=["\'"]csrf-token["\'"]',
        r'<meta\s+name=["\'"]csrf["\']\s+content=["\'"]([^"\']+)["\'"]',
        r'<meta\s+content=["\'"]([^"\']+)["\']\s+name=["\'"]csrf["\'"]',
    ]:
        m = re.search(pat, html)
        if m:
            return m.group(1)
    raise ValueError("CSRF-Token nicht gefunden.")


# ---------------------------------------------------------------------------
# Feed
# ---------------------------------------------------------------------------

def check_session(session: requests.Session) -> bool:
    r = session.get(f"{BASE_URL}/dashboard",
                    headers={**HEADERS, "Accept": "text/html,*/*"},
                    allow_redirects=True, timeout=15)
    return "/login" not in r.url


def get_feed(session: requests.Session) -> list:
    log.info("Lade Friend Feed ...")
    resp = session.get(
        f"{BASE_URL}/dashboard/feed",
        params={"feed_type": "following", "num_entries": FEED_LIMIT},
        headers={
            **HEADERS,
            "Accept":           "application/json, text/javascript, */*; q=0.01",
            "X-Requested-With": "XMLHttpRequest",
            "Referer":          f"{BASE_URL}/dashboard",
        },
        timeout=15,
    )
    log.info("Feed Status: %s | CT: %s", resp.status_code,
             resp.headers.get("Content-Type", "?"))
    if not resp.ok:
        raise RuntimeError(f"Feed {resp.status_code}: {resp.text[:200]}")
    try:
        data = resp.json()
    except json.JSONDecodeError:
        raise RuntimeError(f"Feed kein JSON:\n{resp.text[:400]}")
    if isinstance(data, list):
        return data
    return data.get("entries", data.get("feed", data.get("activities", [])))


# ---------------------------------------------------------------------------
# Kudos
# ---------------------------------------------------------------------------

def _extract_activity_id(entry) -> int | None:
    v = entry.get("activity", {}).get("id")
    if v:
        return int(v)
    for key in ("activity_id", "id", "object_id"):
        v = entry.get(key)
        if v:
            return int(v)
    return None


def _already_kudosed(entry) -> bool:
    act = entry.get("activity", entry)
    kac = act.get("kudosAndComments", {})
    if kac.get("hasKudoed"):
        return True
    if not kac.get("canKudo") and kac:
        return True
    return bool(act.get("kudosed") or act.get("has_kudoed") or entry.get("kudosed"))


def give_kudos_to_feed(session: requests.Session, entries: list) -> tuple:
    log.info("Hole CSRF vom Dashboard ...")
    dash = session.get(f"{BASE_URL}/dashboard",
                       headers={**HEADERS, "Accept": "text/html,*/*"}, timeout=15)
    if "/login" in dash.url:
        raise PermissionError("Session abgelaufen – bitte --set-session ausführen.")
    csrf = _get_csrf(dash.text)

    given = skipped = errors = 0
    kudosed_names = []

    for entry in entries:
        act_id = _extract_activity_id(entry)
        if not act_id:
            continue
        act     = entry.get("activity", entry)
        athlete = act.get("athlete", {}).get("display_name", "?")
        name    = act.get("name", "Activity")

        if _already_kudosed(entry):
            skipped += 1
            continue

        r = session.post(
            f"{BASE_URL}/feed/activity/{act_id}/kudo",
            headers={
                **HEADERS,
                "x-csrf-token":     csrf,
                "X-Requested-With": "XMLHttpRequest",
                "Referer":          f"{BASE_URL}/dashboard",
                "Accept":           "application/json, */*",
            },
            timeout=10,
        )
        if r.status_code in (200, 201, 204):
            log.info("Kudos: %s - %s", athlete, name)
            kudosed_names.append(f"{athlete} - {name}")
            given += 1
            time.sleep(DELAY)
        elif r.status_code == 429:
            log.warning("Rate Limit – stoppe.")
            break
        elif r.status_code == 401:
            log.warning("Privat: %s", act_id)
            skipped += 1
        else:
            log.warning("%s bei %s: %s", r.status_code, act_id, r.text[:100])
            errors += 1

    return given, skipped, errors, kudosed_names


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    # --set-session <value>
    if "--set-session" in sys.argv:
        idx = sys.argv.index("--set-session")
        if idx + 1 >= len(sys.argv):
            print("Verwendung: python kudos_bot.py --set-session <_strava4_session-Cookie-Wert>")
            sys.exit(1)
        cookie_val = sys.argv[idx + 1]
        save_session_cookie(cookie_val)
        session = build_session(cookie_val)
        if check_session(session):
            log.info("Session gueltig!")
        else:
            log.error("Session ungueltig – Cookie nochmals pruefen.")
            sys.exit(1)
        sys.exit(0)

    # Normal run
    cookie = load_session_cookie()
    if not cookie:
        msg = (
            "Kein Session-Cookie gefunden.\n"
            "Einmalig ausfuehren:\n"
            "  python kudos_bot.py --set-session <_strava4_session-Cookie-Wert>"
        )
        log.error(msg)
        send_telegram(f"Strava Kudos Bot\n\n{msg}")
        sys.exit(1)

    session = build_session(cookie)
    ts = datetime.now().strftime("%d.%m.%Y %H:%M")

    try:
        if not check_session(session):
            msg = (
                "Session abgelaufen.\n"
                "Neu einloggen und Cookie aktualisieren:\n"
                "  python kudos_bot.py --set-session <neuer_cookie_wert>"
            )
            log.error(msg)
            send_telegram(
                f"Strava Kudos Bot - {ts}\n\n"
                f"Session abgelaufen.\n"
                f"Cookie muss erneuert werden:\n"
                f"python kudos_bot.py --set-session <cookie>"
            )
            sys.exit(1)

        entries = get_feed(session)
        total   = len(entries)
        log.info("%d Eintraege im Feed.", total)

        if not entries:
            log.warning("Feed leer.")
            send_telegram(
                f"Strava Kudos Bot - {ts}\n\n"
                f"Feed leer - nichts Neues heute."
            )
            sys.exit(0)

        given, skipped, errors, names = give_kudos_to_feed(session, entries)
        log.info("Fertig. %d Kudos | %d Skipped | %d Errors", given, skipped, errors)

        # Telegram-Nachricht bauen
        lines = [
            f"Strava Kudos Bot - {ts}",
            "",
            f"Feed: {total} Aktivitaeten gefunden",
            f"Kudos gegeben: {given}",
            f"Uebersprungen: {skipped} (bereits geliked / privat)",
        ]
        if errors:
            lines.append(f"Fehler: {errors}")
        if names:
            lines.append("")
            lines.append("Geliked:")
            for n in names[:10]:
                lines.append(f"- {n}")
            if len(names) > 10:
                lines.append(f"- ... und {len(names) - 10} weitere")

        send_telegram("\n".join(lines))

    except PermissionError as e:
        log.error("Session-Fehler: %s", e)
        send_telegram(f"Strava Kudos Bot\n\nSession-Fehler: {e}")
        sys.exit(1)
    except Exception as e:
        log.error("Fehler: %s", e)
        send_telegram(f"Strava Kudos Bot\n\nFehler: {e}")
        raise
