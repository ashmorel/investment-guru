import pytest

pytestmark = pytest.mark.asyncio(loop_scope="session")


async def test_admin_ping_allowlisted(auth_client, monkeypatch):
    """Allowlisted user gets 200 on /api/admin/ping."""
    from app.core.config import settings

    monkeypatch.setattr(settings, "admin_emails", ["lee@test.dev"])
    resp = await auth_client.get("/api/admin/ping")
    assert resp.status_code == 200
    assert resp.json() == {"ok": True}


async def test_admin_ping_not_allowlisted(auth_client):
    """Non-allowlisted authed user gets 403."""
    resp = await auth_client.get("/api/admin/ping")
    assert resp.status_code == 403
    data = resp.json()
    assert "admin_only" in data.get("detail", "")


async def test_admin_ping_unauth(client):
    """Unauthed user gets 401."""
    resp = await client.get("/api/admin/ping")
    assert resp.status_code == 401
