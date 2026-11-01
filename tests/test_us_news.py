from datetime import datetime, timezone

import stock_etf.news as news


def test_us_market_news_is_category_balanced_and_recent(monkeypatch):
    now = datetime.now(timezone.utc).isoformat()

    def fake_feed(url, provider, symbol, failures):
        return [
            {
                "title": f"{symbol} headline {index}",
                "source": provider,
                "published": now,
                "summary": "market update",
                "link": f"https://example.com/{symbol.lower()}/{index}",
                "symbol": symbol,
                "provider": provider,
            }
            for index in range(8)
        ]

    monkeypatch.setattr(news, "_feed", fake_feed)
    rows, failures = news.fetch_us_market_news(limit=16, max_age_days=14)

    assert failures == []
    assert len(rows) == 16
    categories = [row["category"] for row in rows]
    assert len(set(categories)) == 8
    assert all(categories.count(category) == 2 for category in set(categories))
    assert all(row["news_freshness"] == "DATED" for row in rows)
