"""
strava_kudos/kudos_bot.py

Automatisch Kudos an alle Aktivitäten im Strava-Feed geben.

Methode: Playwright (headless Chromium) – keine API-Keys nötig.
Session wird nach dem ersten Login in 'session_state.json' gespeichert
und bei jedem weiteren Aufruf wiederverwendet.

Nutzung:
  Einmalig (Login): python kudos_bot.py --login
  Normaler Lauf:    python kudos_bot.py

Cron (alle 30 Min):
  */30 * * * * cd /home/pi/Serbo_bot/strava_kudos && /home/pi/Serbo_bot/.venv/bin/python kudos_bot.py >> kudos.log 2>&1
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import time
from datetime import datetime, timezone
from pathlib import Path

from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout

# ---------------------------------------------------------------------------
# Konfiguration
# ---------------------------------------------------------------------------
BASE_DIR        = Path(__file__).parent
STATE_FILE      = BASE_DIR / "session_state.json"
KUDOSED_FILE    = BASE_DIR / "kudosed.json"
LOG_FILE        = BASE_DIR / "kudos.log"

STRAVA_LOGIN    = "https://www.strava.com/login"
STRAVA_FEED     = "https://www.strava.com/dashboard?num_entries=200"

# Buttons: sowohl der leere Kudo-Button als auch "als Erster kudosen"
KUDO_SELECTOR   = (
    'button[title="Give kudos"], '
    'button[title="Be the first to give kudos!"], '
    'button[data-testid="kudos_button"]:not([class*="active"]):not([class*="kudoed"])'
)

CLICK_DELAY_MS  = 1800   # ms zwischen zwei Kudos (Rate-Limiting)
SCROLL_PAUSE_MS = 2000   # ms nach dem Scrollen (Feed nachladen)
MAX_SCROLLS     = 8      # wie oft nach unten scrollen
KEEP_KUDOSED    = 2000   # max. gecachte Activity-IDs

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(LOG_FILE, encoding="utf-8"),
        logging.StreamHandler(),
    ],
)
logger = logging.getLogger("strava_kudos")


# ---------------------------------------------------------------------------
# Hilfsfunktionen
# ---------------------------------------------------------------------------
def load_kudosed() -> set[str]:
    if KUDOSED_FILE.exists():
        return set(json.loads(KUDOSED_FILE.read_text(encoding="utf-8")))
    return set()


def save_kudosed(ids: set[str]):
    recent = sorted(ids)[-KEEP_KUDOSED:]
    KUDOSED_FILE.write_text(json.dumps(recent, indent=2), encoding="utf-8")


def _get_env(key: str) -> str:
    val = os.environ.get(key, "")
    if not val:
        raise EnvironmentError(
            f"Umgebungsvariable '{key}' fehlt. "
            f"In .env eintragen oder: export {key}=..."
        )
    return val


# ---------------------------------------------------------------------------
# Login (einmalig)
# ---------------------------------------------------------------------------
def do_login():
    """
    Öffnet einen sichtbaren Browser, meldet sich bei Strava an und
    speichert die Session in session_state.json.
    Nur einmalig nötig – danach läuft alles headless.
    """
    email    = _get_env("STRAVA_EMAIL")
    password = _get_env("STRAVA_PASSWORD")

    logger.info("Starte Login-Browser (NICHT headless)...")
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False, slow_mo=200)
        ctx     = browser.new_context()
        page    = ctx.new_page()

        page.goto(STRAVA_LOGIN)
        page.fill("#email",    email)
        page.fill("#password", password)
        page.click("#login-button")

        try:
            page.wait_for_url("**/dashboard**", timeout=20_000)
            logger.info("Login erfolgreich!")
        except PWTimeout:
            logger.error("Login-Timeout – falsches Passwort oder 2FA aktiv?")
            browser.close()
            return

        ctx.storage_state(path=str(STATE_FILE))
        logger.info("Session gespeichert in %s", STATE_FILE)
        browser.close()


# ---------------------------------------------------------------------------
# Kudos geben
# ---------------------------------------------------------------------------
def give_kudos(headless: bool = True) -> int:
    """
    Navigiert zum Strava-Feed, gibt Kudos an alle noch nicht gekudosten
    Aktivitäten und gibt die Anzahl neuer Kudos zurück.
    """
    if not STATE_FILE.exists():
        logger.error(
            "Keine Session gefunden. Bitte einmalig --login ausführen."
        )
        return 0

    kudosed = load_kudosed()
    new_kudos = 0

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=headless)
        ctx     = browser.new_context(storage_state=str(STATE_FILE))
        page    = ctx.new_page()

        # Feed laden
        logger.info("Lade Feed: %s", STRAVA_FEED)
        page.goto(STRAVA_FEED, wait_until="networkidle", timeout=30_000)

        # Prüfe ob Session noch gültig
        if "login" in page.url:
            logger.warning("Session abgelaufen – bitte erneut --login ausführen.")
            browser.close()
            return 0

        # Feed nach unten scrollen um mehr Aktivitäten zu laden
        for i in range(MAX_SCROLLS):
            page.evaluate("window.scrollBy(0, window.innerHeight * 2)")
            page.wait_for_timeout(SCROLL_PAUSE_MS)
            logger.debug("Scroll %d/%d", i + 1, MAX_SCROLLS)

        # Alle Kudos-Buttons finden
        buttons = page.query_selector_all(KUDO_SELECTOR)
        logger.info("%d Kudos-Buttons gefunden", len(buttons))

        for btn in buttons:
            try:
                # Activity-ID aus dem nächsten Elternelement ermitteln
                activity_id = (
                    btn.evaluate(
                        "el => el.closest('[data-activity-id]')"
                        "?.getAttribute('data-activity-id') || ''"
                    )
                    or btn.evaluate(
                        "el => el.closest('article, [id^=\"activity\"]')"
                        "?.id?.replace('activity-', '') || ''"
                    )
                )

                if activity_id and activity_id in kudosed:
                    logger.debug("Bereits gekudost: %s", activity_id)
                    continue

                btn.scroll_into_view_if_needed()
                page.wait_for_timeout(CLICK_DELAY_MS)
                btn.click()
                new_kudos += 1

                if activity_id:
                    kudosed.add(activity_id)
                    logger.info("👍 Kudos gegeben (Activity %s)", activity_id)
                else:
                    logger.info("👍 Kudos gegeben (ID unbekannt)")

            except Exception as exc:
                logger.warning("Fehler beim Kudos-Klick: %s", exc)

        # Aktualisierte Session speichern (verlängert Lebensdauer)
        ctx.storage_state(path=str(STATE_FILE))
        browser.close()

    save_kudosed(kudosed)
    logger.info(
        "✅ Fertig: %d neue Kudos | %s",
        new_kudos,
        datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
    )
    return new_kudos


# ---------------------------------------------------------------------------
# Einstiegspunkt
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Strava Auto-Kudos Bot")
    parser.add_argument(
        "--login",
        action="store_true",
        help="Einmaligen Login durchführen und Session speichern",
    )
    parser.add_argument(
        "--visible",
        action="store_true",
        help="Browser sichtbar starten (Debug)",
    )
    args = parser.parse_args()

    if args.login:
        do_login()
    else:
        give_kudos(headless=not args.visible)
