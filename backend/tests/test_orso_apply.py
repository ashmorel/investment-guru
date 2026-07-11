from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import func, select

from app.models import OrsoAllocation, OrsoFund, OrsoFundPrice, OrsoSwitchLog

pytestmark = pytest.mark.asyncio(loop_scope="session")


async def test_apply_creates_new_fund_price_and_allocation_with_switchlog(orso_client, db_session):
    body = {
        "new_funds": [{"code": "NEWEQ", "name": "New Equity", "currency": "HKD",
                       "asset_class": "equity", "risk_rating": 5}],
        "allocations": [{"new_fund_code": "NEWEQ", "units": "100",
                         "contribution_pct": "100",
                         "price": {"market_value": "1500", "as_of": date.today().isoformat()}}],
        "note": "from statement",
    }
    r = await orso_client.post("/api/orso/allocation/apply", json=body)
    assert r.status_code == 200
    assert r.json()["switched"] is True

    fund = (await db_session.execute(
        select(OrsoFund).where(OrsoFund.code == "NEWEQ"))).scalar_one()
    alloc = (await db_session.execute(
        select(OrsoAllocation).where(OrsoAllocation.fund_id == fund.id))).scalar_one()
    assert alloc.units == Decimal("100.0000")

    price = (await db_session.execute(
        select(OrsoFundPrice).where(OrsoFundPrice.fund_id == fund.id))).scalar_one()
    assert str(price.price) == "15.0000"          # 1500 / 100
    assert price.source == "manual"
    n_switch = (await db_session.execute(
        select(func.count()).select_from(OrsoSwitchLog).where(
            OrsoSwitchLog.user_id == fund.user_id))).scalar_one()
    assert n_switch == 1


async def test_apply_is_all_or_nothing_on_bad_row(orso_client, db_session):
    # a fund_id that doesn't belong to the user -> whole apply rejected, nothing created
    before = (await db_session.execute(select(func.count()).select_from(OrsoFund))).scalar_one()
    body = {
        "new_funds": [{"code": "GHOST", "name": "Ghost", "currency": "HKD",
                       "asset_class": "equity", "risk_rating": 4}],
        "allocations": [{"fund_id": 999999, "units": "1", "contribution_pct": "100"}],
        "note": None,
    }
    r = await orso_client.post("/api/orso/allocation/apply", json=body)
    assert r.status_code == 422
    after = (await db_session.execute(select(func.count()).select_from(OrsoFund))).scalar_one()
    assert after == before        # GHOST was NOT created (rolled back)


async def test_apply_rejects_other_users_fund(orso_client, client, db_session):
    from app.core.security import hash_password
    from app.models.user import User
    # user A creates a fund
    fid = (await orso_client.post("/api/orso/funds", json={
        "code": "AONLY", "name": "A only", "asset_class": "equity",
        "risk_rating": 4})).json()["id"]
    # user B logs in and tries to allocate to A's fund
    db_session.add(User(email="bapply@test.dev", password_hash=hash_password("pw123456")))
    await db_session.commit()
    await client.post("/api/auth/login", json={"email": "bapply@test.dev", "password": "pw123456"})
    r = await client.post("/api/orso/allocation/apply", json={
        "new_funds": [], "allocations": [{"fund_id": fid, "units": "1",
                                          "contribution_pct": "100"}], "note": None})
    assert r.status_code == 422
