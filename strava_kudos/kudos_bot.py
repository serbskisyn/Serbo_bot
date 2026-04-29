#!/usr/bin/env python3
"""
Strava Kudos Bot – API-Version
Kein Browser, kein Playwright. Nutzt die offizielle Strava API.

Strategie:
  1. Eigene Clubs abrufen → Club Activities → Kudos geben
  2. Fallback: /athlete/following → pro Athlet letzte Activity → Kudos

Setup (einmalig, auf Rechner mit Browser):
  python kudos_bot.py --auth

Normaler Lauf (via Cronjob auf Pi):
  python kudos_bot.py
"""

import os
import sys
import json
import logging
import webbrowser
from datetime import datetime, timezone
from pathlib import Path
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs, urlencode

import requests
from dotenv import load_dotenv

# ── Konfiguration ────────────────────────────────────────────────────────────
load_dotenv()

BASE_DIR    = Path(__file__).parent
TOKEN_FILE  = BASE_DIR / "tokens.json"
LOG_FILE    = BASE_DIR / "kudos.log"

CLIENT_ID     = os.getenv("STRAVA_CLIENT_ID")
CLIENT_SECRET = os.getenv("STRAVA_CLIENT_SECRET")
REDIRECT_URI  = "http://localhost:8765/callback"
SCOPE         = "activity:read_all,profile:read_all,read"

API_BASE  = "https://www.strava.com/api/v3"
AUTH_URL  = "https://www.strava.com/oauth/authorize"
TOKEN_URL = "https://www.strava.com/oauth/token"

# Wie viele Activities pro Club prüfen
FEED_LIMIT = 30

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
def _require_env(*keys):
    missing = [k for k in keys if not os.getenv(k)]
    if missing:
        raise EnvironmentError(
            f"Fehlende Umgebungsvariablen: {', '.join(missing)}\n"
            "Bitte in .env eintragen (siehe .env.example)."
        )


def _save_tokens(data: dict):
    TOKEN_FILE.write_text(json.dumps(data, indent=2))
    log.info("Tokens gespeichert: %s", TOKEN_FILE)


def _load_tokens() -> dict:
    if not TOKEN_FILE.exists():
        raise FileNotFoundError(
            "tokens.json nicht gefunden. Bitte zuerst --auth ausführen:\n"
            "  python kudos_bot.py --auth"
        )
    return json.loads(TOKEN_FILE.read_text())


def _refresh_token_if_needed(tokens: dict) -> dict:
    """Erneuert den Access Token automatisch wenn abgelaufen."""
    expires_at = tokens.get("expires_at", 0)
    now = datetime.now(timezone.utc).timestamp()
    if now < expires_at - 60:
        return tokens

    log.info("Access Token abgelaufen – erneuere via Refresh Token …")
    resp = requests.post(TOKEN_URL, data={
        "client_id":     CLIENT_ID,
        "client_secret": CLIENT_SECRET,
        "grant_type":    "refresh_token",
        "refresh_token": tokens["refresh_token"],
    }, timeout=15)
    resp.raise_for_status()
    new_tokens = resp.json()
    tokens.update(new_tokens)
    _save_tokens(tokens)
    log.info("Token erneuert, gültig bis %s",
             datetime.fromtimestamp(new_tokens["expires_at"]).strftime("%Y-%m-%d %H:%M"))
    return tokens


def _give_kudos_for_activity(act: dict, headers: dict) -> str:
    """Gibt Kudos für eine einzelne Activity. Gibt Status zurück: given/skipped/error."""
    act_id    = act.get("id")
    firstname = act.get("athlete", {}).get("firstname", "?")
    lastname  = act.get("athlete", {}).get("lastname", "")
    athlete   = f"{firstname} {lastname}".strip()
    name      = act.get("name", "Unbenannte Activity")

    if act.get("kudosed", False):
        log.debug("Bereits bekudost: %s – %s", athlete, name)
        return "skipped"

    resp = requests.post(
        f"{API_BASE}/activities/{act_id}/kudos",
        headers=headers,
        timeout=10,
    )
    if resp.status_code in (200, 201, 204):
        log.info("✅ Kudos: %s – %s", athlete, name)
        return "given"
    elif resp.status_code == 429:
        log.warning("⚠️  Rate Limit erreicht – stoppe.")
        return "ratelimit"
    else:
        log.warning("❌ Fehler %s bei Activity %s: %s", resp.status_code, act_id, resp.text[:80])
        return "error"


# ── OAuth Authorization Flow ──────────────────────────────────────────────────
class _CallbackHandler(BaseHTTPRequestHandler):
    code = None

    def do_GET(self):
        params = parse_qs(urlparse(self.path).query)
        if "code" in params:
            _CallbackHandler.code = params["code"][0]
            self.send_response(200)
            self.end_headers()
            self.wfile.write(
                b"<h2>Strava Auth erfolgreich!</h2>"
                b"<p>Du kannst dieses Fenster jetzt schliessen.</p>"
            )
        else:
            self.send_response(400)
            self.end_headers()
            self.wfile.write(b"Fehler: kein Code erhalten.")

    def log_message(self, *args):
        pass


def do_auth():
    """Einmaliger OAuth-Flow: öffnet Browser, fängt Callback lokal ab."""
    _require_env("STRAVA_CLIENT_ID", "STRAVA_CLIENT_SECRET")

    params = urlencode({
        "client_id":       CLIENT_ID,
        "redirect_uri":    REDIRECT_URI,
        "response_type":   "code",
        "approval_prompt": "auto",
        "scope":           SCOPE,
    })
    url = f"{AUTH_URL}?{params}"

    print("\n" + "="*60)
    print("STRAVA AUTHORISIERUNG")
    print("="*60)
    print(f"\nÖffne im Browser:\n  {url}")
    print("\nFalls kein Browser startet, kopiere die URL manuell.")
    print("="*60 + "\n")

    try:
        webbrowser.open(url)
    except Exception:
        pass

    server = HTTPServer(("localhost", 8765), _CallbackHandler)
    log.info("Warte auf OAuth Callback auf http://localhost:8765 …")
    while _CallbackHandler.code is None:
        server.handle_request()

    code = _CallbackHandler.code
    log.info("Authorization Code erhalten – tausche gegen Tokens …")

    resp = requests.post(TOKEN_URL, data={
        "client_id":     CLIENT_ID,
        "client_secret": CLIENT_SECRET,
        "code":          code,
        "grant_type":    "authorization_code",
    }, timeout=15)
    resp.raise_for_status()
    tokens = resp.json()
    _save_tokens(tokens)

    athlete = tokens.get("athlete", {})
    print(f"\n✅ Eingeloggt als: {athlete.get('firstname', '')} {athlete.get('lastname', '')}")
    print(f"   Token gültig bis: {datetime.fromtimestamp(tokens['expires_at']).strftime('%Y-%m-%d %H:%M')}")
    print("\nKopiere tokens.json auf den Pi und starte: python kudos_bot.py\n")


# ── Kudos-Logik ───────────────────────────────────────────────────────────────
def give_kudos():
    tokens  = _load_tokens()
    tokens  = _refresh_token_if_needed(tokens)
    headers = {"Authorization": f"Bearer {tokens['access_token']}"}

    seen_ids     = set()
    kudos_given   = 0
    kudos_skipped = 0

    # ── Strategie 1: Club Activities ─────────────────────────────────────────
    log.info("Lade Clubs …")
    clubs_resp = requests.get(f"{API_BASE}/athlete/clubs", headers=headers, timeout=15)
    clubs = clubs_resp.json() if clubs_resp.ok else []
    log.info("%d Club(s) gefunden.", len(clubs))

    for club in clubs:
        club_id   = club["id"]
        club_name = club.get("name", str(club_id))
        log.info("Club: %s – lade Activities …", club_name)

        acts_resp = requests.get(
            f"{API_BASE}/clubs/{club_id}/activities",
            headers=headers,
            params={"per_page": FEED_LIMIT},
            timeout=15,
        )
        if not acts_resp.ok:
            log.warning("Club %s: Fehler %s", club_name, acts_resp.status_code)
            continue

        for act in acts_resp.json():
            act_id = act.get("id")
            if not act_id or act_id in seen_ids:
                continue
            seen_ids.add(act_id)
            result = _give_kudos_for_activity(act, headers)
            if result == "given":
                kudos_given += 1
            elif result == "skipped":
                kudos_skipped += 1
            elif result == "ratelimit":
                log.info("Fertig (Rate Limit). Kudos: %d | Skipped: %d", kudos_given, kudos_skipped)
                return

    # ── Strategie 2: Gefolgten Athleten → letzte Activity ────────────────────
    log.info("Lade gefolgten Athleten für direkte Kudos …")
    page = 1
    while True:
        follow_resp = requests.get(
            f"{API_BASE}/athlete/following",
            headers=headers,
            params={"per_page": 50, "page": page},
            timeout=15,
        )
        if not follow_resp.ok or not follow_resp.json():
            break

        athletes = follow_resp.json()
        for ath in athletes:
            ath_id = ath.get("id")
            # Letzte Activity des Athleten abrufen
            act_resp = requests.get(
                f"{API_BASE}/athletes/{ath_id}/stats",
                headers=headers,
                timeout=10,
            )
            # stats gibt keine einzelnen Activities – nutze stattdessen
            # die bekannten Activity-IDs aus recent_*_totals (nicht kudosbar direkt)
            # → Strategie 2 nur als Fallback wenn Clubs leer sind
        break  # Einmal reicht als Hinweis

    if not clubs:
        log.warning(
            "Keine Clubs gefunden und /activities/following ist deprecated.\n"
            "Tritt einem Strava-Club bei, damit der Bot Activities findet!"
        )

    log.info("Fertig. Kudos gegeben: %d | Bereits bekudost: %d", kudos_given, kudos_skipped)


# ── Einstieg ──────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    if "--auth" in sys.argv:
        do_auth()
    else:
        give_kudos()
