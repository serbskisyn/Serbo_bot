import re
import httpx
import logging
from datetime import datetime, timezone, timedelta
from xml.etree import ElementTree as ET
from dataclasses import dataclass
from urllib.parse import quote_plus

logger = logging.getLogger(__name__)

MAX_AGE_HOURS = 48


@dataclass
class NewsItem:
    title: str
    url: str
    source: str
    published: datetime | None = None
    snippet: str = ""


# Funktionierende RSS Feeds (getestet)
RSS_FEEDS = [
    ("transfermarkt.de", "https://www.transfermarkt.de/rss/news"),
    ("skysports.com",    "https://www.skysports.com/rss/12040"),
    ("BBC Sport",        "https://feeds.bbci.co.uk/sport/football/rss.xml"),
    ("ESPN FC",          "https://www.espn.com/espn/rss/soccer/news"),
    ("90min.com",        "https://www.90min.com/posts.rss"),
]


def _clean_query(text: str) -> str:
    """Entfernt Klammern, Sonderzeichen für saubere Google News Queries."""
    text = re.sub(r"\(.*?\)", "", text)   # z.B. "(BVB 09)" entfernen
    text = re.sub(r"[^\w\s-]", "", text)  # Sonderzeichen entfernen
    return text.strip()


def _google_news_url(query: str) -> str:
    return f"https://news.google.com/rss/search?q={quote_plus(query)}&hl=de&gl=DE&ceid=DE:de"


def _google_news_url_en(query: str) -> str:
    return f"https://news.google.com/rss/search?q={quote_plus(query)}&hl=en&gl=US&ceid=US:en"


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
        return True
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
    """Fetcht News für einen Club aus RSS-Feeds + Google News (DE + EN)."""
    all_items: list[NewsItem] = []
    clean_name = _clean_query(club_name)
    keywords   = _club_keywords(club_name)

    async with httpx.AsyncClient(
        headers={"User-Agent": "Mozilla/5.0 (compatible; Googlebot/2.1)"}
    ) as client:

        # Google News DE
        for query in [clean_name, f"{clean_name} transfers", f"{clean_name} news"]:
            items = await _fetch_feed(client, _google_news_url(query), "Google News DE")
            all_items.extend(items)

        # Google News EN (internationale Transfers etc.)
        for query in [clean_name, f"{clean_name} transfer"]:
            items = await _fetch_feed(client, _google_news_url_en(query), "Google News EN")
            all_items.extend(items)

        # Statische RSS Feeds
        for source, url in RSS_FEEDS:
            items = await _fetch_feed(client, url, source)
            all_items.extend(items)

    # Filter: nur Artikel die mindestens ein Keyword im Titel oder Snippet haben
    filtered = [
        item for item in all_items
        if any(
            kw in item.title.lower() or kw in item.snippet.lower()
            for kw in keywords
        )
    ]

    logger.info(f"fetch_club_news({club_name}): {len(filtered)} Artikel gefunden (von {len(all_items)} gesamt)")
    return filtered


def _club_keywords(club_name: str) -> list[str]:
    """Generiert robuste Suchbegriffe inkl. Kurzformen."""
    # Klammerzusatz entfernen: "Borussia Dortmund (BVB 09)" → "borussia dortmund"
    clean = re.sub(r"\(.*?\)", "", club_name).strip().lower()
    keywords = [clean]

    # Klammerninhalt als zusätzliches Keyword
    bracket = re.findall(r"\(([^)]+)\)", club_name.lower())
    for b in bracket:
        # "bvb 09" → ["bvb 09", "bvb"]
        keywords.append(b.strip())
        keywords.append(b.split()[0].strip())  # erstes Wort aus Klammer

    aliases = {
        "fc bayern münchen":         ["bayern", "fcb", "fc bayern", "bavaria"],
        "borussia dortmund":          ["dortmund", "bvb"],
        "rb leipzig":                 ["leipzig", "rbl", "red bull leipzig"],
        "bayer leverkusen":           ["leverkusen", "bayer"],
        "borussia mönchengladbach":  ["gladbach", "bmg", "mönchengladbach"],
        "vfb stuttgart":              ["stuttgart", "vfb"],
        "eintracht frankfurt":        ["frankfurt", "sge"],
        "sc freiburg":                ["freiburg"],
        "fc schalke 04":              ["schalke", "s04"],
        "hamburger sv":               ["hsv", "hamburg"],
        "werder bremen":              ["werder", "bremen"],
        "1. fc köln":                ["köln", "cologne", "koeln"],
        "hertha bsc":                 ["hertha", "bsc"],
        "real madrid":                ["real madrid", "madrid", "los blancos"],
        "fc barcelona":               ["barcelona", "barça", "barca", "blaugrana"],
        "manchester city":            ["man city", "city", "mcfc"],
        "manchester united":          ["man united", "united", "man utd", "mufc"],
        "liverpool fc":               ["liverpool", "reds", "lfc"],
        "chelsea fc":                 ["chelsea", "blues", "cfc"],
        "arsenal fc":                 ["arsenal", "gunners"],
        "tottenham hotspur":          ["tottenham", "spurs", "thfc"],
        "paris saint-germain":        ["psg", "paris", "saint-germain"],
        "juventus":                   ["juventus", "juve", "bianconeri"],
        "inter milan":                ["inter", "inter mailand", "internazionale"],
        "ac milan":                   ["milan", "ac milan", "rossoneri"],
        "atletico madrid":            ["atletico", "atlético"],
        "as roma":                    ["roma", "as roma"],
        "ssc napoli":                 ["napoli"],
    }

    for key, alias_list in aliases.items():
        if key in clean or clean in key:
            keywords.extend(alias_list)

    return list(set(keywords))
