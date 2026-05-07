"""
news_fetcher.py — Holt Club-News aus mehreren Quellen.

Architektur:
- Layer 1: GNews API (strukturiert, mit Backoff)
- Layer 2: Google News RSS (2 Queries pro Club)
- Layer 3: Allgemeine Fussball-RSS-Feeds
- Layer 4: Club-spezifische Feeds aus config/clubs.json

Neuerungen:
- ClubConfig: liest clubs.json, loest Aliases/Feeds/Excludes auf
- Retry/Backoff: _fetch_rss + _fetch_gnews wiederholen bei 429/503/502
- FeedHealthTracker: protokolliert fehlgeschlagene Feeds pro Run
  und ermoeglicht Telegram-Alerts bei >50% Feed-Ausfall
"""
import re
import json
import asyncio
import httpx
import logging
from pathlib import Path
from datetime import datetime, timezone, timedelta
from xml.etree import ElementTree as ET
from dataclasses import dataclass, field
from urllib.parse import quote_plus
from app.config import GNEWS_API_KEY

logger = logging.getLogger(__name__)

MAX_AGE_HOURS = 48
SNIPPET_MAX_WORDS = 300

# Retry-Konfiguration fuer Backoff
RETRY_MAX       = 3          # Max. Versuche
RETRY_STATUSES  = {429, 502, 503}  # HTTP-Status die einen Retry ausloesen
RETRY_BASE_SECS = 2.0        # Basis fuer Exponential Backoff: 2^n Sekunden

# Feed-Health: Alert-Schwelle (Anteil fehlgeschlagener Feeds)
FEED_ALERT_THRESHOLD = 0.5  # 50% der Feeds fehlgeschlagen -> Alert

# Globale Ausschluss-Keywords (alle Clubs)
EXCLUDE_KEYWORDS = [
    " ii ", " u23", " u21", " u19", " u17", " reserve", " reserv",
    "frauen", "women", "female", "damen", "handball", "basketball",
    "esport", "youth", "jugend", "amateure", "dritte liga", "regionalliga",
]

# Allgemeine Fussball-RSS-Feeds (quellen-unabhaengig, immer abgefragt)
RSS_FEEDS = [
    ("sportbild.de",    "http://sportbild.bild.de/rss/vw-fussball/vw-fussball-45036878,sort=1,view=rss2.sport.xml"),
    ("bild.de",         "http://www.bild.de/rss-feeds/rss-16725492,feed=sport.bild.html"),
    ("skysports.com",   "https://www.skysports.com/rss/12040"),
    ("sportschau.de",   "https://www.sportschau.de/fussball/bundesliga2/index~rss2.xml"),
    ("kicker.de",       "https://newsfeed.kicker.de/news/fussball"),
    ("spox.com",        "https://feeds.feedburner.com/spox-sport/"),
    ("spiegel.de",      "https://www.spiegel.de/sport/fussball/index.rss"),
    ("sueddeutsche.de", "https://rss.sueddeutsche.de/rss/Sport"),
    ("transfermarkt.de","https://www.transfermarkt.de/rss/news"),
]


# ---------------------------------------------------------------------------
# Schritt 1: Club-Konfiguration aus clubs.json
# ---------------------------------------------------------------------------

class ClubConfig:
    """
    Liest config/clubs.json und stellt Aliases, Feeds und Exclude-Keywords
    pro Club bereit. Faellt bei fehlendem File auf leere Defaults zurueck.
    """
    _CONFIG_PATH = Path(__file__).parent.parent.parent / "config" / "clubs.json"
    _data: dict = {}

    @classmethod
    def _load(cls):
        if cls._data:
            return
        try:
            with open(cls._CONFIG_PATH, encoding="utf-8") as f:
                raw = json.load(f)
            cls._data = {k: v for k, v in raw.items() if not k.startswith("_")}
            logger.info(f"ClubConfig: {len(cls._data)} Clubs geladen aus {cls._CONFIG_PATH}")
        except FileNotFoundError:
            logger.warning(f"ClubConfig: clubs.json nicht gefunden unter {cls._CONFIG_PATH}")
        except json.JSONDecodeError as e:
            logger.error(f"ClubConfig: JSON-Fehler in clubs.json: {e}")

    @classmethod
    def _find(cls, club_name: str) -> dict:
        cls._load()
        name_clean = re.sub(r"\(.*?\)", "", club_name).lower().strip()
        for key, val in cls._data.items():
            if key in name_clean or name_clean in key:
                return val
        return {}

    @classmethod
    def get_aliases(cls, club_name: str) -> list[str]:
        entry = cls._find(club_name)
        name_clean = re.sub(r"\(.*?\)", "", club_name).lower().strip()
        aliases = [name_clean] + entry.get("aliases", [])
        return list(set(aliases))

    @classmethod
    def get_feeds(cls, club_name: str) -> list[str]:
        return cls._find(club_name).get("feeds", [])

    @classmethod
    def get_exclude_keywords(cls, club_name: str) -> list[str]:
        return cls._find(club_name).get("exclude_keywords", [])


# ---------------------------------------------------------------------------
# Schritt 3: Feed-Health-Tracking
# ---------------------------------------------------------------------------

@dataclass
class FeedHealthTracker:
    """
    Protokolliert fehlgeschlagene und erfolgreiche Feeds pro Fetch-Run.
    Ermoeglicht Alerts wenn ein zu hoher Anteil der Quellen nicht erreichbar ist.
    """
    total:  int = 0
    failed: int = 0
    failed_feeds: list[str] = field(default_factory=list)

    def record_ok(self):
        self.total += 1

    def record_fail(self, source: str):
        self.total  += 1
        self.failed += 1
        self.failed_feeds.append(source)

    @property
    def failure_rate(self) -> float:
        return self.failed / self.total if self.total > 0 else 0.0

    @property
    def alert_needed(self) -> bool:
        return self.total >= 3 and self.failure_rate >= FEED_ALERT_THRESHOLD

    def get_report(self) -> str:
        if not self.failed_feeds:
            return ""
        rate_pct = int(self.failure_rate * 100)
        feeds_str = ", ".join(self.failed_feeds[:8])
        return (
            f"\u26a0\ufe0f Feed-Health: {self.failed}/{self.total} Quellen fehlgeschlagen "
            f"({rate_pct}%)\n"
            f"Fehlgeschlagen: {feeds_str}"
        )


# ---------------------------------------------------------------------------
# Datenstrukturen
# ---------------------------------------------------------------------------

@dataclass
class NewsItem:
    title:     str
    url:       str
    source:    str
    published: datetime | None = None
    snippet:   str = ""


# ---------------------------------------------------------------------------
# Hilfsfunktionen
# ---------------------------------------------------------------------------

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


def _is_excluded(text: str, extra_keywords: list[str] | None = None) -> bool:
    low = text.lower()
    if any(kw in low for kw in EXCLUDE_KEYWORDS):
        return True
    if extra_keywords and any(kw in low for kw in extra_keywords):
        return True
    return False


def _is_homepage_url(url: str) -> bool:
    try:
        path = url.rstrip("/").split("/", 3)
        return len(path) < 4 or not path[3]
    except Exception:
        return False


def _is_google_news_redirect(url: str) -> bool:
    return "news.google.com" in url


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


def _extract_google_source(item_elem) -> str:
    source_tag = item_elem.find("source")
    if source_tag is not None and source_tag.text:
        return source_tag.text.strip()
    return "Google News"


def _google_news_url(query: str) -> str:
    return f"https://news.google.com/rss/search?q={quote_plus(query)}&hl=de&gl=DE&ceid=DE:de"


# ---------------------------------------------------------------------------
# Schritt 2: Retry / Exponential Backoff
# ---------------------------------------------------------------------------

async def _fetch_with_retry(
    client: httpx.AsyncClient,
    url: str,
    timeout: float = 10.0,
) -> httpx.Response:
    """
    GET-Request mit Exponential Backoff bei 429/502/503.
    Wirft httpx.HTTPStatusError wenn nach RETRY_MAX Versuchen kein Erfolg.
    Wartezeit: 2^0=1s, 2^1=2s, 2^2=4s (Basiswert RETRY_BASE_SECS).
    """
    last_exc: Exception | None = None
    for attempt in range(RETRY_MAX):
        try:
            r = await client.get(url, timeout=timeout, follow_redirects=True)
            if r.status_code in RETRY_STATUSES:
                wait = RETRY_BASE_SECS ** attempt
                logger.warning(
                    f"HTTP {r.status_code} fuer {url} — Retry {attempt + 1}/{RETRY_MAX} "
                    f"in {wait:.0f}s"
                )
                await asyncio.sleep(wait)
                last_exc = httpx.HTTPStatusError(
                    f"HTTP {r.status_code}", request=r.request, response=r
                )
                continue
            r.raise_for_status()
            return r
        except (httpx.TimeoutException, httpx.ConnectError) as e:
            wait = RETRY_BASE_SECS ** attempt
            logger.warning(f"Verbindungsfehler {url}: {e} — Retry {attempt + 1} in {wait:.0f}s")
            await asyncio.sleep(wait)
            last_exc = e
    raise last_exc or RuntimeError(f"Alle {RETRY_MAX} Versuche fehlgeschlagen: {url}")


# ---------------------------------------------------------------------------
# Fetch-Funktionen
# ---------------------------------------------------------------------------

def _parse_feed(
    xml_text: str,
    source_name: str,
    extra_exclude: list[str] | None = None,
) -> list[NewsItem]:
    items = []
    is_google_feed = source_name == "Google News"
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
            if _is_excluded(title + " " + snippet, extra_exclude):
                continue
            if _is_homepage_url(url):
                continue
            if _is_google_news_redirect(url):
                continue

            actual_source = _extract_google_source(item) if is_google_feed else source_name
            items.append(NewsItem(
                title=title,
                url=url,
                source=actual_source,
                published=pub,
                snippet=_truncate_words(snippet),
            ))
    except ET.ParseError as e:
        logger.warning(f"RSS Parse Fehler ({source_name}): {e}")
    return items


async def _fetch_rss(
    client: httpx.AsyncClient,
    url: str,
    source: str,
    extra_exclude: list[str] | None = None,
    tracker: FeedHealthTracker | None = None,
) -> list[NewsItem]:
    """Holt einen RSS-Feed mit Retry/Backoff. Protokolliert Erfolg/Fehler im Tracker."""
    try:
        r = await _fetch_with_retry(client, url)
        if tracker:
            tracker.record_ok()
        return _parse_feed(r.text, source, extra_exclude)
    except Exception as e:
        logger.warning(f"RSS Feed Fehler ({source}): {e}")
        if tracker:
            tracker.record_fail(source)
        return []


async def _fetch_gnews(
    client: httpx.AsyncClient,
    club_name: str,
    extra_exclude: list[str],
    tracker: FeedHealthTracker | None = None,
) -> list[NewsItem]:
    """GNews API mit Retry/Backoff."""
    name_clean = re.sub(r"\(.*?\)", "", club_name).strip()
    items = []
    gnews_url = (
        f"https://gnews.io/api/v4/search"
        f"?q={quote_plus(name_clean)}"
        f"&lang=de&lang=en"
        f"&from={_gnews_from()}"
        f"&max=10"
        f"&apikey={GNEWS_API_KEY}"
    )
    try:
        r    = await _fetch_with_retry(client, gnews_url)
        data = r.json()
        if tracker:
            tracker.record_ok()
        for art in data.get("articles", []):
            title   = art.get("title", "").strip()
            art_url = art.get("url", "").strip()
            snippet = art.get("description", "").strip()
            source  = art.get("source", {}).get("name", "GNews")
            pub     = _parse_date(art.get("publishedAt", ""))
            if not title or not art_url:
                continue
            if _is_excluded(title + " " + snippet, extra_exclude):
                continue
            if _is_homepage_url(art_url):
                continue
            items.append(NewsItem(
                title=title,
                url=art_url,
                source=source,
                published=pub,
                snippet=_truncate_words(snippet),
            ))
    except Exception as e:
        logger.warning(f"GNews Fehler ({club_name}): {e}")
        if tracker:
            tracker.record_fail("GNews API")
    return items


def _gnews_from() -> str:
    dt = datetime.now(timezone.utc) - timedelta(hours=MAX_AGE_HOURS)
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


# ---------------------------------------------------------------------------
# Haupt-Funktion
# ---------------------------------------------------------------------------

async def fetch_club_news(
    club_name: str,
) -> tuple[list[NewsItem], FeedHealthTracker]:
    """
    Holt News fuer einen Club aus allen Quellen.
    Gibt (items, tracker) zurueck — tracker enthaelt Feed-Health-Infos
    fuer optionale Telegram-Alerts (alert_needed-Property).
    """
    all_items: list[NewsItem] = []
    tracker = FeedHealthTracker()

    keywords      = ClubConfig.get_aliases(club_name)
    extra_exclude = ClubConfig.get_exclude_keywords(club_name)
    club_feeds    = ClubConfig.get_feeds(club_name)

    async with httpx.AsyncClient(headers={"User-Agent": "Mozilla/5.0"}) as client:

        # Layer 1: GNews API
        gnews_items = await _fetch_gnews(client, club_name, extra_exclude, tracker)
        all_items.extend(gnews_items)
        logger.info(f"GNews: {len(gnews_items)} Artikel fuer {club_name}")

        # Layer 2: Google News RSS
        name_clean = re.sub(r"\(.*?\)", "", club_name).strip()
        for query in [name_clean, f"{name_clean} Bundesliga"]:
            items = await _fetch_rss(
                client, _google_news_url(query), "Google News", extra_exclude, tracker
            )
            all_items.extend(items)

        # Layer 3: Allgemeine RSS Feeds
        for source, url in RSS_FEEDS:
            items = await _fetch_rss(client, url, source, extra_exclude, tracker)
            all_items.extend(items)

        # Layer 4: Club-spezifische Feeds aus clubs.json
        for url in club_feeds:
            domain = url.split("/")[2].replace("www.", "")
            items  = await _fetch_rss(client, url, domain, extra_exclude, tracker)
            all_items.extend(items)
            logger.info(f"Club-Feed ({domain}): {len(items)} Artikel")

    # Club-Keyword Filter
    filtered = [
        item for item in all_items
        if any(kw in item.title.lower() or kw in item.snippet.lower() for kw in keywords)
    ]

    # URL-Deduplizierung
    seen_urls:   set[str] = set()
    seen_titles: set[str] = set()
    unique = []
    for item in filtered:
        title_key = re.sub(r"\W+", "", item.title.lower())[:60]
        if item.url not in seen_urls and title_key not in seen_titles:
            seen_urls.add(item.url)
            seen_titles.add(title_key)
            unique.append(item)

    logger.info(
        f"fetch_club_news({club_name}): {len(unique)} Artikel "
        f"(von {len(all_items)} gesamt, "
        f"{tracker.failed}/{tracker.total} Feeds fehlgeschlagen)"
    )
    return unique, tracker
