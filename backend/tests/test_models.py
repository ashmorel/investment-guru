from decimal import Decimal

import pytest

from app.models import Instrument, Portfolio, Position, User

pytestmark = pytest.mark.asyncio(loop_scope="session")


async def test_portfolio_with_positions(db_session):
    user = User(email="m@test.dev", password_hash="x")
    inst = Instrument(symbol="AAPL", name="Apple Inc.", exchange="NMS", market="US", currency="USD")
    db_session.add_all([user, inst])
    await db_session.flush()

    pf = Portfolio(user_id=user.id, name="Growth", kind="real", base_currency="GBP")
    db_session.add(pf)
    await db_session.flush()

    pos = Position(
        portfolio_id=pf.id, instrument_id=inst.id,
        quantity=Decimal("10.5"), avg_cost=Decimal("150.25"),
    )
    db_session.add(pos)
    await db_session.commit()

    loaded = await db_session.get(Portfolio, pf.id)
    await db_session.refresh(loaded, ["positions"])
    assert len(loaded.positions) == 1
    assert loaded.positions[0].quantity == Decimal("10.500000")


async def test_watchlist_position_allows_null_quantity(db_session):
    user = User(email="w@test.dev", password_hash="x")
    inst = Instrument(symbol="0700.HK", name="Tencent", exchange="HKG", market="HK", currency="HKD")
    db_session.add_all([user, inst])
    await db_session.flush()
    pf = Portfolio(user_id=user.id, name="Watch", kind="watchlist", base_currency="HKD")
    db_session.add(pf)
    await db_session.flush()
    db_session.add(Position(portfolio_id=pf.id, instrument_id=inst.id))
    await db_session.commit()
