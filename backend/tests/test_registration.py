import pytest

pytestmark = pytest.mark.asyncio(loop_scope="session")


async def test_register_creates_user_and_logs_in(client):
    r = await client.post("/api/auth/register",
                          json={"email": "new@test.dev", "password": "goodpass1"})
    assert r.status_code == 204
    cookie = r.headers.get("set-cookie", "")
    assert "session=" in cookie
    me = await client.get("/api/auth/me")
    assert me.status_code == 200 and me.json()["email"] == "new@test.dev"
    assert me.json()["is_admin"] is False


async def test_register_duplicate_email_409(client):
    body = {"email": "dupe@test.dev", "password": "goodpass1"}
    assert (await client.post("/api/auth/register", json=body)).status_code == 204
    r = await client.post("/api/auth/register", json=body)
    assert r.status_code == 409 and r.json()["detail"] == "email_taken"


async def test_register_rejects_weak_password(client):
    r = await client.post("/api/auth/register",
                          json={"email": "weak@test.dev", "password": "short"})
    assert r.status_code == 422


async def test_register_rejects_bad_email(client):
    r = await client.post("/api/auth/register",
                          json={"email": "notanemail", "password": "goodpass1"})
    assert r.status_code == 422


async def test_me_is_admin_true_for_allowlisted(client, monkeypatch):
    from app.core.config import settings
    monkeypatch.setattr(settings, "admin_emails", ["boss@test.dev"])
    await client.post("/api/auth/register",
                      json={"email": "boss@test.dev", "password": "goodpass1"})
    assert (await client.get("/api/auth/me")).json()["is_admin"] is True
