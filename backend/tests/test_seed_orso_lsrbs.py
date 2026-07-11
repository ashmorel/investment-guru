from decimal import Decimal

import pytest
from sqlalchemy import select

from app.core.security import hash_password
from app.models import OrsoAllocation, OrsoFund, User
from app.seed_orso_lsrbs import LSRBS_FUNDS, seed_lsrbs_funds

pytestmark = pytest.mark.asyncio(loop_scope="session")


async def _make_user(db_session, email: str) -> User:
    user = User(email=email, password_hash=hash_password("pw123456"))
    db_session.add(user)
    await db_session.commit()
    await db_session.refresh(user)
    return user


async def test_seeds_all_lsrbs_funds_with_currency_and_class(db_session):
    user = await _make_user(db_session, "lsrbs1@test.dev")
    result = await seed_lsrbs_funds(db_session, user.id)
    assert result["created"] == len(LSRBS_FUNDS) == 18

    funds = (await db_session.execute(
        select(OrsoFund).where(OrsoFund.user_id == user.id))).scalars().all()
    by_code = {f.code: f for f in funds}
    # the EUR fund proves multi-currency seeding
    assert by_code["IEUI"].currency == "EUR"
    assert by_code["HGMF"].currency == "HKD" and by_code["HGMF"].asset_class == "cash"
    assert by_code["IDWI"].currency == "USD" and by_code["IDWI"].asset_class == "equity"
    assert all(1 <= f.risk_rating <= 7 for f in funds)


async def test_reseed_is_idempotent(db_session):
    user = await _make_user(db_session, "lsrbs2@test.dev")
    await seed_lsrbs_funds(db_session, user.id)
    again = await seed_lsrbs_funds(db_session, user.id)
    assert again == {"created": 0, "updated": 0, "archived": 0}


async def test_updates_stale_metadata_and_unarchives(db_session):
    user = await _make_user(db_session, "lsrbs3@test.dev")
    # pre-existing LSRBS fund with wrong currency + archived
    db_session.add(OrsoFund(user_id=user.id, code="IEUI", name="Old Name",
                            asset_class="equity", risk_rating=5, currency="USD",
                            archived=True))
    await db_session.commit()
    result = await seed_lsrbs_funds(db_session, user.id)
    assert result["created"] == 17 and result["updated"] == 1
    fund = (await db_session.execute(
        select(OrsoFund).where(OrsoFund.user_id == user.id,
                               OrsoFund.code == "IEUI"))).scalar_one()
    assert fund.currency == "EUR" and fund.archived is False
    assert fund.name == "iShares Europe Index Fund (IE)"


async def test_archives_zero_alloc_legacy_wmfs_but_keeps_held(db_session):
    user = await _make_user(db_session, "lsrbs4@test.dev")
    # two legacy WMFS funds: one empty (archive), one held (keep)
    empty = OrsoFund(user_id=user.id, code="NAEF", name="North American Equity Fund",
                     asset_class="equity", risk_rating=4, currency="HKD")
    held = OrsoFund(user_id=user.id, code="HKEF", name="Hong Kong Equity Fund",
                    asset_class="equity", risk_rating=5, currency="HKD")
    db_session.add_all([empty, held])
    await db_session.commit()
    await db_session.refresh(held)
    db_session.add(OrsoAllocation(user_id=user.id, fund_id=held.id,
                                  units=Decimal("100"), contribution_pct=Decimal("100")))
    await db_session.commit()

    result = await seed_lsrbs_funds(db_session, user.id)
    assert result["archived"] == 1  # only the empty legacy fund
    refreshed = {f.code: f for f in (await db_session.execute(
        select(OrsoFund).where(OrsoFund.user_id == user.id))).scalars().all()}
    assert refreshed["NAEF"].archived is True
    assert refreshed["HKEF"].archived is False  # held -> never archived
