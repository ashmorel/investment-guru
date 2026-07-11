import pytest

pytestmark = pytest.mark.asyncio(loop_scope="session")


async def test_profile_requires_auth(client):
    assert (await client.get("/api/guru/profile")).status_code == 401


async def test_profile_defaults_when_unset(auth_client):
    resp = await auth_client.get("/api/guru/profile")
    assert resp.status_code == 200
    assert resp.json() == {"risk_appetite": "balanced", "horizon": "medium",
                           "sector_interests": [], "free_text": "", "digest_enabled": False}


async def test_profile_put_upserts_and_persists(auth_client):
    body = {"risk_appetite": "adventurous", "horizon": "long",
            "sector_interests": ["tech", "energy"], "free_text": "prefer dividends",
            "digest_enabled": False}
    resp = await auth_client.put("/api/guru/profile", json=body)
    assert resp.status_code == 200 and resp.json() == body
    assert (await auth_client.get("/api/guru/profile")).json() == body
    body["horizon"] = "short"
    assert (await auth_client.put("/api/guru/profile", json=body)).json()["horizon"] == "short"


async def test_profile_rejects_invalid_enum(auth_client):
    resp = await auth_client.put("/api/guru/profile", json={
        "risk_appetite": "yolo", "horizon": "long", "sector_interests": [], "free_text": ""})
    assert resp.status_code == 422


async def test_profile_digest_enabled_defaults_false_and_omittable(auth_client):
    # digest_enabled has a Pydantic default, so a PUT that omits it entirely
    # must still persist false rather than erroring or leaving it unset.
    body = {"risk_appetite": "balanced", "horizon": "medium",
            "sector_interests": [], "free_text": ""}
    resp = await auth_client.put("/api/guru/profile", json=body)
    assert resp.status_code == 200
    assert resp.json()["digest_enabled"] is False


async def test_profile_put_digest_enabled_true_persists(auth_client):
    body = {"risk_appetite": "balanced", "horizon": "medium",
            "sector_interests": [], "free_text": "", "digest_enabled": True}
    resp = await auth_client.put("/api/guru/profile", json=body)
    assert resp.status_code == 200
    assert resp.json()["digest_enabled"] is True
    assert (await auth_client.get("/api/guru/profile")).json()["digest_enabled"] is True
