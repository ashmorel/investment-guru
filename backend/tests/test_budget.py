from datetime import UTC, datetime
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.core.security import hash_password
from app.models import LlmUsage, User
from app.services.guru.budget import BudgetExhausted, check_budget

pytestmark = pytest.mark.asyncio(loop_scope="session")

_NOW = datetime(2026, 7, 8, 10, 0, 0, tzinfo=UTC)


async def _make_user(db_session, email: str) -> User:
    user = User(email=email, password_hash=hash_password("pw123456"))
    db_session.add(user)
    await db_session.commit()
    await db_session.refresh(user)
    return user


def _usage_row(user_id: int, cost: Decimal | None, created_at: datetime) -> LlmUsage:
    return LlmUsage(
        user_id=user_id, mode="review", model="claude-opus-4-8",
        input_tokens=100, output_tokens=50, est_cost_usd=cost,
        created_at=created_at,
    )


async def test_check_budget_under_cap_passes(db_session):
    user = await _make_user(db_session, "under@test.dev")
    db_session.add(_usage_row(user.id, Decimal("0.50"), datetime(2026, 7, 8, 9, 0, 0)))
    await db_session.commit()

    await check_budget(db_session, user.id, now=_NOW)  # no raise


async def test_check_budget_at_cap_raises(db_session):
    user = await _make_user(db_session, "atcap@test.dev")
    db_session.add(_usage_row(user.id, Decimal("1.00"), datetime(2026, 7, 8, 9, 0, 0)))
    await db_session.commit()

    with pytest.raises(BudgetExhausted):
        await check_budget(db_session, user.id, now=_NOW)


async def test_check_budget_over_cap_raises(db_session):
    user = await _make_user(db_session, "overcap@test.dev")
    db_session.add(_usage_row(user.id, Decimal("1.50"), datetime(2026, 7, 8, 9, 0, 0)))
    await db_session.commit()

    with pytest.raises(BudgetExhausted):
        await check_budget(db_session, user.id, now=_NOW)


async def test_check_budget_sums_multiple_rows_to_cap(db_session):
    user = await _make_user(db_session, "sums@test.dev")
    db_session.add_all([
        _usage_row(user.id, Decimal("0.60"), datetime(2026, 7, 8, 8, 0, 0)),
        _usage_row(user.id, Decimal("0.40"), datetime(2026, 7, 8, 9, 0, 0)),
    ])
    await db_session.commit()

    with pytest.raises(BudgetExhausted):
        await check_budget(db_session, user.id, now=_NOW)


async def test_check_budget_ignores_yesterdays_usage(db_session):
    user = await _make_user(db_session, "yesterday-budget@test.dev")
    # guru_timezone defaults to Europe/London; on 2026-07-08 (BST, UTC+1) local
    # midnight is 2026-07-07T23:00:00 UTC. A row at 20:00 UTC on 07-07 is still
    # "yesterday" locally and must not count toward today's spend.
    db_session.add(_usage_row(user.id, Decimal("5.00"), datetime(2026, 7, 7, 20, 0, 0)))
    await db_session.commit()

    await check_budget(db_session, user.id, now=_NOW)  # no raise -- outside today's window


async def test_check_budget_ignores_other_users_usage(db_session):
    user = await _make_user(db_session, "self@test.dev")
    other = await _make_user(db_session, "other@test.dev")
    db_session.add(_usage_row(other.id, Decimal("5.00"), datetime(2026, 7, 8, 9, 0, 0)))
    await db_session.commit()

    await check_budget(db_session, user.id, now=_NOW)  # no raise -- other user's spend


async def test_check_budget_null_cost_counts_as_zero(db_session):
    user = await _make_user(db_session, "nullcost@test.dev")
    db_session.add(_usage_row(user.id, None, datetime(2026, 7, 8, 9, 0, 0)))
    await db_session.commit()

    await check_budget(db_session, user.id, now=_NOW)  # no raise


async def _seed_portfolio(client, make_instrument) -> int:
    await make_instrument("AAPL")
    pid = (await client.post(
        "/api/portfolios", json={"name": "P", "kind": "real", "base_currency": "USD"}
    )).json()["id"]
    await client.post(
        f"/api/portfolios/{pid}/positions",
        json={"symbol": "AAPL", "quantity": "10", "avg_cost": "100"},
    )
    return pid


async def test_review_returns_429_when_budget_exhausted(guru_client, db_session, make_instrument):
    pf_id = await _seed_portfolio(guru_client, make_instrument)
    user = (await db_session.execute(
        select(User).where(User.email == "lee@test.dev")
    )).scalar_one()
    db_session.add(_usage_row(user.id, Decimal("1.00"), datetime.now(UTC).replace(tzinfo=None)))
    await db_session.commit()

    resp = await guru_client.post("/api/guru/reviews", json={"portfolio_id": pf_id})
    assert resp.status_code == 429
    assert resp.json()["detail"] == "budget_exhausted"

    # nothing persisted
    listed = (await guru_client.get(f"/api/guru/reviews?portfolio_id={pf_id}")).json()
    assert listed["reviews"] == []
