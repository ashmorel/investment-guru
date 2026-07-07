from datetime import UTC, datetime
from decimal import Decimal

import pytest

from app.models import FxRate, Portfolio, Position, User
from app.services.market_data.base import Quote
from app.services.market_data.quotes import QuoteService
from app.services.valuation import FxService, normalise, value_portfolio

pytestmark = pytest.mark.asyncio(loop_scope="session")


async def test_normalise_pence():
    amount, ccy = normalise(Decimal("702.30"), "GBp")
    assert (amount, ccy) == (Decimal("7.023"), "GBP")
    assert normalise(Decimal("5"), "USD") == (Decimal("5"), "USD")


class FakeFxProvider:
    async def get_fx_rate(self, base, quote):
        return {"USDGBP": Decimal("0.8"), "HKDGBP": Decimal("0.1")}[f"{base}{quote}"]

    async def get_quotes(self, symbols):
        return {}

    async def lookup(self, symbol):
        return None


class FakeQuoteProvider:
    def __init__(self, quotes):
        self._q = quotes

    async def get_quotes(self, symbols):
        return {s: q for s, q in self._q.items() if s in symbols}

    async def get_fx_rate(self, base, quote):
        raise NotImplementedError

    async def lookup(self, symbol):
        return None


def _quote(symbol, price, currency, prev):
    return Quote(symbol=symbol, price=Decimal(price), currency=currency,
                 previous_close=Decimal(prev), as_of=datetime.now(UTC))


async def test_fx_service_caches_daily(db_session):
    fx = FxService(FakeFxProvider())
    rate = await fx.get_rate(db_session, "USD", "GBP")
    assert rate == Decimal("0.8")
    row_count = len((await db_session.execute(
        __import__("sqlalchemy").select(FxRate)
    )).scalars().all())
    assert row_count == 1
    # same-currency shortcut
    assert await fx.get_rate(db_session, "GBP", "GBP") == Decimal("1")


async def test_value_portfolio_mixed_currencies(db_session, make_instrument):
    user = User(email="v@test.dev", password_hash="x")
    db_session.add(user)
    await db_session.flush()
    aapl = await make_instrument("AAPL")  # USD
    hsba = await make_instrument("HSBA.L", market="UK", currency="GBp", exchange="LSE")
    pf = Portfolio(user_id=user.id, name="Mix", kind="real", base_currency="GBP")
    db_session.add(pf)
    await db_session.flush()
    db_session.add_all([
        Position(portfolio_id=pf.id, instrument_id=aapl.id,
                 quantity=Decimal("10"), avg_cost=Decimal("100")),   # USD cost
        Position(portfolio_id=pf.id, instrument_id=hsba.id,
                 quantity=Decimal("200"), avg_cost=Decimal("650")),  # GBp cost
    ])
    await db_session.commit()
    await db_session.refresh(pf, ["positions"])

    quotes = QuoteService(FakeQuoteProvider({
        "AAPL": _quote("AAPL", "150", "USD", "148"),
        "HSBA.L": _quote("HSBA.L", "700", "GBp", "690"),
    }))
    summary = await value_portfolio(db_session, pf, quotes, FxService(FakeFxProvider()))

    # AAPL: 10 * 150 USD * 0.8 = 1200 GBP; HSBA: 200 * 7.00 GBP = 1400 GBP
    assert summary.total_value == Decimal("2600.00")
    # cost: 10*100*0.8 + 200*6.50 = 800 + 1300 = 2100
    assert summary.total_cost == Decimal("2100.00")
    assert summary.total_pnl == Decimal("500.00")
    assert summary.currency_exposure == {"USD": Decimal("1200.00"), "GBP": Decimal("1400.00")}
    assert summary.priced_positions == 2


async def test_missing_quote_degrades(db_session, make_instrument):
    user = User(email="v2@test.dev", password_hash="x")
    db_session.add(user)
    await db_session.flush()
    inst = await make_instrument("MYST")
    pf = Portfolio(user_id=user.id, name="M", kind="real", base_currency="GBP")
    db_session.add(pf)
    await db_session.flush()
    db_session.add(Position(portfolio_id=pf.id, instrument_id=inst.id,
                            quantity=Decimal("5"), avg_cost=Decimal("10")))
    await db_session.commit()
    await db_session.refresh(pf, ["positions"])

    summary = await value_portfolio(
        db_session, pf, QuoteService(FakeQuoteProvider({})), FxService(FakeFxProvider())
    )
    assert summary.unpriced_positions == 1
    assert summary.positions[0].market_value_base is None
