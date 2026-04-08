import re
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
    ("sportbild.de", "http://sportbild.bild.de/rss/vw-fussball/vw-fussball-45036878,sort=1,view=rss2.sport.xml"),
    ("bild.de",      "http://www.bild.de/rss-feeds/rss-16725492,feed=sport.bild.html"),
    ("skysports.com","https://www.skysports.com/rss/12040"),
]

CLUB_FEEDS: dict[str, list[str]] = {
    "borussia dortmund": [
        "https://www.transfermarkt.de/borussia-dortmund/rss/verein/16",
    ],
    "dynamo dresden": [
        "https://www.dynamo-dresden.de/news/rss.xml",
        "https://www.transfermarkt.de/dynamo-dresden/rss/verein/377",
        "https://www.saechsische.de/sport/rss.xml",
        "https://www.mdr.de/sport/index-rss.xml",
    ],
    "fc bayern münchen": [
        "https://www.transfermarkt.de/fc-bayern-munchen/rss/verein/27",
    ],
    "rb leipzig": [
        "https://www.transfermarkt.de/rb-leipzig/rss/verein/23826",
    ],
    "bayer leverkusen": [
        "https://www.transfermarkt.de/bayer-04-leverkusen/rss/verein/15",
    ],
    "eintracht frankfurt": [
        "https://www.transfermarkt.de/eintracht-frankfurt/rss/verein/24",
    ],
    "vfb stuttgart": [
        "https://www.transfermarkt.de/vfb-stuttgart/rss/verein/79",
    ],
    "sc freiburg": [
        "https://www.transfermarkt.de/sport-club-freiburg/rss/verein/17",
    ],
    "real madrid": [
        "https://www.transfermarkt.de/real-madrid/rss/verein/418",
    ],
    "fc barcelona": [
        "https://www.transfermarkt.de/fc-barcelona/rss/verein/131",
    ],
    "manchester city": [
        "https://www.transfermarkt.de/manchester-city/rss/verein/281",
    ],
    "manchester united": [
        "https://www.transfermarkt.de/manchester-united/rss/verein/985",
    ],
    "liverpool fc": [
        "https://www.transfermarkt.de/fc-liverpool/rss/verein/31",
    ],
}


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
    return " ".join(words[:max_words]) + "..."


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


def _is_homepage_url(url: str) -> bool:
    try:
        path = url.rstrip("/").split("/", 3)
        return len(path) < 4 or not path[3]
    except Exception:
        return False


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
            dt = datetime.strptime(date_str.strip(), fmt)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt
        except ValueError:
            continue
    return None


def _club_keywords(club_name: str) -> list[str]:
    name = club_name.lower()
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
        "fc barcelona":      ["barcelona", "barca"],
        "manchester city":   ["man city", "city"],
        "manchester united": ["man united", "united", "man utd"],
        "liverpool fc":      ["liverpool"],
        "chelsea fc":        ["chelsea"],
        "arsenal fc":        ["arsenal"],
        "paris saint-germain": ["psg", "paris"],
        "juventus":          ["juventus", "juve"],
        "inter milan":       ["inter", "inter mailand"],
        "ac milan":          ["milan", "ac milan"],
        "dynamo dresden":    ["dynamo", "sgd"],
    }
    for key, aliases in replacements.items():
        if key in name_clean or name_clean in key:
            keywords.extend(aliases)

    bracket = re.search(r"\(([^)]+)\)", club_name.lower())
    if bracket:
        short = bracket.group(1).split()[0]
        keywords.append(short)

    return list(set(keywords))


def _get_club_feeds(club_name: str) -> list[str]:
    name_clean = re.sub(r"\(.*?\)", "", club_name).lower().strip()
    for key, feeds in CLUB_FEEDS.items():
        if key in name_clean or name_clean in key:
            return feeds
    return []


def _gnews_from() -> str:
    dt = datetime.now(timezone.utc) - timedelta(hours=MAX_AGE_HOURS)
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


async def _fetch_gnews(client: httpx.AsyncClient, club_name: str) -> list[NewsItem]:
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
                pub     = _parse_date(art.get("publishedAt", ""))

                if not title or not art_url:
                    continue
                if _is_excluded(title + " " + snippet):
                    continue
                if _is_homepage_url(art_url):
                    continue

                logger.info(f"GNews URL: {art_url}")
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
            if _is_homepage_url(url):
                continue

            items.append(NewsItem(
                title=title,
                url=url,
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


async def fetch_club_news(club_name: str) -> list[NewsItem]:
    all_items: list[NewsItem] = []
    keywords = _club_keywords(club_name)

    async with httpx.AsyncClient(headers={"User-Agent": "Mozilla/5.0"}) as client:

        # Layer 1: GNews API
        gnews_items = await _fetch_gnews(client, club_name)
        all_items.extend(gnews_items)
        logger.info(f"GNews: {len(gnews_items)} Artikel fuer {club_name}")

        # Layer 2: Google News RSS
        name_clean = re.sub(r"\(.*?\)", "", club_name).strip()
        for query in [name_clean, f"{name_clean} Bundesliga", f"{name_clean} transfer"]:
            items = await _fetch_rss(client, _google_news_url(query), "Google News")
            all_items.extend(items)

        # Layer 3: Statische RSS Feeds
        for source, url in RSS_FEEDS:
            items = await _fetch_rss(client, url, source)
            all_items.extend(items)

        # Layer 4: Club-spezifische Feeds
        club_specific = _get_club_feeds(club_name)
        for url in club_specific:
            domain = url.split("/")[2].replace("www.", "")
            items  = await _fetch_rss(client, url, domain)
            all_items.extend(items)
            logger.info(f"Club-Feed ({domain}): {len(items)} Artikel")

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