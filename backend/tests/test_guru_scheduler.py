import logging
from datetime import UTC, datetime

import pytest
from sqlalchemy import select

from app.core.security import hash_password
from app.models import GuruReport, User
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


async def _make_user(db_session, email: str) -> User:
    user = User(email=email, password_hash=hash_password("pw123456"))
    db_session.add(user)
    await db_session.commit()
    await db_session.refresh(user)
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
    db_session.add(GuruReport(
        user_id=user.id, kind="digest", portfolio_id=None,
        payload={}, model="x", created_at=datetime.now(UTC).replace(tzinfo=None)))
    await db_session.commit()

    await catch_up(session_factory=TestSession)

    assert fake_llm.calls == []
