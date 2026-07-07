from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from app.models import QuoteCache
from app.services.market_data.base import Quote
from app.services.market_data.quotes import QuoteService

pytestmark = pytest.mark.asyncio(loop_scope="session")


def _quote(symbol: str, price: str) -> Quote:
    return Quote(
        symbol=symbol, price=Decimal(price), currency="USD",
        previous_close=Decimal(price), as_of=datetime.now(UTC),
    )


class FakeProvider:
    def __init__(self, quotes: dict[str, Quote] | None = None, fail: bool = False):
        self.quotes = quotes or {}
        self.fail = fail
        self.calls = 0

    async def get_quotes(self, symbols):
        self.calls += 1
        if self.fail:
            raise RuntimeError("provider down")
        return {s: self.quotes[s] for s in symbols if s in self.quotes}

    async def get_fx_rate(self, base, quote):
        raise NotImplementedError

    async def lookup(self, symbol):
        return None


async def test_fresh_fetch_populates_cache(db_session):
    svc = QuoteService(FakeProvider({"AAPL": _quote("AAPL", "100")}))
    result = await svc.get_quotes(db_session, ["AAPL"])
    assert result["AAPL"].price == Decimal("100")
    assert await db_session.get(QuoteCache, "AAPL") is not None


async def test_cache_hit_skips_provider(db_session):
    db_session.add(QuoteCache(
        symbol="AAPL", price=Decimal("99"), currency="USD",
        previous_close=Decimal("98"), fetched_at=datetime.now(UTC).replace(tzinfo=None),
    ))
    await db_session.commit()
    provider = FakeProvider({"AAPL": _quote("AAPL", "100")})
    svc = QuoteService(provider)
    result = await svc.get_quotes(db_session, ["AAPL"])
    assert result["AAPL"].price == Decimal("99.0000")
    assert provider.calls == 0


async def test_provider_failure_serves_stale_cache(db_session):
    stale = datetime.now(UTC).replace(tzinfo=None) - timedelta(hours=6)
    db_session.add(QuoteCache(
        symbol="AAPL", price=Decimal("95"), currency="USD",
        previous_close=None, fetched_at=stale,
    ))
    await db_session.commit()
    svc = QuoteService(FakeProvider(fail=True))
    result = await svc.get_quotes(db_session, ["AAPL", "MSFT"])
    assert result["AAPL"].price == Decimal("95.0000")
    assert "MSFT" not in result
