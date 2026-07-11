from decimal import Decimal

import pytest
from sqlalchemy import text

pytestmark = pytest.mark.asyncio(loop_scope="session")


async def test_position_amounts_encrypted_at_rest(db_session, make_instrument):
    from app.models import Portfolio, Position, User

    u = User(email="enc@test.dev", password_hash="x")
    db_session.add(u)
    await db_session.commit()
    pf = Portfolio(user_id=u.id, name="P", kind="real", base_currency="GBP")
    db_session.add(pf)
    await db_session.commit()
    inst = await make_instrument("AAPL")
    db_session.add(
        Position(
            portfolio_id=pf.id,
            instrument_id=inst.id,
            quantity=Decimal("10.5"),
            avg_cost=Decimal("123.4567"),
        )
    )
    await db_session.commit()
    # ORM round-trips as Decimal
    pos = (await db_session.execute(text("SELECT quantity, avg_cost FROM positions"))).one()
    assert pos.quantity.startswith("v1:") and pos.avg_cost.startswith("v1:")  # raw = ciphertext
    from sqlalchemy import select

    orm_pos = (await db_session.execute(select(Position))).scalar_one()
    assert orm_pos.quantity == Decimal("10.5") and orm_pos.avg_cost == Decimal("123.4567")


async def test_digest_enabled_defaults_false(db_session):
    from app.models import InvestorProfile, User

    u = User(email="dig@test.dev", password_hash="x")
    db_session.add(u)
    await db_session.commit()
    p = InvestorProfile(user_id=u.id)
    db_session.add(p)
    await db_session.commit()
    await db_session.refresh(p)
    assert p.digest_enabled is False
