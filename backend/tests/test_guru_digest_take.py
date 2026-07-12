import pytest
from sqlalchemy import select

from app.models import GuruReport, LlmUsage
from app.services.guru.persona import DISCLAIMER
from app.services.guru.schemas import (
    DigestPayload,
    EarningsItem,
    IdeaItem,
    MoverItem,
    NewsFlag,
    RiskItem,
    TakePayload,
)
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


def _take():
    return TakePayload(
        commentary="Portfolio steady this week; no material changes needed.",
        risks=[RiskItem(kind="concentration", note="tech heavy")],
        ideas=[IdeaItem(symbol="AAPL", action="hold", conviction="med", rationale="steady")],
        disclaimer=DISCLAIMER,
    )


async def test_digest_generates_with_scan_model(guru_client, db_session):
    guru_client.fake_llm.structured_queue.append(_digest())
    guru_client.fake_llm.structured_queue.append(_take())  # create_digest also refreshes the take

    resp = await guru_client.post("/api/guru/digest")
    assert resp.status_code == 201
    body = resp.json()
    assert body["kind"] == "digest"
    assert body["portfolio_id"] is None
    assert body["model"] == "test-scan"
    assert guru_client.fake_llm.calls[0]["model"] == "test-scan"

    latest = await guru_client.get("/api/guru/digest/latest")
    assert latest.status_code == 200
    assert latest.json()["id"] == body["id"]

    rows = (await db_session.execute(
        select(LlmUsage).where(LlmUsage.report_id == body["id"])
    )).scalars().all()
    assert len(rows) == 1
    assert rows[0].mode == "digest"
    assert rows[0].model == "test-scan"


async def test_take_uses_advice_model_and_sees_latest_digest(guru_client, db_session):
    digest = _digest()
    guru_client.fake_llm.structured_queue.append(digest)
    guru_client.fake_llm.structured_queue.append(_take())  # create_digest also refreshes the take
    digest_resp = await guru_client.post("/api/guru/digest")
    assert digest_resp.status_code == 201

    digest_call = guru_client.fake_llm.calls[0]
    assert digest_call["max_tokens"] == 2048
    take_via_digest_call = guru_client.fake_llm.calls[1]
    assert take_via_digest_call["max_tokens"] == 4096

    guru_client.fake_llm.structured_queue.append(_take())
    resp = await guru_client.post("/api/guru/take")
    assert resp.status_code == 201
    body = resp.json()
    assert body["kind"] == "take"
    assert body["portfolio_id"] is None
    assert body["model"] == "test-advice"

    take_call = guru_client.fake_llm.calls[-1]
    assert take_call["model"] == "test-advice"
    assert take_call["max_tokens"] == 4096
    user_message = take_call["messages"][0]["content"]
    assert digest.summary in user_message  # context handoff from latest digest

    latest = await guru_client.get("/api/guru/take/latest")
    assert latest.status_code == 200
    assert latest.json()["id"] == body["id"]

    rows = (await db_session.execute(
        select(LlmUsage).where(LlmUsage.report_id == body["id"])
    )).scalars().all()
    assert len(rows) == 1
    assert rows[0].mode == "take"


async def test_take_without_prior_digest_still_succeeds(guru_client):
    guru_client.fake_llm.structured_queue.append(_take())
    resp = await guru_client.post("/api/guru/take")
    assert resp.status_code == 201
    assert resp.json()["kind"] == "take"


async def test_latest_404_when_none(guru_client):
    assert (await guru_client.get("/api/guru/digest/latest")).status_code == 404
    assert (await guru_client.get("/api/guru/digest/latest")).json()["detail"] == "Not found"
    assert (await guru_client.get("/api/guru/take/latest")).status_code == 404


async def test_digest_provider_failure_502_nothing_persisted(guru_client, db_session):
    guru_client.fake_llm.fail_structured = 1  # digest makes exactly one call, no retry

    resp = await guru_client.post("/api/guru/digest")
    assert resp.status_code == 502
    assert resp.json()["detail"] == "llm_error"

    assert (await guru_client.get("/api/guru/digest/latest")).status_code == 404
    assert (await db_session.execute(select(GuruReport))).scalars().all() == []
    assert (await db_session.execute(select(LlmUsage))).scalars().all() == []


async def test_take_provider_failure_502_nothing_persisted(guru_client, db_session):
    guru_client.fake_llm.fail_structured = 1  # take makes exactly one call, no retry

    resp = await guru_client.post("/api/guru/take")
    assert resp.status_code == 502
    assert resp.json()["detail"] == "llm_error"

    assert (await guru_client.get("/api/guru/take/latest")).status_code == 404
    assert (await db_session.execute(select(GuruReport))).scalars().all() == []
    assert (await db_session.execute(select(LlmUsage))).scalars().all() == []


async def test_digest_unconfigured_503(auth_client):
    from app.api.guru import get_guru

    svc = GuruService(None, *(_test_services()),
                      advice_model="test-advice", scan_model="test-scan")
    auth_client.app.dependency_overrides[get_guru] = lambda: svc

    resp = await auth_client.post("/api/guru/digest")
    assert resp.status_code == 503
    assert resp.json()["detail"] == "llm_unconfigured"


async def test_digest_http_409_when_generation_locked(guru_client):
    guru_client.fake_llm.structured_queue.append(_digest())

    lock = guru_client.guru_service._lock("digest")
    async with lock:
        resp = await guru_client.post("/api/guru/digest")
        assert resp.status_code == 409
        assert resp.json()["detail"] == "generation_in_progress"


async def test_manual_digest_also_refreshes_the_take(guru_client, db_session):
    guru_client.fake_llm.structured_queue.append(_digest())
    guru_client.fake_llm.structured_queue.append(_take())

    resp = await guru_client.post("/api/guru/digest")
    assert resp.status_code == 201
    assert resp.json()["kind"] == "digest"

    rows = (await db_session.execute(select(GuruReport))).scalars().all()
    kinds = sorted(r.kind for r in rows)
    assert kinds == ["digest", "take"]

    latest_take = await guru_client.get("/api/guru/take/latest")
    assert latest_take.status_code == 200


async def test_manual_digest_survives_take_failure(guru_client, db_session, monkeypatch):
    from app.services.guru.llm.base import LLMError

    guru_client.fake_llm.structured_queue.append(_digest())

    # Digest succeeds normally; the take call is forced to fail so we can assert
    # create_digest's try/except around generate_take doesn't fail the response.
    async def _fail_take(db, user):
        raise LLMError("injected take failure")

    monkeypatch.setattr(guru_client.guru_service, "generate_take", _fail_take)

    resp = await guru_client.post("/api/guru/digest")
    assert resp.status_code == 201
    body = resp.json()
    assert body["kind"] == "digest"

    rows = (await db_session.execute(select(GuruReport))).scalars().all()
    kinds = sorted(r.kind for r in rows)
    assert kinds == ["digest"]

    assert (await guru_client.get("/api/guru/take/latest")).status_code == 404


async def test_manual_digest_survives_take_budget_exhausted(guru_client, db_session, monkeypatch):
    from app.services.guru.budget import BudgetExhausted

    guru_client.fake_llm.structured_queue.append(_digest())

    # Digest succeeds normally; the take call is forced to hit the daily budget
    # cap (as could genuinely happen if the digest call itself pushed the user
    # over the cap) so we can assert create_digest's try/except around
    # generate_take treats this the same as any other take-refresh failure.
    async def _budget_exhausted_take(db, user):
        raise BudgetExhausted("cap hit")

    monkeypatch.setattr(guru_client.guru_service, "generate_take", _budget_exhausted_take)

    resp = await guru_client.post("/api/guru/digest")
    assert resp.status_code == 201
    body = resp.json()
    assert body["kind"] == "digest"

    rows = (await db_session.execute(select(GuruReport))).scalars().all()
    kinds = sorted(r.kind for r in rows)
    assert kinds == ["digest"]

    assert (await guru_client.get("/api/guru/take/latest")).status_code == 404
