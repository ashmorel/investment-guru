import pytest

from app.core.security import hash_password
from app.models.user import User

pytestmark = pytest.mark.asyncio(loop_scope="session")


async def test_valuation_endpoint_shape(auth_client, make_instrument):
    await make_instrument("AAPL")
    pid = (await auth_client.post(
        "/api/portfolios", json={"name": "P", "kind": "real", "base_currency": "GBP"}
    )).json()["id"]
    await auth_client.post(
        f"/api/portfolios/{pid}/positions",
        json={"symbol": "AAPL", "quantity": "10", "avg_cost": "100"},
    )
    resp = await auth_client.get(f"/api/portfolios/{pid}/valuation")
    assert resp.status_code == 200
    body = resp.json()
    # no quote cache + fake-less provider will fail network-free: positions unpriced
    assert body["base_currency"] == "GBP"
    assert len(body["positions"]) == 1


async def test_dashboard_endpoint(auth_client):
    await auth_client.post(
        "/api/portfolios", json={"name": "P1", "kind": "real", "base_currency": "GBP"}
    )
    resp = await auth_client.get("/api/dashboard")
    assert resp.status_code == 200
    assert len(resp.json()["portfolios"]) == 1


async def test_valuation_requires_auth(client):
    assert (await client.get("/api/portfolios/1/valuation")).status_code == 401
    assert (await client.get("/api/dashboard")).status_code == 401


async def test_other_users_portfolio_valuation_is_404(auth_client, client, db_session):
    created = await auth_client.post(
        "/api/portfolios", json={"name": "Mine", "kind": "real", "base_currency": "GBP"}
    )
    pid = created.json()["id"]

    other = User(email="other2@test.dev", password_hash=hash_password("pw123456"))
    db_session.add(other)
    await db_session.commit()
    login = await client.post(
        "/api/auth/login", json={"email": "other2@test.dev", "password": "pw123456"}
    )
    assert login.status_code == 204

    assert (await client.get(f"/api/portfolios/{pid}/valuation")).status_code == 404
