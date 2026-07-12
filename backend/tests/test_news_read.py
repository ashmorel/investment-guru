from datetime import datetime

from app.models import NewsItem
from app.services.market_data.news_read import dedupe, norm_title, rank_groups


def _n(title, when):
    return NewsItem(instrument_id=1, title=title, source="Yahoo", url=title,
                    published_at=datetime(2026, 1, when), fetched_at=datetime(2026, 1, when))


def test_norm_title_folds_case_ws_punct():
    assert norm_title("Apple  Beats!  Estimates.") == norm_title("apple beats estimates")


def test_dedupe_keeps_earliest_returns_newest_first():
    items = [_n("Apple beats estimates", 3), _n("APPLE  beats estimates!", 1),
             _n("Rival launches phone", 2)]
    out = dedupe(items)
    # duplicate collapsed to one (earliest published kept), newest-first order
    assert len(out) == 2
    assert out[0].title == "Rival launches phone"      # day 2 newest of the survivors
    assert out[1].published_at.day == 1                # the kept dup is the earliest (day 1)


def test_rank_groups_by_count_then_recency():
    groups = [
        {"symbol": "A", "latest_published_at": "2026-01-05", "items": [1, 2]},
        {"symbol": "B", "latest_published_at": "2026-01-09", "items": [1, 2]},
        {"symbol": "C", "latest_published_at": "2026-01-01", "items": [1]},
    ]
    ranked = [g["symbol"] for g in rank_groups(groups)]
    assert ranked == ["B", "A", "C"]   # A,B tie on count(2) -> B newer first; C fewer
