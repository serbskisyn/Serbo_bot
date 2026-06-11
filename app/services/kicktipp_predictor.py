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

BEWERTUNGSFAKTOREN (gewichtet nutzen, NICHT nur Quoten):
- Buchmacher-Quoten (gute Basis — aber nicht das letzte Wort).
- Aktuelle Form / Momentum (letzte Spiele) — ein ECHTES Signal, das die Quoten oft schlägt.
- FIFA-Weltrangliste / Kaderqualität / Marktwert der Teams.
- Verletzungen/Sperren von Schlüsselspielern, Head-to-Head, Heimvorteil, Reise/Erholung.
- Turnierkontext: Gruppenphase vs. K.o.; Tabellenlage; Motivation.

STRATEGIE — MUTIG, nicht stur Favoriten-Tippen:
1. Triff die Tendenz überlegt (sichere 2 Punkte als Basis). Aber: Wenn jüngste Form, Momentum oder klare News dem Quoten-Favoriten WIDERSPRECHEN, vertraue der Form — auch gegen die Quote.
2. Pro Spieltag sind 1–2 begründete ÜBERRASCHUNGSTIPPS ausdrücklich erwünscht (Außenseiter mit gutem Lauf, müder Favorit, Verletzungen). Nicht zufällig — es muss einen echten Grund geben.
3. Trau dich zu klaren/exakten Ergebnissen, wenn die Argumente da sind (z. B. 3:1, 2:2, 3:2) — nicht reflexartig das defensive 1:0. Häufige Resultate (1:0, 2:1, 2:0, 1:1, 2:2) bleiben Standard, aber sei kein Feigling.
4. Eine 1-Tor-Differenz (1:0, 2:1) trifft oft zusätzlich die Tordifferenz (3 P.) — gut für sichere Spiele; bei Mut-Tipps darf's auch deutlicher sein.
Kurz: Quoten als Kompass, Form als Mut-Geber, gelegentlich ein kalkuliertes Risiko für die 5 Punkte.

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
    from app.services.llm_client import chat
    return await chat(
        [{"role": "system", "content": system}, {"role": "user", "content": user}],
        model=KICKTIPP_PREDICT_MODEL, temperature=0.3, max_tokens=1200, timeout=timeout,
    )


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


# ── Bonus questions (tournament-outcome predictions) ─────────────────────────

_BONUS_SYSTEM = """Du bist ein Weltklasse-Fußball-Experte und beantwortest die Bonusfragen eines Kicktipp-Tippspiels (z.B. WM 2026).
Jede richtige Antwort gibt Punkte. Wähle die Antwort(en) mit der höchsten Eintreffwahrscheinlichkeit.

Stütze dich auf: Buchmacher-Outright-Quoten (falls angegeben — STÄRKSTES Signal, niedrigere Quote = wahrscheinlicher), FIFA-Weltrangliste, Kaderqualität/Marktwert, aktuelle Form, Turnier-Auslosung/Gruppenstärke, historische Turnierleistung.
- Wenn Outright-Quoten vorliegen, richte dich beim Weltmeister maßgeblich danach und nutze sie als Stärke-Prior für Gruppensieger/Halbfinale (Eigenwissen nur zur Feinjustierung).
- Bei Fragen mit mehreren Antwort-Slots: nenne GENAU so viele verschiedene Teams wie Slots, stärkste zuerst.
- Wähle NUR aus den vorgegebenen Optionen, exakt in der Schreibweise der Option.

Antworte NUR mit validem JSON-Array:
[{"qid": "<id>", "antworten": ["<Option>", ...]}]
Pro Frage so viele Antworten wie Slots. Keine Erklärungen."""


def build_bonus_prompt(questions: list, outrights_block: str = "") -> str:
    lines = ["Beantworte diese Bonusfragen (wähle nur aus den Optionen):\n"]
    for q in questions:
        n = len(q.fields)
        labels = [lbl for lbl, _val in q.options]
        # For long option lists (all teams) keep it compact
        opts = ", ".join(labels) if len(labels) <= 12 else f"{', '.join(labels[:12])} … (alle Turnier-Teams)"
        slots = f" — nenne {n} Teams" if n > 1 else ""
        lines.append(f'[qid {q.qid}] {q.text}{slots}\n   Optionen: {opts}')
    if outrights_block:
        lines.append(outrights_block)
    return "\n".join(lines)


def parse_bonus_answers(raw: str, questions: list) -> dict[str, str]:
    """Map the LLM's chosen labels back to {select_field_name: option_value}."""
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
    by_qid = {str(q.qid): q for q in questions}
    out: dict[str, str] = {}
    for item in data:
        if not isinstance(item, dict):
            continue
        q = by_qid.get(str(item.get("qid")))
        if not q:
            continue
        answers = item.get("antworten") or []
        if isinstance(answers, str):
            answers = [answers]
        # label → value lookup (case-insensitive)
        val_by_label = {lbl.strip().lower(): val for lbl, val in q.options}
        used: set[str] = set()
        slot = 0
        for ans in answers:
            if slot >= len(q.fields):
                break
            val = val_by_label.get(str(ans).strip().lower())
            if val and val not in used:
                out[q.fields[slot]] = val
                used.add(val)
                slot += 1
    return out


async def predict_bonus(questions: list) -> dict[str, str]:
    """Answer all bonus questions in one LLM call.
    Returns {select_field_name: option_value}."""
    if not questions:
        return {}
    outrights_block = ""
    try:
        from app.services.kicktipp_odds import fetch_outrights, format_outrights_block
        outrights_block = format_outrights_block(await fetch_outrights())
    except Exception as exc:
        logger.debug("kicktipp: outright odds skipped: %s", exc)
    try:
        raw = await _call_llm(_BONUS_SYSTEM, build_bonus_prompt(questions, outrights_block))
    except Exception as exc:
        logger.warning("kicktipp: bonus LLM failed: %s", exc)
        return {}
    return parse_bonus_answers(raw, questions)
