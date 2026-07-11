import logging
from datetime import UTC, datetime
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.core.security import hash_password
from app.models import GuruReport, InvestorProfile, LlmUsage, User
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
from tests.conftest import TestSession, _test_services

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


async def _make_user(db_session, email: str, *, digest_enabled: bool = True) -> User:
    # digest_enabled defaults True here (not the model's own False default) so
    # existing single-user scheduler tests, written before opt-in existed,
    # keep exercising the scheduler without every call site having to opt in.
    user = User(email=email, password_hash=hash_password("pw123456"))
    db_session.add(user)
    await db_session.commit()
    await db_session.refresh(user)
    db_session.add(InvestorProfile(user_id=user.id, digest_enabled=digest_enabled))
    await db_session.commit()
    return user


async def test_digest_exists_today_respects_timezone(db_session):
    from app.services.guru.scheduler import digest_exists_today

    user_today = await _make_user(db_session, "today@test.dev")
    user_yesterday = await _make_user(db_session, "yesterday@test.dev")

    # 2026-07-08 is BST (UTC+1). London-local "today" starts at
    # 2026-07-07T23:00:00 UTC. A row at 23:30 UTC on 07-07 is already
    # 2026-07-08 00:30 local -> counts as today.
    db_session.add(GuruReport(
        user_id=user_today.id, kind="digest", portfolio_id=None,
        payload={}, model="x", created_at=datetime(2026, 7, 7, 23, 30, 0)))
    # A row at 22:00 UTC on 07-07 is still 2026-07-07 23:00 local -> yesterday.
    db_session.add(GuruReport(
        user_id=user_yesterday.id, kind="digest", portfolio_id=None,
        payload={}, model="x", created_at=datetime(2026, 7, 7, 22, 0, 0)))
    await db_session.commit()

    now = datetime(2026, 7, 8, 10, 0, 0, tzinfo=UTC)
    assert await digest_exists_today(db_session, user_today.id, now=now) is True
    assert await digest_exists_today(db_session, user_yesterday.id, now=now) is False


async def test_run_daily_job_generates_digest_then_take(db_session, fake_llm, monkeypatch):
    from app.services.guru import service as service_mod
    from app.services.guru.scheduler import run_daily_job

    monkeypatch.setattr(service_mod, "_service", GuruService(fake_llm, *_test_services()))

    await _make_user(db_session, "runner@test.dev")
    fake_llm.structured_queue.append(_digest())
    fake_llm.structured_queue.append(_take())

    await run_daily_job(session_factory=TestSession)

    rows = (await db_session.execute(select(GuruReport))).scalars().all()
    kinds = sorted(r.kind for r in rows)
    assert kinds == ["digest", "take"]


async def test_run_daily_job_skips_without_key(db_session, monkeypatch, caplog):
    from app.services.guru import service as service_mod
    from app.services.guru.scheduler import run_daily_job

    monkeypatch.setattr(service_mod, "_service", GuruService(None, *_test_services()))

    await _make_user(db_session, "nokey@test.dev")

    with caplog.at_level(logging.INFO, logger="app.services.guru.scheduler"):
        await run_daily_job(session_factory=TestSession)

    rows = (await db_session.execute(select(GuruReport))).scalars().all()
    assert rows == []
    assert "skipping" in caplog.text


async def test_run_daily_job_swallows_llm_failure(db_session, fake_llm, monkeypatch, caplog):
    from app.services.guru import service as service_mod
    from app.services.guru.scheduler import run_daily_job

    monkeypatch.setattr(service_mod, "_service", GuruService(fake_llm, *_test_services()))
    fake_llm.fail_structured = 1

    await _make_user(db_session, "failure@test.dev")

    with caplog.at_level(logging.INFO, logger="app.services.guru.scheduler"):
        await run_daily_job(session_factory=TestSession)

    rows = (await db_session.execute(select(GuruReport))).scalars().all()
    assert rows == []
    assert caplog.records  # the exception was logged, not raised


async def test_catch_up_runs_only_when_missing(db_session, fake_llm, monkeypatch):
    from app.services.guru import service as service_mod
    from app.services.guru.scheduler import catch_up

    monkeypatch.setattr(service_mod, "_service", GuruService(fake_llm, *_test_services()))

    user = await _make_user(db_session, "hasdigest@test.dev")
    now = datetime.now(UTC).replace(tzinfo=None)
    db_session.add(GuruReport(
        user_id=user.id, kind="digest", portfolio_id=None,
        payload={}, model="x", created_at=now))
    db_session.add(GuruReport(
        user_id=user.id, kind="take", portfolio_id=None,
        payload={}, model="x", created_at=now))
    await db_session.commit()

    await catch_up(session_factory=TestSession)

    assert fake_llm.calls == []


async def test_run_daily_job_take_failure_leaves_digest(db_session, fake_llm, monkeypatch, caplog):
    from app.services.guru import service as service_mod
    from app.services.guru.scheduler import run_daily_job

    monkeypatch.setattr(service_mod, "_service", GuruService(fake_llm, *_test_services()))

    await _make_user(db_session, "takefail@test.dev")
    # Only the digest payload is queued; the take call hits the empty-queue
    # assertion inside FakeLLMProvider, which run_daily_job's generic
    # except clause must swallow without raising.
    fake_llm.structured_queue.append(_digest())

    with caplog.at_level(logging.INFO, logger="app.services.guru.scheduler"):
        await run_daily_job(session_factory=TestSession)

    rows = (await db_session.execute(select(GuruReport))).scalars().all()
    kinds = sorted(r.kind for r in rows)
    assert kinds == ["digest"]
    assert caplog.records  # the take failure was logged, not raised


async def test_catch_up_regenerates_missing_take_only(db_session, fake_llm, monkeypatch):
    from app.services.guru import service as service_mod
    from app.services.guru.scheduler import catch_up

    monkeypatch.setattr(service_mod, "_service", GuruService(fake_llm, *_test_services()))

    user = await _make_user(db_session, "missingtake@test.dev")
    db_session.add(GuruReport(
        user_id=user.id, kind="digest", portfolio_id=None,
        payload={}, model="x", created_at=datetime.now(UTC).replace(tzinfo=None)))
    await db_session.commit()

    fake_llm.structured_queue.append(_take())

    await catch_up(session_factory=TestSession)

    rows = (await db_session.execute(select(GuruReport))).scalars().all()
    kinds = sorted(r.kind for r in rows)
    assert kinds == ["digest", "take"]
    assert len(fake_llm.calls) == 1


async def test_catch_up_take_failure_never_raises(db_session, fake_llm, monkeypatch, caplog):
    from app.services.guru import service as service_mod
    from app.services.guru.scheduler import catch_up

    monkeypatch.setattr(service_mod, "_service", GuruService(fake_llm, *_test_services()))
    fake_llm.fail_structured = 1

    user = await _make_user(db_session, "takefailcatchup@test.dev")
    db_session.add(GuruReport(
        user_id=user.id, kind="digest", portfolio_id=None,
        payload={}, model="x", created_at=datetime.now(UTC).replace(tzinfo=None)))
    await db_session.commit()

    with caplog.at_level(logging.INFO, logger="app.services.guru.scheduler"):
        await catch_up(session_factory=TestSession)

    rows = (await db_session.execute(select(GuruReport))).scalars().all()
    kinds = sorted(r.kind for r in rows)
    assert kinds == ["digest"]
    assert "guru scheduler: catch-up take failed" in caplog.text


async def test_catch_up_runs_full_job_when_digest_missing(db_session, fake_llm, monkeypatch):
    from app.services.guru import service as service_mod
    from app.services.guru.scheduler import catch_up

    monkeypatch.setattr(service_mod, "_service", GuruService(fake_llm, *_test_services()))

    await _make_user(db_session, "fullcatchup@test.dev")
    fake_llm.structured_queue.append(_digest())
    fake_llm.structured_queue.append(_take())

    await catch_up(session_factory=TestSession)

    assert len(fake_llm.calls) == 2
    rows = (await db_session.execute(select(GuruReport))).scalars().all()
    kinds = sorted(r.kind for r in rows)
    assert kinds == ["digest", "take"]


async def test_run_daily_job_only_generates_for_opted_in_user(db_session, fake_llm, monkeypatch):
    from app.services.guru import service as service_mod
    from app.services.guru.scheduler import run_daily_job

    monkeypatch.setattr(service_mod, "_service", GuruService(fake_llm, *_test_services()))

    opted_in = await _make_user(db_session, "optedin@test.dev", digest_enabled=True)
    await _make_user(db_session, "optedout@test.dev", digest_enabled=False)
    fake_llm.structured_queue.append(_digest())
    fake_llm.structured_queue.append(_take())

    await run_daily_job(session_factory=TestSession)

    rows = (await db_session.execute(select(GuruReport))).scalars().all()
    assert {r.user_id for r in rows} == {opted_in.id}
    assert sorted(r.kind for r in rows) == ["digest", "take"]


async def test_run_daily_job_skips_opted_in_user_over_budget(db_session, fake_llm, monkeypatch):
    from app.services.guru import service as service_mod
    from app.services.guru.scheduler import run_daily_job

    monkeypatch.setattr(service_mod, "_service", GuruService(fake_llm, *_test_services()))

    user = await _make_user(db_session, "overbudget@test.dev")
    db_session.add(LlmUsage(
        user_id=user.id, mode="chat", model="x", input_tokens=0, output_tokens=0,
        est_cost_usd=Decimal("5.00"), created_at=datetime.now(UTC).replace(tzinfo=None)))
    await db_session.commit()

    await run_daily_job(session_factory=TestSession)

    rows = (await db_session.execute(select(GuruReport))).scalars().all()
    assert rows == []
    assert fake_llm.calls == []  # check_budget raised before any LLM call


async def test_run_daily_job_one_user_failure_does_not_block_others(
    db_session, fake_llm, monkeypatch, caplog,
):
    from app.services.guru import service as service_mod
    from app.services.guru.scheduler import run_daily_job

    monkeypatch.setattr(service_mod, "_service", GuruService(fake_llm, *_test_services()))

    # Created (and therefore processed, by User.id order) first: its digest
    # call is the very first structured call, which fail_structured=1 fails.
    failing = await _make_user(db_session, "failing@test.dev")
    healthy = await _make_user(db_session, "healthy@test.dev")

    fake_llm.fail_structured = 1
    fake_llm.structured_queue.append(_digest())
    fake_llm.structured_queue.append(_take())

    with caplog.at_level(logging.INFO, logger="app.services.guru.scheduler"):
        await run_daily_job(session_factory=TestSession)

    rows = (await db_session.execute(select(GuruReport))).scalars().all()
    assert {r.user_id for r in rows} == {healthy.id}
    assert sorted(r.kind for r in rows) == ["digest", "take"]
    assert not (await db_session.execute(
        select(GuruReport).where(GuruReport.user_id == failing.id))).scalars().all()
    assert caplog.records  # the failing user's exception was logged, not raised
