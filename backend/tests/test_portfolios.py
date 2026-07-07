import pytest

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
