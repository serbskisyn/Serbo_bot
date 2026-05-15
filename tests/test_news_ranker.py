from datetime import datetime
import pytest
from app.services.news_ranker import rank_news, _jaccard, _normalize
from app.services.news_fetcher import NewsItem


def _item(title, url="http://example.de", published=None, snippet="", source="TestSource"):
    return NewsItem(title=title, url=url, source=source, published=published, snippet=snippet)


# ── _normalize ────────────────────────────────────────────────────────────────
def test_normalize_returns_set():
    assert isinstance(_normalize("Hallo Welt Test"), set)


def test_normalize_filters_tokens_shorter_than_3_chars():
    result = _normalize("FC Bayern")
    assert "fc" not in result


def test_normalize_removes_stopwords():
    assert _normalize("der die das") == set()


def test_normalize_empty_string():
    assert _normalize("") == set()


# ── _jaccard ──────────────────────────────────────────────────────────────────
def test_jaccard_identical_sets():
    s = {"transfer", "ablöse", "spieler"}
    assert _jaccard(s, s) == 1.0


def test_jaccard_disjoint_sets():
    assert _jaccard({"abc"}, {"xyz"}) == 0.0


def test_jaccard_empty_set_returns_zero():
    assert _jaccard(set(), {"abc"}) == 0.0
    assert _jaccard({"abc"}, set()) == 0.0


def test_jaccard_partial_overlap():
    a = {"transfer", "spieler", "ablöse"}
    b = {"transfer", "spieler", "vertrag"}
    result = _jaccard(a, b)
    assert 0.0 < result < 1.0


# ── rank_news ─────────────────────────────────────────────────────────────────
def test_rank_news_empty_input():
    assert rank_news([]) == []


def test_rank_news_single_item():
    result = rank_news([_item("Transfernews Spieler wechselt nach Hamburg")])
    assert len(result) == 1
    assert result[0].score == 1


def test_rank_news_published_none_no_crash():
    result = rank_news([_item("Transfer Spieler Wechsel Ablöse", published=None)])
    assert result[0].published == ""


def test_rank_news_published_date_is_formatted():
    dt = datetime(2024, 5, 1, 10, 0)
    result = rank_news([_item("Transfer Spieler Wechsel Ablöse", published=dt)])
    assert "01.05.2024" in result[0].published


def test_rank_news_deduplicates_same_url():
    url = "http://example.de/news/1"
    items = [
        _item("Transfer Spieler Wechsel Ablöse", url=url, source="Sport1"),
        _item("Transfer Spieler Wechsel Ablöse", url=url, source="Kicker"),
    ]
    result = rank_news(items)
    assert len(result) == 1
    assert len(result[0].urls) == 1


def test_rank_news_clusters_similar_titles():
    items = [
        _item("Haaland wechselt Transfer Real Madrid bestätigt", url="http://a.de"),
        _item("Haaland Transfer Real Madrid fix ablöse rekord", url="http://b.de"),
    ]
    result = rank_news(items)
    assert result[0].score >= 2


def test_rank_news_distinct_titles_stay_separate():
    items = [
        _item("Schalke steigt ab Relegation verloren", url="http://a.de"),
        _item("Bayern gewinnt Meisterschaft Titel Rekord", url="http://b.de"),
    ]
    result = rank_news(items)
    assert len(result) == 2


def test_rank_news_respects_top_n():
    items = [_item(f"Spieler {i} wechselt Transfer Ablöse", url=f"http://x.de/{i}") for i in range(20)]
    result = rank_news(items, top_n=5)
    assert len(result) <= 5
