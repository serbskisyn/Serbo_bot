#!/usr/bin/env python3
"""
Strava Kudos Bot – Web-Session via gespeichertem Browser-Cookie

Setup (einmalig, wenn Session abläuft):
  1. Im Browser bei Strava einloggen
  2. DevTools öffnen (F12) → Application → Cookies → https://www.strava.com
  3. Wert von '_strava4_session' kopieren
  4. python kudos_bot.py --set-session WERT_HIER_EINFÜGEN

Cronjob (alle 30 Min):
  */30 * * * * cd /home/pi/Serbo_bot/strava_kudos && \
    /home/pi/Serbo_bot/.venv/bin/python kudos_bot.py >> kudos.log 2>&1
"""

import os
import re
import sys
import json
import time
import logging
from pathlib import Path

import requests
from dotenv import load_dotenv

load_dotenv()

BASE_DIR      = Path(__file__).parent
LOG_FILE      = BASE_DIR / "kudos.log"
SESSION_FILE  = BASE_DIR / "session.json"

BASE_URL   = "https://www.strava.com"
FEED_LIMIT = 30
DELAY      = 2.0

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
# Session management
# ---------------------------------------------------------------------------

def load_session_cookie() -> str:
    """Load _strava4_session cookie from session.json or .env."""
    if SESSION_FILE.exists():
        data = json.loads(SESSION_FILE.read_text())
        cookie = data.get("_strava4_session", "")
        if cookie:
            return cookie
    cookie = os.getenv("STRAVA_SESSION_COOKIE", "")
    if cookie:
        return cookie
    return ""


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
        r'<meta\s+name=["\']csrf-token["\']\s+content=["\']([^"\']+)["\']',
        r'<meta\s+content=["\']([^"\']+)["\']\s+name=["\']csrf-token["\']',
        r'<meta\s+name=["\']csrf["\']\s+content=["\']([^"\']+)["\']',
        r'<meta\s+content=["\']([^"\']+)["\']\s+name=["\']csrf["\']',
    ]:
        m = re.search(pat, html)
        if m:
            return m.group(1)
    raise ValueError("CSRF-Token nicht gefunden.")


# ---------------------------------------------------------------------------
# Feed
# ---------------------------------------------------------------------------

def check_session(session: requests.Session) -> bool:
    """Returns True if the session cookie is still valid."""
    r = session.get(f"{BASE_URL}/dashboard",
                    headers={**HEADERS, "Accept": "text/html,*/*"},
                    allow_redirects=True, timeout=15)
    return "/login" not in r.url


def get_feed(session: requests.Session) -> list:
    log.info("Lade Friend Feed …")
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
    # New format: {"entries": [...], "pagination": {...}}
    return data.get("entries", data.get("feed", data.get("activities", [])))


# ---------------------------------------------------------------------------
# Kudos
# ---------------------------------------------------------------------------

def _extract_activity_id(entry) -> int | None:
    # New feed format: entry.activity.id
    v = entry.get("activity", {}).get("id")
    if v:
        return int(v)
    # Legacy fallback
    for key in ("activity_id", "id", "object_id"):
        v = entry.get(key)
        if v:
            return int(v)
    return None


def _already_kudosed(entry) -> bool:
    act = entry.get("activity", entry)
    # New format: kudosAndComments.hasKudoed / canKudo
    kac = act.get("kudosAndComments", {})
    if kac.get("hasKudoed"):
        return True
    if not kac.get("canKudo") and kac:
        return True  # own activity or unavailable
    # Legacy fallback
    return bool(act.get("kudosed") or act.get("has_kudoed") or entry.get("kudosed"))


def give_kudos_to_feed(session: requests.Session, entries: list) -> tuple:
    log.info("Hole CSRF vom Dashboard …")
    dash = session.get(f"{BASE_URL}/dashboard",
                       headers={**HEADERS, "Accept": "text/html,*/*"}, timeout=15)
    if "/login" in dash.url:
        raise PermissionError("Session abgelaufen – bitte --set-session ausführen.")
    csrf = _get_csrf(dash.text)

    given = skipped = 0
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
            log.info("✅ Kudos: %s – %s", athlete, name)
            given += 1
            time.sleep(DELAY)
        elif r.status_code == 429:
            log.warning("⚠️ Rate Limit – stoppe.")
            break
        elif r.status_code == 401:
            log.warning("🔒 Privat: %s", act_id)
        else:
            log.warning("❌ %s bei %s: %s", r.status_code, act_id, r.text[:100])
    return given, skipped


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    # --set-session <value>
    if "--set-session" in sys.argv:
        idx = sys.argv.index("--set-session")
        if idx + 1 >= len(sys.argv):
            print("Verwendung: python kudos_bot.py --set-session <_strava4_session-Cookie-Wert>")
            print()
            print("So findest du den Cookie:")
            print("  1. Im Browser bei strava.com einloggen")
            print("  2. F12 → Application → Cookies → https://www.strava.com")
            print("  3. Wert von '_strava4_session' kopieren")
            sys.exit(1)
        cookie_val = sys.argv[idx + 1]
        save_session_cookie(cookie_val)
        # Verify
        session = build_session(cookie_val)
        if check_session(session):
            log.info("✅ Session gültig!")
        else:
            log.error("❌ Session ungültig – Cookie nochmals prüfen.")
            sys.exit(1)
        sys.exit(0)

    # Normal run
    cookie = load_session_cookie()
    if not cookie:
        log.error(
            "Kein Session-Cookie gefunden.\n"
            "Einmalig ausführen:\n"
            "  python kudos_bot.py --set-session <_strava4_session-Cookie-Wert>\n"
            "\n"
            "Den Cookie findest du im Browser:\n"
            "  F12 → Application → Cookies → https://www.strava.com → _strava4_session"
        )
        sys.exit(1)

    session = build_session(cookie)

    try:
        if not check_session(session):
            log.error(
                "🔒 Session abgelaufen.\n"
                "Neu einloggen und Cookie aktualisieren:\n"
                "  python kudos_bot.py --set-session <neuer_cookie_wert>"
            )
            sys.exit(1)

        entries = get_feed(session)
        log.info("%d Einträge im Feed.", len(entries))

        if not entries:
            log.warning("Feed leer – evtl. gibt es nichts Neues.")
            sys.exit(0)

        given, skipped = give_kudos_to_feed(session, entries)
        log.info("Fertig. ✅ %d Kudos | ⏭ %d Skipped", given, skipped)

    except PermissionError as e:
        log.error("🔒 %s", e)
        sys.exit(1)
    except Exception as e:
        log.error("❌ %s", e)
        raise
