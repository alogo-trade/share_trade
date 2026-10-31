from datetime import datetime, timedelta, timezone

from stock_etf.news import _dedupe


def test_news_deduplicates_tracking_urls_and_keeps_newest():
    now = datetime(2026, 10, 31, 12, tzinfo=timezone.utc)
    rows = [
        {"title": "Company earnings beat", "link": "https://news.example/story?utm_source=feed", "published": "2026-10-30T10:00:00Z"},
        {"title": "Company earnings beat duplicate", "link": "https://news.example/story?utm_campaign=other", "published": "2026-10-30T10:00:00Z"},
        {"title": "New regulation announced", "link": "https://news.example/regulation", "published": "2026-10-31T09:00:00Z"},
    ]
    result = _dedupe(rows, 10, max_age_days=30, now=now)
    assert len(result) == 2
    assert result[0]["title"] == "New regulation announced"
    assert result[0]["news_freshness"] == "DATED"


def test_news_excludes_articles_older_than_max_age():
    now = datetime(2026, 10, 31, 12, tzinfo=timezone.utc)
    rows = [
        {"title": "Old story", "link": "https://news.example/old", "published": "2026-09-01T10:00:00Z"},
        {"title": "Recent story", "link": "https://news.example/new", "published": "2026-10-29T10:00:00Z"},
    ]
    result = _dedupe(rows, 10, max_age_days=30, now=now)
    assert [row["title"] for row in result] == ["Recent story"]


def test_news_keeps_undated_articles_but_marks_freshness_unknown():
    now = datetime(2026, 10, 31, 12, tzinfo=timezone.utc)
    result = _dedupe(
        [{"title": "No publication date", "link": "https://news.example/undated"}],
        10, max_age_days=30, now=now,
    )
    assert result[0]["news_freshness"] == "UNKNOWN_DATE"
    assert result[0]["published_at_utc"] is None
