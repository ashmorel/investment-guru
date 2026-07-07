from datetime import UTC, date, datetime

import pytest

from app.models import Instrument, InstrumentFundamentals, NewsItem, Portfolio, Signal, User

pytestmark = pytest.mark.asyncio(loop_scope="session")


async def test_signal_persists_with_json_data(db_session):
    user = User(email="s@test.dev", password_hash="x")
    inst = Instrument(symbol="AAPL", name="Apple", exchange="NMS", market="US", currency="USD")
    db_session.add_all([user, inst])
    await db_session.flush()
    pf = Portfolio(user_id=user.id, name="P", kind="real", base_currency="GBP")
    db_session.add(pf)
    await db_session.flush()
    sig = Signal(
        portfolio_id=pf.id, instrument_id=inst.id, kind="price_move_day",
        severity="watch", title="AAPL -6.1% today", detail="Down 6.1% on the day",
        data={"pct": "-6.1", "close": "188.22"},
        computed_at=datetime.now(UTC).replace(tzinfo=None),
    )
    db_session.add(sig)
    await db_session.commit()
    loaded = await db_session.get(Signal, sig.id)
    assert loaded.data["pct"] == "-6.1"
    assert loaded.instrument_id == inst.id


async def test_portfolio_level_signal_allows_null_instrument(db_session):
    user = User(email="s2@test.dev", password_hash="x")
    db_session.add(user)
    await db_session.flush()
    pf = Portfolio(user_id=user.id, name="P", kind="real", base_currency="GBP")
    db_session.add(pf)
    await db_session.flush()
    db_session.add(Signal(
        portfolio_id=pf.id, instrument_id=None, kind="concentration",
        severity="high", title="AAPL is 32% of portfolio", detail="Single-name concentration",
        data={"pct": "32.0"}, computed_at=datetime.now(UTC).replace(tzinfo=None),
    ))
    await db_session.commit()


async def test_fundamentals_and_news(db_session):
    inst = Instrument(symbol="NVDA", name="Nvidia", exchange="NMS", market="US", currency="USD")
    db_session.add(inst)
    await db_session.flush()
    db_session.add(InstrumentFundamentals(
        instrument_id=inst.id, next_earnings_date=date(2026, 8, 20),
        fetched_at=datetime.now(UTC).replace(tzinfo=None),
    ))
    db_session.add(NewsItem(
        instrument_id=inst.id, title="Nvidia announces X", source="Yahoo",
        url="https://example.com/n1", published_at=datetime.now(UTC).replace(tzinfo=None),
        fetched_at=datetime.now(UTC).replace(tzinfo=None),
    ))
    await db_session.commit()
