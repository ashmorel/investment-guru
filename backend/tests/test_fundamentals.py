from datetime import UTC, date, datetime

import pytest

from app.services.market_data.fundamentals import FundamentalsService, get_earnings_dates
from app.services.market_data.yahoo import parse_earnings_date

pytestmark = pytest.mark.asyncio(loop_scope="session")


def test_parse_earnings_from_timestamp():
    # 2026-08-20 00:00 UTC epoch
    ts = int(datetime(2026, 8, 20, tzinfo=UTC).timestamp())
    assert parse_earnings_date({"earningsTimestamp": ts}) == date(2026, 8, 20)


def test_parse_earnings_missing_returns_none():
    assert parse_earnings_date({}) is None


class FakeProvider:
    async def get_earnings_date(self, symbol):
        return date(2026, 8, 20) if symbol == "NVDA" else None

    async def get_quotes(self, symbols):
        return {}

    async def get_fx_rate(self, base, quote):
        raise NotImplementedError

    async def lookup(self, symbol):
        return None

    async def get_history(self, symbol, days=400):
        return []


async def test_fundamentals_refresh_and_read(db_session, make_instrument):
    inst = await make_instrument("NVDA")
    svc = FundamentalsService(FakeProvider())
    await svc.refresh(db_session, [inst])
    await db_session.commit()
    mapping = await get_earnings_dates(db_session, [inst.id])
    assert mapping[inst.id] == date(2026, 8, 20)
