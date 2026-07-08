import pytest
from sqlalchemy import select

from app.core.security import hash_password
from app.models import LlmUsage, Portfolio
from app.models.user import User
from app.services.guru.persona import DISCLAIMER
from app.services.guru.schemas import DigestPayload, EarningsItem, MoverItem, NewsFlag
from app.services.guru.service import GuruService
from tests.conftest import _test_services

pytestmark = pytest.mark.asyncio(loop_scope="session")


def _digest():
    return DigestPayload(
        earnings_this_week=[EarningsItem(symbol="AAPL", date="2026-07-10", note="Q3 earnings")],
        movers=[MoverItem(symbol="MSFT", note="+3% on AI news")],
        news_flags=[NewsFlag(symbol="AAPL", headline="Supplier deal", comment="benign")],
        summary="Quiet week overall, nothing to act on.",
        disclaimer=DISCLAIMER,
    )


async def test_thread_crud_and_ownership(guru_client, client, db_session):
    created = await guru_client.post("/api/guru/chat/threads", json={"title": "Ideas"})
    assert created.status_code == 201
    thread = created.json()
    assert thread["title"] == "Ideas"
    assert thread["portfolio_id"] is None
    assert "created_at" in thread

    listed = (await guru_client.get("/api/guru/chat/threads")).json()
    assert listed["threads"][0]["id"] == thread["id"]

    detail = await guru_client.get(f"/api/guru/chat/threads/{thread['id']}")
    assert detail.status_code == 200
    assert detail.json()["messages"] == []

    # a second user logs in on the same underlying client
    other = User(email="other-chat@test.dev", password_hash=hash_password("pw123456"))
    db_session.add(other)
    await db_session.commit()
    login = await client.post(
        "/api/auth/login", json={"email": "other-chat@test.dev", "password": "pw123456"}
    )
    assert login.status_code == 204

    resp = await client.get(f"/api/guru/chat/threads/{thread['id']}")
    assert resp.status_code == 404


async def test_chat_turn_streams_and_persists(guru_client, db_session):
    t = (await guru_client.post("/api/guru/chat/threads", json={"title": "T"})).json()
    guru_client.fake_llm.stream_chunks = ["Buy ", "low."]
    async with guru_client.stream(
        "POST", f"/api/guru/chat/threads/{t['id']}/messages",
        json={"content": "thoughts?"},
    ) as resp:
        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("text/event-stream")
        body = "".join([chunk async for chunk in resp.aiter_text()])
    assert "Buy " in body and "event: done" in body

    detail = (await guru_client.get(f"/api/guru/chat/threads/{t['id']}")).json()
    roles = [m["role"] for m in detail["messages"]]
    assert roles == ["user", "assistant"]
    assert detail["messages"][0]["content"] == "thoughts?"
    assert detail["messages"][1]["content"] == "Buy low."

    rows = (await db_session.execute(
        select(LlmUsage).where(LlmUsage.thread_id == t["id"])
    )).scalars().all()
    assert len(rows) == 1
    assert rows[0].mode == "chat"
    assert rows[0].input_tokens == 100
    assert rows[0].output_tokens == 50

    # context (profile) + the new user turn reached the provider
    call = guru_client.fake_llm.calls[-1]
    assert call["kind"] == "stream"
    assert call["messages"][-1]["role"] == "user"
    assert "thoughts?" in call["messages"][-1]["content"]
    assert '"profile"' in call["messages"][-1]["content"]


async def test_chat_turn_includes_prior_history(guru_client):
    t = (await guru_client.post("/api/guru/chat/threads", json={"title": "T"})).json()
    guru_client.fake_llm.stream_chunks = ["ok"]
    async with guru_client.stream(
        "POST", f"/api/guru/chat/threads/{t['id']}/messages", json={"content": "first"},
    ) as resp:
        [_ async for _ in resp.aiter_text()]

    guru_client.fake_llm.stream_chunks = ["ok2"]
    async with guru_client.stream(
        "POST", f"/api/guru/chat/threads/{t['id']}/messages", json={"content": "second"},
    ) as resp:
        [_ async for _ in resp.aiter_text()]

    call = guru_client.fake_llm.calls[-1]
    contents = [m["content"] for m in call["messages"]]
    assert contents[-1] == "second"
    assert "first" in contents[0]  # context prepended to the FIRST user turn
    assert "ok" in contents[1]  # prior assistant turn carried forward


async def test_chat_thread_scoped_to_portfolio_uses_its_context(guru_client, make_instrument):
    await make_instrument("AAPL")
    pf_id = (await guru_client.post(
        "/api/portfolios", json={"name": "Growth", "kind": "real", "base_currency": "USD"}
    )).json()["id"]
    await guru_client.post(
        f"/api/portfolios/{pf_id}/positions",
        json={"symbol": "AAPL", "quantity": "10", "avg_cost": "100"},
    )

    t = (await guru_client.post(
        "/api/guru/chat/threads", json={"title": "T", "portfolio_id": pf_id}
    )).json()
    assert t["portfolio_id"] == pf_id

    guru_client.fake_llm.stream_chunks = ["noted"]
    async with guru_client.stream(
        "POST", f"/api/guru/chat/threads/{t['id']}/messages",
        json={"content": "how's this book doing?"},
    ) as resp:
        [_ async for _ in resp.aiter_text()]

    call = guru_client.fake_llm.calls[-1]
    first_user_content = call["messages"][0]["content"]
    assert '"Growth"' in first_user_content  # scoped to the thread's portfolio only


async def test_chat_thread_with_foreign_portfolio_404(guru_client, db_session):
    # Seed a portfolio owned by a different user directly via the DB, so guru_client's
    # session (logged in as "lee") is left untouched — unlike the client fixture, which
    # shares its cookie jar across every user login in a test.
    other = User(email="other-pf@test.dev", password_hash=hash_password("pw123456"))
    db_session.add(other)
    await db_session.flush()
    other_pf = Portfolio(user_id=other.id, name="Theirs", kind="real", base_currency="USD")
    db_session.add(other_pf)
    await db_session.commit()

    resp = await guru_client.post(
        "/api/guru/chat/threads", json={"title": "T", "portfolio_id": other_pf.id}
    )
    assert resp.status_code == 404


async def test_chat_stream_failure_keeps_user_message_only(guru_client):
    t = (await guru_client.post("/api/guru/chat/threads", json={"title": "T"})).json()
    guru_client.fake_llm.fail_stream = True
    async with guru_client.stream(
        "POST", f"/api/guru/chat/threads/{t['id']}/messages",
        json={"content": "thoughts?"},
    ) as resp:
        body = "".join([chunk async for chunk in resp.aiter_text()])
    assert "event: error" in body

    detail = (await guru_client.get(f"/api/guru/chat/threads/{t['id']}")).json()
    roles = [m["role"] for m in detail["messages"]]
    assert roles == ["user"]


async def test_chat_unconfigured_503(auth_client):
    from app.api.guru import get_guru

    svc = GuruService(None, *(_test_services()))
    auth_client.app.dependency_overrides[get_guru] = lambda: svc

    created = await auth_client.post("/api/guru/chat/threads", json={"title": "T"})
    assert created.status_code == 201
    t = created.json()

    resp = await auth_client.post(
        f"/api/guru/chat/threads/{t['id']}/messages", json={"content": "hi"}
    )
    assert resp.status_code == 503
    assert resp.json()["detail"] == "llm_unconfigured"


async def test_usage_summary_aggregates(guru_client):
    guru_client.fake_llm.structured_queue.append(_digest())
    resp = await guru_client.post("/api/guru/digest")
    assert resp.status_code == 201

    summary = (await guru_client.get("/api/guru/usage/summary")).json()
    by_mode = {row["mode"]: row for row in summary["by_mode"]}
    assert by_mode["digest"]["calls"] == 1
    assert by_mode["digest"]["input_tokens"] == 100
    assert by_mode["digest"]["output_tokens"] == 50
    assert by_mode["digest"]["est_cost_usd"] is not None
    assert summary["total_cost_30d"] is not None
