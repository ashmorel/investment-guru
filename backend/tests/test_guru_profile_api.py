import pytest

pytestmark = pytest.mark.asyncio(loop_scope="session")


async def test_profile_requires_auth(client):
    assert (await client.get("/api/guru/profile")).status_code == 401


async def test_profile_defaults_when_unset(auth_client):
    resp = await auth_client.get("/api/guru/profile")
    assert resp.status_code == 200
    assert resp.json() == {"risk_appetite": "balanced", "horizon": "medium",
                           "sector_interests": [], "free_text": ""}


async def test_profile_put_upserts_and_persists(auth_client):
    body = {"risk_appetite": "adventurous", "horizon": "long",
            "sector_interests": ["tech", "energy"], "free_text": "prefer dividends"}
    resp = await auth_client.put("/api/guru/profile", json=body)
    assert resp.status_code == 200 and resp.json() == body
    assert (await auth_client.get("/api/guru/profile")).json() == body
    body["horizon"] = "short"
    assert (await auth_client.put("/api/guru/profile", json=body)).json()["horizon"] == "short"


async def test_profile_rejects_invalid_enum(auth_client):
    resp = await auth_client.put("/api/guru/profile", json={
        "risk_appetite": "yolo", "horizon": "long", "sector_interests": [], "free_text": ""})
    assert resp.status_code == 422
