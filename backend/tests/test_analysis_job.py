import logging
from datetime import UTC, date, datetime
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
from tests.conftest import TestSession

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


def _fake_engine(market) -> SignalEngine:
    qs = QuoteService(market)
    return SignalEngine(
        quotes=qs, fx=FxService(market), history=HistoryService(market),
        fundamentals=FundamentalsService(market), news=NewsService(FakeNews()), provider=market,
    )


async def _make_real_portfolio(db, email, symbol, make_instrument):
    user = User(email=email, password_hash="x")
    db.add(user)
    await db.flush()
    inst = await make_instrument(symbol)
    pf = Portfolio(user_id=user.id, name="P", kind="real", base_currency="USD")
    db.add(pf)
    await db.flush()
    db.add(Position(portfolio_id=pf.id, instrument_id=inst.id,
                    quantity=Decimal("10"), avg_cost=Decimal("100")))
    await db.commit()
    return user, pf, inst


async def test_run_analysis_job_writes_fresh_signal(db_session, make_instrument, monkeypatch):
    from app.services.signals import refresh as refresh_mod

    _, pf, _ = await _make_real_portfolio(db_session, "analyze@test.dev", "AAPL", make_instrument)
    market = FakeMarket(quotes={"AAPL": _q("AAPL", 88, 100)}, earnings={"AAPL": date.today()})
    monkeypatch.setattr(refresh_mod, "get_engine", lambda: _fake_engine(market))

    await refresh_mod.run_analysis_job(session_factory=TestSession)

    rows = (await db_session.execute(
        select(Signal).where(Signal.portfolio_id == pf.id))).scalars().all()
    assert rows
    assert any(s.kind == "price_move_day" for s in rows)


async def test_run_analysis_job_ignores_watchlist_portfolios(
    db_session, make_instrument, monkeypatch,
):
    from app.services.signals import refresh as refresh_mod

    user = User(email="watchlist@test.dev", password_hash="x")
    db_session.add(user)
    await db_session.flush()
    inst = await make_instrument("AAPL")
    pf = Portfolio(user_id=user.id, name="W", kind="watchlist", base_currency="USD")
    db_session.add(pf)
    await db_session.flush()
    db_session.add(Position(portfolio_id=pf.id, instrument_id=inst.id,
                    quantity=Decimal("10"), avg_cost=Decimal("100")))
    await db_session.commit()

    market = FakeMarket(quotes={"AAPL": _q("AAPL", 88, 100)})
    monkeypatch.setattr(refresh_mod, "get_engine", lambda: _fake_engine(market))

    await refresh_mod.run_analysis_job(session_factory=TestSession)

    rows = (await db_session.execute(
        select(Signal).where(Signal.portfolio_id == pf.id))).scalars().all()
    assert rows == []


async def test_one_portfolio_failure_does_not_block_sibling_portfolio(
    db_session, make_instrument, monkeypatch, caplog,
):
    """Same user, two real portfolios; one analyze() raise must not stop the other
    (fresh per-user session, but the two portfolios share it -- a failed analyze
    must not leave the session unusable for the sibling portfolio)."""
    from app.services.signals import refresh as refresh_mod

    user = User(email="twopf@test.dev", password_hash="x")
    db_session.add(user)
    await db_session.flush()
    inst_a = await make_instrument("AAPL")
    inst_b = await make_instrument("MSFT")
    pf_bad = Portfolio(user_id=user.id, name="Bad", kind="real", base_currency="USD")
    pf_good = Portfolio(user_id=user.id, name="Good", kind="real", base_currency="USD")
    db_session.add_all([pf_bad, pf_good])
    await db_session.flush()
    db_session.add(Position(portfolio_id=pf_bad.id, instrument_id=inst_a.id,
                    quantity=Decimal("1"), avg_cost=Decimal("1")))
    db_session.add(Position(portfolio_id=pf_good.id, instrument_id=inst_b.id,
                    quantity=Decimal("1"), avg_cost=Decimal("1")))
    await db_session.commit()

    market = FakeMarket(quotes={"AAPL": _q("AAPL", 88, 100), "MSFT": _q("MSFT", 88, 100)},
                         earnings={"MSFT": date.today()})
    engine = _fake_engine(market)
    orig_analyze = engine.analyze

    async def flaky_analyze(db, pf):
        if pf.id == pf_bad.id:
            raise RuntimeError("simulated feed failure")
        return await orig_analyze(db, pf)

    monkeypatch.setattr(engine, "analyze", flaky_analyze)
    monkeypatch.setattr(refresh_mod, "get_engine", lambda: engine)

    with caplog.at_level(logging.INFO, logger="app.services.signals.refresh"):
        await refresh_mod.run_analysis_job(session_factory=TestSession)

    rows_bad = (await db_session.execute(
        select(Signal).where(Signal.portfolio_id == pf_bad.id))).scalars().all()
    rows_good = (await db_session.execute(
        select(Signal).where(Signal.portfolio_id == pf_good.id))).scalars().all()
    assert rows_bad == []
    assert rows_good
    assert caplog.records  # the failure was logged, not raised


async def test_one_user_failure_does_not_block_other_users(
    db_session, make_instrument, monkeypatch, caplog,
):
    from app.services.signals import refresh as refresh_mod

    _, pf_bad, _ = await _make_real_portfolio(
        db_session, "failing@test.dev", "AAPL", make_instrument)
    _, pf_good, _ = await _make_real_portfolio(
        db_session, "healthy@test.dev", "MSFT", make_instrument)

    market = FakeMarket(quotes={"AAPL": _q("AAPL", 88, 100), "MSFT": _q("MSFT", 88, 100)},
                         earnings={"MSFT": date.today()})
    engine = _fake_engine(market)
    orig_analyze = engine.analyze

    async def flaky_analyze(db, pf):
        if pf.id == pf_bad.id:
            raise RuntimeError("simulated feed failure")
        return await orig_analyze(db, pf)

    monkeypatch.setattr(engine, "analyze", flaky_analyze)
    monkeypatch.setattr(refresh_mod, "get_engine", lambda: engine)

    with caplog.at_level(logging.INFO, logger="app.services.signals.refresh"):
        await refresh_mod.run_analysis_job(session_factory=TestSession)

    rows_bad = (await db_session.execute(
        select(Signal).where(Signal.portfolio_id == pf_bad.id))).scalars().all()
    rows_good = (await db_session.execute(
        select(Signal).where(Signal.portfolio_id == pf_good.id))).scalars().all()
    assert rows_bad == []
    assert rows_good
    assert caplog.records


async def test_analysis_catch_up_swallows_exceptions(monkeypatch, caplog):
    from app.services.signals import refresh as refresh_mod

    async def boom(session_factory=None):
        raise RuntimeError("simulated catch-up boom")

    monkeypatch.setattr(refresh_mod, "run_analysis_job", boom)

    with caplog.at_level(logging.INFO, logger="app.services.signals.refresh"):
        await refresh_mod.analysis_catch_up()  # must not raise

    assert caplog.records
