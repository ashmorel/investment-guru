import pytest

pytestmark = pytest.mark.asyncio(loop_scope="session")


@pytest.fixture(autouse=True)
def _isolated_register_throttle(monkeypatch):
    # Mirrors test_hardening.py's login_throttle isolation: all requests made
    # through the `client`/`auth_client` fixtures report the same client IP
    # (127.0.0.1, from httpx's ASGITransport), so tests sharing the
    # module-level register_throttle singleton would bleed failure counts
    # into each other. Give every test in this module a fresh throttle.
    from app.core import hardening
    fresh = hardening.LoginThrottle(max_failures=10, lockout_seconds=60.0)
    monkeypatch.setattr(hardening, "register_throttle", fresh)
    monkeypatch.setattr("app.api.auth.register_throttle", fresh)


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


async def test_register_duplicate_email_409_via_precheck(client, db_session):
    # Deterministic fast path: a row already exists before the request is
    # even made, so the pre-check SELECT catches it and returns 409.
    from app.core.security import hash_password
    from app.models.user import User
    db_session.add(User(email="precheck@test.dev", password_hash=hash_password("existingpw1")))
    await db_session.commit()

    r = await client.post("/api/auth/register",
                          json={"email": "precheck@test.dev", "password": "goodpass1"})
    assert r.status_code == 409 and r.json()["detail"] == "email_taken"


async def test_register_commit_race_integrity_error_maps_to_409():
    # Simulates the TOCTOU race: the pre-check SELECT reports no match (the
    # concurrent winner hasn't committed yet from this request's point of
    # view), but the INSERT still collides at commit time on the unique
    # email constraint. The handler must map that IntegrityError to 409,
    # not let it bubble up as an unhandled 500. Exercised directly against
    # the route function with a fake session so the race is deterministic
    # rather than depending on real concurrent DB timing.
    from fastapi import HTTPException, Response
    from sqlalchemy.exc import IntegrityError
    from starlette.requests import Request as StarletteRequest

    from app.api.auth import RegisterIn, register

    class _EmptyResult:
        def scalar_one_or_none(self):
            return None

    class _RaceSession:
        def __init__(self):
            self.rolled_back = False

        async def execute(self, stmt):
            # The pre-check select finds nothing — the race window.
            return _EmptyResult()

        def add(self, obj):
            pass

        async def commit(self):
            # The real unique constraint fires at commit time.
            raise IntegrityError("INSERT", {}, Exception("duplicate key value"))

        async def rollback(self):
            self.rolled_back = True

        async def refresh(self, obj):
            pass

    db = _RaceSession()
    scope = {"type": "http", "client": ("127.0.0.1", 12345), "headers": []}
    request = StarletteRequest(scope)
    body = RegisterIn(email="race@test.dev", password="goodpass1")

    with pytest.raises(HTTPException) as exc:
        await register(body, request, Response(), db)

    assert exc.value.status_code == 409
    assert exc.value.detail == "email_taken"
    assert db.rolled_back is True
