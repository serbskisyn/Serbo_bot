import httpx
import logging
from datetime import datetime, timezone, timedelta
from xml.etree import ElementTree as ET
from dataclasses import dataclass
from urllib.parse import quote_plus
from app.config import GNEWS_API_KEY

logger = logging.getLogger(__name__)

MAX_AGE_HOURS = 48
SNIPPET_MAX_WORDS = 300

EXCLUDE_KEYWORDS = [
    " ii ", " u23", " u21", " u19", " u17", " reserve", " reserv",
    "frauen", "women", "female", "damen", "handball", "basketball",
    "esport", "youth", "jugend", "amateure", "dritte liga", "regionalliga",
]

RSS_FEEDS = [
    ("goal.com",      "https://www.goal.com/feeds/en/news"),
    ("eurosport.de",  "https://www.eurosport.de/rss.xml"),
    ("uefa.com",      "https://www.uefa.com/rssfeed/news/"),
    ("dfb.de",        "https://www.dfb.de/news/rss-feed/"),
    ("skysports.com", "https://www.skysports.com/rss/12040"),
    ("bild.de",       "https://www.bild.de/feed/sport-rss.xml"),
]


@dataclass
class NewsItem:
    title:     str
    url:       str
    source:    str
    published: datetime | None = None
    snippet:   str = ""


def _truncate_words(text: str, max_words: int = SNIPPET_MAX_WORDS) -> str:
    words = text.split()
    if len(words) <= max_words:
        return text
    return " ".join(words[:max_words]) + "…"


def _is_recent(pub: datetime | None) -> bool:
    if pub is None:
        return True
    cutoff = datetime.now(timezone.utc) - timedelta(hours=MAX_AGE_HOURS)
    if pub.tzinfo is None:
        pub = pub.replace(tzinfo=timezone.utc)
    return pub >= cutoff


def _is_excluded(text: str) -> bool:
    low = text.lower()
    return any(kw in low for kw in EXCLUDE_KEYWORDS)


def _parse_date(date_str: str | None) -> datetime | None:
    if not date_str:
        return None
    for fmt in (
        "%a, %d %b %Y %H:%M:%S %z",
        "%a, %d %b %Y %H:%M:%S GMT",
        "%Y-%m-%dT%H:%M:%S%z",
        "%Y-%m-%dT%H:%M:%SZ",
    ):
        try:
            return datetime.strptime(date_str.strip(), fmt)
        except ValueError:
            continue
    return None


def _club_keywords(club_name: str) -> list[str]:
    name = club_name.lower()
    # Klammern entfernen z.B. "Borussia Dortmund (BVB 09)" → "borussia dortmund"
    import re
    name_clean = re.sub(r"\(.*?\)", "", name).strip()
    keywords = [name_clean]

    replacements = {
        "borussia dortmund": ["dortmund", "bvb"],
        "fc bayern münchen": ["bayern", "fcb", "fc bayern"],
        "rb leipzig":        ["leipzig", "rbl"],
        "bayer leverkusen":  ["leverkusen"],
        "borussia mönchengladbach": ["gladbach", "bmg"],
        "vfb stuttgart":     ["stuttgart", "vfb"],
        "eintracht frankfurt": ["frankfurt", "sge"],
        "sc freiburg":       ["freiburg"],
        "fc schalke 04":     ["schalke", "s04"],
        "hamburger sv":      ["hsv", "hamburg"],
        "real madrid":       ["real madrid", "madrid"],
        "fc barcelona":      ["barcelona", "barça", "barca"],
        "manchester city":   ["man city", "city"],
        "manchester united": ["man united", "united", "man utd"],
        "liverpool fc":      ["liverpool"],
        "chelsea fc":        ["chelsea"],
        "arsenal fc":        ["arsenal"],
        "paris saint-germain": ["psg", "paris"],
        "juventus":          ["juventus", "juve"],
        "inter milan":       ["inter", "inter mailand"],
        "ac milan":          ["milan", "ac milan"],
    }
    for key, aliases in replacements.items():
        if key in name_clean or name_clean in key:
            keywords.extend(aliases)

    # Klammer-Inhalt als zusätzliches Keyword (z.B. "bvb 09" → "bvb")
    import re as _re
    bracket = _re.search(r"\(([^)]+)\)", club_name.lower())
    if bracket:
        short = bracket.group(1).split()[0]  # "bvb" aus "bvb 09"
        keywords.append(short)

    return list(set(keywords))


# ── GNews API ─────────────────────────────────────────────────────────────────

async def _fetch_gnews(client: httpx.AsyncClient, club_name: str) -> list[NewsItem]:
    """Fetcht Club-News direkt via GNews API inkl. Snippet."""
    import re
    name_clean = re.sub(r"\(.*?\)", "", club_name).strip()
    items = []

    for query in [name_clean, f"{name_clean} transfer", f"{name_clean} Champions League"]:
        try:
            url = (
                f"https://gnews.io/api/v4/search"
                f"?q={quote_plus(query)}"
                f"&lang=de&lang=en"
                f"&from={_gnews_from()}"
                f"&max=10"
                f"&apikey={GNEWS_API_KEY}"
            )
            r = await client.get(url, timeout=10.0)
            r.raise_for_status()
            data = r.json()
            for art in data.get("articles", []):
                title   = art.get("title", "").strip()
                art_url = art.get("url", "").strip()
                snippet = art.get("description", "").strip()
                source  = art.get("source", {}).get("name", "GNews")
                pub_str = art.get("publishedAt", "")
                pub     = _parse_date(pub_str)

                if not title or not art_url:
                    continue
                if _is_excluded(title + " " + snippet):
                    continue

                items.append(NewsItem(
                    title=title,
                    url=art_url,
                    source=source,
                    published=pub,
                    snippet=_truncate_words(snippet),
                ))
        except Exception as e:
            logger.warning(f"GNews Fehler ({query}): {e}")

    return items


def _gnews_from() -> str:
    """ISO8601 Timestamp für 48h zurück."""
    dt = datetime.now(timezone.utc) - timedelta(hours=MAX_AGE_HOURS)
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


# ── RSS Feeds (Fallback) ──────────────────────────────────────────────────────

def _parse_feed(xml_text: str, source_name: str) -> list[NewsItem]:
    items = []
    try:
        root    = ET.fromstring(xml_text)
        channel = root.find("channel") or root
        for item in channel.findall("item"):
            title   = (item.findtext("title") or "").strip()
            url     = (item.findtext("link") or "").strip()
            snippet = (item.findtext("description") or "").strip()
            pub     = _parse_date(item.findtext("pubDate"))

            if not title or not url:
                continue
            if not _is_recent(pub):
                continue
            if _is_excluded(title + " " + snippet):
                continue

            # Echte URL aus Google News source-Tag
            source_el = item.find("source")
            if source_el is not None and source_el.get("url"):
                real_source = source_el.get("url", "")
            else:
                real_source = url

            items.append(NewsItem(
                title=title,
                url=real_source,
                source=source_name,
                published=pub,
                snippet=_truncate_words(snippet),
            ))
    except ET.ParseError as e:
        logger.warning(f"RSS Parse Fehler ({source_name}): {e}")
    return items


def _google_news_url(query: str) -> str:
    return f"https://news.google.com/rss/search?q={quote_plus(query)}&hl=de&gl=DE&ceid=DE:de"


async def _fetch_rss(client: httpx.AsyncClient, url: str, source: str) -> list[NewsItem]:
    try:
        r = await client.get(url, timeout=10.0, follow_redirects=True)
        r.raise_for_status()
        return _parse_feed(r.text, source)
    except Exception as e:
        logger.warning(f"RSS Feed Fehler ({source}): {e}")
        return []


# ── Hauptfunktion ─────────────────────────────────────────────────────────────

async def fetch_club_news(club_name: str) -> list[NewsItem]:
    """
    Fetcht News für einen Club:
    1. GNews API (primär) — liefert Snippet direkt
    2. Google News RSS + statische Feeds (Fallback)
    """
    all_items: list[NewsItem] = []
    keywords = _club_keywords(club_name)

    async with httpx.AsyncClient(headers={"User-Agent": "Mozilla/5.0"}) as client:

        # Layer 1: GNews API
        gnews_items = await _fetch_gnews(client, club_name)
        all_items.extend(gnews_items)
        logger.info(f"GNews: {len(gnews_items)} Artikel für {club_name}")

        # Layer 2: Google News RSS
        import re
        name_clean = re.sub(r"\(.*?\)", "", club_name).strip()
        for query in [name_clean, f"{name_clean} Bundesliga", f"{name_clean} transfer"]:
            items = await _fetch_rss(client, _google_news_url(query), "Google News")
            all_items.extend(items)

        # Layer 3: Statische RSS Feeds
        for source, url in RSS_FEEDS:
            items = await _fetch_rss(client, url, source)
            all_items.extend(items)

    # Club-Keyword Filter
    filtered = [
        item for item in all_items
        if any(kw in item.title.lower() or kw in item.snippet.lower() for kw in keywords)
    ]

    # URL-Deduplizierung
    seen: set[str] = set()
    unique = []
    for item in filtered:
        if item.url not in seen:
            seen.add(item.url)
            unique.append(item)

    logger.info(f"fetch_club_news({club_name}): {len(unique)} Artikel (von {len(all_items)} gesamt)")
    return unique