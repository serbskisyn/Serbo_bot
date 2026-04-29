#!/usr/bin/env python3
"""
Strava Kudos Bot – Web-Session Version
Kein Browser, kein Playwright, kein API-Key.
Login via HTTP direkt, echter Friend Feed.

Setup:
  cp .env.example .env
  nano .env  # STRAVA_EMAIL + STRAVA_PASSWORD eintragen

Lauf:
  python kudos_bot.py

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

# ── Konfiguration ────────────────────────────────────────────────────────────
load_dotenv()

BASE_DIR = Path(__file__).parent
LOG_FILE = BASE_DIR / "kudos.log"

EMAIL    = os.getenv("STRAVA_EMAIL")
PASSWORD = os.getenv("STRAVA_PASSWORD")

BASE_URL   = "https://www.strava.com"
FEED_LIMIT = 30   # Activities pro Lauf
DELAY      = 2.0  # Sekunden zwischen Kudos (Rate-Limit-Schutz)

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "de-DE,de;q=0.9,en;q=0.8",
}

# ── Logging ──────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(LOG_FILE),
        logging.StreamHandler(sys.stdout),
    ],
)
log = logging.getLogger(__name__)


# ── Session / Login ───────────────────────────────────────────────────────────
def _get_csrf_token(html: str) -> str:
    """
    Extrahiert CSRF-Token aus HTML.
    Unterstützt alle bekannten Strava-Formate:
      - <meta name="csrf" content="...">          (aktuell, Next.js)
      - <meta name="csrf-token" content="...">    (alt, Rails)
      - authenticity_token value="..."            (Formular-Fallback)
    """
    patterns = [
        r'<meta\s+name=["\']csrf["\']\s+content=["\']([^"\']+)["\']',
        r'<meta\s+content=["\']([^"\']+)["\']\s+name=["\']csrf["\']',
        r'<meta\s+name=["\']csrf-token["\']\s+content=["\']([^"\']+)["\']',
        r'<meta\s+content=["\']([^"\']+)["\']\s+name=["\']csrf-token["\']',
        r'authenticity_token["\']\s+value=["\']([^"\']+)["\']',
    ]
    for pattern in patterns:
        match = re.search(pattern, html)
        if match:
            return match.group(1)
    raise ValueError(
        "CSRF-Token nicht gefunden. HTML-Snippet:\n"
        + html[:500]
    )


def create_session() -> requests.Session:
    """Loggt sich bei Strava ein und gibt eine authentifizierte Session zurück."""
    if not EMAIL or not PASSWORD:
        raise EnvironmentError(
            "STRAVA_EMAIL oder STRAVA_PASSWORD fehlt. Bitte .env befüllen."
        )

    session = requests.Session()
    session.headers.update(HEADERS)

    # 1. Login-Seite laden (CSRF-Token holen)
    log.info("Lade Login-Seite …")
    resp = session.get(f"{BASE_URL}/login", timeout=15)
    resp.raise_for_status()
    csrf = _get_csrf_token(resp.text)
    log.info("CSRF-Token gefunden: %s…", csrf[:16])

    # 2. Login POST
    log.info("Logge ein als %s …", EMAIL)
    login_resp = session.post(
        f"{BASE_URL}/session",
        data={
            "utf8":               "✓",
            "authenticity_token": csrf,
            "plan":               "",
            "email":              EMAIL,
            "password":           PASSWORD,
            "remember_me":        "on",
        },
        headers={
            **HEADERS,
            "Content-Type": "application/x-www-form-urlencoded",
            "Referer":      f"{BASE_URL}/login",
            "Origin":       BASE_URL,
        },
        allow_redirects=True,
        timeout=15,
    )

    # Login-Check: nach /dashboard weitergeleitet?
    if "/login" in login_resp.url or "/session" in login_resp.url:
        raise PermissionError(
            "Login fehlgeschlagen. Email/Passwort prüfen oder 2FA aktiv."
        )

    log.info("✅ Eingeloggt. URL: %s", login_resp.url)
    return session


# ── Feed abrufen ──────────────────────────────────────────────────────────────
def get_feed(session: requests.Session) -> list:
    """Holt den Friend-Feed als Liste von Activity-Dicts."""
    log.info("Lade Friend Feed …")
    resp = session.get(
        f"{BASE_URL}/dashboard/feed",
        params={
            "feed_type":   "following",
            "num_entries": FEED_LIMIT,
        },
        headers={
            **HEADERS,
            "Accept":             "application/json, text/javascript, */*; q=0.01",
            "X-Requested-With":   "XMLHttpRequest",
            "Referer":            f"{BASE_URL}/dashboard",
        },
        timeout=15,
    )

    if not resp.ok:
        raise RuntimeError(
            f"Feed-Abruf fehlgeschlagen: {resp.status_code}\n{resp.text[:300]}"
        )

    try:
        data = resp.json()
    except json.JSONDecodeError:
        raise RuntimeError(
            f"Feed ist kein JSON (Strava-Struktur geändert?):\n{resp.text[:400]}"
        )

    log.debug("Feed-Rohdaten Typ: %s, Keys: %s",
              type(data).__name__,
              list(data.keys()) if isinstance(data, dict) else "(Liste)")

    if isinstance(data, list):
        return data
    return data.get("entries", data.get("feed", data.get("activities", [])))


# ── Kudos geben ───────────────────────────────────────────────────────────────
def _extract_activity_id(entry: dict) -> int | None:
    """Extrahiert die Activity-ID aus einem Feed-Eintrag."""
    for key in ("activity_id", "id", "object_id"):
        val = entry.get(key)
        if val:
            return int(val)
    activity = entry.get("activity", {})
    val = activity.get("id")
    if val:
        return int(val)
    return None


def _already_kudosed(entry: dict) -> bool:
    activity = entry.get("activity", entry)
    return bool(
        activity.get("kudosed")
        or activity.get("has_kudoed")
        or entry.get("kudosed")
    )


def give_kudos_to_feed(session: requests.Session, entries: list) -> tuple[int, int]:
    """Gibt Kudos auf alle nicht-bekudosten Activities."""
    # Frisches CSRF-Token vom Dashboard holen
    log.info("Hole CSRF-Token vom Dashboard …")
    dash_resp = session.get(f"{BASE_URL}/dashboard", timeout=15)
    csrf = _get_csrf_token(dash_resp.text)
    log.info("Dashboard CSRF: %s…", csrf[:16])

    given   = 0
    skipped = 0

    for entry in entries:
        act_id = _extract_activity_id(entry)
        if not act_id:
            log.debug("Kein Activity-ID: %s", str(entry)[:100])
            continue

        activity = entry.get("activity", entry)
        athlete  = activity.get("athlete", {}).get("display_name", "?")
        name     = activity.get("name", "Activity")

        if _already_kudosed(entry):
            log.debug("Bereits bekudost: %s – %s", athlete, name)
            skipped += 1
            continue

        resp = session.post(
            f"{BASE_URL}/activities/{act_id}/kudos",
            headers={
                **HEADERS,
                "X-CSRF-Token":      csrf,
                "X-Requested-With":  "XMLHttpRequest",
                "Referer":           f"{BASE_URL}/dashboard",
                "Accept":            "application/json, text/javascript, */*; q=0.01",
            },
            timeout=10,
        )

        if resp.status_code in (200, 201, 204):
            log.info("✅ Kudos: %s – %s", athlete, name)
            given += 1
            time.sleep(DELAY)
        elif resp.status_code == 429:
            log.warning("⚠️  Rate Limit – stoppe.")
            break
        elif resp.status_code == 401:
            log.warning("🔒 Privat/nicht authorisiert: Activity %s", act_id)
        else:
            log.warning("❌ Fehler %s bei Activity %s: %s",
                        resp.status_code, act_id, resp.text[:120])

    return given, skipped


# ── Einstieg ──────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    try:
        session = create_session()
        entries = get_feed(session)
        log.info("%d Einträge im Feed.", len(entries))

        if not entries:
            log.warning("Feed leer – Strava-Struktur geändert? Bitte melden.")
            sys.exit(0)

        given, skipped = give_kudos_to_feed(session, entries)
        log.info("Fertig. ✅ Kudos: %d | ⏭ Skipped: %d", given, skipped)

    except PermissionError as e:
        log.error("🔒 %s", e)
        sys.exit(1)
    except Exception as e:
        log.error("❌ %s", e)
        raise
