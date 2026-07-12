from datetime import UTC, datetime, timedelta

import pytest

from app.models import NewsItem
from app.services.market_data.news import NewsService

pytestmark = pytest.mark.asyncio(loop_scope="session")


class _NoFetchNews:
    """News provider that never returns anything — cache-only tests seed rows
    with fresh fetched_at so refresh() is a no-op skip; this guards against any
    accidental network call."""
    async def get_news(self, symbol):
        return []


def _override_news(client):
    from app.api.news import get_news_service
    client.app.dependency_overrides[get_news_service] = lambda: NewsService(_NoFetchNews())


async def _seed_news(db, instrument_id, titles, *, fresh=True):
    now = datetime.now(UTC).replace(tzinfo=None)
    for i, t in enumerate(titles):
        db.add(NewsItem(instrument_id=instrument_id, title=t, source="Yahoo",
                        url=f"http://x/{instrument_id}/{i}", published_at=now - timedelta(hours=i),
                        fetched_at=now))
    await db.commit()


async def _add_position(auth_client, symbol):
    pid = (await auth_client.post("/api/portfolios",
           json={"name": "P", "kind": "real", "base_currency": "GBP"})).json()["id"]
    await auth_client.post(f"/api/portfolios/{pid}/positions",
                           json={"symbol": symbol, "quantity": "1"})


async def test_get_news_groups_and_ranks(auth_client, db_session, make_instrument):
    _override_news(auth_client)
    a = await make_instrument("AAPL")
    b = await make_instrument("MSFT")
    await _add_position(auth_client, "AAPL")
    await _add_position(auth_client, "MSFT")
    await _seed_news(db_session, a.id, ["Apple beats", "Apple beats!", "Apple ships"])  # 2 dedup'd
    await _seed_news(db_session, b.id, ["MSFT cloud grows"])  # 1

    body = (await auth_client.get("/api/news")).json()
    syms = [g["symbol"] for g in body["groups"]]
    assert syms == ["AAPL", "MSFT"]                 # AAPL more headlines -> first
    aapl = body["groups"][0]
    assert len(aapl["items"]) == 2                  # deduped
    assert aapl["summary_available"] is False


async def test_get_stock_news_404_when_not_held(auth_client):
    _override_news(auth_client)
    r = await auth_client.get("/api/news/NVDA")
    assert r.status_code == 404


async def test_news_excludes_other_users(auth_client, client, db_session, make_instrument):
    _override_news(auth_client)
    a = await make_instrument("AAPL")
    await _add_position(auth_client, "AAPL")
    await _seed_news(db_session, a.id, ["Apple news"])
    # second user sees no groups
    from app.core.security import hash_password
    from app.models.user import User
    db_session.add(User(email="bnews@test.dev", password_hash=hash_password("pw123456")))
    await db_session.commit()
    _override_news(client)
    await client.post("/api/auth/login", json={"email": "bnews@test.dev", "password": "pw123456"})
    body = (await client.get("/api/news")).json()
    assert body["groups"] == []
