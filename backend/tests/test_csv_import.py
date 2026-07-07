from decimal import Decimal
from pathlib import Path

import pytest

from app.services.csv_import import CsvFormatError, parse_yahoo_csv

FIXTURE = Path(__file__).parent / "fixtures" / "yahoo_portfolio_export.csv"


def test_parse_yahoo_export():
    rows = parse_yahoo_csv(FIXTURE.read_bytes())
    assert len(rows) == 3  # $$CASH skipped
    aapl = rows[0]
    assert (aapl.symbol, aapl.quantity, aapl.purchase_price) == (
        "AAPL", Decimal("10"), Decimal("150.25")
    )
    assert aapl.comment == "core holding"
    tencent = rows[2]
    assert tencent.symbol == "0700.HK"
    assert tencent.quantity is None  # watchlist-style row


def test_missing_symbol_column_raises():
    with pytest.raises(CsvFormatError):
        parse_yahoo_csv(b"Name,Price\nApple,100\n")


def test_non_finite_decimals_yield_none():
    csv_bytes = b"Symbol,Quantity,Purchase Price,Comment\nAAPL,inf,NaN,bad row\n"
    rows = parse_yahoo_csv(csv_bytes)
    assert len(rows) == 1
    row = rows[0]
    assert row.quantity is None
    assert row.purchase_price is None


def test_symbol_normalised_to_uppercase():
    csv_bytes = b"Symbol,Quantity,Purchase Price,Comment\n aapl ,10,150.25,\n"
    rows = parse_yahoo_csv(csv_bytes)
    assert rows[0].symbol == "AAPL"
