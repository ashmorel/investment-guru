from datetime import UTC, datetime
from decimal import Decimal

import pytest

from app.models import ChatMessage, ChatThread, GuruReport, InvestorProfile, LlmUsage, User

pytestmark = pytest.mark.asyncio(loop_scope="session")


async def _user(db_session) -> User:
    u = User(email="guru@test.dev", password_hash="x")
    db_session.add(u)
    await db_session.commit()
    return u


async def test_guru_tables_roundtrip(db_session):
    u = await _user(db_session)
    now = datetime.now(UTC).replace(tzinfo=None)
    db_session.add(InvestorProfile(user_id=u.id, risk_appetite="balanced",
                                   horizon="long", sector_interests=["tech"], free_text="hi"))
    report = GuruReport(user_id=u.id, kind="digest", portfolio_id=None,
                        payload={"summary": "s"}, model="claude-haiku-4-5", created_at=now)
    db_session.add(report)
    thread = ChatThread(user_id=u.id, title="t", portfolio_id=None, seed_context=None)
    db_session.add(thread)
    await db_session.commit()
    db_session.add(ChatMessage(thread_id=thread.id, role="user", content="hello", created_at=now))
    db_session.add(LlmUsage(user_id=u.id, mode="digest", model="claude-haiku-4-5",
                            input_tokens=100, output_tokens=50,
                            est_cost_usd=Decimal("0.0004"), report_id=report.id, created_at=now))
    await db_session.commit()
    assert report.id and thread.id


async def test_investor_profile_unique_per_user(db_session):
    u = await _user(db_session)
    db_session.add(InvestorProfile(user_id=u.id))
    await db_session.commit()
    db_session.add(InvestorProfile(user_id=u.id))
    with pytest.raises(Exception):  # noqa: B017 (brief specifies generic Exception verbatim)
        await db_session.commit()
