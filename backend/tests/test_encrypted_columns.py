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


async def test_position_quantity_rounds_half_up(db_session, make_instrument):
    """Position.quantity rounds halfway values up (ROUND_HALF_UP), not banker's rounding."""
    from sqlalchemy import select

    from app.models import Portfolio, Position, User

    u = User(email="qty_round@test.dev", password_hash="x")
    db_session.add(u)
    await db_session.commit()
    pf = Portfolio(user_id=u.id, name="P", kind="real", base_currency="GBP")
    db_session.add(pf)
    await db_session.commit()
    inst = await make_instrument("TEST")
    # Halfway value for 6dp: 1.0000005 should round to 1.000001 (half-up), not 1.000000 (banker's)
    db_session.add(
        Position(
            portfolio_id=pf.id,
            instrument_id=inst.id,
            quantity=Decimal("1.0000005"),
        )
    )
    await db_session.commit()
    pos = (await db_session.execute(select(Position))).scalar_one()
    assert pos.quantity == Decimal("1.000001")


async def test_position_avg_cost_rounds_half_up(db_session, make_instrument):
    """Position.avg_cost rounds halfway values up (ROUND_HALF_UP), not banker's rounding."""
    from sqlalchemy import select

    from app.models import Portfolio, Position, User

    u = User(email="cost_round@test.dev", password_hash="x")
    db_session.add(u)
    await db_session.commit()
    pf = Portfolio(user_id=u.id, name="P", kind="real", base_currency="GBP")
    db_session.add(pf)
    await db_session.commit()
    inst = await make_instrument("TEST")
    # Halfway value for 4dp: 1.00005 should round to 1.0001 (half-up), not 1.0000 (banker's)
    db_session.add(
        Position(
            portfolio_id=pf.id,
            instrument_id=inst.id,
            avg_cost=Decimal("1.00005"),
        )
    )
    await db_session.commit()
    pos = (await db_session.execute(select(Position))).scalar_one()
    assert pos.avg_cost == Decimal("1.0001")


async def test_orso_allocation_units_rounds_half_up(db_session):
    """OrsoAllocation.units rounds halfway values up (ROUND_HALF_UP), not banker's rounding."""
    from sqlalchemy import select

    from app.models import OrsoAllocation, OrsoFund, User

    u = User(email="orso_units@test.dev", password_hash="x")
    db_session.add(u)
    await db_session.commit()
    fund = OrsoFund(user_id=u.id, code="F1", name="Fund1", asset_class="Equity", risk_rating=5)
    db_session.add(fund)
    await db_session.commit()
    # Halfway value for 4dp: 1.00005 should round to 1.0001 (half-up), not 1.0000 (banker's)
    db_session.add(
        OrsoAllocation(
            user_id=u.id,
            fund_id=fund.id,
            units=Decimal("1.00005"),
            contribution_pct=Decimal("50"),
        )
    )
    await db_session.commit()
    alloc = (await db_session.execute(select(OrsoAllocation))).scalar_one()
    assert alloc.units == Decimal("1.0001")


async def test_orso_allocation_contribution_pct_rounds_half_up(db_session):
    """OrsoAllocation.contribution_pct rounds halfway values up, not banker's rounding."""
    from sqlalchemy import select

    from app.models import OrsoAllocation, OrsoFund, User

    u = User(email="orso_pct@test.dev", password_hash="x")
    db_session.add(u)
    await db_session.commit()
    fund = OrsoFund(user_id=u.id, code="F2", name="Fund2", asset_class="Equity", risk_rating=5)
    db_session.add(fund)
    await db_session.commit()
    # Halfway value for 2dp: 1.005 should round to 1.01 (half-up), not 1.00 (banker's)
    db_session.add(
        OrsoAllocation(
            user_id=u.id,
            fund_id=fund.id,
            units=Decimal("100"),
            contribution_pct=Decimal("1.005"),
        )
    )
    await db_session.commit()
    alloc = (await db_session.execute(select(OrsoAllocation))).scalar_one()
    assert alloc.contribution_pct == Decimal("1.01")
