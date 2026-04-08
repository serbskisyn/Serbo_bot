import httpx
import logging
from datetime import datetime, timezone, timedelta
from xml.etree import ElementTree as ET
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

MAX_AGE_HOURS = 48


@dataclass
class NewsItem:
    title: str
    url: str
    source: str
    published: datetime | None = None
    snippet: str = ""


RSS_FEEDS = [
    ("kicker.de",       "https://www.kicker.de/news/fussball/bundesliga/rss/bundesliga.rss"),
    ("sport1.de",       "https://www.sport1.de/news.rss"),
    ("transfermarkt.de","https://www.transfermarkt.de/rss/news"),
    ("skysports.com",   "https://www.skysports.com/rss/12040"),
]


def _google_news_url(query: str) -> str:
    q = query.replace(" ", "+")
    return f"https://news.google.com/rss/search?q={q}&hl=de&gl=DE&ceid=DE:de"


def _parse_date(date_str: str | None) -> datetime | None:
    if not date_str:
        return None
    for fmt in (
        "%a, %d %b %Y %H:%M:%S %z",
        "%a, %d %b %Y %H:%M:%S GMT",
        "%Y-%m-%dT%H:%M:%S%z",
    ):
        try:
            return datetime.strptime(date_str.strip(), fmt)
        except ValueError:
            continue
    return None


def _is_recent(pub: datetime | None) -> bool:
    if pub is None:
        return True  # kein Datum → nicht ausfiltern
    cutoff = datetime.now(timezone.utc) - timedelta(hours=MAX_AGE_HOURS)
    if pub.tzinfo is None:
        pub = pub.replace(tzinfo=timezone.utc)
    return pub >= cutoff


def _parse_feed(xml_text: str, source_name: str) -> list[NewsItem]:
    items = []
    try:
        root = ET.fromstring(xml_text)
        channel = root.find("channel")
        if channel is None:
            channel = root
        for item in channel.findall("item"):
            title   = (item.findtext("title") or "").strip()
            url     = (item.findtext("link")  or "").strip()
            snippet = (item.findtext("description") or "").strip()[:300]
            pub     = _parse_date(item.findtext("pubDate"))

            if not title or not url:
                continue
            if not _is_recent(pub):
                continue

            items.append(NewsItem(
                title=title,
                url=url,
                source=source_name,
                published=pub,
                snippet=snippet,
            ))
    except ET.ParseError as e:
        logger.warning(f"RSS Parse Fehler ({source_name}): {e}")
    return items


async def _fetch_feed(client: httpx.AsyncClient, url: str, source: str) -> list[NewsItem]:
    try:
        r = await client.get(url, timeout=10.0, follow_redirects=True)
        r.raise_for_status()
        return _parse_feed(r.text, source)
    except Exception as e:
        logger.warning(f"Feed Fehler ({source}): {e}")
        return []


async def fetch_club_news(club_name: str) -> list[NewsItem]:
    """Fetcht News für einen Club aus RSS-Feeds + Google News."""
    all_items: list[NewsItem] = []

    async with httpx.AsyncClient(headers={"User-Agent": "Mozilla/5.0"}) as client:
        # Google News (club-spezifisch, DE + EN)
        for query in [club_name, f"{club_name} transfers", f"{club_name} news"]:
            items = await _fetch_feed(client, _google_news_url(query), "Google News")
            all_items.extend(items)

        # Statische RSS Feeds (allgemein, werden später per Club gefiltert)
        for source, url in RSS_FEEDS:
            items = await _fetch_feed(client, url, source)
            all_items.extend(items)

    # Nur Artikel die den Club-Namen im Titel oder Snippet enthalten
    club_lower = club_name.lower()
    # Auch gängige Kurzformen berücksichtigen (z.B. "Bayern" für "FC Bayern München")
    keywords = _club_keywords(club_name)
    filtered = [
        item for item in all_items
        if any(kw in item.title.lower() or kw in item.snippet.lower() for kw in keywords)
    ]

    logger.info(f"fetch_club_news({club_name}): {len(filtered)} Artikel gefunden")
    return filtered


def _club_keywords(club_name: str) -> list[str]:
    """Generiert Suchbegriffe für einen Club (Kurzformen etc.)."""
    name = club_name.lower()
    keywords = [name]
    # Gängige Muster
    replacements = {
        "fc bayern münchen": ["bayern", "fcb", "fc bayern"],
        "borussia dortmund": ["dortmund", "bvb"],
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
        "manchester united":  ["man united", "united", "man utd"],
        "liverpool fc":      ["liverpool"],
        "chelsea fc":        ["chelsea"],
        "arsenal fc":        ["arsenal"],
        "paris saint-germain": ["psg", "paris"],
        "juventus":          ["juventus", "juve"],
        "inter milan":       ["inter", "inter mailand"],
        "ac milan":          ["milan", "ac milan"],
    }
    for key, aliases in replacements.items():
        if key in name or name in key:
            keywords.extend(aliases)
    return list(set(keywords))