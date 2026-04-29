#!/usr/bin/env python3
"""
Strava Kudos Bot – Web-Session Version
Login via HTTP (2-Step: Email → Passwort, auth_version=v2)

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

load_dotenv()

BASE_DIR = Path(__file__).parent
LOG_FILE  = BASE_DIR / "kudos.log"

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

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(LOG_FILE),
        logging.StreamHandler(sys.stdout),
    ],
)
log = logging.getLogger(__name__)


# ── CSRF ─────────────────────────────────────────────────────────────────────
def _get_csrf_token(html: str) -> str:
    for pattern in [
        r'<meta\s+name=["\']csrf["\']\s+content=["\']([^"\']+)["\']',
        r'<meta\s+content=["\']([^"\']+)["\']\s+name=["\']csrf["\']',
        r'<meta\s+name=["\']csrf-token["\']\s+content=["\']([^"\']+)["\']',
        r'<meta\s+content=["\']([^"\']+)["\']\s+name=["\']csrf-token["\']',
        r'authenticity_token[^>]+value=["\']([^"\']+)["\']',
    ]:
        m = re.search(pattern, html)
        if m:
            return m.group(1)
    raise ValueError("CSRF-Token nicht gefunden.\nHTML-Anfang:\n" + html[:200])


# ── Login ──────────────────────────────────────────────────────────────────────
def _post_session(session, data, csrf):
    """
    POST /session ohne automatische Redirects.
    Gibt (status_code, headers, body_text) zurück.
    Strava gibt bei Erfolg manchmal 302 (Location-Header)
    oder 200 mit JSON {redirect_url: '...'} zurück.
    """
    resp = session.post(
        f"{BASE_URL}/session",
        data=data,
        headers={
            **HEADERS,
            "Accept":             "application/json, text/html, */*; q=0.01",
            "Content-Type":      "application/x-www-form-urlencoded; charset=UTF-8",
            "X-Requested-With":  "XMLHttpRequest",
            "Referer":           f"{BASE_URL}/login",
            "Origin":            BASE_URL,
            "X-CSRF-Token":      csrf,
        },
        allow_redirects=False,
        timeout=15,
    )
    return resp


def create_session() -> requests.Session:
    if not EMAIL or not PASSWORD:
        raise EnvironmentError("STRAVA_EMAIL oder STRAVA_PASSWORD fehlt.")

    session = requests.Session()
    session.headers.update(HEADERS)

    # 1. Login-Seite → CSRF
    log.info("Lade Login-Seite …")
    page = session.get(
        f"{BASE_URL}/login",
        headers={**HEADERS, "Accept": "text/html,*/*"},
        timeout=15,
    )
    page.raise_for_status()
    csrf = _get_csrf_token(page.text)
    log.info("CSRF: %s…", csrf[:16])

    # 2. Step 1: E-Mail einreichen
    log.info("Step 1: Email …")
    r1 = _post_session(session, {
        "authenticity_token": csrf,
        "email":              EMAIL,
        "logging_in":         "true",
        "auth_version":       "v2",
    }, csrf)
    log.info("Step 1 → %s", r1.status_code)
    log.debug("Step 1 Body: %s", r1.text[:300])

    otp_state = None
    try:
        d1 = r1.json()
        otp_state = d1.get("otp_state")
        log.info("Step 1 JSON: %s", d1)
    except Exception:
        pass

    # 3. Step 2: Passwort einreichen
    log.info("Step 2: Passwort …")
    payload2 = {
        "authenticity_token": csrf,
        "password":           PASSWORD,
        "remember_me":        "on",
        "auth_version":       "v2",
    }
    if otp_state:
        payload2["otp_state"] = otp_state

    r2 = _post_session(session, payload2, csrf)
    log.info("Step 2 → %s", r2.status_code)
    log.debug("Step 2 Body: %s", r2.text[:400])
    log.debug("Step 2 Headers: %s", dict(r2.headers))

    # 4. Redirect auflösen
    redirect_url = None

    # Fall A: HTTP 302
    if r2.status_code in (301, 302, 303):
        redirect_url = r2.headers.get("Location", "/dashboard")
        if redirect_url.startswith("/"):
            redirect_url = BASE_URL + redirect_url
        log.info("302 Redirect → %s", redirect_url)

    # Fall B: JSON mit redirect_url
    elif r2.status_code == 200:
        try:
            d2 = r2.json()
            log.info("Step 2 JSON: %s", d2)
            redirect_url = d2.get("redirect_url") or d2.get("redirectUrl")
            if redirect_url and redirect_url.startswith("/"):
                redirect_url = BASE_URL + redirect_url
        except Exception:
            pass

    if not redirect_url:
        # Fallback: Dashboard direkt ansteuern
        redirect_url = f"{BASE_URL}/dashboard"
        log.warning("Kein Redirect gefunden, versuche %s direkt.", redirect_url)

    # 5. Redirect GET → Session-Cookie setzen lassen
    final = session.get(
        redirect_url,
        headers={**HEADERS, "Accept": "text/html,*/*", "Referer": f"{BASE_URL}/login"},
        allow_redirects=True,
        timeout=15,
    )
    log.info("Final URL: %s | Status: %s", final.url, final.status_code)

    if "/login" in final.url:
        log.error("Login-HTML-Snippet: %s", final.text[final.text.find('<title'):final.text.find('<title')+80])
        raise PermissionError(
            f"Login fehlgeschlagen – Email/Passwort prüfen oder 2FA aktiv.\nFinal URL: {final.url}"
        )

    log.info("✅ Eingeloggt! URL: %s", final.url)
    return session


# ── Feed ───────────────────────────────────────────────────────────────────────
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
    log.info("Feed Status: %s | Content-Type: %s", resp.status_code,
             resp.headers.get("Content-Type", "?"))
    if not resp.ok:
        raise RuntimeError(f"Feed fehlgeschlagen: {resp.status_code}\n{resp.text[:200]}")
    try:
        data = resp.json()
    except json.JSONDecodeError:
        raise RuntimeError(f"Feed kein JSON (nicht eingeloggt?):\n{resp.text[:400]}")
    if isinstance(data, list):
        return data
    return data.get("entries", data.get("feed", data.get("activities", [])))


# ── Kudos ───────────────────────────────────────────────────────────────────────
def _extract_activity_id(entry):
    for key in ("activity_id", "id", "object_id"):
        v = entry.get(key)
        if v:
            return int(v)
    v = entry.get("activity", {}).get("id")
    return int(v) if v else None


def _already_kudosed(entry) -> bool:
    act = entry.get("activity", entry)
    return bool(act.get("kudosed") or act.get("has_kudoed") or entry.get("kudosed"))


def give_kudos_to_feed(session, entries) -> tuple:
    log.info("Hole CSRF vom Dashboard …")
    dash = session.get(f"{BASE_URL}/dashboard", timeout=15)
    csrf = _get_csrf_token(dash.text)

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
            f"{BASE_URL}/activities/{act_id}/kudos",
            headers={
                **HEADERS,
                "X-CSRF-Token":     csrf,
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


# ── Main ───────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    try:
        session = create_session()
        entries = get_feed(session)
        log.info("%d Einträge im Feed.", len(entries))
        if not entries:
            log.warning("Feed leer.")
            sys.exit(0)
        given, skipped = give_kudos_to_feed(session, entries)
        log.info("Fertig. ✅ Kudos: %d | ⏭ Skipped: %d", given, skipped)
    except PermissionError as e:
        log.error("🔒 %s", e)
        sys.exit(1)
    except Exception as e:
        log.error("❌ %s", e)
        raise
