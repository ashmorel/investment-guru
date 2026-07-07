import pytest

from app.core.security import hash_password
from app.models.user import User

pytestmark = pytest.mark.asyncio(loop_scope="session")


async def test_create_list_update_delete_portfolio(auth_client):
    created = await auth_client.post(
        "/api/portfolios", json={"name": "Growth", "kind": "real", "base_currency": "GBP"}
    )
    assert created.status_code == 201
    pid = created.json()["id"]

    listed = await auth_client.get("/api/portfolios")
    assert [p["name"] for p in listed.json()] == ["Growth"]

    patched = await auth_client.patch(f"/api/portfolios/{pid}", json={"name": "Core Growth"})
    assert patched.json()["name"] == "Core Growth"

    deleted = await auth_client.delete(f"/api/portfolios/{pid}")
    assert deleted.status_code == 204
    assert (await auth_client.get("/api/portfolios")).json() == []


async def test_invalid_kind_rejected(auth_client):
    resp = await auth_client.post(
        "/api/portfolios", json={"name": "X", "kind": "maybe", "base_currency": "GBP"}
    )
    assert resp.status_code == 422


async def test_requires_auth(client):
    assert (await client.get("/api/portfolios")).status_code == 401


async def test_other_users_portfolio_is_404(auth_client, client, db_session):
    # auth_client's user creates a portfolio
    created = await auth_client.post(
        "/api/portfolios", json={"name": "Mine", "kind": "real", "base_currency": "GBP"}
    )
    pid = created.json()["id"]

    # a second user logs in on the same client
    other = User(email="other@test.dev", password_hash=hash_password("pw123456"))
    db_session.add(other)
    await db_session.commit()
    login = await client.post(
        "/api/auth/login", json={"email": "other@test.dev", "password": "pw123456"}
    )
    assert login.status_code == 204

    # user B cannot see or touch user A's portfolio
    assert (await client.get("/api/portfolios")).json() == []
    assert (await client.patch(f"/api/portfolios/{pid}", json={"name": "X"})).status_code == 404
    assert (await client.delete(f"/api/portfolios/{pid}")).status_code == 404
