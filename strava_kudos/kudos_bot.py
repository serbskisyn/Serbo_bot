#!/usr/bin/env python3
"""
Strava Kudos Bot – API-Version
Kein Browser, kein Playwright. Nutzt die offizielle Strava API.

Setup (einmalig):
  python kudos_bot.py --auth

Normaler Lauf (via Cronjob):
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
SCOPE         = "activity:read,profile:read_all"

API_BASE = "https://www.strava.com/api/v3"
AUTH_URL = "https://www.strava.com/oauth/authorize"
TOKEN_URL = "https://www.strava.com/oauth/token"

# Wie viele Activities aus dem Feed prüfen (max 200)
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
            "tokens.json nicht gefunden. Bitte zuerst --auth ausführen:"
            "  python kudos_bot.py --auth"
        )
    return json.loads(TOKEN_FILE.read_text())


def _refresh_token_if_needed(tokens: dict) -> dict:
    """Erneuert den Access Token automatisch wenn abgelaufen."""
    expires_at = tokens.get("expires_at", 0)
    now = datetime.now(timezone.utc).timestamp()
    if now < expires_at - 60:  # 60s Puffer
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


# ── OAuth Authorization Flow ──────────────────────────────────────────────────
class _CallbackHandler(BaseHTTPRequestHandler):
    """Minimaler HTTP-Server fängt den OAuth Callback ab."""
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

    def log_message(self, *args):  # HTTP-Logs unterdrücken
        pass


def do_auth():
    """Einmaliger OAuth-Flow: öffnet Browser, fängt Callback lokal ab."""
    _require_env("STRAVA_CLIENT_ID", "STRAVA_CLIENT_SECRET")

    params = urlencode({
        "client_id":     CLIENT_ID,
        "redirect_uri":  REDIRECT_URI,
        "response_type": "code",
        "approval_prompt": "auto",
        "scope":         SCOPE,
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

    # Lokalen Server starten und auf Callback warten
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
    print("\nDu kannst den Bot jetzt starten: python kudos_bot.py\n")


# ── Kudos-Logik ───────────────────────────────────────────────────────────────
def give_kudos():
    """Holt den Activity-Feed und gibt Kudos auf alle noch nicht bekudosten Activities."""
    tokens = _load_tokens()
    tokens = _refresh_token_if_needed(tokens)
    headers = {"Authorization": f"Bearer {tokens['access_token']}"}

    # Feed der gefolgten Athleten abrufen
    log.info("Lade Activity-Feed (max %d Activities) …", FEED_LIMIT)
    resp = requests.get(
        f"{API_BASE}/activities/following",
        headers=headers,
        params={"per_page": FEED_LIMIT},
        timeout=15,
    )
    resp.raise_for_status()
    activities = resp.json()
    log.info("%d Activities im Feed gefunden.", len(activities))

    kudos_given = 0
    kudos_skipped = 0

    for act in activities:
        act_id    = act["id"]
        athlete   = act.get("athlete", {}).get("firstname", "?") + " " + act.get("athlete", {}).get("lastname", "")
        name      = act.get("name", "Unbenannte Activity")
        has_kudos = act.get("kudosed", False)

        if has_kudos:
            kudos_skipped += 1
            log.debug("Bereits bekudost: %s – %s", athlete.strip(), name)
            continue

        # Kudos POST
        kudo_resp = requests.post(
            f"{API_BASE}/activities/{act_id}/kudos",
            headers=headers,
            timeout=10,
        )

        if kudo_resp.status_code in (200, 201, 204):
            log.info("✅ Kudos gegeben: %s – %s", athlete.strip(), name)
            kudos_given += 1
        elif kudo_resp.status_code == 429:
            log.warning("⚠️  Rate Limit erreicht – stoppe für heute.")
            break
        else:
            log.warning("❌ Fehler bei Activity %s: %s %s",
                        act_id, kudo_resp.status_code, kudo_resp.text[:100])

    log.info("Fertig. Kudos gegeben: %d | Bereits bekudost: %d",
             kudos_given, kudos_skipped)


# ── Einstieg ──────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    if "--auth" in sys.argv:
        do_auth()
    else:
        give_kudos()
