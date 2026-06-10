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

from app.services.mcp_runner import run_mcp_subprocess

logger = logging.getLogger(__name__)

_LOOKBACK_DAYS = 180
_TIMEOUT_SEC = 150   # product_mentions JOIN canonical_products braucht etwas länger


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


def _repair_truncated_json(s: str) -> dict | None:
    """Recover a JSON object that was cut off mid-generation (the common Pepper
    failure: a large multi-country reply truncated before its closing braces).

    Walks the string tracking string-literal state + an opener stack, trims to
    the last completed container boundary, drops any trailing partial token,
    then appends the missing closers. Recovers the complete prefix (e.g. all
    fully-received countries) instead of losing the whole signal.
    """
    start = s.find("{")
    if start < 0:
        return None
    s = s[start:]

    in_str = esc = False
    last_close = -1            # index just after the last completed '}' or ']'
    for i, ch in enumerate(s):
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch in "}]":
            last_close = i + 1
    if last_close < 0:
        return None

    frag = s[:last_close]
    # Re-scan the kept fragment to compute which openers are still unclosed.
    stack: list[str] = []
    in_str = esc = False
    for ch in frag:
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch in "{[":
            stack.append(ch)
        elif ch == "}":
            if stack and stack[-1] == "{":
                stack.pop()
        elif ch == "]":
            if stack and stack[-1] == "[":
                stack.pop()
    closers = "".join("}" if c == "{" else "]" for c in reversed(stack))
    candidate = frag.rstrip().rstrip(",") + closers
    try:
        return json.loads(candidate)
    except json.JSONDecodeError:
        return None


def _extract_json(raw: str) -> dict | None:
    """Extract a JSON object from Claude output — tolerant of markdown fences,
    surrounding prose, AND mid-generation truncation."""
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
            pass
    # Last resort: the reply was cut off before its closing braces — salvage the
    # complete prefix so a truncated tail doesn't zero out the whole signal.
    return _repair_truncated_json(raw)


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
        raw = await run_mcp_subprocess(prompt, timeout=_TIMEOUT_SEC, label="pepper")
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
    """One-line English summary for sheet column / Telegram."""
    if not result.get("found"):
        return "No Pepper mentions"
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
# Strategie: Zwei separate SQL-Queries — beide vorab aggregiert, kein Claude-
# Side-Aggregation nötig. Subprocess muss nur die Rows in JSON umformatieren.

_MULTI_PROMPT = """Hi! I'm running the Atolls Lead-Qualifying-Bot. I need brand-sentiment data from Pepper for the company "{firma}" (matching brands: {brand_names_csv}).

Please run this SQL via mcp__claude_ai_Pepper_Intelligence__query_intelligence:

SELECT
  cp.brand,
  pm.country_code                                                        AS country,
  COUNT(*)                                                               AS total,
  COUNT(*) FILTER (WHERE pm.sentiment = 'positive')                     AS pos,
  COUNT(*) FILTER (WHERE pm.sentiment = 'neutral')                      AS neu,
  COUNT(*) FILTER (WHERE pm.sentiment = 'negative')                     AS neg,
  COUNT(*) FILTER (WHERE pm.sentiment = 'mixed')                        AS mixed,
  COUNT(DISTINCT pm.deal_id)                                             AS deals
FROM product_mentions pm
JOIN canonical_products cp ON cp.id = pm.canonical_product_id
WHERE ({where_clause})
  AND pm.comment_created_at >= current_date - {lookback}
GROUP BY cp.brand, pm.country_code
ORDER BY total DESC
LIMIT 200;

A Python script reads your reply with json.loads(). Return the rows as a JSON object with this exact shape:

{{"by_brand":{{
  "<brand>":{{
    "total_mentions":<int>,
    "total_deals":<int>,
    "by_country":{{
      "<country>":{{"pos":int,"neu":int,"neg":int,"mixed":int,"total":int,"deals":int}}
    }}
  }}
}},"brands_found":<int>,"total_mentions_all":<int>,"total_deals_all":<int>}}

If the query returns zero rows: {{"by_brand":{{}},"brands_found":0,"total_mentions_all":0,"total_deals_all":0}}

Respond with the bare JSON only, no markdown fences. Thanks!"""


def _build_where_clause(brand_names: list[str]) -> str:
    """Baut OR-verkettete ILIKE-Bedingungen auf cp.brand für mehrere Brand-Patterns."""
    if not brand_names:
        return "1=0"
    clauses = []
    for n in brand_names:
        pat = _normalize_brand(n)
        if not pat:
            continue
        pat_sql = pat.replace("'", "''")
        clauses.append(f"cp.brand ILIKE '%{pat_sql}%'")
    return " OR ".join(clauses) if clauses else "1=0"


_MCP_UNAVAILABLE_MARKERS = (
    "is not available in my current toolset",
    "not listed among my deferred tools",
    "is not available",
    "isn't available",
    "tool not available",
    "tool isn't available",
    # Auth-failure phrasings — the connector dropped its session
    "isn't authenticated",
    "is not authenticated",
    "not authenticated",
    "authenticate",
    "oauth/authenticate",
    "run `/mcp`",
    "run /mcp",
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

    # Cap auf 5 Brands — größere Multi-Brand-SQLs lassen den Subprocess hängen
    brand_names = brand_names[:5]
    where_clause = _build_where_clause(brand_names)
    brand_names_csv = ", ".join(brand_names)

    prompt = _MULTI_PROMPT.format(
        firma=firma.replace('"', '\\"'),
        brand_names_csv=brand_names_csv,
        where_clause=where_clause,
        lookback=_LOOKBACK_DAYS,
    )

    logger.info("pepper_multi: '%s' — %d Brands → Pepper-Subprocess", firma, len(brand_names))

    # Subprocess + 1 Retry bei MCP-Unavailable. Timeout pro Versuch _TIMEOUT_SEC.
    raw = ""
    attempts = 2
    last_error = ""
    for attempt in range(1, attempts + 1):
        try:
            raw = await run_mcp_subprocess(prompt, timeout=_TIMEOUT_SEC, label="pepper_multi")
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
        logger.warning("pepper_multi: JSON-Parse-Fehler; len=%d head=%r tail=%r",
                       len(raw), raw[:200], raw[-200:])
        return {"by_brand": {}, "brands_found": 0, "total_mentions_all": 0,
                "total_deals_all": 0, "error": "JSON parse failed"}

    by_brand = parsed.get("by_brand") or {}

    # Derive totals from by_brand rather than trusting Claude's root-level sums:
    # those sit AFTER by_brand in the JSON and are the first thing lost when the
    # reply is truncated. Recomputing here keeps the score correct even if the
    # tail was salvaged/missing.
    derived_mentions = derived_deals = 0
    for stats in by_brand.values():
        for c in (stats.get("by_country") or {}).values():
            derived_mentions += int(c.get("total") or 0)
            derived_deals += int(c.get("deals") or 0)

    total_all   = int(parsed.get("total_mentions_all") or 0) or derived_mentions
    total_deals = int(parsed.get("total_deals_all") or 0) or derived_deals
    brands_found = int(parsed.get("brands_found") or 0) or len(by_brand)

    if parsed.get("total_mentions_all") in (None, 0) and derived_mentions > 0:
        logger.info("pepper_multi: recovered %d mentions from truncated/partial JSON", derived_mentions)
    logger.info(
        "pepper_multi: '%s' → %d Brands, %d Total-Mentions, %d Deals",
        firma, len(by_brand), total_all, total_deals,
    )
    return {
        "by_brand":           by_brand,
        "brands_found":       brands_found,
        "total_mentions_all": total_all,
        "total_deals_all":    total_deals,
    }


def _aggregate_country(by_brand: dict, country_iso: str) -> dict:
    """Aggregiert Brand-Daten für ein Country zu einem flachen Dict."""
    if not by_brand or not country_iso:
        return {}
    pos = neg = neu = mixed = total = total_deals = 0
    brands_seen: list[str] = []
    for brand, stats in by_brand.items():
        c = (stats.get("by_country") or {}).get(country_iso)
        if not c:
            continue
        ct = int(c.get("total") or 0)
        if ct == 0:
            continue
        pos   += int(c.get("pos")   or 0)
        neg   += int(c.get("neg")   or 0)
        neu   += int(c.get("neu")   or 0)
        mixed += int(c.get("mixed") or 0)
        total += ct
        total_deals += int(c.get("deals") or 0)
        brands_seen.append(f"{brand}:{ct}")
    if total == 0:
        return {}
    return {
        "pos": pos, "neg": neg, "neu": neu, "mixed": mixed,
        "total": total,
        "deals": total_deals,
        "pos_rate": round(pos / total, 3) if total > 0 else None,
        "neg_rate": round(neg / total, 3) if total > 0 else None,
        "brands": brands_seen,
    }


def _sentiment_emoji(pos: int, neg: int, total: int) -> str:
    """Traffic-light emoji based on positive vs negative share."""
    if total <= 0:
        return "⚪"
    pos_pct = pos / total
    neg_pct = neg / total
    if pos_pct >= 0.55:
        return "🟢"
    if neg_pct >= 0.55:
        return "🔴"
    return "🟡"


def _fmt_country_line(iso: str, pos: int, neu: int, neg: int, mixed: int, total: int) -> str:
    """RAG-compact: 🔴 DE: 3106m (10%↑ 70%↓ 20%~)"""
    if total <= 0:
        return f"⚪ {iso.upper()}: 0m"
    pos_pct = round(pos / total * 100)
    neg_pct = round(neg / total * 100)
    neu_pct = round((neu + mixed) / total * 100)
    emoji = _sentiment_emoji(pos, neg, total)
    return f"{emoji} {iso.upper()}: {total}m ({pos_pct}%↑ {neg_pct}%↓ {neu_pct}%~)"


def _sentiment_label(pos: int, neg: int, total: int) -> str:
    """Short dominant-sentiment label for summary lines."""
    if total <= 0:
        return "no data"
    pos_pct = pos / total
    neg_pct = neg / total
    if pos_pct >= 0.55:
        return f"mostly positive ({pos_pct*100:.0f}%↑)"
    if neg_pct >= 0.55:
        return f"mostly negative ({neg_pct*100:.0f}%↓)"
    if pos_pct >= 0.40:
        return f"leaning positive ({pos_pct*100:.0f}%↑)"
    if neg_pct >= 0.40:
        return f"leaning negative ({neg_pct*100:.0f}%↓)"
    return "mixed"


def format_country_sentiment(by_brand: dict, country_iso: str) -> str:
    """RAG-compact domestic target sentiment: summary header + detail line."""
    agg = _aggregate_country(by_brand, country_iso)
    if not agg:
        return "—"
    total = agg["total"]
    pos   = agg["pos"]
    neg   = agg["neg"]
    deals = agg.get("deals", 0)
    n_brands = len([b for b in (agg.get("brands") or []) if b])

    label = _sentiment_label(pos, neg, total)
    summary_parts = [f"{total:,}m — {label}"]
    if n_brands > 1:
        summary_parts.append(f"{n_brands} brands")
    if deals:
        summary_parts.append(f"{deals:,} deals")
    summary = "Domestic · " + " · ".join(summary_parts)

    detail = _fmt_country_line(
        country_iso,
        agg["pos"], agg["neu"], agg["neg"], agg["mixed"], agg["total"],
    )
    return f"{summary}\n{detail}"


def format_cross_country_summary(by_brand: dict, exclude_iso: str | None = None,
                                  top_n: int = 4) -> str:
    """RAG-compact cross-country matrix: summary header + one line per country."""
    if not by_brand:
        return "—"
    per_country: dict[str, dict] = {}
    for brand, stats in by_brand.items():
        for iso, c in (stats.get("by_country") or {}).items():
            row = per_country.setdefault(iso, {"pos": 0, "neg": 0, "neu": 0, "mixed": 0, "total": 0})
            row["pos"]   += int(c.get("pos")   or 0)
            row["neg"]   += int(c.get("neg")   or 0)
            row["neu"]   += int(c.get("neu")   or 0)
            row["mixed"] += int(c.get("mixed") or 0)
            row["total"] += int(c.get("total") or 0)

    rows = sorted(
        ((iso, r["pos"], r["neu"], r["neg"], r["mixed"], r["total"])
         for iso, r in per_country.items() if r["total"] > 0),
        key=lambda x: x[5], reverse=True,
    )
    if not rows:
        return "—"

    grand_total = sum(r[5] for r in rows)
    grand_pos   = sum(r[1] for r in rows)
    grand_neg   = sum(r[3] for r in rows)
    n_markets   = len(rows)
    top_market  = rows[0][0].upper()

    label = _sentiment_label(grand_pos, grand_neg, grand_total)
    summary = (
        f"Cross-country · {grand_total:,}m across {n_markets} market{'s' if n_markets != 1 else ''}"
        f" — {label} · top: {top_market}"
    )

    lines = [summary] + [_fmt_country_line(iso, p, nu, ng, mx, t) for iso, p, nu, ng, mx, t in rows]
    return "\n".join(lines)
