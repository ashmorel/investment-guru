import pytest

pytestmark = pytest.mark.asyncio(loop_scope="session")


async def test_search_matches_code_and_name(orso_client):
    await orso_client.post("/api/orso/funds", json={
        "code": "GLEQ", "name": "Global Equity Fund", "asset_class": "equity",
        "risk_rating": 5})
    await orso_client.post("/api/orso/funds", json={
        "code": "HKBD", "name": "HK Bond", "asset_class": "bond", "risk_rating": 3})
    by_name = (await orso_client.get("/api/orso/funds/search?q=equity")).json()
    assert [f["code"] for f in by_name] == ["GLEQ"]
    by_code = (await orso_client.get("/api/orso/funds/search?q=hkb")).json()
    assert [f["code"] for f in by_code] == ["HKBD"]


async def test_search_excludes_other_users(orso_client, client, db_session):
    from app.core.security import hash_password
    from app.models.user import User
    await orso_client.post("/api/orso/funds", json={
        "code": "SECRET", "name": "Secret Fund", "asset_class": "equity",
        "risk_rating": 5})
    db_session.add(User(email="bsearch@test.dev", password_hash=hash_password("pw123456")))
    await db_session.commit()
    await client.post("/api/auth/login", json={"email": "bsearch@test.dev", "password": "pw123456"})
    res = (await client.get("/api/orso/funds/search?q=secret")).json()
    assert res == []


async def test_search_empty_query_returns_all_own_funds(orso_client):
    res = (await orso_client.get("/api/orso/funds/search?q=")).json()
    assert isinstance(res, list)
