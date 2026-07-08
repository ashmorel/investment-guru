from datetime import UTC, date, datetime
from decimal import Decimal

import pytest

from app.api.signals import get_analyzer
from app.models import User
from app.services.market_data.base import Quote

pytestmark = pytest.mark.asyncio(loop_scope="session")


class FakeMarket:
    def __init__(self, quotes):
        self._q = quotes

    async def get_quotes(self, symbols):
        return {s: self._q[s] for s in symbols if s in self._q}

    async def get_fx_rate(self, base, quote):
        return Decimal("1")

    async def lookup(self, symbol):
        return None

    async def get_history(self, symbol, days=400):
        return []

    async def get_earnings_date(self, symbol):
        return date.today()


class FakeNews:
    async def get_news(self, symbol):
        return []


def _analyzer_with(market):
    from app.services.market_data.fundamentals import FundamentalsService
    from app.services.market_data.history import HistoryService
    from app.services.market_data.news import NewsService
    from app.services.market_data.quotes import QuoteService
    from app.services.signals.engine import SignalEngine
    from app.services.valuation import FxService

    qs = QuoteService(market)
    return SignalEngine(qs, FxService(market), HistoryService(market),
                        FundamentalsService(market), NewsService(FakeNews()), market)


async def _seed_portfolio(auth_client, db_session, make_instrument):
    await make_instrument("AAPL")
    pid = (await auth_client.post(
        "/api/portfolios", json={"name": "P", "kind": "real", "base_currency": "USD"}
    )).json()["id"]
    await auth_client.post(
        f"/api/portfolios/{pid}/positions",
        json={"symbol": "AAPL", "quantity": "10", "avg_cost": "100"},
    )
    return pid


async def test_analyze_then_read_and_attention(auth_client, db_session, make_instrument):
    pid = await _seed_portfolio(auth_client, db_session, make_instrument)
    market = FakeMarket({"AAPL": Quote("AAPL", Decimal("88"), "USD", Decimal("100"),
                                       datetime.now(UTC))})
    auth_client.app.dependency_overrides[get_analyzer] = lambda: _analyzer_with(market)

    resp = await auth_client.post(f"/api/portfolios/{pid}/analyze")
    assert resp.status_code == 200
    kinds = {s["kind"] for s in resp.json()["signals"]}
    assert "price_move_day" in kinds and "earnings_upcoming" in kinds

    read = await auth_client.get(f"/api/portfolios/{pid}/signals")
    assert read.status_code == 200
    assert len(read.json()["signals"]) == len(resp.json()["signals"])

    att = await auth_client.get("/api/dashboard/attention")
    assert att.status_code == 200
    sev = [s["severity"] for s in att.json()["signals"]]
    # high sorts before watch/info
    assert sev == sorted(sev, key=lambda x: {"high": 0, "watch": 1, "info": 2}[x])
    assert att.json()["signals"][0]["portfolio_name"] == "P"


async def test_analyze_requires_auth(client):
    assert (await client.post("/api/portfolios/1/analyze")).status_code == 401
    assert (await client.get("/api/dashboard/attention")).status_code == 401


async def test_other_users_portfolio_analyze_is_404(
    auth_client, client, db_session, make_instrument
):
    pid = await _seed_portfolio(auth_client, db_session, make_instrument)
    from app.core.security import hash_password
    other = User(email="other@test.dev", password_hash=hash_password("pw123456"))
    db_session.add(other)
    await db_session.commit()
    await client.post("/api/auth/login", json={"email": "other@test.dev", "password": "pw123456"})
    assert (await client.post(f"/api/portfolios/{pid}/analyze")).status_code == 404
    assert (await client.get(f"/api/portfolios/{pid}/signals")).status_code == 404
