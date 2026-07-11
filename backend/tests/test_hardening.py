import pytest

from app.core.config import Settings
from app.core.hardening import LoginThrottle, validate_production_settings

# NOTE: unlike other test files, this module mixes sync and async tests, so the
# asyncio marker is applied per-async-test below rather than via a blanket
# module-level `pytestmark` — this repo's `filterwarnings = ["error"]` turns
# pytest-asyncio's "marked but not async" warning into a collection error if a
# sync test picks up the mark from a module-wide pytestmark.


def test_is_production_flag():
    assert Settings(env="production").is_production is True
    assert Settings().is_production is False


def test_validate_production_settings_rejects_default_and_short_keys():
    from app.core.crypto import _active_key

    bad_default = Settings(
        env="production", secret_key="dev-secret-not-for-production",
        data_encryption_key=_active_key(),
    )
    with pytest.raises(RuntimeError):
        validate_production_settings(bad_default)
    short = Settings(env="production", secret_key="x" * 31, data_encryption_key=_active_key())
    with pytest.raises(RuntimeError):
        validate_production_settings(short)
    ok = Settings(env="production", secret_key="x" * 32, data_encryption_key=_active_key())
    validate_production_settings(ok)  # no raise
    dev = Settings()  # default key fine outside production
    validate_production_settings(dev)


def test_validate_production_settings_rejects_missing_and_invalid_encryption_key():
    from app.core.crypto import _active_key

    missing = Settings(env="production", secret_key="x" * 32, data_encryption_key="")
    with pytest.raises(RuntimeError):
        validate_production_settings(missing)
    invalid = Settings(
        env="production", secret_key="x" * 32, data_encryption_key="not-a-fernet-key"
    )
    with pytest.raises(RuntimeError):
        validate_production_settings(invalid)
    ok = Settings(env="production", secret_key="x" * 32, data_encryption_key=_active_key())
    validate_production_settings(ok)  # no raise


def test_login_throttle_state_stays_bounded_under_spray():
    from app.core.hardening import _MAX_TRACKED

    now = [1000.0]
    t = LoginThrottle(clock=lambda: now[0])
    for i in range(_MAX_TRACKED + 500):
        t.record_failure(f"user{i}@spray.test")
        assert len(t._failures) + len(t._locked_until) <= _MAX_TRACKED


def test_login_throttle_check_sweeps_expired_lockout():
    now = [1000.0]
    t = LoginThrottle(clock=lambda: now[0])
    for _ in range(5):
        t.record_failure("locked@b.c")
    assert "locked@b.c" in t._locked_until
    now[0] += 61  # lockout expires
    t.check("locked@b.c")  # no raise; sweeps the expired entry
    assert "locked@b.c" not in t._locked_until
    assert "locked@b.c" not in t._failures


def test_login_throttle_locks_after_five_failures_and_resets():
    now = [1000.0]
    t = LoginThrottle(clock=lambda: now[0])
    for _ in range(5):
        t.check("a@b.c")
        t.record_failure("a@b.c")
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as exc:
        t.check("a@b.c")
    assert exc.value.status_code == 429 and exc.value.detail == "too_many_attempts"
    now[0] += 61  # lockout expires
    t.check("a@b.c")
    t.record_success("a@b.c")
    t.record_failure("a@b.c")
    t.check("a@b.c")  # 1 failure after success != locked


@pytest.mark.asyncio(loop_scope="session")
async def test_login_endpoint_throttles(auth_client, monkeypatch):
    # auth_client's user is lee@test.dev / pw123456; wrong password 5x -> 429 on 6th
    from app.core import hardening
    monkeypatch.setattr(hardening, "login_throttle", hardening.LoginThrottle())
    monkeypatch.setattr("app.api.auth.login_throttle", hardening.login_throttle)
    for _ in range(5):
        r = await auth_client.post("/api/auth/login",
                                   json={"email": "lee@test.dev", "password": "wrong"})
        assert r.status_code == 401
    r = await auth_client.post("/api/auth/login",
                               json={"email": "lee@test.dev", "password": "wrong"})
    assert r.status_code == 429 and r.json()["detail"] == "too_many_attempts"


@pytest.mark.asyncio(loop_scope="session")
async def test_login_correct_password_wins_after_lockout(auth_client, monkeypatch):
    # Owner-lockout mitigation: after 5 wrong-password attempts lock the
    # account out for guessers, the real owner's correct password must still
    # succeed instead of being 429'd.
    from app.core import hardening
    monkeypatch.setattr(hardening, "login_throttle", hardening.LoginThrottle())
    monkeypatch.setattr("app.api.auth.login_throttle", hardening.login_throttle)
    for _ in range(5):
        r = await auth_client.post("/api/auth/login",
                                   json={"email": "lee@test.dev", "password": "wrong"})
        assert r.status_code == 401
    r = await auth_client.post("/api/auth/login",
                               json={"email": "lee@test.dev", "password": "pw123456"})
    assert r.status_code == 204


@pytest.mark.asyncio(loop_scope="session")
async def test_upload_cap_413(auth_client):
    big = b"symbol,quantity\n" + b"x" * (2 * 1024 * 1024)
    r = await auth_client.post("/api/imports/preview",
                               files={"file": ("big.csv", big, "text/csv")})
    assert r.status_code == 413 and r.json()["detail"] == "upload_too_large"


@pytest.mark.asyncio(loop_scope="session")
async def test_security_headers_present(client):
    r = await client.get("/api/health")
    assert r.headers["x-content-type-options"] == "nosniff"
    assert r.headers["x-frame-options"] == "DENY"
    assert r.headers["referrer-policy"] == "same-origin"


@pytest.mark.asyncio(loop_scope="session")
async def test_cookie_secure_flag_in_production(client, db_session, monkeypatch):
    from app.core.config import settings
    from app.core.security import hash_password
    from app.models.user import User
    monkeypatch.setattr(settings, "env", "production")
    db_session.add(User(email="prod@test.dev", password_hash=hash_password("pw123456")))
    await db_session.commit()
    r = await client.post("/api/auth/login",
                          json={"email": "prod@test.dev", "password": "pw123456"})
    cookie = r.headers["set-cookie"].lower()
    assert "secure" in cookie and "samesite=lax" in cookie


@pytest.mark.asyncio(loop_scope="session")
async def test_seed_refuses_defaults_in_production(monkeypatch):
    from app.core.config import settings
    monkeypatch.setattr(settings, "env", "production")
    from app.seed import main as seed_main
    with pytest.raises(RuntimeError):
        await seed_main()


def test_database_url_normalised():
    s = Settings(database_url="postgres://u:p@host:5432/db")
    assert s.database_url.startswith("postgresql+asyncpg://")
    s2 = Settings(database_url="postgresql://u:p@host:5432/db")
    assert s2.database_url.startswith("postgresql+asyncpg://")
    s3 = Settings(database_url="postgresql+asyncpg://u:p@host:5432/db")
    assert s3.database_url == "postgresql+asyncpg://u:p@host:5432/db"
