import pytest
from sqlalchemy import select

from app.api.instruments import get_provider
from app.models import Instrument
from app.services.market_data.base import InstrumentInfo

pytestmark = pytest.mark.asyncio(loop_scope="session")

TENCENT = InstrumentInfo(
    symbol="0700.HK", name="Tencent Holdings", exchange="HKG",
    market="HK", currency="HKD", sector="Communication Services", industry="Internet",
)

APPLE = InstrumentInfo(
    symbol="AAPL", name="Apple Inc.", exchange="NMS",
    market="US", currency="USD", sector="Technology", industry="Consumer Electronics",
)


class FakeLookupProvider:
    async def lookup(self, symbol):
        # only matches the exact uppercased symbol — proves the endpoint
        # normalises the query before it ever reaches the provider.
        if symbol == "0700.HK":
            return TENCENT
        if symbol == "AAPL":
            return APPLE
        return None

    async def get_quotes(self, symbols):
        return {}

    async def get_fx_rate(self, base, quote):
        raise NotImplementedError


def _override(client):
    # client fixture exposes the app via its transport
    app = client._transport.app  # httpx.ASGITransport
    app.dependency_overrides[get_provider] = lambda: FakeLookupProvider()


async def test_lookup_creates_instrument(auth_client, db_session):
    _override(auth_client)
    resp = await auth_client.get("/api/instruments/lookup", params={"symbol": "0700.HK"})
    assert resp.status_code == 200
    assert resp.json()["market"] == "HK"
    row = (
        await db_session.execute(select(Instrument).where(Instrument.symbol == "0700.HK"))
    ).scalar_one()
    assert row.name == "Tencent Holdings"


async def test_lookup_unknown_404(auth_client):
    _override(auth_client)
    resp = await auth_client.get("/api/instruments/lookup", params={"symbol": "NOPE"})
    assert resp.status_code == 404


async def test_lookup_normalises_symbol_case(auth_client, db_session):
    _override(auth_client)
    resp = await auth_client.get("/api/instruments/lookup", params={"symbol": "aapl"})
    assert resp.status_code == 200
    assert resp.json()["symbol"] == "AAPL"
    row = (
        await db_session.execute(select(Instrument).where(Instrument.symbol == "AAPL"))
    ).scalar_one()
    assert row.symbol == "AAPL"


async def test_lookup_requires_auth(client):
    resp = await client.get("/api/instruments/lookup", params={"symbol": "AAPL"})
    assert resp.status_code == 401
