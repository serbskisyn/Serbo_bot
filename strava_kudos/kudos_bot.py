#!/usr/bin/env python3
"""
Strava Kudos Bot – Web-Session Version
Kein Browser, kein Playwright, kein API-Key.
Login via HTTP (2-Step: Email → Passwort), echter Friend Feed.

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


# ── Hilfsfunktionen ──────────────────────────────────────────────────────────
def _get_csrf_token(html: str) -> str:
    """
    Unterstützt alle bekannten Strava-CSRF-Formate:
      <meta name="csrf" content="...">       (aktuell, Next.js)
      <meta name="csrf-token" content="..."> (alt, Rails)
    """
    patterns = [
        r'<meta\s+name=["\']csrf["\']\s+content=["\']([^"\']+)["\']',
        r'<meta\s+content=["\']([^"\']+)["\']\s+name=["\']csrf["\']',
        r'<meta\s+name=["\']csrf-token["\']\s+content=["\']([^"\']+)["\']',
        r'<meta\s+content=["\']([^"\']+)["\']\s+name=["\']csrf-token["\']',
        r'authenticity_token[^>]+value=["\']([^"\']+)["\']',
    ]
    for pattern in patterns:
        match = re.search(pattern, html)
        if match:
            return match.group(1)
    raise ValueError("CSRF-Token nicht gefunden.\nHTML-Anfang:\n" + html[:300])


# ── Login (2-Step: Email → Passwort) ──────────────────────────────────────────
def create_session() -> requests.Session:
    """Loggt sich bei Strava ein (neuer 2-Step-Flow mit auth_version=v2)."""
    if not EMAIL or not PASSWORD:
        raise EnvironmentError("STRAVA_EMAIL oder STRAVA_PASSWORD fehlt.")

    session = requests.Session()
    session.headers.update(HEADERS)

    # ─ 1. Login-Seite laden → CSRF-Token ────────────────────────────────
    log.info("Lade Login-Seite …")
    page = session.get(
        f"{BASE_URL}/login",
        headers={**HEADERS, "Accept": "text/html,*/*"},
        timeout=15,
    )
    page.raise_for_status()
    csrf = _get_csrf_token(page.text)
    log.info("CSRF-Token: %s…", csrf[:16])

    post_headers = {
        **HEADERS,
        "Accept":          "application/json, text/javascript, */*; q=0.01",
        "Content-Type":    "application/x-www-form-urlencoded; charset=UTF-8",
        "X-Requested-With": "XMLHttpRequest",
        "Referer":          f"{BASE_URL}/login",
        "Origin":           BASE_URL,
        "X-CSRF-Token":     csrf,
    }

    # ─ 2. Step 1: E-Mail einreichen → otp_state + use_password holen ──────
    log.info("Step 1: Email einreichen …")
    step1 = session.post(
        f"{BASE_URL}/session",
        data={
            "authenticity_token": csrf,
            "email":              EMAIL,
            "logging_in":         "true",
            "auth_version":       "v2",
        },
        headers=post_headers,
        allow_redirects=False,
        timeout=15,
    )
    log.info("Step 1 Status: %s", step1.status_code)

    otp_state = None
    if step1.status_code == 200:
        try:
            data1 = step1.json()
            log.info("Step 1 Response: %s", data1)
            otp_state = data1.get("otp_state")
        except Exception:
            log.debug("Step 1 kein JSON: %s", step1.text[:200])

    # ─ 3. Step 2: Passwort + otp_state einreichen ───────────────────────
    log.info("Step 2: Passwort einreichen …")
    step2_data = {
        "authenticity_token": csrf,
        "password":           PASSWORD,
        "remember_me":        "on",
        "auth_version":       "v2",
    }
    if otp_state:
        step2_data["otp_state"] = otp_state

    step2 = session.post(
        f"{BASE_URL}/session",
        data=step2_data,
        headers={
            **post_headers,
            "Referer": f"{BASE_URL}/login",
        },
        allow_redirects=True,
        timeout=15,
    )
    log.info("Step 2 Status: %s | URL: %s", step2.status_code, step2.url)

    # Login-Check
    if "/login" in step2.url:
        try:
            err = step2.json()
            log.error("Login-Fehler Details: %s", err)
        except Exception:
            pass
        raise PermissionError(
            "Login fehlgeschlagen – Email/Passwort prüfen oder 2FA aktiv.\n"
            f"Final URL: {step2.url}"
        )

    log.info("✅ Eingeloggt! URL: %s", step2.url)
    return session


# ── Feed abrufen ──────────────────────────────────────────────────────────────
def get_feed(session: requests.Session) -> list:
    log.info("Lade Friend Feed …")
    resp = session.get(
        f"{BASE_URL}/dashboard/feed",
        params={"feed_type": "following", "num_entries": FEED_LIMIT},
        headers={
            **HEADERS,
            "Accept":            "application/json, text/javascript, */*; q=0.01",
            "X-Requested-With":  "XMLHttpRequest",
            "Referer":           f"{BASE_URL}/dashboard",
        },
        timeout=15,
    )
    if not resp.ok:
        raise RuntimeError(f"Feed fehlgeschlagen: {resp.status_code}\n{resp.text[:300]}")
    try:
        data = resp.json()
    except json.JSONDecodeError:
        raise RuntimeError(f"Feed kein JSON:\n{resp.text[:400]}")
    log.debug("Feed-Typ: %s", type(data).__name__)
    if isinstance(data, list):
        return data
    return data.get("entries", data.get("feed", data.get("activities", [])))


# ── Kudos geben ───────────────────────────────────────────────────────────────
def _extract_activity_id(entry: dict):
    for key in ("activity_id", "id", "object_id"):
        val = entry.get(key)
        if val:
            return int(val)
    val = entry.get("activity", {}).get("id")
    return int(val) if val else None


def _already_kudosed(entry: dict) -> bool:
    act = entry.get("activity", entry)
    return bool(act.get("kudosed") or act.get("has_kudoed") or entry.get("kudosed"))


def give_kudos_to_feed(session: requests.Session, entries: list) -> tuple[int, int]:
    log.info("Hole CSRF-Token vom Dashboard …")
    dash = session.get(f"{BASE_URL}/dashboard", timeout=15)
    csrf = _get_csrf_token(dash.text)

    given = skipped = 0
    for entry in entries:
        act_id = _extract_activity_id(entry)
        if not act_id:
            continue
        activity = entry.get("activity", entry)
        athlete  = activity.get("athlete", {}).get("display_name", "?")
        name     = activity.get("name", "Activity")

        if _already_kudosed(entry):
            log.debug("Skip: %s – %s", athlete, name)
            skipped += 1
            continue

        resp = session.post(
            f"{BASE_URL}/activities/{act_id}/kudos",
            headers={
                **HEADERS,
                "X-CSRF-Token":      csrf,
                "X-Requested-With":  "XMLHttpRequest",
                "Referer":           f"{BASE_URL}/dashboard",
                "Accept":            "application/json, */*; q=0.01",
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
            log.warning("🔒 Privat: %s", act_id)
        else:
            log.warning("❌ %s bei %s: %s", resp.status_code, act_id, resp.text[:120])
    return given, skipped


# ── Einstieg ──────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    try:
        session = create_session()
        entries = get_feed(session)
        log.info("%d Einträge im Feed.", len(entries))
        if not entries:
            log.warning("Feed leer – Strava-Struktur geändert?")
            sys.exit(0)
        given, skipped = give_kudos_to_feed(session, entries)
        log.info("Fertig. ✅ Kudos: %d | ⏭ Skipped: %d", given, skipped)
    except PermissionError as e:
        log.error("🔒 %s", e)
        sys.exit(1)
    except Exception as e:
        log.error("❌ %s", e)
        raise
