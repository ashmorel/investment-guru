# Phase 5 — Cloud Deployment — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking. **Tasks 1–3 are subagent-implementable; Tasks 4–5 are OPERATOR tasks the controller drives directly with the user's Railway/Vercel accounts.**

**Goal:** Deploy Investment Guru to Railway (backend + Postgres, always-on scheduler) and Vercel (frontend, `/api/*` rewrites), with production hardening, per spec `docs/superpowers/specs/2026-07-09-phase-5-cloud-design.md`.

**Architecture:** A production flag (`ENV=production`) gates the Phase-1-deferred hardening; a Dockerfile + start command runs migrations then uvicorn behind Railway's proxy; Vercel rewrites keep the browser single-origin so no frontend code changes. Deploys gate on green CI.

**Tech Stack:** Docker (python:3.12-slim), Railway (service + Postgres), Vercel (Vite static + rewrites), existing FastAPI/SQLAlchemy/APScheduler stack. No new Python deps.

## Global Constraints

- Never read or modify any `.env`; secrets go into Railway/Vercel dashboards or are generated fresh.
- TDD for all code (Tasks 1–2). Verify: `ruff check . && pytest -q` (backend, venv `backend/.venv`), `npm run check` (frontend — should be untouched).
- Hardening OFF in dev/tests by default; security headers always on. Exact values (spec §4): secret key min length **32**; throttle **5** consecutive failures → **60s** lockout → HTTP **429** detail `too_many_attempts`; upload cap **2 MB** → HTTP **413** detail `upload_too_large`; headers `X-Content-Type-Options: nosniff`, `X-Frame-Options: DENY`, `Referrer-Policy: same-origin`.
- Production seed refuses `you@example.com` / `change-me`.
- Single replica on Railway (in-process scheduler is the only instance).
- Commits end `Co-Authored-By:` trailer per session convention. Update `.superpowers/sdd/progress.md` after each task.

---

### Task 1: Production hardening

**Files:**
- Modify: `backend/app/core/config.py`
- Modify: `backend/app/api/auth.py`
- Modify: `backend/app/api/imports.py`
- Modify: `backend/app/main.py`
- Modify: `backend/app/seed.py`
- Create: `backend/app/core/hardening.py`
- Test: `backend/tests/test_hardening.py`

**Interfaces:**
- Produces: `Settings.env: str = "dev"` + `Settings.is_production` property; `hardening.SecurityHeadersMiddleware`; `hardening.LoginThrottle` (`check(email) -> None | raises HTTPException(429)`, `record_failure(email)`, `record_success(email)`, module singleton `login_throttle`, injectable clock for tests); `hardening.validate_production_settings(settings)` raising `RuntimeError` on default/short secret key; `MAX_UPLOAD_BYTES = 2 * 1024 * 1024`.
- Consumes: existing `create_app()`, login handler, `UploadFile` endpoints in imports.py.

- [ ] **Step 1: Failing tests** — `backend/tests/test_hardening.py`:

```python
import time

import pytest

from app.core.config import Settings
from app.core.hardening import LoginThrottle, validate_production_settings

pytestmark = pytest.mark.asyncio(loop_scope="session")


def test_is_production_flag():
    assert Settings(env="production").is_production is True
    assert Settings().is_production is False


def test_validate_production_settings_rejects_default_and_short_keys():
    bad_default = Settings(env="production", secret_key="dev-secret-not-for-production")
    with pytest.raises(RuntimeError):
        validate_production_settings(bad_default)
    short = Settings(env="production", secret_key="x" * 31)
    with pytest.raises(RuntimeError):
        validate_production_settings(short)
    ok = Settings(env="production", secret_key="x" * 32)
    validate_production_settings(ok)  # no raise
    dev = Settings()  # default key fine outside production
    validate_production_settings(dev)


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


async def test_upload_cap_413(auth_client):
    big = b"symbol,quantity\n" + b"x" * (2 * 1024 * 1024)
    r = await auth_client.post("/api/imports/preview",
                               files={"file": ("big.csv", big, "text/csv")})
    assert r.status_code == 413 and r.json()["detail"] == "upload_too_large"


async def test_security_headers_present(client):
    r = await client.get("/api/health")
    assert r.headers["x-content-type-options"] == "nosniff"
    assert r.headers["x-frame-options"] == "DENY"
    assert r.headers["referrer-policy"] == "same-origin"


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


async def test_seed_refuses_defaults_in_production(monkeypatch):
    from app.core.config import settings
    monkeypatch.setattr(settings, "env", "production")
    from app.seed import main as seed_main
    with pytest.raises(RuntimeError):
        await seed_main()
```

Check the exact import endpoint path (`/api/imports/preview` — confirm in `app/api/imports.py`, adjust if the router prefix differs) before finalising.

- [ ] **Step 2: Run** `pytest tests/test_hardening.py -q` — FAIL (ImportError).
- [ ] **Step 3: Implement.**

`app/core/config.py` — add:

```python
    env: str = "dev"

    @property
    def is_production(self) -> bool:
        return self.env == "production"
```

`app/core/hardening.py`:

```python
import time
from collections.abc import Callable

from fastapi import HTTPException
from starlette.middleware.base import BaseHTTPMiddleware

from app.core.config import Settings

MAX_UPLOAD_BYTES = 2 * 1024 * 1024
_MAX_FAILURES = 5
_LOCKOUT_SECONDS = 60.0

_DEFAULT_SECRET = "dev-secret-not-for-production"
_MIN_SECRET_LEN = 32


def validate_production_settings(settings: Settings) -> None:
    if not settings.is_production:
        return
    if settings.secret_key == _DEFAULT_SECRET or len(settings.secret_key) < _MIN_SECRET_LEN:
        raise RuntimeError(
            "SECRET_KEY must be set to a strong value (>=32 chars) in production"
        )


class LoginThrottle:
    """Per-email consecutive-failure lockout. Single-process state (single replica)."""

    def __init__(self, clock: Callable[[], float] = time.monotonic):
        self._clock = clock
        self._failures: dict[str, int] = {}
        self._locked_until: dict[str, float] = {}

    def check(self, email: str) -> None:
        until = self._locked_until.get(email, 0.0)
        if self._clock() < until:
            raise HTTPException(status_code=429, detail="too_many_attempts")

    def record_failure(self, email: str) -> None:
        n = self._failures.get(email, 0) + 1
        self._failures[email] = n
        if n >= _MAX_FAILURES:
            self._locked_until[email] = self._clock() + _LOCKOUT_SECONDS
            self._failures[email] = 0

    def record_success(self, email: str) -> None:
        self._failures.pop(email, None)
        self._locked_until.pop(email, None)


login_throttle = LoginThrottle()


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "same-origin"
        return response
```

`app/api/auth.py` — import `settings` and `login_throttle`; in `login`: `login_throttle.check(body.email)` first; on bad credentials `login_throttle.record_failure(body.email)` before raising 401; on success `login_throttle.record_success(body.email)`; add `secure=settings.is_production` to `set_cookie` (samesite already "lax").

`app/api/imports.py` — in both upload-consuming endpoints, after reading: 

```python
raw = await file.read()
if len(raw) > MAX_UPLOAD_BYTES:
    raise HTTPException(status_code=413, detail="upload_too_large")
```

(Adapt to how the file is currently read — cap at the read site, one shared check helper if both endpoints read.)

`app/main.py` — in `create_app()`: `validate_production_settings(settings)` first line; `app.add_middleware(SecurityHeadersMiddleware)`.

`app/seed.py` — in `main()`, before creating anything:

```python
if settings.is_production and (
    settings.initial_user_email == "you@example.com"
    or settings.initial_user_password == "change-me"
):
    raise RuntimeError("Set real INITIAL_USER_EMAIL/INITIAL_USER_PASSWORD in production")
```

- [ ] **Step 4: Run** `pytest -q` full suite + `ruff check .` — green (existing tests unaffected: dev flag default).
- [ ] **Step 5: Commit** — `feat(prod): hardening — secure cookies, fail-hard secrets, login throttle, upload cap, security headers`

---

### Task 2: Dockerfile + DATABASE_URL normalisation + container boot check

**Files:**
- Create: `backend/Dockerfile`
- Create: `backend/.dockerignore`
- Modify: `backend/app/core/config.py`
- Test: `backend/tests/test_hardening.py` (add URL-normalisation cases)

**Interfaces:**
- Produces: `Settings.database_url` accepts `postgres://` / `postgresql://` URLs and normalises to `postgresql+asyncpg://` (pydantic `field_validator`); image runs `alembic upgrade head && uvicorn app.main:app --host 0.0.0.0 --port $PORT --proxy-headers --forwarded-allow-ips '*'` as default CMD (Railway overrides `$PORT`).

- [ ] **Step 1: Failing tests** (append to test_hardening.py):

```python
def test_database_url_normalised():
    s = Settings(database_url="postgres://u:p@host:5432/db")
    assert s.database_url.startswith("postgresql+asyncpg://")
    s2 = Settings(database_url="postgresql://u:p@host:5432/db")
    assert s2.database_url.startswith("postgresql+asyncpg://")
    s3 = Settings(database_url="postgresql+asyncpg://u:p@host:5432/db")
    assert s3.database_url == "postgresql+asyncpg://u:p@host:5432/db"
```

- [ ] **Step 2: Run** — FAIL. **Step 3: Implement** validator in config.py:

```python
from pydantic import field_validator

    @field_validator("database_url")
    @classmethod
    def _normalise_db_url(cls, v: str) -> str:
        if v.startswith("postgres://"):
            return "postgresql+asyncpg://" + v[len("postgres://"):]
        if v.startswith("postgresql://") and not v.startswith("postgresql+asyncpg://"):
            return "postgresql+asyncpg://" + v[len("postgresql://"):]
        return v
```

`backend/Dockerfile`:

```dockerfile
FROM python:3.12-slim
WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
COPY pyproject.toml ./
COPY app ./app
COPY alembic.ini ./
COPY alembic ./alembic
RUN pip install --no-cache-dir .
RUN useradd -m appuser
USER appuser
EXPOSE 8000
CMD ["sh", "-c", "alembic upgrade head && uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000} --proxy-headers --forwarded-allow-ips '*'"]
```

`backend/.dockerignore`: `.venv`, `__pycache__`, `tests`, `.env*`, `*.pyc`.

- [ ] **Step 4: Local container boot check** — from `backend/`: `docker build -t guru-backend .` then `docker run --rm -e DATABASE_URL="postgresql://guru:guru@host.docker.internal:5433/guru" -e PORT=8100 -p 8100:8100 guru-backend` in background; `curl -s localhost:8100/api/health` → `{"status":"ok"}`; stop container. Capture output in report. (Dev DB must be up; migrations are already at head so alembic is a no-op.)
- [ ] **Step 5: Run** full suite + ruff — green. **Step 6: Commit** — `feat(prod): Dockerfile + Railway-style DATABASE_URL normalisation`

---

### Task 3: vercel.json + deployment runbook

**Files:**
- Create: `frontend/vercel.json`
- Create: `docs/deployment.md`

**Interfaces:**
- Produces: `frontend/vercel.json` with the rewrite destination containing the literal placeholder `RAILWAY_BACKEND_DOMAIN` (Task 4 replaces it with the real domain once known).

- [ ] **Step 1:** `frontend/vercel.json`:

```json
{
  "rewrites": [
    { "source": "/api/:path*", "destination": "https://RAILWAY_BACKEND_DOMAIN/api/:path*" },
    { "source": "/((?!api/).*)", "destination": "/index.html" }
  ]
}
```

- [ ] **Step 2:** `docs/deployment.md` — write the full runbook per spec §6 with these sections: **Env vars** (table: name / where set / purpose / how to obtain — DATABASE_URL Railway-ref, SECRET_KEY `python -c "import secrets; print(secrets.token_urlsafe(48))"`, ANTHROPIC_API_KEY console link, INITIAL_USER_EMAIL/PASSWORD real values, ENV=production, optional ORSO_HSBC_CLIENT_ID/SECRET devtools instructions, GURU_DIGEST_HOUR/TIMEZONE defaults); **First deploy** (ordered: Railway project + Postgres → backend service from repo `backend/` root Dockerfile → set vars → enable "wait for CI" → note generated domain → replace RAILWAY_BACKEND_DOMAIN in vercel.json + commit → Vercel project root `frontend/` → deploy → seed via `railway run python -m app.seed`); **Rollback** (redeploy previous build in either dashboard); **Backups** (`railway connect postgres` + `pg_dump` one-liner; run before risky migrations); **Scheduler** (single replica, digest 07:00 Europe/London, catch-up on restart); **Key rotation**; **SSE note** (placeholder line "verified through rewrite: <result>" — Task 5 fills the result in).
- [ ] **Step 3:** `npm run check` (frontend untouched but confirm vercel.json breaks nothing) + backend suite still green.
- [ ] **Step 4: Commit** — `docs: deployment runbook + vercel rewrites config`

---

### Task 4: Provision Railway + Vercel (OPERATOR — controller drives, user's accounts)

No repo files except the vercel.json domain replacement. Controller uses the railway MCP/CLI and vercel CLI; the user may need to approve auth prompts.

- [ ] **Step 1:** Railway: create project `investment-guru`; add Postgres; create backend service from the GitHub repo (root `backend/`, Dockerfile build); set env vars per the runbook (generate SECRET_KEY fresh; copy ANTHROPIC key from the user's local env — ask the user to paste it into the Railway dashboard rather than echoing it anywhere); single replica; healthcheck path `/api/health`; enable "wait for CI" check-suite gating; generate/note the public domain.
- [ ] **Step 2:** Replace `RAILWAY_BACKEND_DOMAIN` in `frontend/vercel.json` with the real domain; commit `chore: point vercel rewrites at railway backend` + push (CI must go green → Railway deploys).
- [ ] **Step 3:** Vercel: create/link project, root directory `frontend`, framework Vite, auto-deploy on push; trigger first production deploy; note the public URL.
- [ ] **Step 4:** Seed: `railway run python -m app.seed` with the real INITIAL_USER_* vars set (verify it refuses defaults if unset — expected failure first is a feature check).
- [ ] **Step 5:** Record all identifiers (project ids, domains) in the ledger; update `docs/deployment.md` if any step deviated.

---

### Task 5: Live smoke + docs + final review (OPERATOR)

- [ ] **Step 1: Smoke per spec §7** from the public Vercel URL: login (inspect `Set-Cookie` for `Secure`+`SameSite=Lax`); 5 wrong passwords → 429; >2 MB CSV → 413; digest+take+review generate in prod; chat streams token-by-token through the rewrite (record the SSE result in the runbook — if buffered, execute the documented contingency and note it); ORSO manual price + advice; scheduler behaviour confirmed via report timestamps or Railway logs.
- [ ] **Step 2: User acceptance:** user imports real Yahoo CSV + enters real ORSO allocation from the live UI.
- [ ] **Step 3: Docs** — README Status (all five phases complete; live URL note), PROGRESS.md Phase 5 section (infra, hardening, smoke evidence), runbook SSE line filled, ledger closed.
- [ ] **Step 4: Commit + push + CI green.**
- [ ] **Step 5: Final whole-branch review on Opus** (base = pre-Phase-5 commit `82591f8`); fix wave if needed; re-review to merge-clean.

---

## Self-review notes (completed)

- **Spec coverage:** §2→T2+T4, §3→T3+T4+T5, §4→T1, §5→T4 (gating) — CI file itself unchanged per spec, §6→T3+T4, §7→T5, §8 behaviours covered by T1 tests + existing degradation, §9 exclusions honoured, §10 order preserved.
- **Type consistency:** `login_throttle` import path in auth.py matches the module singleton; `MAX_UPLOAD_BYTES` shared constant; `is_production` property used consistently.
- **Judgment calls:** import-endpoint path in T1 tests must be confirmed against `app/api/imports.py` (router prefix) before finalising; cookie test toggles `settings.env` via monkeypatch (settings is a module singleton — patch the instance attribute, works with pydantic-settings).
