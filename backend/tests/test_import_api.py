from pathlib import Path

import pytest

from app.api.instruments import get_provider
from app.core.security import hash_password
from app.models.user import User
from app.services.market_data.base import InstrumentInfo, infer_market

pytestmark = pytest.mark.asyncio(loop_scope="session")

FIXTURE = Path(__file__).parent / "fixtures" / "yahoo_portfolio_export.csv"


class AllKnownProvider:
    async def lookup(self, symbol):
        return InstrumentInfo(
            symbol=symbol, name=f"{symbol} Co", exchange="X",
            market=infer_market(symbol), currency="USD", sector=None, industry=None,
        )

    async def get_quotes(self, symbols):
        return {}

    async def get_fx_rate(self, base, quote):
        raise NotImplementedError


class SomeUnknownProvider(AllKnownProvider):
    def __init__(self, unknown: set[str]):
        self.unknown = unknown

    async def lookup(self, symbol):
        if symbol in self.unknown:
            return None
        return await super().lookup(symbol)


def _override(client, provider=None):
    client._transport.app.dependency_overrides[get_provider] = (
        lambda: provider or AllKnownProvider()
    )


async def test_preview_then_commit_new_portfolio(auth_client):
    _override(auth_client)
    preview = await auth_client.post(
        "/api/imports/preview",
        files={"file": ("pf.csv", FIXTURE.read_bytes(), "text/csv")},
    )
    assert preview.status_code == 200
    rows = preview.json()["rows"]
    assert len(rows) == 3
    assert all(r["known"] for r in rows)

    commit = await auth_client.post(
        "/api/imports/commit",
        json={
            "portfolio_id": None,
            "new_portfolio": {"name": "Imported", "kind": "real", "base_currency": "GBP"},
            "merge": "update",
            "rows": [
                {"symbol": "AAPL", "quantity": "10", "avg_cost": "150.25"},
                {"symbol": "HSBA.L", "quantity": "200", "avg_cost": "650.00"},
            ],
        },
    )
    assert commit.status_code == 200
    assert commit.json()["created"] == 2
    pid = commit.json()["portfolio_id"]
    positions = (await auth_client.get(f"/api/portfolios/{pid}/positions")).json()
    assert {p["symbol"] for p in positions} == {"AAPL", "HSBA.L"}


async def test_commit_merge_update_and_skip(auth_client, make_instrument):
    _override(auth_client)
    await make_instrument("AAPL")
    pid = (await auth_client.post(
        "/api/portfolios", json={"name": "P", "kind": "real", "base_currency": "GBP"}
    )).json()["id"]
    await auth_client.post(
        f"/api/portfolios/{pid}/positions",
        json={"symbol": "AAPL", "quantity": "5", "avg_cost": "90"},
    )

    resp = await auth_client.post(
        "/api/imports/commit",
        json={"portfolio_id": pid, "new_portfolio": None, "merge": "skip",
              "rows": [{"symbol": "AAPL", "quantity": "10", "avg_cost": "100"}]},
    )
    assert resp.json() == {"created": 0, "updated": 0, "skipped": 1, "portfolio_id": pid}

    resp = await auth_client.post(
        "/api/imports/commit",
        json={"portfolio_id": pid, "new_portfolio": None, "merge": "update",
              "rows": [{"symbol": "AAPL", "quantity": "10", "avg_cost": "100"}]},
    )
    assert resp.json()["updated"] == 1
    positions = (await auth_client.get(f"/api/portfolios/{pid}/positions")).json()
    assert positions[0]["quantity"] == "10.000000"


async def test_commit_merge_replace(auth_client, make_instrument):
    _override(auth_client)
    await make_instrument("AAPL")
    pid = (await auth_client.post(
        "/api/portfolios", json={"name": "R", "kind": "real", "base_currency": "GBP"}
    )).json()["id"]
    await auth_client.post(
        f"/api/portfolios/{pid}/positions",
        json={"symbol": "AAPL", "quantity": "5", "avg_cost": "90"},
    )

    resp = await auth_client.post(
        "/api/imports/commit",
        json={"portfolio_id": pid, "new_portfolio": None, "merge": "replace",
              "rows": [{"symbol": "AAPL", "quantity": "7", "avg_cost": "120"}]},
    )
    assert resp.status_code == 200
    assert resp.json()["updated"] == 1
    positions = (await auth_client.get(f"/api/portfolios/{pid}/positions")).json()
    assert len(positions) == 1
    assert positions[0]["quantity"] == "7.000000"
    assert positions[0]["avg_cost"] == "120.0000"


async def test_import_requires_auth(client):
    resp = await client.post(
        "/api/imports/commit",
        json={"portfolio_id": 1, "new_portfolio": None, "merge": "update", "rows": []},
    )
    assert resp.status_code == 401


async def test_commit_unknown_symbol_writes_nothing(auth_client):
    _override(auth_client, SomeUnknownProvider(unknown={"ZZZNOPE"}))
    pid = (await auth_client.post(
        "/api/portfolios", json={"name": "Atomic", "kind": "real", "base_currency": "GBP"}
    )).json()["id"]

    resp = await auth_client.post(
        "/api/imports/commit",
        json={"portfolio_id": pid, "new_portfolio": None, "merge": "update",
              "rows": [
                  {"symbol": "AAPL", "quantity": "10", "avg_cost": "150.25"},
                  {"symbol": "ZZZNOPE", "quantity": "1", "avg_cost": "1"},
              ]},
    )
    assert resp.status_code == 422

    # all-or-nothing: the known row must NOT have been written either
    positions = (await auth_client.get(f"/api/portfolios/{pid}/positions")).json()
    assert positions == []


async def test_commit_other_users_portfolio_is_404(auth_client, client, db_session):
    _override(auth_client)
    # auth_client's user creates a portfolio
    pid = (await auth_client.post(
        "/api/portfolios", json={"name": "Mine", "kind": "real", "base_currency": "GBP"}
    )).json()["id"]

    # a second user logs in on the same client
    other = User(email="other@test.dev", password_hash=hash_password("pw123456"))
    db_session.add(other)
    await db_session.commit()
    login = await client.post(
        "/api/auth/login", json={"email": "other@test.dev", "password": "pw123456"}
    )
    assert login.status_code == 204

    resp = await client.post(
        "/api/imports/commit",
        json={"portfolio_id": pid, "new_portfolio": None, "merge": "update",
              "rows": [{"symbol": "AAPL", "quantity": "10", "avg_cost": "150.25"}]},
    )
    assert resp.status_code == 404
