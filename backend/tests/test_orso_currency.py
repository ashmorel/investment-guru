import pytest

pytestmark = pytest.mark.asyncio(loop_scope="session")


async def test_create_fund_defaults_currency_hkd(orso_client):
    r = await orso_client.post("/api/orso/funds", json={
        "code": "GEQ", "name": "Global Equity", "asset_class": "equity", "risk_rating": 5,
    })
    assert r.status_code == 201
    assert r.json()["currency"] == "HKD"


async def test_create_fund_accepts_and_uppercases_currency(orso_client):
    r = await orso_client.post("/api/orso/funds", json={
        "code": "USB", "name": "US Bond", "asset_class": "bond", "risk_rating": 3,
        "currency": "usd",
    })
    assert r.status_code == 201
    assert r.json()["currency"] == "USD"


async def test_patch_fund_currency(orso_client):
    fid = (await orso_client.post("/api/orso/funds", json={
        "code": "MMF", "name": "Money Market", "asset_class": "cash", "risk_rating": 1,
    })).json()["id"]
    r = await orso_client.patch(f"/api/orso/funds/{fid}", json={"currency": "gbp"})
    assert r.status_code == 200 and r.json()["currency"] == "GBP"


async def test_invalid_currency_rejected(orso_client):
    r = await orso_client.post("/api/orso/funds", json={
        "code": "BAD", "name": "Bad", "asset_class": "equity", "risk_rating": 4,
        "currency": "US",
    })
    assert r.status_code == 422
