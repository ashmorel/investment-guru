from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.models import Portfolio, Position, Signal, User
from app.services.market_data.base import Quote
from app.services.market_data.fundamentals import FundamentalsService
from app.services.market_data.history import HistoryService
from app.services.market_data.news import NewsService
from app.services.market_data.quotes import QuoteService
from app.services.signals.engine import SignalEngine
from app.services.valuation import FxService

pytestmark = pytest.mark.asyncio(loop_scope="session")


class FakeMarket:
    def __init__(self, quotes=None, earnings=None):
        self._quotes = quotes or {}
        self._earnings = earnings or {}

    async def get_quotes(self, symbols):
        return {s: self._quotes[s] for s in symbols if s in self._quotes}

    async def get_fx_rate(self, base, quote):
        return Decimal("1")

    async def lookup(self, symbol):
        return None

    async def get_history(self, symbol, days=400):
        return []

    async def get_earnings_date(self, symbol):
        return self._earnings.get(symbol)


class FakeNews:
    async def get_news(self, symbol):
        return []


def _q(sym, price, prev):
    return Quote(symbol=sym, price=Decimal(str(price)), currency="USD",
                 previous_close=Decimal(str(prev)), as_of=datetime.now(UTC))


async def _make_pf(db, make_instrument):
    user = User(email="e@test.dev", password_hash="x")
    db.add(user)
    await db.flush()
    inst = await make_instrument("AAPL")
    pf = Portfolio(user_id=user.id, name="P", kind="real", base_currency="USD")
    db.add(pf)
    await db.flush()
    db.add(Position(portfolio_id=pf.id, instrument_id=inst.id,
                    quantity=Decimal("10"), avg_cost=Decimal("100")))
    await db.commit()
    await db.refresh(pf, ["positions"])
    return pf, inst


def _engine(market, news=None):
    news = news or FakeNews()
    qs = QuoteService(market)
    return SignalEngine(
        quotes=qs, fx=FxService(market), history=HistoryService(market),
        fundamentals=FundamentalsService(market), news=NewsService(news), provider=market,
    )


async def test_analyze_produces_and_replaces_snapshot(db_session, make_instrument):
    pf, inst = await _make_pf(db_session, make_instrument)
    market = FakeMarket(quotes={"AAPL": _q("AAPL", 88, 100)},  # -12% day move
                        earnings={"AAPL": date.today()})
    result = await _engine(market).analyze(db_session, pf)
    await db_session.commit()
    kinds = {s.kind for s in result.signals}
    assert "price_move_day" in kinds
    assert "earnings_upcoming" in kinds
    stored = (await db_session.execute(
        select(Signal).where(Signal.portfolio_id == pf.id)
    )).scalars().all()
    assert len(stored) == len(result.signals) > 0

    # re-analyze with calm quote → snapshot replaced (old price_move_day gone)
    calm = FakeMarket(quotes={"AAPL": _q("AAPL", 100, 100)}, earnings={})
    result2 = await _engine(calm).analyze(db_session, pf)
    await db_session.commit()
    assert "price_move_day" not in {s.kind for s in result2.signals}
    stored2 = (await db_session.execute(
        select(Signal).where(Signal.portfolio_id == pf.id)
    )).scalars().all()
    assert len(stored2) == len(result2.signals)


async def test_analyze_ignores_persisted_nan_bars(db_session, make_instrument):
    """A NaN close already persisted in price_bars (e.g. from a bad historical write
    before Fix A existed) must be filtered by the engine before rules see it, so
    price_move_week never raises decimal.InvalidOperation. Postgres Numeric happily
    stores NaN even though it isn't a real number, which is exactly how the bug
    reached production — so this test writes one directly, bypassing Fix A."""
    from app.models import PriceBar

    pf, inst = await _make_pf(db_session, make_instrument)
    today = date.today()
    # 6 bars so period_return(bars, 5) has enough history; the most recent bar has a
    # NaN close, simulating a bad row that slipped into the DB pre-Fix-A.
    closes = [150, 151, 152, 153, 154, float("nan")]
    for i, close in enumerate(closes):
        db_session.add(PriceBar(
            instrument_id=inst.id, date=today - timedelta(days=5 - i),
            open=Decimal("150"), high=Decimal("155"), low=Decimal("149"),
            close=Decimal(str(close)), volume=1000,
        ))
    await db_session.commit()

    market = FakeMarket(quotes={"AAPL": _q("AAPL", 100, 100)})
    result = await _engine(market).analyze(db_session, pf)
    await db_session.commit()
    # must not raise, and the NaN bar must not produce a bogus signal
    assert "price_move_week" not in {s.kind for s in result.signals}


async def test_provider_failure_is_isolated(db_session, make_instrument):
    pf, inst = await _make_pf(db_session, make_instrument)

    class Boom(FakeMarket):
        async def get_history(self, symbol, days=400):
            raise RuntimeError("down")

        async def get_earnings_date(self, symbol):
            raise RuntimeError("down")

    market = Boom(quotes={"AAPL": _q("AAPL", 88, 100)})
    result = await _engine(market).analyze(db_session, pf)
    await db_session.commit()
    # day-move (from quote) still computed; history/earnings reported unavailable
    assert "price_move_day" in {s.kind for s in result.signals}
    assert "history" in result.unavailable_inputs
    assert "earnings" in result.unavailable_inputs
