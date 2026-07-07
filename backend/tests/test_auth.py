import pytest

from app.core.security import hash_password
from app.models.user import User

pytestmark = pytest.mark.asyncio(loop_scope="session")


async def _seed_user(db_session, email="lee@test.dev", password="pw123456"):
    user = User(email=email, password_hash=hash_password(password))
    db_session.add(user)
    await db_session.commit()
    return user


async def test_login_sets_cookie_and_me_works(client, db_session):
    await _seed_user(db_session)
    resp = await client.post(
        "/api/auth/login",
        json={"email": "lee@test.dev", "password": "pw123456"},
    )
    assert resp.status_code == 204
    assert "session" in resp.cookies

    me = await client.get("/api/auth/me")
    assert me.status_code == 200
    assert me.json()["email"] == "lee@test.dev"


async def test_bad_password_rejected(client, db_session):
    await _seed_user(db_session)
    resp = await client.post(
        "/api/auth/login",
        json={"email": "lee@test.dev", "password": "wrong"},
    )
    assert resp.status_code == 401


async def test_me_requires_auth(client):
    resp = await client.get("/api/auth/me")
    assert resp.status_code == 401
