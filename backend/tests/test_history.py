import json
from datetime import date
from decimal import Decimal
from pathlib import Path

from app.services.market_data.history import avg_volume, fifty_two_week_range, period_return
from app.services.market_data.yahoo import parse_history

FIXTURES = Path(__file__).parent / "fixtures"


def _bars():
    rows = json.loads((FIXTURES / "yahoo_history_aapl.json").read_text())
    return parse_history(rows)


def test_parse_history_ascending_decimal():
    bars = _bars()
    assert len(bars) == 6
    assert bars[0].date == date(2026, 6, 30)
    assert bars[-1].close == Decimal("161.0")
    assert bars[-1].volume == 52000000


def test_parse_history_skips_null_close():
    bars = parse_history([{"date": "2026-07-07", "open": 1, "high": 1, "low": 1,
                           "close": None, "volume": 1}])
    assert bars == []


def test_period_return():
    bars = _bars()
    # from close 151.0 (index -6) to 161.0 (last) over 5 trading days
    r = period_return(bars, 5)
    assert r == Decimal("6.62")  # (161-151)/151*100 rounded 2dp


def test_fifty_two_week_range():
    bars = _bars()
    low, high = fifty_two_week_range(bars)
    assert (low, high) == (Decimal("149.0"), Decimal("162.0"))


def test_avg_volume():
    bars = _bars()
    # avg of last 3 volumes: 61000000, 47000000, 52000000 -> mean 53333333.33
    assert avg_volume(bars, 3) == Decimal("53333333.33")
