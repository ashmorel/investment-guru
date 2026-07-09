from datetime import UTC, date, datetime
from decimal import Decimal

import pytest

from app.models import (
    ChatThread,
    InvestorProfile,
    OrsoAllocation,
    OrsoFund,
    OrsoFundPrice,
    OrsoSwitchLog,
    User,
)

pytestmark = pytest.mark.asyncio(loop_scope="session")


async def _user(db_session) -> User:
    u = User(email="orso@test.dev", password_hash="x")
    db_session.add(u)
    await db_session.commit()
    return u


async def test_orso_tables_roundtrip(db_session):
    u = await _user(db_session)
    now = datetime.now(UTC).replace(tzinfo=None)
    fund = OrsoFund(user_id=u.id, code="HSBC-EQ-HK", name="HK Equity Fund",
                    asset_class="equity", risk_rating=4)
    db_session.add(fund)
    await db_session.commit()
    db_session.add(OrsoAllocation(user_id=u.id, fund_id=fund.id,
                                  units=Decimal("1234.5678"), contribution_pct=Decimal("60.00")))
    db_session.add(OrsoFundPrice(fund_id=fund.id, price=Decimal("23.4567"),
                                 as_of=date(2026, 7, 8), source="manual", fetched_at=now))
    db_session.add(OrsoSwitchLog(user_id=u.id, changed_at=now,
                                 old_state={"allocations": []},
                                 new_state={"allocations": [{"code": "HSBC-EQ-HK",
                                            "units": "1234.5678", "contribution_pct": "60.00"}]},
                                 note="initial"))
    await db_session.commit()
    assert fund.id and fund.archived is False


async def test_fund_code_unique_per_user_and_price_unique_per_day(db_session):
    u = await _user(db_session)
    f = OrsoFund(user_id=u.id, code="X", name="X", asset_class="bond", risk_rating=2)
    db_session.add(f)
    await db_session.commit()
    fund_id = f.id  # captured before rollback: rollback() expires all attrs, incl. PKs,
    # and re-reading an expired attr on an AsyncSession object requires an explicit
    # async-safe reload (refresh/execute) rather than plain attribute access.
    db_session.add(OrsoFund(user_id=u.id, code="X", name="dup", asset_class="bond", risk_rating=2))
    with pytest.raises(Exception):  # noqa: B017 - IntegrityError via asyncpg
        await db_session.commit()
    await db_session.rollback()
    now = datetime.now(UTC).replace(tzinfo=None)
    db_session.add(OrsoFundPrice(fund_id=fund_id, price=Decimal("1.0"), as_of=date(2026, 7, 8),
                                 source="hsbc", fetched_at=now))
    await db_session.commit()
    db_session.add(OrsoFundPrice(fund_id=fund_id, price=Decimal("2.0"), as_of=date(2026, 7, 8),
                                 source="manual", fetched_at=now))
    with pytest.raises(Exception):  # noqa: B017
        await db_session.commit()


async def test_profile_goal_columns_and_thread_scope(db_session):
    u = await _user(db_session)
    db_session.add(InvestorProfile(user_id=u.id, birth_year=1980, retirement_target_age=60,
                                   retirement_target_pot=Decimal("5000000.00"),
                                   orso_monthly_contribution=Decimal("15000.00")))
    db_session.add(ChatThread(user_id=u.id, title="orso chat", scope="orso"))
    await db_session.commit()


async def test_seed_orso_funds_is_idempotent(db_session):
    from app.seed import STARTER_FUNDS, seed_orso_funds

    u = await _user(db_session)
    created_first = await seed_orso_funds(db_session, u.id)
    assert created_first == len(STARTER_FUNDS)
    created_second = await seed_orso_funds(db_session, u.id)
    assert created_second == 0
