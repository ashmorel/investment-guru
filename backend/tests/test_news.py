from pathlib import Path

from app.services.market_data.news import parse_rss

FIXTURES = Path(__file__).parent / "fixtures"


def test_parse_rss_extracts_items():
    data = (FIXTURES / "yahoo_news_aapl.xml").read_bytes()
    items = parse_rss(data, source="Yahoo")
    assert len(items) == 2
    assert items[0].title == "Apple unveils new product line"
    assert items[0].url == "https://example.com/aapl-1"
    assert items[0].source == "Yahoo"
    assert items[0].published_at is not None


def test_parse_rss_empty_on_garbage():
    assert parse_rss(b"not xml at all", source="Yahoo") == []
