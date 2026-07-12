from datetime import UTC, datetime

import pytest

from app.core.security import hash_password
from app.models import GuruReport
from app.models.user import User

pytestmark = pytest.mark.asyncio(loop_scope="session")


def _now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def _fake_rotation_payload() -> dict:
    return {"market_view": "neutral rotation into defensives", "moves": []}


async def _fake_generate_rotation(db, user):
    report = GuruReport(user_id=user.id, kind="rotation", portfolio_id=None,
                        payload=_fake_rotation_payload(), model="test-advice",
                        created_at=_now())
    db.add(report)
    await db.flush()
    await db.commit()
    await db.refresh(report)
    return report


async def _login_as_user_b(client, db_session) -> None:
    other = User(email="rot-b@test.dev", password_hash=hash_password("pw123456"))
    db_session.add(other)
    await db_session.commit()
    resp = await client.post(
        "/api/auth/login", json={"email": "rot-b@test.dev", "password": "pw123456"}
    )
    assert resp.status_code == 204


async def test_rotation_get_null_before_any(auth_client):
    resp = await auth_client.get("/api/groups/rotation")
    assert resp.status_code == 200
    assert resp.json() is None


async def test_rotation_post_generates_then_get_returns_it(guru_client, monkeypatch):
    monkeypatch.setattr(guru_client.guru_service, "generate_rotation", _fake_generate_rotation)

    posted = await guru_client.post("/api/groups/rotation")
    assert posted.status_code == 201
    body = posted.json()
    assert body["kind"] == "rotation"
    assert body["payload"]["market_view"]

    got = await guru_client.get("/api/groups/rotation")
    assert got.status_code == 200
    assert got.json()["id"] == body["id"]
    assert got.json()["payload"]["market_view"]


async def test_rotation_requires_auth(client):
    assert (await client.post("/api/groups/rotation")).status_code == 401
    assert (await client.get("/api/groups/rotation")).status_code == 401


async def test_rotation_cross_user_isolation(guru_client, db_session, monkeypatch):
    monkeypatch.setattr(guru_client.guru_service, "generate_rotation", _fake_generate_rotation)

    posted = await guru_client.post("/api/groups/rotation")
    assert posted.status_code == 201

    await _login_as_user_b(guru_client, db_session)
    got = await guru_client.get("/api/groups/rotation")
    assert got.status_code == 200
    assert got.json() is None
