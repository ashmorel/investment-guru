"""Central cross-user isolation sweep.

One test, one user A with one of everything (portfolio + position + guru
review + chat thread + ORSO fund + ORSO allocation), then user B logs in on
the *same* client/cookie jar (the established pattern in this suite -- see
test_portfolios.py::test_other_users_portfolio_is_404 and
test_guru_chat.py::test_thread_crud_and_ownership) and hits every
owned-resource route with user A's ids. Every case must come back 404 (or,
for the ORSO allocation replace, the existing 422 "foreign fund" rejection --
see test_orso_api.py::test_allocation_rejects_foreign_fund) -- never 403 and
never 200-with-data.

Route/method notes vs. the task brief: portfolios.py has no single-resource
GET and no PUT -- only PATCH (and DELETE) take a `portfolio_id`. The sweep
below substitutes PATCH (the real owned-resource mutate route) for the
brief's "GET/PUT". Likewise orso funds are mutated via PATCH, not PUT.

The guru review and chat thread are seeded directly via db_session (not
through the API) so this test needs no LLM provider, per the brief.
"""

from datetime import UTC, datetime

import pytest
from sqlalchemy import select

from app.core.security import hash_password
from app.models import ChatThread, GuruReport
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

    # (label, coroutine-producing call) -- each entry hits an owned-resource
    # route with user A's id while authenticated as user B.
    cases = [
        ("GET valuation", orso_client.get(f"/api/portfolios/{pf_id}/valuation")),
        ("GET signals", orso_client.get(f"/api/portfolios/{pf_id}/signals")),
        ("POST analyze", orso_client.post(f"/api/portfolios/{pf_id}/analyze")),
        ("PATCH portfolio", orso_client.patch(
            f"/api/portfolios/{pf_id}", json={"name": "hijacked"})),
        ("GET guru review", orso_client.get(f"/api/guru/reviews/{report_id}")),
        ("GET chat thread", orso_client.get(f"/api/guru/chat/threads/{thread_id}")),
        ("POST chat message", orso_client.post(
            f"/api/guru/chat/threads/{thread_id}/messages", json={"content": "steal this"})),
        ("PATCH orso fund", orso_client.patch(
            f"/api/orso/funds/{fund_id}", json={"name": "hijacked"})),
    ]

    for label, coro in cases:
        resp = await coro
        assert resp.status_code == 404, (
            f"{label}: expected 404 (owned-resource leak check), "
            f"got {resp.status_code}: {resp.text}"
        )
        assert resp.headers.get("content-type", "").startswith("text/event-stream") is False, (
            f"{label}: response opened a stream instead of 404ing pre-stream"
        )

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

    # Belt-and-braces: user B's own allocation must still be empty -- the
    # rejected PUT must not have partially applied against A's fund.
    own_alloc = await orso_client.get("/api/orso/allocation")
    assert own_alloc.json() == []
