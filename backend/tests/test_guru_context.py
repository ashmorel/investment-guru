import json
from datetime import UTC, datetime
from decimal import Decimal

import pytest

from app.models import InvestorProfile, Portfolio, Position, Signal, User
from app.services.guru import context
from app.services.guru.context import build_context
from app.services.market_data.base import Quote
from app.services.market_data.quotes import QuoteService
from app.services.valuation import FxService

pytestmark = pytest.mark.asyncio(loop_scope="session")


class FakeFxProvider:
    async def get_fx_rate(self, base, quote):
        return {"USDGBP": Decimal("0.8")}[f"{base}{quote}"]

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


async def _make_user(db_session, email):
    user = User(email=email, password_hash="x")
    db_session.add(user)
    await db_session.flush()
    return user


async def _make_portfolio(db_session, user, name="P", base_currency="GBP"):
    pf = Portfolio(user_id=user.id, name=name, kind="real", base_currency=base_currency)
    db_session.add(pf)
    await db_session.flush()
    return pf


async def test_context_includes_profile_defaults_when_none(db_session):
    user = await _make_user(db_session, "ctx1@test.dev")
    pf = await _make_portfolio(db_session, user, "Empty")
    await db_session.commit()
    await db_session.refresh(pf, ["positions"])

    qs = QuoteService(FakeQuoteProvider({}))
    fx = FxService(FakeFxProvider())

    ctx = await build_context(
        db_session, user, quote_service=qs, fx=fx, portfolios=[pf], profile=None
    )

    assert ctx["profile"] == {
        "risk_appetite": "balanced",
        "horizon": "medium",
        "sector_interests": [],
        "free_text": "",
    }
    assert ctx["context_truncated"] is False
    assert ctx["portfolios"][0]["name"] == "Empty"
    assert ctx["signals"] == []
    json.dumps(ctx)  # must never raise


async def test_context_positions_and_signals(db_session, make_instrument):
    user = await _make_user(db_session, "ctx2@test.dev")
    aapl = await make_instrument("AAPL")
    watch_inst = await make_instrument("WATCH")
    pf = await _make_portfolio(db_session, user, "Main")
    db_session.add_all([
        Position(portfolio_id=pf.id, instrument_id=aapl.id,
                 quantity=Decimal("10"), avg_cost=Decimal("100")),
        Position(portfolio_id=pf.id, instrument_id=watch_inst.id,
                 quantity=None, avg_cost=None),
    ])
    now = datetime.now(UTC).replace(tzinfo=None)
    db_session.add(Signal(
        portfolio_id=pf.id, instrument_id=aapl.id, kind="price_move_day",
        severity="watch", title="AAPL moved", detail="AAPL down today",
        data={}, computed_at=now,
    ))
    profile = InvestorProfile(
        user_id=user.id, risk_appetite="aggressive", horizon="long",
        sector_interests=["tech"], free_text="likes AI",
    )
    db_session.add(profile)
    await db_session.commit()
    await db_session.refresh(pf, ["positions"])

    qs = QuoteService(FakeQuoteProvider({
        "AAPL": _quote("AAPL", "150", "USD", "148"),
    }))
    fx = FxService(FakeFxProvider())

    ctx = await build_context(
        db_session, user, quote_service=qs, fx=fx, portfolios=[pf], profile=profile
    )

    assert ctx["profile"]["risk_appetite"] == "aggressive"
    positions = ctx["portfolios"][0]["positions"]
    by_symbol = {p["symbol"]: p for p in positions}
    assert by_symbol["AAPL"]["watchlist_entry"] is False
    assert Decimal(by_symbol["AAPL"]["quantity"]) == Decimal("10")
    assert by_symbol["AAPL"]["currency"] == "USD"
    assert by_symbol["WATCH"]["watchlist_entry"] is True
    assert by_symbol["WATCH"]["quantity"] is None

    assert len(ctx["signals"]) == 1
    assert ctx["signals"][0] == {
        "portfolio": "Main", "symbol": "AAPL", "kind": "price_move_day",
        "severity": "watch", "title": "AAPL moved", "detail": "AAPL down today",
    }

    json.dumps(ctx)  # Decimal fields must already be rendered as str


async def test_context_truncates_largest_value_first(db_session, make_instrument, monkeypatch):
    monkeypatch.setattr(context, "MAX_CONTEXT_CHARS", 700)
    user = await _make_user(db_session, "ctx3@test.dev")
    small = await make_instrument("SMALL")
    mid = await make_instrument("MID")
    big = await make_instrument("BIG")
    pf = await _make_portfolio(db_session, user, "Big")
    db_session.add_all([
        Position(portfolio_id=pf.id, instrument_id=small.id,
                 quantity=Decimal("1"), avg_cost=Decimal("1")),
        Position(portfolio_id=pf.id, instrument_id=mid.id,
                 quantity=Decimal("1"), avg_cost=Decimal("1")),
        Position(portfolio_id=pf.id, instrument_id=big.id,
                 quantity=Decimal("1"), avg_cost=Decimal("1")),
    ])
    await db_session.commit()
    await db_session.refresh(pf, ["positions"])

    qs = QuoteService(FakeQuoteProvider({
        "SMALL": _quote("SMALL", "10", "GBP", "10"),
        "MID": _quote("MID", "1000", "GBP", "1000"),
        "BIG": _quote("BIG", "100000", "GBP", "100000"),
    }))
    fx = FxService(FakeFxProvider())

    ctx = await build_context(
        db_session, user, quote_service=qs, fx=fx, portfolios=[pf], profile=None
    )

    assert ctx["context_truncated"] is True
    kept_symbols = {p["symbol"] for p in ctx["portfolios"][0]["positions"]}
    assert "BIG" in kept_symbols
    assert "SMALL" not in kept_symbols
    json.dumps(ctx)
