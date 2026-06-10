"""
kicktipp_odds.py — optional bookmaker odds from The Odds API (the-odds-api.com).

Kicktipp doesn't always show quotes (e.g. the World Cup round had none), so we
pull h2h decimal odds from a free external source and feed them into the
predictor. Team names come back in English; rather than maintain a brittle
DE→EN alias table, we hand the raw odds list to the LLM and let it map
"Mexiko" ↔ "Mexico" itself.

Free tier: 500 requests/month with a free API key. Disabled (returns []) when
ODDS_API_KEY is unset, so the predictor falls back to LLM knowledge alone.
"""
from __future__ import annotations

import logging

import httpx

from app.config import ODDS_API_KEY, ODDS_API_SPORT, ODDS_API_WINNER_SPORT

logger = logging.getLogger(__name__)

_URL = "https://api.the-odds-api.com/v4/sports/{sport}/odds"
_TIMEOUT = 12.0


def _avg_h2h(event: dict) -> tuple[float, float, float] | None:
    """Average home/draw/away decimal odds across all bookmakers of an event."""
    home, away = event.get("home_team"), event.get("away_team")
    if not home or not away:
        return None
    sums = {"home": 0.0, "draw": 0.0, "away": 0.0}
    counts = {"home": 0, "draw": 0, "away": 0}
    for bm in event.get("bookmakers", []):
        for market in bm.get("markets", []):
            if market.get("key") != "h2h":
                continue
            for o in market.get("outcomes", []):
                name, price = o.get("name"), o.get("price")
                if not price:
                    continue
                if name == home:
                    sums["home"] += price; counts["home"] += 1
                elif name == away:
                    sums["away"] += price; counts["away"] += 1
                elif name == "Draw":
                    sums["draw"] += price; counts["draw"] += 1
    if not (counts["home"] and counts["away"]):
        return None
    h = sums["home"] / counts["home"]
    a = sums["away"] / counts["away"]
    d = sums["draw"] / counts["draw"] if counts["draw"] else 0.0
    return (round(h, 2), round(d, 2), round(a, 2))


async def fetch_odds() -> list[dict]:
    """Return upcoming-match odds as
    [{"home": str, "away": str, "odds": (home, draw, away)}], English team names.
    Empty list when disabled or on any error (fail-soft)."""
    if not ODDS_API_KEY:
        return []
    params = {
        "apiKey": ODDS_API_KEY,
        "regions": "eu",
        "markets": "h2h",
        "oddsFormat": "decimal",
    }
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            r = await client.get(_URL.format(sport=ODDS_API_SPORT), params=params)
            r.raise_for_status()
            events = r.json()
    except Exception as exc:
        logger.warning("odds_api: fetch failed: %s", exc)
        return []

    out: list[dict] = []
    for ev in events:
        odds = _avg_h2h(ev)
        if odds:
            out.append({"home": ev["home_team"], "away": ev["away_team"], "odds": odds})
    logger.info("odds_api: %d Spiele mit Quoten geladen (sport=%s)", len(out), ODDS_API_SPORT)
    return out


async def fetch_outrights() -> list[tuple[str, float]]:
    """Return tournament-winner outright odds as [(team, decimal_odds), …],
    favourites first, averaged across bookmakers. [] when disabled/on error."""
    if not ODDS_API_KEY:
        return []
    sport = ODDS_API_WINNER_SPORT or f"{ODDS_API_SPORT}_winner"
    params = {
        "apiKey": ODDS_API_KEY,
        "regions": "eu",
        "markets": "outrights",
        "oddsFormat": "decimal",
    }
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            r = await client.get(_URL.format(sport=sport), params=params)
            r.raise_for_status()
            events = r.json()
    except Exception as exc:
        logger.warning("odds_api: outrights fetch failed: %s", exc)
        return []

    sums: dict[str, float] = {}
    counts: dict[str, int] = {}
    for ev in events:
        for bm in ev.get("bookmakers", []):
            for market in bm.get("markets", []):
                if market.get("key") != "outrights":
                    continue
                for o in market.get("outcomes", []):
                    name, price = o.get("name"), o.get("price")
                    if name and price:
                        sums[name] = sums.get(name, 0.0) + price
                        counts[name] = counts.get(name, 0) + 1
    out = [(name, round(sums[name] / counts[name], 1)) for name in sums]
    out.sort(key=lambda t: t[1])
    logger.info("odds_api: %d Outright-Quoten geladen (sport=%s)", len(out), sport)
    return out


def format_outrights_block(outrights: list[tuple[str, float]], top_n: int = 24) -> str:
    """Render outright winner odds as a strength-prior block for the bonus LLM."""
    if not outrights:
        return ""
    lines = ["\nOutright-Quoten Turniersieger (Markt, NIEDRIGER = wahrscheinlicher) — "
             "maßgeblich für die Weltmeister-Frage und als Staerke-Prior fuer Gruppensieger/Halbfinale:"]
    for name, odd in outrights[:top_n]:
        lines.append(f"- {name}: {odd}")
    return "\n".join(lines)


def format_odds_block(odds_list: list[dict]) -> str:
    """Render external odds as a prompt block the LLM can map to the matches."""
    if not odds_list:
        return ""
    lines = ["\nVerfügbare Buchmacher-Quoten (Team-Namen evtl. englisch — ordne selbst dem passenden Spiel zu, Format Heim/Unentschieden/Auswärts):"]
    for o in odds_list[:40]:
        h, d, a = o["odds"]
        lines.append(f"- {o['home']} vs {o['away']}: {h}/{d}/{a}")
    return "\n".join(lines)
