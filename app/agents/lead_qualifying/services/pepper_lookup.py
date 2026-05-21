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


# ── Multi-Brand × Multi-Country Lookup mit Aspects + Deals ───────────────────

_MULTI_PROMPT = """Hi! I'm running the Atolls Lead-Qualifying-Bot and need detailed country-level brand-sentiment data from Pepper Intelligence to enrich an inbound lead.

The lead's company is "{firma}" — they operate the following eCommerce brands:
{brand_list}

Could you please run this SQL via mcp__claude_ai_Pepper_Intelligence__query_intelligence — it aggregates mentions, deals and aspect-level sentiment per brand per country for the last {lookback} days:

SELECT canonical_retailer_name,
       country_code,
       sentiment,
       aspect,
       count(*)                  AS mentions,
       count(DISTINCT deal_id)   AS deals
FROM v_retailer_mentions
WHERE ({where_clause})
  AND comment_created_at >= current_date - {lookback}
GROUP BY canonical_retailer_name, country_code, sentiment, aspect
ORDER BY mentions DESC
LIMIT 2000;

A Python script parses your reply with json.loads(). Please aggregate the rows and respond with this JSON shape — brand × country with overall sentiment buckets, deal count, and the TOP 3 aspects per country (by total mentions across all sentiment categories):

{{
  "by_brand": {{
    "<canonical_retailer_name>": {{
      "total_mentions": <int>,
      "total_deals": <int>,
      "by_country": {{
         "<country_code>": {{
            "pos": <int>, "neg": <int>, "neu": <int>, "total": <int>,
            "pos_rate": <float pos/(pos+neg) rounded to 3 decimals, or null>,
            "deals": <int — distinct deal_id count for this brand+country>,
            "top_aspects": [
              {{"aspect": "<name>", "pos": int, "neg": int, "neu": int, "total": int}},
              ... up to 3 entries, sorted by total descending, skip rows where aspect is null ...
            ]
         }}
      }}
    }}
  }},
  "brands_found": <int>,
  "total_mentions_all": <int>,
  "total_deals_all": <int>
}}

If the query returns no rows, please respond with:
{{"by_brand": {{}}, "brands_found": 0, "total_mentions_all": 0, "total_deals_all": 0}}

Since json.loads() will fail on surrounding prose or markdown fences, the cleanest reply is the bare JSON object. Thanks!"""


def _build_where_clause(brand_names: list[str]) -> str:
    """Baut OR-verkettete ILIKE-Bedingungen für mehrere Brand-Patterns."""
    if not brand_names:
        return "1=0"
    clauses = []
    for n in brand_names:
        pat = _normalize_brand(n)
        if not pat:
            continue
        # SQL-Escape Single-Quotes
        pat_sql = pat.replace("'", "''")
        clauses.append(f"canonical_retailer_name ILIKE '%{pat_sql}%'")
    return " OR ".join(clauses) if clauses else "1=0"


_MCP_UNAVAILABLE_MARKERS = (
    "is not available in my current toolset",
    "not listed among my deferred tools",
    "is not available",
    "tool not available",
)


def _looks_like_mcp_unavailable(raw: str) -> bool:
    """Erkennt wenn der Subprocess sagt: MCP-Tool nicht ladbar."""
    low = raw[:600].lower()
    return any(marker in low for marker in _MCP_UNAVAILABLE_MARKERS)


async def get_multi_brand_sentiment(firma: str, brand_names: list[str]) -> dict:
    """Pepper-Lookup für mehrere Brands gleichzeitig, aufgeschlüsselt nach Land.

    Output:
      {
        "by_brand": {brand: {total, pos_rate, by_country: {iso: {pos, neg, neu, total, pos_rate}}}},
        "brands_found": int,
        "total_mentions_all": int,
        "error": str (nur bei Fehler)
      }
    """
    if not brand_names:
        return {"by_brand": {}, "brands_found": 0, "total_mentions_all": 0}

    where_clause = _build_where_clause(brand_names)
    brand_list = "\n".join(f"- {n}" for n in brand_names if n.strip())

    prompt = _MULTI_PROMPT.format(
        firma=firma.replace('"', '\\"'),
        brand_list=brand_list,
        where_clause=where_clause,
        lookback=_LOOKBACK_DAYS,
    )

    logger.info("pepper_multi: '%s' — %d Brands → Pepper-Subprocess", firma, len(brand_names))

    # Subprocess + 1 Retry bei MCP-Unavailable (Claude Code lädt MCP-Connector
    # gelegentlich nicht beim ersten Cold-Start, zweiter Versuch klappt oft).
    raw = ""
    attempts = 2
    last_error = ""
    for attempt in range(1, attempts + 1):
        try:
            raw = await run_claude_agent(prompt, timeout=_TIMEOUT_SEC * 2)
        except Exception as exc:
            logger.warning("pepper_multi: subprocess-Exception (attempt %d): %s", attempt, exc)
            last_error = f"subprocess: {exc}"
            continue

        if raw.startswith("❌") or raw.startswith("⏳"):
            logger.warning("pepper_multi: subprocess-Fehler (attempt %d): %s", attempt, raw[:200])
            last_error = raw[:300]
            continue

        if _looks_like_mcp_unavailable(raw):
            logger.warning(
                "pepper_multi: MCP-Tool unavailable (attempt %d) — Pi-Claude hat Pepper-Connector nicht geladen. Raw: %r",
                attempt, raw[:300],
            )
            last_error = "Pepper-MCP nicht verfügbar im Subprocess"
            if attempt < attempts:
                import asyncio as _asyncio
                await _asyncio.sleep(5)   # kurze Pause, dann Retry
            continue

        # Erfolg: weiter zur JSON-Parsing
        break

    if _looks_like_mcp_unavailable(raw) or raw.startswith("❌") or raw.startswith("⏳"):
        return {"by_brand": {}, "brands_found": 0, "total_mentions_all": 0,
                "total_deals_all": 0, "error": last_error}

    parsed = _extract_json(raw)
    if parsed is None:
        logger.warning("pepper_multi: JSON-Parse-Fehler; raw=%r", raw[:300])
        return {"by_brand": {}, "brands_found": 0, "total_mentions_all": 0,
                "total_deals_all": 0, "error": "JSON parse failed"}

    by_brand = parsed.get("by_brand") or {}
    logger.info(
        "pepper_multi: '%s' → %d Brands, %d Total-Mentions, %d Deals",
        firma, len(by_brand),
        int(parsed.get("total_mentions_all") or 0),
        int(parsed.get("total_deals_all") or 0),
    )
    return {
        "by_brand":           by_brand,
        "brands_found":       int(parsed.get("brands_found") or 0),
        "total_mentions_all": int(parsed.get("total_mentions_all") or 0),
        "total_deals_all":    int(parsed.get("total_deals_all") or 0),
    }


def _aggregate_country(by_brand: dict, country_iso: str) -> dict:
    """Aggregiert Brand-Daten für ein Country zu einem flachen Dict."""
    if not by_brand or not country_iso:
        return {}
    pos = neg = neu = total_deals = 0
    brands_seen: list[str] = []
    aspect_acc: dict[str, dict[str, int]] = {}      # aspect → {pos, neg, neu, total}
    for brand, stats in by_brand.items():
        c = (stats.get("by_country") or {}).get(country_iso)
        if not c:
            continue
        cp = int(c.get("pos") or 0)
        cn = int(c.get("neg") or 0)
        cu = int(c.get("neu") or 0)
        if (cp + cn + cu) == 0:
            continue
        pos += cp
        neg += cn
        neu += cu
        total_deals += int(c.get("deals") or 0)
        brands_seen.append(f"{brand}:{cp+cn+cu}")
        for a in (c.get("top_aspects") or []):
            name = (a.get("aspect") or "").strip().lower()
            if not name:
                continue
            row = aspect_acc.setdefault(name, {"pos": 0, "neg": 0, "neu": 0, "total": 0})
            row["pos"]   += int(a.get("pos") or 0)
            row["neg"]   += int(a.get("neg") or 0)
            row["neu"]   += int(a.get("neu") or 0)
            row["total"] += int(a.get("total") or 0)
    if (pos + neg + neu) == 0:
        return {}
    top_aspects = sorted(
        ({"aspect": name, **row} for name, row in aspect_acc.items()),
        key=lambda r: r["total"], reverse=True,
    )[:3]
    return {
        "pos": pos, "neg": neg, "neu": neu,
        "total": pos + neg + neu,
        "deals": total_deals,
        "pos_rate": round(pos / (pos + neg), 3) if (pos + neg) > 0 else None,
        "brands": brands_seen,
        "top_aspects": top_aspects,
    }


def format_country_sentiment(by_brand: dict, country_iso: str) -> str:
    """1-Zeilen-Summary für eine spezifische Country-Spalte ('de', 'pl', ...).

    Aggregiert über alle Brands für dieses Land. Enthält Mentions, Pos-Rate,
    Deal-Count und Top-3-Aspects.
    """
    agg = _aggregate_country(by_brand, country_iso)
    if not agg:
        return "—"
    parts = [f"{agg['total']} M ({agg['deals']} Deals)", f"{agg['pos']}↑/{agg['neg']}↓"]
    if agg["pos_rate"] is not None:
        parts.append(f"{agg['pos_rate']*100:.0f}%↑")
    if agg["top_aspects"]:
        asp_strs = []
        for a in agg["top_aspects"]:
            asp_strs.append(f"{a['aspect']}({a['pos']}↑/{a['neg']}↓)")
        parts.append("Top: " + ", ".join(asp_strs))
    return " · ".join(parts)


def format_cross_country_summary(by_brand: dict, exclude_iso: str | None = None,
                                  top_n: int = 4) -> str:
    """Cross-Country-Summary: Top-N Länder (außer Zielland) mit Pepper-Aktivität.

    Zeigt pro Land: Mentions, Deals, Pos-Rate.
    """
    if not by_brand:
        return "—"
    per_country: dict[str, dict] = {}
    for brand, stats in by_brand.items():
        for iso, c in (stats.get("by_country") or {}).items():
            if exclude_iso and iso == exclude_iso:
                continue
            row = per_country.setdefault(iso, {"pos": 0, "neg": 0, "neu": 0, "deals": 0})
            row["pos"]   += int(c.get("pos") or 0)
            row["neg"]   += int(c.get("neg") or 0)
            row["neu"]   += int(c.get("neu") or 0)
            row["deals"] += int(c.get("deals") or 0)
    enriched = [
        (iso, r["pos"] + r["neg"] + r["neu"], r["pos"], r["neg"], r["deals"])
        for iso, r in per_country.items()
    ]
    enriched = sorted(enriched, key=lambda x: x[1], reverse=True)[:top_n]
    parts = []
    for iso, total, p, n, d in enriched:
        if total == 0:
            continue
        rate = p / (p + n) * 100 if (p + n) > 0 else None
        rate_s = f" {rate:.0f}%↑" if rate is not None else ""
        parts.append(f"{iso.upper()}:{total}m/{d}d{rate_s}")
    return " | ".join(parts) or "—"
