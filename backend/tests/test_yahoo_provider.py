import json
from decimal import Decimal
from pathlib import Path

from app.services.market_data.base import infer_market
from app.services.market_data.yahoo import parse_instrument_info, parse_quote

FIXTURES = Path(__file__).parent / "fixtures"


def _load(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text())


def test_parse_us_quote():
    q = parse_quote("AAPL", _load("yahoo_quote_aapl.json"))
    assert q.price == Decimal("231.55")
    assert q.currency == "USD"
    assert q.previous_close == Decimal("229.1")


def test_parse_uk_quote_keeps_pence_currency():
    q = parse_quote("HSBA.L", _load("yahoo_quote_hsba.json"))
    assert q.currency == "GBp"  # pence preserved; valuation layer converts


def test_parse_instrument_info_infers_market():
    info = parse_instrument_info("HSBA.L", _load("yahoo_quote_hsba.json"))
    assert info.market == "UK"
    assert info.sector == "Financial Services"


def test_parse_quote_missing_price_returns_none():
    assert parse_quote("AAPL", {"currency": "USD"}) is None


def test_parse_quote_nan_price_returns_none():
    # yfinance can return a NaN regularMarketPrice; treat it as no quote so
    # downstream Decimal arithmetic never hits decimal.InvalidOperation.
    assert parse_quote("AAPL", {"regularMarketPrice": float("nan"), "currency": "USD"}) is None


def test_parse_quote_nan_previous_close_drops_prev_keeps_quote():
    # A NaN previous_close must not crash: keep the (finite) price, drop prev.
    q = parse_quote(
        "AAPL",
        {
            "regularMarketPrice": 231.55,
            "regularMarketPreviousClose": float("nan"),
            "currency": "USD",
        },
    )
    assert q is not None
    assert q.price == Decimal("231.55")
    assert q.previous_close is None


def test_infer_market():
    assert infer_market("AAPL") == "US"
    assert infer_market("HSBA.L") == "UK"
    assert infer_market("0700.HK") == "HK"
