"""
kicktipp_client.py — lightweight async client for kicktipp.de.

Kicktipp has no public API; this drives the website's forms directly with
httpx + BeautifulSoup (stdlib html.parser, no browser — Pi-friendly). The
flow mirrors the established community bots:

  • login        → POST /info/profil/login  with kennung + passwort
                   → the session 'login' cookie is the reusable token
  • communities  → GET  /info/profil/meinetipprunden
  • open matches → GET  /<community>/tippabgabe   (table in #kicktipp-content)
                   each row: date, home, away, [heimTipp/gastTipp inputs], odds
  • submit       → POST the same form back with the tip inputs filled

A successful login cookie is cached to disk so we don't re-login every run.
Every public coroutine is fail-safe-ish: raises KicktippError on hard
failures so callers can log + skip rather than crash the bot.
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import httpx
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

URL_BASE = "https://www.kicktipp.de"
URL_LOGIN = URL_BASE + "/info/profil/login"
URL_COMMUNITIES = URL_BASE + "/info/profil/meinetipprunden"

_TOKEN_FILE = Path(__file__).parent.parent / "data" / "kicktipp_token.json"
_TIMEOUT = 20.0
_UA = "Mozilla/5.0 (compatible; SerboBot/1.0; +https://kicktipp.de)"


class KicktippError(Exception):
    pass


@dataclass
class Match:
    home: str
    away: str
    kickoff: datetime | None
    odds: tuple[float, float, float] | None     # (home, draw, away)
    field_home: str                              # form input name for home tip
    field_away: str                              # form input name for away tip
    existing_home: str = ""                      # already-placed tip, if any
    existing_away: str = ""

    @property
    def has_bet(self) -> bool:
        return bool(self.existing_home or self.existing_away)

    def __str__(self) -> str:
        ko = self.kickoff.strftime("%d.%m %H:%M") if self.kickoff else "?"
        o = f" ({self.odds[0]:.1f}/{self.odds[1]:.1f}/{self.odds[2]:.1f})" if self.odds else ""
        return f"{ko} {self.home} – {self.away}{o}"


# ── Token cache ────────────────────────────────────────────────────────────────


def _save_token(token: str) -> None:
    try:
        _TOKEN_FILE.parent.mkdir(parents=True, exist_ok=True)
        _TOKEN_FILE.write_text(json.dumps({"login": token}), encoding="utf-8")
    except Exception as exc:
        logger.debug("kicktipp: token save failed: %s", exc)


def _load_token() -> str | None:
    try:
        return json.loads(_TOKEN_FILE.read_text(encoding="utf-8")).get("login")
    except Exception:
        return None


# ── Parsing helpers ──────────────────────────────────────────────────────────


def _parse_kickoff(text: str) -> datetime | None:
    text = (text or "").strip()
    for fmt in ("%d.%m.%y %H:%M", "%d.%m.%Y %H:%M"):
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    return None


def _parse_odds(text: str) -> tuple[float, float, float] | None:
    parts = [p.strip().replace(",", ".") for p in (text or "").split("/")]
    if len(parts) != 3:
        return None
    try:
        return (float(parts[0]), float(parts[1]), float(parts[2]))
    except ValueError:
        return None


def parse_matches(html: str) -> list[Match]:
    """Parse the tippabgabe page HTML into Match objects.

    Row layout (from the community bots): td[0]=date, td[1]=home, td[2]=away,
    td[3]=tip inputs (ids end with _heimTipp / _gastTipp), td[4]=odds 'x / y / z'.
    The date cell is blank for consecutive same-day matches → carry it forward.
    """
    soup = BeautifulSoup(html, "html.parser")
    content = soup.find(id="kicktipp-content") or soup
    tbody = content.find("tbody")
    if not tbody:
        return []

    matches: list[Match] = []
    last_kickoff: datetime | None = None
    for tr in tbody.find_all("tr"):
        tds = tr.find_all("td")
        if len(tds) < 4:
            continue
        home_inp = tds[3].find("input", id=lambda x: x and x.endswith("_heimTipp"))
        away_inp = tds[3].find("input", id=lambda x: x and x.endswith("_gastTipp"))
        if not home_inp or not away_inp or not home_inp.get("name") or not away_inp.get("name"):
            continue

        kickoff = _parse_kickoff(tds[0].get_text()) or last_kickoff
        last_kickoff = kickoff
        odds = _parse_odds(tds[4].get_text()) if len(tds) > 4 else None

        matches.append(Match(
            home=tds[1].get_text(strip=True),
            away=tds[2].get_text(strip=True),
            kickoff=kickoff,
            odds=odds,
            field_home=home_inp["name"],
            field_away=away_inp["name"],
            existing_home=(home_inp.get("value") or "").strip(),
            existing_away=(away_inp.get("value") or "").strip(),
        ))
    return matches


def _hidden_form_fields(html: str) -> dict[str, str]:
    """Collect all input fields (hidden + tip) on the tippabgabe form so a
    POST round-trips every value the server expects."""
    soup = BeautifulSoup(html, "html.parser")
    content = soup.find(id="kicktipp-content") or soup
    form = content.find("form") or soup.find("form")
    fields: dict[str, str] = {}
    if not form:
        return fields
    for inp in form.find_all("input"):
        name = inp.get("name")
        if not name:
            continue
        fields[name] = inp.get("value") or ""
    return fields


# ── Client ──────────────────────────────────────────────────────────────────


class KicktippClient:
    def __init__(self, email: str, password: str):
        self._email = email
        self._password = password
        self._client = httpx.AsyncClient(
            timeout=_TIMEOUT, follow_redirects=True, headers={"User-Agent": _UA}
        )

    async def __aenter__(self) -> "KicktippClient":
        return self

    async def __aexit__(self, *exc) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        await self._client.aclose()

    async def _logged_in(self) -> bool:
        r = await self._client.get(URL_COMMUNITIES)
        # When logged out kicktipp redirects to / or shows the login form
        return "meinetipprunden" in str(r.url) and "kennung" not in r.text.lower()

    async def login(self, use_cache: bool = True) -> None:
        if use_cache:
            token = _load_token()
            if token:
                self._client.cookies.set("login", token, domain="www.kicktipp.de")
                if await self._logged_in():
                    logger.info("kicktipp: reused cached login token")
                    return
                logger.info("kicktipp: cached token stale, re-logging in")

        await self._client.get(URL_LOGIN)  # prime cookies
        r = await self._client.post(
            URL_LOGIN,
            data={"kennung": self._email, "passwort": self._password},
        )
        token = self._client.cookies.get("login")
        if not token or not await self._logged_in():
            raise KicktippError("Login fehlgeschlagen — Zugangsdaten prüfen.")
        _save_token(token)
        logger.info("kicktipp: login OK")

    async def get_communities(self) -> list[str]:
        r = await self._client.get(URL_COMMUNITIES)
        soup = BeautifulSoup(r.text, "html.parser")
        content = soup.find(id="kicktipp-content") or soup
        out: list[str] = []
        for a in content.find_all("a"):
            href = (a.get("href") or "").strip("/")
            if href and href == a.get_text(strip=True):
                out.append(href)
        # de-dupe, keep order
        seen: set[str] = set()
        return [c for c in out if not (c in seen or seen.add(c))]

    def _tippabgabe_url(self, community: str, matchday: int | None = None) -> str:
        url = f"{URL_BASE}/{community}/tippabgabe"
        return f"{url}?&spieltagIndex={matchday}" if matchday else url

    async def get_open_matches(self, community: str, matchday: int | None = None) -> list[Match]:
        r = await self._client.get(self._tippabgabe_url(community, matchday))
        return parse_matches(r.text)

    async def submit_tips(
        self, community: str, tips: dict[str, tuple[int, int]], matchday: int | None = None,
    ) -> int:
        """Submit a {match-key → (home, away)} mapping. The key is the match's
        field_home name (stable per row). Returns number of tips written.

        Re-fetches the form to capture all current hidden fields, fills the
        requested tip inputs, and POSTs the whole form back.
        """
        if not tips:
            return 0
        url = self._tippabgabe_url(community, matchday)
        r = await self._client.get(url)
        matches = parse_matches(r.text)
        form_fields = _hidden_form_fields(r.text)

        by_field_home = {m.field_home: m for m in matches}
        written = 0
        for field_home, (h, a) in tips.items():
            m = by_field_home.get(field_home)
            if not m:
                continue
            form_fields[m.field_home] = str(h)
            form_fields[m.field_away] = str(a)
            written += 1

        if not written:
            return 0
        form_fields.setdefault("submitbutton", "submitbutton")
        resp = await self._client.post(url, data=form_fields)
        if resp.status_code >= 400:
            raise KicktippError(f"Tippabgabe fehlgeschlagen (HTTP {resp.status_code}).")
        return written
