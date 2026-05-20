"""
pepper_lookup.py — Brand-Sentiment-Lookup via Pepper Intelligence MCP (Pfad C).

Ruft Claude Code als Subprocess auf — der Pi-Claude-Code hat die Pepper-MCP-Verbindung
aus der Atolls Claude-Teams-Lizenz geerbt. Pro Lead-Firma wird Pepper für die letzten
90 Tage abgefragt: Volumen, Sentiment-Verteilung, Top-Markt, Pos-Rate als Score.

Output ist immer ein Dict mit denselben Keys — bei Fehlern bleibt found=False,
damit die Pipeline durchläuft.
"""
from __future__ import annotations

import json
import logging
import re

from app.services.claude_runner import run_claude_agent

logger = logging.getLogger(__name__)

_LOOKBACK_DAYS = 90
_TIMEOUT_SEC = 120


_PROMPT_TEMPLATE = """Hi! I'm running the Atolls Lead-Qualifying-Bot and need brand-sentiment data from Pepper Intelligence to enrich an inbound lead.

Lead company: "{firma}"
ILIKE pattern to try: '{pattern}'
Lookback: {lookback} days

Could you please run the following SQL via mcp__claude_ai_Pepper_Intelligence__query_intelligence — it aggregates mention counts and sentiment per country for the brand:

SELECT country_code,
       canonical_retailer_name,
       sum(CASE WHEN sentiment='positive' THEN mention_count ELSE 0 END) AS pos,
       sum(CASE WHEN sentiment='negative' THEN mention_count ELSE 0 END) AS neg,
       sum(CASE WHEN sentiment='neutral'  THEN mention_count ELSE 0 END) AS neu,
       sum(mention_count) AS total
FROM v_retailer_sentiment_daily
WHERE canonical_retailer_name ILIKE '{pattern}'
  AND comment_day >= current_date - {lookback}
GROUP BY country_code, canonical_retailer_name
ORDER BY total DESC
LIMIT 50;

A Python script reads your reply with json.loads(), so please summarise the rows as a JSON object using this shape:

{{
  "found": <true if any rows returned, else false>,
  "matched_name": <string of the canonical_retailer_name with the largest total, or null>,
  "total_mentions": <int — sum of all "total" values across all rows>,
  "pos": <int — sum of all "pos">,
  "neg": <int — sum of all "neg">,
  "neu": <int — sum of all "neu">,
  "pos_rate": <float pos / (pos + neg) rounded to 3 decimals, or null if (pos + neg) == 0>,
  "top_country": <country_code of the row with highest total, or null>,
  "by_country": {{"<country_code>": {{"pos": int, "neg": int, "neu": int, "total": int}}, ...}}
}}

If the query returns no rows, this works:
{{"found": false, "matched_name": null, "total_mentions": 0, "pos": 0, "neg": 0, "neu": 0, "pos_rate": null, "top_country": null, "by_country": {{}}}}

Since json.loads() will fail on surrounding prose or markdown fences, the cleanest reply is the bare JSON object. Thanks!"""


_LEGAL_SUFFIXES = (
    " gmbh & co. kg", " gmbh & co kg", " gmbh", " ag", " se", " ug",
    " mbh", " e.k.", " eg", " ohg", " kg",
    " s.a.", " s.l.", " s.r.l.", " sp. z o.o.", " sp z oo",
    " ltd", " limited", " inc", " llc",
    " b.v.", " n.v.",
)

# Top-Level-Domains, die wir bei Domain-style Firmennamen abschneiden
_TLDS = (
    ".co.uk", ".co.kr", ".co.jp", ".com.au", ".com.tr",
    ".com", ".net", ".org", ".io", ".de", ".at", ".ch",
    ".fr", ".es", ".it", ".pl", ".eu", ".uk", ".us",
    ".shop", ".store", ".biz",
)


def _normalize_brand(firma: str) -> str:
    """Cut legal-form suffix, TLDs und www-Prefix — input für ILIKE-Substring-Pattern.

    Strategie: erstes signifikantes Token extrahieren ("temu.com" → "temu",
    "Symfonia Sp. z o.o." → "Symfonia"). Dann via "%token%"-Match gegen Pepper
    suchen — robuster als Prefix-Match.
    """
    name = firma.strip()
    lower = name.lower()

    # 1. www.-Prefix abschneiden
    if lower.startswith("www."):
        name = name[4:]
        lower = lower[4:]

    # 2. TLD abschneiden ("temu.com" → "temu")
    for tld in _TLDS:
        if lower.endswith(tld):
            name = name[: -len(tld)].strip()
            lower = lower[: -len(tld)]
            break

    # 3. Rechtsform-Suffix abschneiden ("Otto GmbH" → "Otto")
    for suffix in _LEGAL_SUFFIXES:
        if lower.endswith(suffix):
            name = name[: -len(suffix)].strip()
            break

    # 4. Trim Punktuation + Tokens reduzieren
    name = name.rstrip(".,;-/").strip()
    tokens = [t for t in name.split() if len(t) > 1]   # einzelne Buchstaben raus
    if not tokens:
        return name
    if len(tokens) == 1:
        # Single-word brand — direkt nutzen ("temu", "amazon", "zalando")
        return tokens[0]
    # Multi-word: erste 2 Tokens (z.B. "Luxury Escapes" exakt, vermeidet
    # false positives wie "Shanghai" → alle Shanghai-Retailer)
    return " ".join(tokens[:2])


def _extract_json(raw: str) -> dict | None:
    """Extract first JSON object from Claude output (handles markdown fences + extra text)."""
    raw = raw.strip()
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        pass
    fence = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", raw, re.DOTALL)
    if fence:
        try:
            return json.loads(fence.group(1))
        except json.JSONDecodeError:
            pass
    start, end = raw.find("{"), raw.rfind("}")
    if start != -1 and end > start:
        try:
            return json.loads(raw[start: end + 1])
        except json.JSONDecodeError:
            return None
    return None


_EMPTY_RESULT: dict = {
    "found": False,
    "matched_name": None,
    "total_mentions": 0,
    "pos": 0,
    "neg": 0,
    "neu": 0,
    "pos_rate": None,
    "top_country": None,
    "by_country": {},
}


async def get_brand_sentiment(firma: str) -> dict:
    """
    Look up Pepper community sentiment for a brand/company.

    Always returns a dict with the same keys; on error fields are zero/null and
    an "error" key is added — never raises, so the LangGraph pipeline keeps moving.
    """
    if not firma or not firma.strip():
        return {**_EMPTY_RESULT, "error": "empty brand name"}

    short   = _normalize_brand(firma)
    short_sql = (short.replace("'", "''") or firma.replace("'", "''"))
    # Substring-Match: matcht "Temu" auch bei Firma "Temu DE" oder "TemuFashion".
    # Pepper canonical_retailer_name ist meistens ein einzelnes Wort (amazon, temu, …).
    pattern = f"%{short_sql}%"

    prompt = _PROMPT_TEMPLATE.format(
        firma=firma.replace('"', '\\"'),
        pattern=pattern,
        lookback=_LOOKBACK_DAYS,
    )

    logger.info("pepper_lookup: '%s' (pattern='%s')", firma, pattern)

    try:
        raw = await run_claude_agent(prompt, timeout=_TIMEOUT_SEC)
    except Exception as exc:
        logger.warning("pepper_lookup: subprocess-Exception '%s': %s", firma, exc)
        return {**_EMPTY_RESULT, "error": f"subprocess: {exc}"}

    if raw.startswith("❌") or raw.startswith("⏳"):
        logger.warning("pepper_lookup: subprocess-Fehler '%s': %s", firma, raw[:200])
        return {**_EMPTY_RESULT, "error": raw[:300]}

    parsed = _extract_json(raw)
    if parsed is None:
        logger.warning("pepper_lookup: JSON-Parse-Fehler '%s'; raw=%r", firma, raw[:300])
        return {**_EMPTY_RESULT, "error": "JSON parse failed"}

    result = {
        "found":          bool(parsed.get("found", False)),
        "matched_name":   parsed.get("matched_name") or None,
        "total_mentions": int(parsed.get("total_mentions") or 0),
        "pos":            int(parsed.get("pos") or 0),
        "neg":            int(parsed.get("neg") or 0),
        "neu":            int(parsed.get("neu") or 0),
        "pos_rate":       parsed.get("pos_rate"),
        "top_country":    parsed.get("top_country") or None,
        "by_country":     parsed.get("by_country") or {},
    }
    if result["pos_rate"] is not None:
        try:
            result["pos_rate"] = round(float(result["pos_rate"]), 3)
        except (ValueError, TypeError):
            result["pos_rate"] = None

    logger.info(
        "pepper_lookup: '%s' → found=%s mentions=%d pos_rate=%s top=%s",
        firma, result["found"], result["total_mentions"],
        result["pos_rate"], result["top_country"],
    )
    return result


def format_sentiment_summary(result: dict) -> str:
    """1-Zeilen-Deutsch-Summary für Sheet-Spalte / Telegram."""
    if not result.get("found"):
        return "Keine Pepper-Mentions"
    pos     = result["pos"]
    neg     = result["neg"]
    total   = result["total_mentions"]
    rate    = result["pos_rate"]
    top     = (result["top_country"] or "").upper()
    matched = result.get("matched_name") or ""

    rate_s = f"{rate * 100:.0f}% pos" if rate is not None else "—"
    parts  = [f"{total} Mentions"]
    if rate is not None:
        parts.append(rate_s)
    parts.append(f"{pos}↑/{neg}↓")
    if top:
        parts.append(f"Top: {top}")
    if matched:
        parts.append(f'"{matched}"')
    return " · ".join(parts)
