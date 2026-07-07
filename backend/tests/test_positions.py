import pytest

from app.core.security import hash_password
from app.models.user import User

pytestmark = pytest.mark.asyncio(loop_scope="session")


async def _make_portfolio(auth_client, kind="real"):
    resp = await auth_client.post(
        "/api/portfolios", json={"name": "P", "kind": kind, "base_currency": "GBP"}
    )
    return resp.json()["id"]


async def test_position_crud(auth_client, make_instrument):
    await make_instrument("AAPL")
    pid = await _make_portfolio(auth_client)

    created = await auth_client.post(
        f"/api/portfolios/{pid}/positions",
        json={"symbol": "AAPL", "quantity": "10", "avg_cost": "150.25"},
    )
    assert created.status_code == 201
    pos_id = created.json()["id"]
    assert created.json()["symbol"] == "AAPL"

    patched = await auth_client.patch(f"/api/positions/{pos_id}", json={"quantity": "12"})
    assert patched.json()["quantity"] == "12.000000"

    listed = await auth_client.get(f"/api/portfolios/{pid}/positions")
    assert len(listed.json()) == 1

    assert (await auth_client.delete(f"/api/positions/{pos_id}")).status_code == 204


async def test_unknown_symbol_rejected(auth_client):
    pid = await _make_portfolio(auth_client)
    resp = await auth_client.post(
        f"/api/portfolios/{pid}/positions", json={"symbol": "NOPE", "quantity": "1"}
    )
    assert resp.status_code == 422


async def test_watchlist_entry_without_quantity(auth_client, make_instrument):
    await make_instrument("0700.HK", market="HK", currency="HKD")
    pid = await _make_portfolio(auth_client, kind="watchlist")
    resp = await auth_client.post(
        f"/api/portfolios/{pid}/positions", json={"symbol": "0700.HK"}
    )
    assert resp.status_code == 201
    assert resp.json()["quantity"] is None


async def test_other_users_position_is_404(auth_client, client, db_session, make_instrument):
    # auth_client's user creates a position
    await make_instrument("AAPL")
    pid = await _make_portfolio(auth_client)
    created = await auth_client.post(
        f"/api/portfolios/{pid}/positions",
        json={"symbol": "AAPL", "quantity": "10", "avg_cost": "150.25"},
    )
    pos_id = created.json()["id"]

    # a second user logs in on the same client
    other = User(email="other2@test.dev", password_hash=hash_password("pw123456"))
    db_session.add(other)
    await db_session.commit()
    login = await client.post(
        "/api/auth/login", json={"email": "other2@test.dev", "password": "pw123456"}
    )
    assert login.status_code == 204

    # user B cannot see or touch user A's position
    patch_resp = await client.patch(
        f"/api/positions/{pos_id}", json={"quantity": "1"}
    )
    assert patch_resp.status_code == 404
    delete_resp = await client.delete(f"/api/positions/{pos_id}")
    assert delete_resp.status_code == 404
