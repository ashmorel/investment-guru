import types as _types
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest

from app.models import (
    GroupAssignment,
    GroupSnapshot,
    HoldingGroup,
    InvestorProfile,
    NewsItem,
    Portfolio,
    Position,
    Signal,
    User,
)
from app.services.groups.rotation_context import build_rotation_context
from app.services.valuation import FxService

pytestmark = pytest.mark.asyncio(loop_scope="session")


class FakeFxProvider:
    async def get_fx_rate(self, base, quote):
        raise NotImplementedError

    async def get_quotes(self, symbols):
        return {}

    async def lookup(self, symbol):
        return None


def _stub_fx(monkeypatch, rates: dict):
    """base_currency -> rate into GBP (or an Exception instance to simulate
    an FX-provider failure). Mirrors tests/test_group_exposure.py::_stub_fx."""

    async def fake(self, db, base, quote):
        assert quote == "GBP"
        r = rates.get(base)
        if isinstance(r, Exception):
            raise r
        if r is None:
            raise LookupError(base)
        return r

    monkeypatch.setattr(FxService, "get_rate", fake)


def _stub_valuation(monkeypatch, prices: dict):
    """Stub value_portfolio so exposure is deterministic (no live quotes).
    Mirrors tests/test_group_exposure.py::_stub_valuation exactly — patches
    the name imported INTO the exposure module."""
    import app.services.groups.exposure as expo

    async def fake(db, portfolio, quote_service, fx):
        positions = [
            _types.SimpleNamespace(
                symbol=p.instrument.symbol,
                market_value_base=prices.get(p.instrument.symbol),
                day_change_base=(None if prices.get(p.instrument.symbol) is None
                                 else Decimal("1")),
            )
            for p in portfolio.positions
        ]
        return _types.SimpleNamespace(positions=positions)

    monkeypatch.setattr(expo, "value_portfolio", fake)


async def _make_user(db_session, email):
    user = User(email=email, password_hash="x")
    db_session.add(user)
    await db_session.flush()
    return user


async def _seed_holding(db_session, user, symbol, portfolio, market="US", currency="USD"):
    from app.models import Instrument

    inst = Instrument(symbol=symbol, name=f"{symbol} Co", exchange="NMS",
                      market=market, currency=currency)
    db_session.add(inst)
    await db_session.flush()
    db_session.add(Position(portfolio_id=portfolio.id, instrument_id=inst.id,
                            quantity=Decimal("1"), avg_cost=Decimal("1")))
    await db_session.flush()
    return inst


def _services():
    return None, FxService(FakeFxProvider())


async def test_context_has_per_group_weight_and_degrades_without_history(
        db_session, monkeypatch):
    user = await _make_user(db_session, "rot1@test.dev")
    pf_gbp = Portfolio(user_id=user.id, name="UK", kind="real", base_currency="GBP")
    pf_hkd = Portfolio(user_id=user.id, name="HK", kind="real", base_currency="HKD")
    db_session.add_all([pf_gbp, pf_hkd])
    await db_session.flush()

    aapl = await _seed_holding(db_session, user, "AAPL", pf_gbp)
    tencent = await _seed_holding(db_session, user, "0700", pf_hkd, market="HK", currency="HKD")

    group = HoldingGroup(user_id=user.id, name="Big Tech")
    db_session.add(group)
    await db_session.flush()
    db_session.add(GroupAssignment(user_id=user.id, instrument_id=aapl.id, group_id=group.id))
    await db_session.commit()

    _stub_valuation(monkeypatch, {"AAPL": Decimal("70"), "0700": Decimal("30")})
    _stub_fx(monkeypatch, {"HKD": Decimal("0.1")})
    quote_service, fx = _services()

    ctx = await build_rotation_context(db_session, user, quote_service, fx)

    names = {g["name"] for g in ctx["groups"]}
    assert "Big Tech" in names
    assert "Ungrouped" in names
    bt = next(g for g in ctx["groups"] if g["name"] == "Big Tech")
    assert bt["weight_pct"] is not None
    assert bt["holdings"] == ["AAPL"]
    assert bt["drift"] is None  # no GroupSnapshot history yet
    assert bt["momentum"] is None  # no Signal rows yet
    assert bt["news"] == []
    assert ctx["availability"]["trend_history"] is False
    assert ctx["availability"]["signals"] is False
    assert ctx["availability"]["news"] is False
    assert ctx["profile"]["risk_appetite"] == "balanced"  # default, no InvestorProfile row
    assert ctx["profile"]["horizon"] == "medium"
    assert tencent.symbol == "0700"


async def test_context_drift_from_two_snapshots_on_different_dates(db_session, monkeypatch):
    user = await _make_user(db_session, "rot2@test.dev")
    pf = Portfolio(user_id=user.id, name="UK", kind="real", base_currency="GBP")
    db_session.add(pf)
    await db_session.flush()
    aapl = await _seed_holding(db_session, user, "AAPL", pf)
    group = HoldingGroup(user_id=user.id, name="Big Tech")
    db_session.add(group)
    await db_session.flush()
    db_session.add(GroupAssignment(user_id=user.id, instrument_id=aapl.id, group_id=group.id))

    today = date.today()
    then = today - timedelta(days=10)
    # Big Tech + an Ungrouped bucket on both dates, so the date TOTAL (and thus
    # the weight share) is deterministic:
    #   then: 50 / (50 + 50) = 50.00%    now: 70 / (70 + 30) = 70.00%
    db_session.add_all([
        GroupSnapshot(user_id=user.id, group_id=group.id,
                     as_of=then, value_base=Decimal("50.00")),
        GroupSnapshot(user_id=user.id, group_id=None,
                     as_of=then, value_base=Decimal("50.00")),
        GroupSnapshot(user_id=user.id, group_id=group.id,
                     as_of=today, value_base=Decimal("70.00")),
        GroupSnapshot(user_id=user.id, group_id=None,
                     as_of=today, value_base=Decimal("30.00")),
    ])
    await db_session.commit()

    _stub_valuation(monkeypatch, {"AAPL": Decimal("70")})
    quote_service, fx = _services()

    ctx = await build_rotation_context(db_session, user, quote_service, fx)

    bt = next(g for g in ctx["groups"] if g["name"] == "Big Tech")
    assert bt["drift"] is not None
    assert bt["drift"]["days"] == 10
    assert bt["drift"]["from_pct"] == "50.00"
    assert bt["drift"]["to_pct"] == "70.00"
    assert ctx["availability"]["trend_history"] is True


async def test_context_includes_momentum_and_news_when_present(db_session, monkeypatch):
    user = await _make_user(db_session, "rot3@test.dev")
    pf = Portfolio(user_id=user.id, name="UK", kind="real", base_currency="GBP")
    db_session.add(pf)
    await db_session.flush()
    aapl = await _seed_holding(db_session, user, "AAPL", pf)
    group = HoldingGroup(user_id=user.id, name="Big Tech")
    db_session.add(group)
    await db_session.flush()
    db_session.add(GroupAssignment(user_id=user.id, instrument_id=aapl.id, group_id=group.id))

    now = datetime.now(UTC).replace(tzinfo=None)
    db_session.add(Signal(
        portfolio_id=pf.id, instrument_id=aapl.id, kind="price_move_day",
        severity="watch", title="AAPL moved 5%", detail="AAPL down today",
        data={}, computed_at=now,
    ))
    db_session.add(NewsItem(
        instrument_id=aapl.id, title="Apple unveils new product", source="Reuters",
        url="https://example.com/aapl-news", published_at=now, fetched_at=now,
    ))
    db_session.add(InvestorProfile(
        user_id=user.id, risk_appetite="aggressive", horizon="long",
    ))
    await db_session.commit()

    _stub_valuation(monkeypatch, {"AAPL": Decimal("70")})
    quote_service, fx = _services()

    ctx = await build_rotation_context(db_session, user, quote_service, fx)

    bt = next(g for g in ctx["groups"] if g["name"] == "Big Tech")
    assert bt["momentum"] is not None
    assert "AAPL" in bt["momentum"]["notable_movers"]
    assert bt["news"] == [{"title": "Apple unveils new product", "source": "Reuters"}]
    assert ctx["availability"]["signals"] is True
    assert ctx["availability"]["news"] is True
    assert ctx["profile"]["risk_appetite"] == "aggressive"
    assert ctx["profile"]["horizon"] == "long"


async def test_context_is_scoped_to_the_requesting_user(db_session, monkeypatch):
    user = await _make_user(db_session, "rot4@test.dev")
    other = await _make_user(db_session, "rot4-other@test.dev")

    pf = Portfolio(user_id=user.id, name="UK", kind="real", base_currency="GBP")
    other_pf = Portfolio(user_id=other.id, name="Theirs", kind="real", base_currency="GBP")
    db_session.add_all([pf, other_pf])
    await db_session.flush()

    aapl = await _seed_holding(db_session, user, "AAPL", pf)
    msft = await _seed_holding(db_session, other, "MSFT", other_pf)

    own_group = HoldingGroup(user_id=user.id, name="Mine")
    other_group = HoldingGroup(user_id=other.id, name="Theirs")
    db_session.add_all([own_group, other_group])
    await db_session.flush()
    db_session.add(GroupAssignment(user_id=user.id, instrument_id=aapl.id, group_id=own_group.id))
    db_session.add(GroupAssignment(
        user_id=other.id, instrument_id=msft.id, group_id=other_group.id))
    await db_session.commit()

    _stub_valuation(monkeypatch, {"AAPL": Decimal("70"), "MSFT": Decimal("30")})
    quote_service, fx = _services()

    ctx = await build_rotation_context(db_session, user, quote_service, fx)

    names = {g["name"] for g in ctx["groups"]}
    assert names == {"Mine"}
    mine = next(g for g in ctx["groups"] if g["name"] == "Mine")
    assert mine["holdings"] == ["AAPL"]
