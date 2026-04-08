import re
import httpx
import logging
from datetime import datetime, timezone, timedelta
from xml.etree import ElementTree as ET
from dataclasses import dataclass
from urllib.parse import quote_plus, urlparse, parse_qs, unquote

logger = logging.getLogger(__name__)

MAX_AGE_HOURS = 48

# ── Blacklist: Artikel die diese Keywords im Titel enthalten werden gefiltert ──
EXCLUDE_TITLE_KEYWORDS = [
    " ii", " 2", "u23", "u21", "u19", "u17", "u16", "u15",
    "reserve", "reserv",
    "frauen", "women", "female", "damen",
    "handball", "basketball", "esport", "fantasy",
    "youth", "jugend", "nachwuchs",
]

# ── RSS Feeds ─────────────────────────────────────────────────────────────────
RSS_FEEDS = [
    ("transfermarkt.de", "https://www.transfermarkt.de/rss/news"),
    ("skysports.com",    "https://www.skysports.com/rss/12040"),
    ("BBC Sport",        "https://feeds.bbci.co.uk/sport/football/rss.xml"),
    ("ESPN FC",          "https://www.espn.com/espn/rss/soccer/news"),
    ("90min.com",        "https://www.90min.com/posts.rss"),
    ("Goal.com DE",      "https://www.goal.com/feeds/de/news"),
    ("Eurosport DE",     "https://www.eurosport.de/fussball/rss.xml"),
    ("UEFA.com",         "https://www.uefa.com/rssfeed/news"),
    ("DFB.de",           "https://www.dfb.de/news/rss-feed/"),
    ("Bild.de Sport",    "https://www.bild.de/rss/sport/sport-16513140,sort=1,view=rss2.bild.xml"),
]


@dataclass
class NewsItem:
    title: str
    url: str
    source: str
    published: datetime | None = None
    snippet: str = ""


def _clean_query(text: str) -> str:
    """Entfernt Klammern + Sonderzeichen für saubere Google News Queries."""
    text = re.sub(r"\(.*?\)", "", text)
    text = re.sub(r"[^\w\s-]", "", text)
    return text.strip()


def _google_news_url(query: str) -> str:
    return f"https://news.google.com/rss/search?q={quote_plus(query)}&hl=de&gl=DE&ceid=DE:de"


def _google_news_url_en(query: str) -> str:
    return f"https://news.google.com/rss/search?q={quote_plus(query)}&hl=en&gl=US&ceid=US:en"


def _unmask_google_url(url: str) -> str:
    """
    Google News RSS liefert kryptische Redirect-URLs wie:
      https://news.google.com/rss/articles/CBMi...
    Oder manchmal: https://news.google.com/articles/CAIi...
    Wir versuchen die echte URL aus dem <source> Tag zu nehmen
    (wird im Caller gesetzt). Fallback: URL so lassen wie sie ist,
    aber zumindest lesbarer kürzen.
    """
    if "news.google.com" not in url:
        return url
    # Versuche ?url= Parameter zu extrahieren (älteres Format)
    parsed = urlparse(url)
    qs = parse_qs(parsed.query)
    if "url" in qs:
        return unquote(qs["url"][0])
    # Neues Format: kein lesbarer Parameter – URL bleibt, aber wir kürzen
    # den unlesbaren Token-Teil für die Anzeige (die echte URL bleibt im Link)
    return url


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


def _is_blacklisted(title: str) -> bool:
    """Filtert Artikel zu Frauenteams, Reserveteams, anderen Sportarten etc."""
    title_lower = title.lower()
    return any(kw in title_lower for kw in EXCLUDE_TITLE_KEYWORDS)


def _parse_feed(xml_text: str, source_name: str) -> list[NewsItem]:
    items = []
    try:
        root = ET.fromstring(xml_text)
        channel = root.find("channel")
        if channel is None:
            channel = root
        for item in channel.findall("item"):
            title   = (item.findtext("title") or "").strip()
            raw_url = (item.findtext("link")  or "").strip()
            snippet = (item.findtext("description") or "").strip()[:500]
            pub     = _parse_date(item.findtext("pubDate"))

            if not title or not raw_url:
                continue
            if not _is_recent(pub):
                continue
            if _is_blacklisted(title):
                continue

            # Google News Quell-URL bevorzugen wenn vorhanden
            source_url = ""
            source_el = item.find("source")
            if source_el is not None:
                source_url = source_el.get("url", "")

            # Echte URL bestimmen:
            # 1) source url (direkt zur Quelle)
            # 2) Unmask-Versuch
            # 3) raw_url als Fallback
            if source_url and "google.com" not in source_url:
                final_url = source_url
            else:
                final_url = _unmask_google_url(raw_url) or raw_url

            items.append(NewsItem(
                title=title,
                url=final_url,
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

        # Google News EN
        for query in [clean_name, f"{clean_name} transfer"]:
            items = await _fetch_feed(client, _google_news_url_en(query), "Google News EN")
            all_items.extend(items)

        # Statische RSS Feeds
        for source, url in RSS_FEEDS:
            items = await _fetch_feed(client, url, source)
            all_items.extend(items)

    # Club-Keyword Filter
    filtered = [
        item for item in all_items
        if any(
            kw in item.title.lower() or kw in item.snippet.lower()
            for kw in keywords
        )
    ]

    logger.info(
        f"fetch_club_news({club_name}): {len(filtered)} Artikel "
        f"(von {len(all_items)} gesamt, nach Blacklist + Keyword-Filter)"
    )
    return filtered


def _club_keywords(club_name: str) -> list[str]:
    """Generiert robuste Suchbegriffe inkl. Kurzformen."""
    clean = re.sub(r"\(.*?\)", "", club_name).strip().lower()
    keywords = [clean]

    # Klammerninhalt als zusätzliches Keyword (z.B. "bvb" aus "BVB 09")
    bracket = re.findall(r"\(([^)]+)\)", club_name.lower())
    for b in bracket:
        keywords.append(b.strip())
        keywords.append(b.split()[0].strip())

    aliases = {
        "fc bayern münchen":        ["bayern", "fcb", "fc bayern", "bavaria"],
        "borussia dortmund":         ["dortmund", "bvb"],
        "rb leipzig":                ["leipzig", "rbl", "red bull leipzig"],
        "bayer leverkusen":          ["leverkusen", "bayer"],
        "borussia mönchengladbach": ["gladbach", "bmg", "mönchengladbach"],
        "vfb stuttgart":             ["stuttgart", "vfb"],
        "eintracht frankfurt":       ["frankfurt", "sge"],
        "sc freiburg":               ["freiburg"],
        "fc schalke 04":             ["schalke", "s04"],
        "hamburger sv":              ["hsv", "hamburg"],
        "werder bremen":             ["werder", "bremen"],
        "1. fc köln":               ["köln", "cologne", "koeln"],
        "hertha bsc":                ["hertha", "bsc"],
        "real madrid":               ["real madrid", "madrid", "los blancos"],
        "fc barcelona":              ["barcelona", "barça", "barca", "blaugrana"],
        "manchester city":           ["man city", "city", "mcfc"],
        "manchester united":         ["man united", "united", "man utd", "mufc"],
        "liverpool fc":              ["liverpool", "reds", "lfc"],
        "chelsea fc":                ["chelsea", "blues", "cfc"],
        "arsenal fc":                ["arsenal", "gunners"],
        "tottenham hotspur":         ["tottenham", "spurs", "thfc"],
        "paris saint-germain":       ["psg", "paris", "saint-germain"],
        "juventus":                  ["juventus", "juve", "bianconeri"],
        "inter milan":               ["inter", "inter mailand", "internazionale"],
        "ac milan":                  ["milan", "ac milan", "rossoneri"],
        "atletico madrid":           ["atletico", "atlético"],
        "as roma":                   ["roma", "as roma"],
        "ssc napoli":                ["napoli"],
    }

    for key, alias_list in aliases.items():
        if key in clean or clean in key:
            keywords.extend(alias_list)

    return list(set(keywords))
