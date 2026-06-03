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

from app.config import OPENROUTER_API_KEY, OPENROUTER_MODEL, KICKTIPP_NEWS_ENABLED
from app.services.kicktipp_client import Match

logger = logging.getLogger(__name__)

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
_NEWS_TIMEOUT = 8.0
_HEADLINES_PER_TEAM = 3
_MAX_GOAL = 9

_SYSTEM_PROMPT = """Du bist ein erfahrener Fußball-Tippexperte für ein Kicktipp-Tippspiel.
Für jedes Spiel gibst du ein REALISTISCHES Endergebnis (Tore Heim : Gast).

Berücksichtige:
- Buchmacher-Quoten: niedrigere Quote = Favorit. Quoten-Reihenfolge ist (Heimsieg / Unentschieden / Auswärtssieg).
- Aktuelle News/Schlagzeilen zu den Teams (Form, Verletzungen, Wichtigkeit).
- Typische Fußball-Ergebnisse: knappe Spiele 1:0/2:1, klare Favoriten 2:0/3:1, selten >4 Tore.

Gib das Ergebnis aus Sicht der wahrscheinlichsten Tendenz an — nicht zu defensiv.

Antworte NUR mit einem validen JSON-Array, ein Objekt pro Spiel in derselben Reihenfolge:
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


def build_prompt(matches: list[Match], news: dict[str, list[str]] | None = None) -> str:
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
        "model": OPENROUTER_MODEL,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "temperature": 0.3,
        "max_tokens": 700,
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
    try:
        raw = await _call_llm(_SYSTEM_PROMPT, build_prompt(matches, news))
    except Exception as exc:
        logger.warning("kicktipp: prediction LLM failed: %s", exc)
        return {}
    by_index = parse_predictions(raw, len(matches))
    return {matches[i].field_home: score for i, score in by_index.items()}
