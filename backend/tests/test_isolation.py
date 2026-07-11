"""Central cross-user isolation sweep.

One test, one user A with one of everything (portfolio + position + guru
review + chat thread + ORSO fund + ORSO allocation), then user B logs in on
the *same* client/cookie jar (the established pattern in this suite -- see
test_portfolios.py::test_other_users_portfolio_is_404 and
test_guru_chat.py::test_thread_crud_and_ownership) and hits every
owned-resource route with user A's ids. Every case must come back 404 (or,
for the ORSO allocation replace and the manual price PUT, the existing 422/404
"foreign fund" rejections -- see test_orso_api.py::test_allocation_rejects_foreign_fund)
-- never 403 and never 200-with-data.

For every mutation attempt, the rejecting status code alone isn't proof of
isolation: a 404 that still side-effected against user A's row underneath
would be a leak this guard must also catch. So each mutation case is paired
with a read-back (via db_session, always a column-level select -- never a
full-entity select -- to sidestep any SQLAlchemy identity-map staleness from
objects _seed_user_a_data already loaded/refreshed into this same session)
proving user A's row is byte-for-byte what it was before user B's rejected
call.

Route/method notes vs. the task brief: portfolios.py has no single-resource
GET and no PUT -- only PATCH (and DELETE) take a `portfolio_id`. The sweep
below substitutes PATCH (the real owned-resource mutate route) for the
brief's "GET/PUT". Likewise orso funds are mutated via PATCH, not PUT.

The guru review and chat thread are seeded directly via db_session (not
through the API) so this test needs no LLM provider, per the brief.
"""

from datetime import UTC, datetime
from decimal import Decimal

import pytest
from sqlalchemy import func, select

from app.core.security import hash_password
from app.models import (
    ChatMessage,
    ChatThread,
    GuruReport,
    OrsoAllocation,
    OrsoFund,
    OrsoFundPrice,
    Portfolio,
    Signal,
)
from app.models.user import User

pytestmark = pytest.mark.asyncio(loop_scope="session")


async def _seed_user_a_data(orso_client, db_session, make_instrument) -> dict:
    """Create one of everything as user A (orso_client's logged-in user:
    lee@test.dev), all via the real API except the guru review + chat
    thread, which are seeded directly to avoid needing an LLM provider."""
    await make_instrument("AAPL")

    pf_id = (await orso_client.post(
        "/api/portfolios", json={"name": "A's Portfolio", "kind": "real", "base_currency": "USD"}
    )).json()["id"]

    await orso_client.post(
        f"/api/portfolios/{pf_id}/positions",
        json={"symbol": "AAPL", "quantity": "10", "avg_cost": "100"},
    )

    user_a = (await db_session.execute(
        select(User).where(User.email == "lee@test.dev")
    )).scalar_one()

    report = GuruReport(
        user_id=user_a.id, kind="review", portfolio_id=pf_id,
        payload={"positions": [], "observations": [], "watch_next": [], "disclaimer": "x"},
        model="fake", created_at=datetime.now(UTC).replace(tzinfo=None),
    )
    db_session.add(report)

    thread = ChatThread(
        user_id=user_a.id, title="A's thread", portfolio_id=pf_id,
        seed_context=None, scope=None,
    )
    db_session.add(thread)
    await db_session.commit()
    await db_session.refresh(report)
    await db_session.refresh(thread)

    fund_id = (await orso_client.post(
        "/api/orso/funds",
        json={"code": "HKEQ", "name": "HK Equity", "asset_class": "equity", "risk_rating": 3},
    )).json()["id"]

    alloc_resp = await orso_client.put(
        "/api/orso/allocation",
        json={"allocations": [
            {"fund_id": fund_id, "units": "10", "contribution_pct": "100"}
        ]},
    )
    assert alloc_resp.status_code == 200, alloc_resp.text

    return {
        "portfolio_id": pf_id,
        "report_id": report.id,
        "thread_id": thread.id,
        "fund_id": fund_id,
    }


async def _assert_portfolio_unchanged(db_session, pf_id: int) -> None:
    name = (await db_session.execute(
        select(Portfolio.name).where(Portfolio.id == pf_id)
    )).scalar_one()
    assert name == "A's Portfolio", f"portfolio {pf_id} name mutated to {name!r}"


async def _assert_no_signals(db_session, pf_id: int) -> None:
    count = (await db_session.execute(
        select(func.count()).select_from(Signal).where(Signal.portfolio_id == pf_id)
    )).scalar_one()
    assert count == 0, f"portfolio {pf_id} gained {count} signal row(s) from a rejected /analyze"


async def _assert_thread_untouched(db_session, thread_id: int) -> None:
    contents = (await db_session.execute(
        select(ChatMessage.content).where(ChatMessage.thread_id == thread_id)
    )).scalars().all()
    assert contents == [], (
        f"thread {thread_id} gained message(s) from a rejected post: {contents!r}"
    )


async def _assert_fund_unchanged(db_session, fund_id: int) -> None:
    name = (await db_session.execute(
        select(OrsoFund.name).where(OrsoFund.id == fund_id)
    )).scalar_one()
    assert name == "HK Equity", f"fund {fund_id} name mutated to {name!r}"


async def _login_as_user_b(client, db_session) -> None:
    """Log a second user in on the SAME client/cookie jar as user A -- this
    overwrites the session cookie, exactly like the existing
    test_other_users_portfolio_is_404 / test_thread_crud_and_ownership
    pattern elsewhere in this suite."""
    other = User(email="user-b@test.dev", password_hash=hash_password("pw123456"))
    db_session.add(other)
    await db_session.commit()
    resp = await client.post(
        "/api/auth/login", json={"email": "user-b@test.dev", "password": "pw123456"}
    )
    assert resp.status_code == 204


async def test_cross_user_isolation_sweep(orso_client, db_session, make_instrument):
    ids = await _seed_user_a_data(orso_client, db_session, make_instrument)
    await _login_as_user_b(orso_client, db_session)

    pf_id, report_id = ids["portfolio_id"], ids["report_id"]
    thread_id, fund_id = ids["thread_id"], ids["fund_id"]

    # (label, coroutine-producing call, optional read-back verifying A's data
    # is unchanged) -- each entry hits an owned-resource route with user A's
    # id while authenticated as user B. Non-mutating GETs carry no read-back;
    # every mutation attempt does.
    cases = [
        ("GET valuation", orso_client.get(f"/api/portfolios/{pf_id}/valuation"), None),
        ("GET signals", orso_client.get(f"/api/portfolios/{pf_id}/signals"), None),
        ("POST analyze", orso_client.post(f"/api/portfolios/{pf_id}/analyze"),
         lambda: _assert_no_signals(db_session, pf_id)),
        ("PATCH portfolio", orso_client.patch(
            f"/api/portfolios/{pf_id}", json={"name": "hijacked"}),
         lambda: _assert_portfolio_unchanged(db_session, pf_id)),
        ("GET guru review", orso_client.get(f"/api/guru/reviews/{report_id}"), None),
        ("GET chat thread", orso_client.get(f"/api/guru/chat/threads/{thread_id}"), None),
        ("POST chat message", orso_client.post(
            f"/api/guru/chat/threads/{thread_id}/messages", json={"content": "steal this"}),
         lambda: _assert_thread_untouched(db_session, thread_id)),
        ("PATCH orso fund", orso_client.patch(
            f"/api/orso/funds/{fund_id}", json={"name": "hijacked"}),
         lambda: _assert_fund_unchanged(db_session, fund_id)),
    ]

    for label, coro, verify in cases:
        resp = await coro
        assert resp.status_code == 404, (
            f"{label}: expected 404 (owned-resource leak check), "
            f"got {resp.status_code}: {resp.text}"
        )
        assert resp.headers.get("content-type", "").startswith("text/event-stream") is False, (
            f"{label}: response opened a stream instead of 404ing pre-stream"
        )
        if verify is not None:
            await verify()

    # ORSO allocation replace referencing A's fund_id is a distinct shape:
    # the endpoint 422s ("unknown_fund_id") rather than 404ing, because it
    # validates fund ownership as part of a batch-replace payload (see
    # test_orso_api.py::test_allocation_rejects_foreign_fund for the existing
    # single-user-perspective coverage of this same code path).
    alloc_resp = await orso_client.put(
        "/api/orso/allocation",
        json={"allocations": [
            {"fund_id": fund_id, "units": "1", "contribution_pct": "50"}
        ]},
    )
    assert alloc_resp.status_code == 422, (
        f"PUT allocation (foreign fund_id): expected 422, "
        f"got {alloc_resp.status_code}: {alloc_resp.text}"
    )
    assert alloc_resp.json().get("detail") == "unknown_fund_id"

    # A's allocation row (10 units / 100% contribution, seeded above) must be
    # untouched by the rejected PUT -- not partially applied, not overwritten.
    a_units, a_pct = (await db_session.execute(
        select(OrsoAllocation.units, OrsoAllocation.contribution_pct)
        .where(OrsoAllocation.fund_id == fund_id)
    )).one()
    assert a_units == Decimal("10.0000"), f"A's allocation units mutated to {a_units}"
    assert a_pct == Decimal("100.00"), f"A's allocation contribution_pct mutated to {a_pct}"

    # Belt-and-braces: user B's own allocation must still be empty -- the
    # rejected PUT must not have partially applied against A's fund.
    own_alloc = await orso_client.get("/api/orso/allocation")
    assert own_alloc.json() == []

    # PUT /api/orso/prices/manual with a foreign fund_id: get_owned_fund must
    # 404 before any OrsoFundPrice row is created against A's fund.
    manual_resp = await orso_client.put(
        "/api/orso/prices/manual",
        json={"fund_id": fund_id, "price": "12.5", "as_of": "2026-07-01"},
    )
    assert manual_resp.status_code == 404, (
        f"PUT prices/manual (foreign fund_id): expected 404, "
        f"got {manual_resp.status_code}: {manual_resp.text}"
    )
    price_count = (await db_session.execute(
        select(func.count()).select_from(OrsoFundPrice).where(OrsoFundPrice.fund_id == fund_id)
    )).scalar_one()
    assert price_count == 0, (
        f"fund {fund_id} gained a price row from user B's rejected manual-price PUT"
    )

    # GET /api/guru/reviews?portfolio_id=<A's portfolio> as user B must not
    # return A's review -- the list filter is scoped by user_id, so this
    # comes back 200 with an empty (or B's-own-only) list, never A's data.
    reviews_resp = await orso_client.get(
        "/api/guru/reviews", params={"portfolio_id": pf_id}
    )
    assert reviews_resp.status_code == 200, (
        f"GET reviews?portfolio_id=<A's>: expected 200, "
        f"got {reviews_resp.status_code}: {reviews_resp.text}"
    )
    review_ids = [r["id"] for r in reviews_resp.json()["reviews"]]
    assert report_id not in review_ids, (
        f"GET reviews?portfolio_id=<A's>: user B's response leaked A's review {report_id}"
    )
