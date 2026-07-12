from datetime import UTC, datetime

import pytest

from app.models import GuruReport, User
from app.services.guru.schemas import NewsSummaryPayload

pytestmark = pytest.mark.asyncio(loop_scope="session")


async def test_news_summary_payload_schema():
    p = NewsSummaryPayload(summary="Up on earnings.", sentiment="positive",
                           key_points=["Beat estimates"], disclaimer="Not advice.")
    assert p.sentiment == "positive"
    with pytest.raises(Exception):  # noqa: B017 (brief specifies generic Exception verbatim)
        NewsSummaryPayload(summary="x", sentiment="bullish", key_points=[], disclaimer="d")


async def test_guru_report_accepts_instrument_id_and_news_kind(db_session, make_instrument):
    # Create a user for the FK
    u = User(email="news_test@test.dev", password_hash="x")
    db_session.add(u)
    await db_session.commit()

    inst = await make_instrument("AAPL")
    r = GuruReport(user_id=u.id, kind="news", instrument_id=inst.id,
                   payload={"summary": "s", "sentiment": "neutral", "key_points": [],
                            "disclaimer": "d"},
                   model="m", created_at=datetime.now(UTC).replace(tzinfo=None))
    db_session.add(r)
    await db_session.commit()
    await db_session.refresh(r)
    assert r.instrument_id == inst.id and r.kind == "news"
