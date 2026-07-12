from datetime import UTC, datetime

import pytest

from app.models import GuruReport, User
from app.services.guru.schemas import NewsSummaryPayload
from app.services.guru.schemas import NewsSummaryPayload as _NSP

pytestmark = pytest.mark.asyncio(loop_scope="session")


class _NoFetchNews:
    """News provider that never returns anything -- guards against any
    accidental network call from the summary routes under test."""
    async def get_news(self, symbol):
        return []


def _override_news(client):
    from app.api.news import get_news_service
    from app.services.market_data.news import NewsService
    client.app.dependency_overrides[get_news_service] = lambda: NewsService(_NoFetchNews())


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


async def _hold_with_news(auth_client, db_session, make_instrument, symbol):
    from app.models import NewsItem
    _override_news(auth_client)
    inst = await make_instrument(symbol)
    pid = (await auth_client.post("/api/portfolios",
           json={"name": "P", "kind": "real", "base_currency": "GBP"})).json()["id"]
    await auth_client.post(f"/api/portfolios/{pid}/positions",
                           json={"symbol": symbol, "quantity": "1"})
    now = datetime.now(UTC).replace(tzinfo=None)
    db_session.add(NewsItem(instrument_id=inst.id, title=f"{symbol} up", source="Yahoo",
                            url=f"http://x/{symbol}", published_at=now, fetched_at=now))
    await db_session.commit()
    return inst


async def test_generate_and_get_summary(guru_client, db_session, make_instrument):
    guru_client.fake_llm.structured_queue.append(_NSP(
        summary="Strong quarter.", sentiment="positive", key_points=["Beat"],
        disclaimer="Not advice."))
    await _hold_with_news(guru_client, db_session, make_instrument, "AAPL")
    r = await guru_client.post("/api/news/AAPL/summary")
    assert r.status_code == 201
    assert r.json()["payload"]["sentiment"] == "positive"
    got = await guru_client.get("/api/news/AAPL/summary")
    assert got.status_code == 200 and got.json()["payload"]["summary"] == "Strong quarter."
    # the run used the SCAN model (cheap)
    assert guru_client.fake_llm.calls[-1]["model"] == guru_client.guru_service.scan_model


async def test_summary_422_when_no_headlines(guru_client, db_session, make_instrument):
    _override_news(guru_client)
    await make_instrument("MSFT")
    pid = (await guru_client.post("/api/portfolios",
           json={"name": "P", "kind": "real", "base_currency": "GBP"})).json()["id"]
    await guru_client.post(f"/api/portfolios/{pid}/positions",
                           json={"symbol": "MSFT", "quantity": "1"})
    r = await guru_client.post("/api/news/MSFT/summary")
    assert r.status_code == 422


async def test_summary_budget_exhausted_429(guru_client, db_session, make_instrument, monkeypatch):
    await _hold_with_news(guru_client, db_session, make_instrument, "AAPL")
    async def over(db, user_id, *, now=None):
        from app.services.guru.budget import BudgetExhausted
        raise BudgetExhausted()
    monkeypatch.setattr("app.services.guru.service.check_budget", over)
    r = await guru_client.post("/api/news/AAPL/summary")
    assert r.status_code == 429 and r.json()["detail"] == "budget_exhausted"


async def test_summary_404_when_not_held(guru_client):
    r = await guru_client.post("/api/news/TSLA/summary")
    assert r.status_code == 404
