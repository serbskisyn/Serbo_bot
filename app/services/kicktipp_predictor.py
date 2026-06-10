"""
kicktipp_predictor.py — LLM + News scoreline predictor for Kicktipp matches.

For a whole matchday we make ONE LLM call (not one per match) to keep cost
down. The model is grounded on:
  • the bookmaker odds Kicktipp shows per match (low odd = favourite), and
  • best-effort recent German headlines per team (Google News RSS, one
    request per distinct team, short timeout, fail-soft → odds-only).

Prompt building and response parsing are pure functions so they can be
unit-tested without network or an LLM.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
from urllib.parse import quote_plus
from xml.etree import ElementTree as ET

import httpx

from app.config import OPENROUTER_API_KEY, KICKTIPP_PREDICT_MODEL, KICKTIPP_NEWS_ENABLED
from app.services.kicktipp_client import Match

logger = logging.getLogger(__name__)

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
_NEWS_TIMEOUT = 8.0
_HEADLINES_PER_TEAM = 3
_MAX_GOAL = 9

# Encodes the round's scoring rule so the model maximises EXPECTED points,
# not just the single most likely scoreline.
_SYSTEM_PROMPT = """Du bist ein Weltklasse-Fußball-Tippexperte für ein Kicktipp-Tippspiel.
Für jedes Spiel gibst du EIN Endergebnis (Tore Heim:Gast) ab.

PUNKTEREGEL dieser Runde (DARAUF optimieren — erwarteten Punktwert maximieren):
- Exaktes Ergebnis richtig: 5 Punkte
- Richtige Tordifferenz (richtiger Sieger + richtige Tordifferenz, aber Ergebnis nicht exakt): 3 Punkte
- Nur richtige Tendenz (richtiger Sieger ODER Unentschieden erkannt, aber falsche Differenz/Ergebnis): 2 Punkte
- Falsche Tendenz: 0 Punkte
- Bei Unentschieden gibt es keine Tordifferenz-Stufe: exaktes Remis 5, sonst richtige Tendenz 2.
Es wird das Ergebnis NACH VERLÄNGERUNG getippt: in K.-o.-Spielen ist also KEIN Unentschieden möglich — tippe den Sieger nach Verlängerung. Gruppenspiele sind 90 Min (Remis möglich).

STRATEGIE (genau so vorgehen):
1. Zuerst die Tendenz sicher treffen (das sind die sicheren 2 Punkte) — orientiere dich primär an den Quoten (niedrigste Quote = Favorit; Reihenfolge Heim/Unentschieden/Auswärts).
2. Dann das EINE wahrscheinlichste exakte Ergebnis für diese Tendenz wählen — bevorzuge häufige, niedrige Resultate (1:0, 2:1, 2:0, 1:1, 2:2, 0:0). Jage NICHT exotischen hohen Ergebnissen hinterher.
3. Eine 1-Tor-Differenz (1:0, 2:1) maximiert die Chance, zusätzlich die Tordifferenz (3 P.) zu treffen.
4. News (Form/Verletzungen) nur zur Feinjustierung, Quoten schlagen News.

Antworte NUR mit einem validen JSON-Array, ein Objekt pro Spiel-Index:
[{"i": 0, "heim": 2, "gast": 1}, {"i": 1, "heim": 1, "gast": 1}]
Keine Erklärungen, kein Text drumherum."""


# ── News (best-effort, fail-soft) ────────────────────────────────────────────


def _google_news_rss(query: str) -> str:
    return (
        f"https://news.google.com/rss/search?q={quote_plus(query)}"
        "&hl=de&gl=DE&ceid=DE:de"
    )


async def _fetch_team_headlines(client: httpx.AsyncClient, team: str) -> list[str]:
    try:
        r = await client.get(_google_news_rss(f"{team} Fußball"), timeout=_NEWS_TIMEOUT)
        r.raise_for_status()
        root = ET.fromstring(r.content)
        titles = [el.text.strip() for el in root.iter("title") if el.text]
        # first <title> is the feed name → drop it
        return titles[1:_HEADLINES_PER_TEAM + 1]
    except Exception as exc:
        logger.debug("kicktipp: headlines for %s failed: %s", team, exc)
        return []


async def gather_news(teams: list[str]) -> dict[str, list[str]]:
    if not KICKTIPP_NEWS_ENABLED or not teams:
        return {}
    out: dict[str, list[str]] = {}
    sem = asyncio.Semaphore(4)
    async with httpx.AsyncClient(follow_redirects=True) as client:
        async def _one(t: str):
            async with sem:
                out[t] = await _fetch_team_headlines(client, t)
        await asyncio.gather(*[_one(t) for t in dict.fromkeys(teams)], return_exceptions=True)
    return out


# ── Prompt building + parsing (pure) ─────────────────────────────────────────


def build_prompt(matches: list[Match], news: dict[str, list[str]] | None = None,
                 odds_block: str = "") -> str:
    news = news or {}
    lines = ["Spiele dieses Spieltags:\n"]
    for i, m in enumerate(matches):
        odds = (f"Quoten {m.odds[0]:.2f}/{m.odds[1]:.2f}/{m.odds[2]:.2f}"
                if m.odds else "Quoten unbekannt")
        lines.append(f"[{i}] {m.home} vs {m.away} — {odds}")
        for team in (m.home, m.away):
            heads = news.get(team) or []
            if heads:
                lines.append(f"    News {team}: " + " | ".join(h[:90] for h in heads[:2]))
    if odds_block:
        lines.append(odds_block)
    lines.append("\nGib für jeden Index [0..%d] ein Ergebnis als JSON-Array zurück." % (len(matches) - 1))
    return "\n".join(lines)


def parse_predictions(raw: str, n: int) -> dict[int, tuple[int, int]]:
    """Parse the LLM JSON array into {index: (heim, gast)}, clamped to 0..9."""
    if not raw:
        return {}
    raw = re.sub(r"```(?:json)?\s*", "", raw).strip().rstrip("`").strip()
    m = re.search(r"\[.*\]", raw, re.DOTALL)
    if not m:
        return {}
    try:
        data = json.loads(m.group())
    except json.JSONDecodeError:
        return {}
    out: dict[int, tuple[int, int]] = {}
    for item in data:
        if not isinstance(item, dict):
            continue
        try:
            i = int(item["i"])
            h = max(0, min(_MAX_GOAL, int(item["heim"])))
            a = max(0, min(_MAX_GOAL, int(item["gast"])))
        except (KeyError, ValueError, TypeError):
            continue
        if 0 <= i < n:
            out[i] = (h, a)
    return out


# ── LLM call ──────────────────────────────────────────────────────────────────


async def _call_llm(system: str, user: str, timeout: float = 40.0) -> str:
    payload = {
        "model": KICKTIPP_PREDICT_MODEL,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "temperature": 0.3,
        "max_tokens": 1200,
    }
    headers = {
        "Authorization": f"Bearer {OPENROUTER_API_KEY}",
        "Content-Type": "application/json",
    }
    async with httpx.AsyncClient(timeout=timeout) as client:
        r = await client.post(OPENROUTER_URL, json=payload, headers=headers)
        r.raise_for_status()
        return r.json()["choices"][0]["message"]["content"]


async def predict_matchday(matches: list[Match]) -> dict[str, tuple[int, int]]:
    """Predict scorelines for a list of matches.
    Returns {match.field_home: (heim, gast)} so the caller can submit directly.
    """
    if not matches:
        return {}
    teams = [t for m in matches for t in (m.home, m.away)]
    news = await gather_news(teams)

    # External bookmaker odds (best-effort) — especially valuable when Kicktipp
    # itself shows no quotes. The LLM maps English team names to our matches.
    odds_block = ""
    have_kicktipp_odds = any(m.odds for m in matches)
    if not have_kicktipp_odds:
        try:
            from app.services.kicktipp_odds import fetch_odds, format_odds_block
            odds_block = format_odds_block(await fetch_odds())
        except Exception as exc:
            logger.debug("kicktipp: external odds skipped: %s", exc)

    try:
        raw = await _call_llm(_SYSTEM_PROMPT, build_prompt(matches, news, odds_block))
    except Exception as exc:
        logger.warning("kicktipp: prediction LLM failed: %s", exc)
        return {}
    by_index = parse_predictions(raw, len(matches))
    return {matches[i].field_home: score for i, score in by_index.items()}
