# Investment Guru Phase 1 — Foundations + Portfolio Core Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A locally-running app where the user logs in, creates real/watchlist portfolios, imports a Yahoo Finance CSV, sees live-priced positions with P&L in the portfolio's base currency, and a dashboard summary.

**Architecture:** Monorepo — `backend/` FastAPI (async SQLAlchemy 2 + Alembic + Postgres 16 via docker-compose) and `frontend/` (React + Vite + TS + Tailwind + React Query). Market data flows through a `MarketDataProvider` interface (yfinance implementation) with a Postgres quote cache. Deterministic valuation service converts native currencies (incl. GBp pence) to each portfolio's base currency.

**Tech Stack:** Python 3.12, FastAPI, SQLAlchemy 2 (asyncpg), Alembic, pydantic-settings, bcrypt, itsdangerous, yfinance, pandas, pytest + pytest-asyncio, httpx; React 18, Vite 5, TypeScript, Tailwind v4, @tanstack/react-query, react-router-dom, vitest + Testing Library.

## Global Constraints

- Spec: `docs/superpowers/specs/2026-07-07-investment-guru-design.md` — all tasks implicitly bound by it.
- **Public repo: no real holdings data is ever committed** — fixtures/seeds use synthetic portfolios only.
- Never read or modify any `.env`; `.env.example` documents variables.
- Every table that stores user data carries `user_id` (multi-user foundations).
- All money/quantity columns are `Numeric` (quantity 18,6; money 18,4); never float.
- Async tests use `pytestmark = pytest.mark.asyncio(loop_scope="session")` and the shared `client`/`db_session` fixtures — never a raw AsyncClient on the app engine.
- Provider failures must degrade (stale cache / null quote), never crash an endpoint.
- yfinance/network is NEVER called in tests — providers are fixture/mock-tested.
- Commit messages end with: `Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>`.
- Local Postgres runs on port **5433** (5432 may be occupied by InvestiKid's).

---

### Task 1: Backend scaffold, config, health endpoint, docker-compose, repo hygiene

**Files:**
- Create: `.gitignore`, `README.md`, `CLAUDE.md`, `docker-compose.yml`, `db/init/01-create-test-db.sql`
- Create: `backend/pyproject.toml`, `backend/.env.example`
- Create: `backend/app/__init__.py`, `backend/app/main.py`, `backend/app/core/__init__.py`, `backend/app/core/config.py`
- Test: `backend/tests/__init__.py`, `backend/tests/test_health.py`

**Interfaces:**
- Produces: `app.main.create_app() -> FastAPI` (app factory used by all later API tasks); `app.core.config.settings` (pydantic-settings singleton with `database_url: str`, `secret_key: str`, `initial_user_email: str`, `initial_user_password: str`).

- [ ] **Step 1: Create repo hygiene files**

`.gitignore`:
```gitignore
.venv/
__pycache__/
*.pyc
.pytest_cache/
.ruff_cache/
node_modules/
dist/
.env
.env.*
!.env.example
*.local
.DS_Store
```

`docker-compose.yml`:
```yaml
services:
  db:
    image: postgres:16
    environment:
      POSTGRES_USER: guru
      POSTGRES_PASSWORD: guru
      POSTGRES_DB: guru
    ports:
      - "5433:5432"
    volumes:
      - guru_pgdata:/var/lib/postgresql/data
      - ./db/init:/docker-entrypoint-initdb.d
volumes:
  guru_pgdata:
```

`db/init/01-create-test-db.sql`:
```sql
CREATE DATABASE guru_test;
```

`README.md`:
```markdown
# Investment Guru

Personal portfolio management with an AI adviser (US/UK/HK markets) and HK ORSO fund tracking.
Spec: `docs/superpowers/specs/2026-07-07-investment-guru-design.md`.

## Local setup
```bash
docker compose up -d db
cd backend && python3.12 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env   # then edit values
alembic upgrade head && python -m app.seed
uvicorn app.main:app --reload --factory  # (app.main:create_app)
# frontend (from repo root, once Task 12 lands):
cd frontend && npm install && npm run dev
```
```

`CLAUDE.md`:
```markdown
# Investment Guru

Personal investment app: portfolios/watchlists, Yahoo CSV import, market signals, AI adviser (the Guru), HK ORSO tracking. Spec + plans live in `docs/superpowers/`.

## Golden rules
- **Public repo: NEVER commit real holdings data** — synthetic fixtures only. Never read/modify `.env`.
- Money/quantity = `Numeric`, never float. Every user-data table has `user_id`.
- DB change = hand-written chained Alembic migration (check `alembic heads` first).
- Async tests: `pytestmark = pytest.mark.asyncio(loop_scope="session")` + shared fixtures from `conftest.py`.
- Providers (yfinance etc.) are fixture-mocked in tests; endpoints degrade on provider failure, never 500.
- TDD: failing test → minimal code → commit. Verify with `ruff check . && pytest` (backend), `npm run check` (frontend).
- Local Postgres: `docker compose up -d db` (port 5433; DBs `guru` + `guru_test`).
```

- [ ] **Step 2: Create backend package config**

`backend/pyproject.toml`:
```toml
[build-system]
requires = ["setuptools>=68"]
build-backend = "setuptools.backend"

[project]
name = "investment-guru-backend"
version = "0.1.0"
requires-python = ">=3.12"
dependencies = [
  "fastapi>=0.115",
  "uvicorn[standard]>=0.30",
  "sqlalchemy[asyncio]>=2.0.30",
  "asyncpg>=0.29",
  "alembic>=1.13",
  "pydantic-settings>=2.4",
  "itsdangerous>=2.2",
  "bcrypt>=4.1",
  "yfinance>=0.2.50",
  "pandas>=2.2",
  "python-multipart>=0.0.9",
]

[project.optional-dependencies]
dev = [
  "pytest>=8",
  "pytest-asyncio>=0.24",
  "httpx>=0.27",
  "ruff>=0.6",
]

[tool.setuptools.packages.find]
include = ["app*"]

[tool.pytest.ini_options]
asyncio_mode = "auto"
asyncio_default_fixture_loop_scope = "session"
testpaths = ["tests"]

[tool.ruff]
line-length = 100
target-version = "py312"

[tool.ruff.lint]
select = ["E", "F", "I", "UP", "B"]
```

Note: if `build-backend = "setuptools.backend"` errors on install, use `"setuptools.build_meta"` (the standard value).

`backend/.env.example`:
```bash
DATABASE_URL=postgresql+asyncpg://guru:guru@localhost:5433/guru
SECRET_KEY=change-me-long-random
INITIAL_USER_EMAIL=you@example.com
INITIAL_USER_PASSWORD=change-me
```

- [ ] **Step 3: Write the failing health test**

`backend/tests/test_health.py`:
```python
import httpx
import pytest

from app.main import create_app

pytestmark = pytest.mark.asyncio(loop_scope="session")


async def test_health():
    app = create_app()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/api/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}
```

- [ ] **Step 4: Run test to verify it fails**

Run: `cd backend && python3.12 -m venv .venv && source .venv/bin/activate && pip install -e ".[dev]" && pytest tests/test_health.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app'` (or ImportError on `create_app`)

- [ ] **Step 5: Implement config + app factory**

`backend/app/core/config.py`:
```python
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql+asyncpg://guru:guru@localhost:5433/guru"
    secret_key: str = "dev-secret-not-for-production"
    initial_user_email: str = "you@example.com"
    initial_user_password: str = "change-me"


settings = Settings()
```

`backend/app/main.py`:
```python
from fastapi import FastAPI


def create_app() -> FastAPI:
    app = FastAPI(title="Investment Guru")

    @app.get("/api/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    return app


app = create_app()
```

Create empty `backend/app/__init__.py`, `backend/app/core/__init__.py`, `backend/tests/__init__.py`.

- [ ] **Step 6: Run test to verify it passes + lint**

Run: `cd backend && pytest tests/test_health.py -v && ruff check .`
Expected: PASS, no lint errors

- [ ] **Step 7: Commit**

```bash
git add -A
git commit -m "feat: backend scaffold — FastAPI app factory, config, health endpoint, docker-compose Postgres

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 2: Async database layer, Alembic, users table, shared test fixtures

**Files:**
- Create: `backend/app/core/db.py`, `backend/app/models/__init__.py`, `backend/app/models/base.py`, `backend/app/models/user.py`
- Create: `backend/alembic.ini`, `backend/alembic/env.py`, `backend/alembic/script.py.mako`, `backend/alembic/versions/0001_users.py`
- Create: `backend/tests/conftest.py`
- Test: `backend/tests/test_db.py`

**Interfaces:**
- Consumes: `settings` from Task 1.
- Produces: `app.core.db.Base` (DeclarativeBase), `app.core.db.get_session()` (FastAPI dependency yielding `AsyncSession`), `app.core.db.engine`; `app.models.user.User` (id: int PK, email: str unique, password_hash: str, created_at: datetime); test fixtures `db_session` (AsyncSession) and `client` (httpx.AsyncClient wired to app with DB override) used by ALL later test files.

- [ ] **Step 1: Write the failing test**

`backend/tests/test_db.py`:
```python
import pytest
from sqlalchemy import select

from app.models.user import User

pytestmark = pytest.mark.asyncio(loop_scope="session")


async def test_create_and_read_user(db_session):
    user = User(email="t@example.com", password_hash="x")
    db_session.add(user)
    await db_session.commit()

    result = await db_session.execute(select(User).where(User.email == "t@example.com"))
    found = result.scalar_one()
    assert found.id is not None
    assert found.created_at is not None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && docker compose -f ../docker-compose.yml up -d db && pytest tests/test_db.py -v`
Expected: FAIL — `ModuleNotFoundError` / fixture `db_session` not found

- [ ] **Step 3: Implement DB core, User model, conftest**

`backend/app/core/db.py`:
```python
from collections.abc import AsyncIterator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from app.core.config import settings


class Base(DeclarativeBase):
    pass


engine = create_async_engine(settings.database_url)
SessionLocal = async_sessionmaker(engine, expire_on_commit=False)


async def get_session() -> AsyncIterator[AsyncSession]:
    async with SessionLocal() as session:
        yield session
```

`backend/app/models/base.py`:
```python
from datetime import datetime

from sqlalchemy import func
from sqlalchemy.orm import Mapped, mapped_column


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
```

`backend/app/models/user.py`:
```python
from sqlalchemy import String
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base
from app.models.base import TimestampMixin


class User(TimestampMixin, Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255))
```

`backend/app/models/__init__.py`:
```python
from app.models.user import User

__all__ = ["User"]
```

`backend/tests/conftest.py`:
```python
from collections.abc import AsyncIterator

import httpx
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.db import Base, get_session
from app.main import create_app

TEST_DATABASE_URL = "postgresql+asyncpg://guru:guru@localhost:5433/guru_test"

test_engine = create_async_engine(TEST_DATABASE_URL)
TestSession = async_sessionmaker(test_engine, expire_on_commit=False)


@pytest_asyncio.fixture(scope="session", autouse=True)
async def _create_schema():
    import app.models  # noqa: F401  (register all models)

    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    yield


@pytest_asyncio.fixture(autouse=True)
async def _truncate_tables(_create_schema):
    yield
    async with test_engine.begin() as conn:
        for table in reversed(Base.metadata.sorted_tables):
            await conn.execute(text(f'TRUNCATE TABLE "{table.name}" RESTART IDENTITY CASCADE'))


@pytest_asyncio.fixture
async def db_session() -> AsyncIterator[AsyncSession]:
    async with TestSession() as session:
        yield session


@pytest_asyncio.fixture
async def client() -> AsyncIterator[httpx.AsyncClient]:
    app = create_app()

    async def _override_session() -> AsyncIterator[AsyncSession]:
        async with TestSession() as session:
            yield session

    app.dependency_overrides[get_session] = _override_session
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c
```

- [ ] **Step 4: Set up Alembic**

Run: `cd backend && alembic init alembic`
Then replace `backend/alembic/env.py` contents with a sync-engine version pointing at our metadata (Alembic runs migrations synchronously via psycopg-style URL swap):
```python
from logging.config import fileConfig

from alembic import context
from sqlalchemy import create_engine

import app.models  # noqa: F401
from app.core.config import settings
from app.core.db import Base

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def _sync_url() -> str:
    return settings.database_url.replace("postgresql+asyncpg", "postgresql+psycopg2")


def run_migrations_offline() -> None:
    context.configure(url=_sync_url(), target_metadata=target_metadata, literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    engine = create_engine(_sync_url())
    with engine.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
```
Add `psycopg2-binary>=2.9` to `dependencies` in `backend/pyproject.toml` and `pip install -e ".[dev]"` again.

`backend/alembic/versions/0001_users.py` (hand-written):
```python
"""users table

Revision ID: 0001
Revises:
Create Date: 2026-07-07
"""
import sqlalchemy as sa
from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("email", sa.String(255), nullable=False),
        sa.Column("password_hash", sa.String(255), nullable=False),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_users_email", "users", ["email"], unique=True)


def downgrade() -> None:
    op.drop_table("users")
```

Run: `cd backend && alembic upgrade head`
Expected: `Running upgrade  -> 0001`

- [ ] **Step 5: Run tests to verify pass + lint**

Run: `cd backend && pytest -v && ruff check .`
Expected: both tests PASS

- [ ] **Step 6: Commit**

```bash
git add -A
git commit -m "feat: async SQLAlchemy core, User model, Alembic baseline, shared test fixtures

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 3: Auth-lite — password hashing, login with session cookie, current-user dependency, seed script

**Files:**
- Create: `backend/app/core/security.py`, `backend/app/api/__init__.py`, `backend/app/api/deps.py`, `backend/app/api/auth.py`, `backend/app/seed.py`
- Modify: `backend/app/main.py` (include auth router)
- Test: `backend/tests/test_auth.py`

**Interfaces:**
- Consumes: `User`, `get_session`, `settings`, `client` fixture.
- Produces: `security.hash_password(pw: str) -> str`, `security.verify_password(pw: str, hashed: str) -> bool`, `security.sign_session(user_id: int) -> str`, `security.read_session(token: str) -> int | None`; dependency `deps.get_current_user() -> User` (raises 401 without valid `session` cookie); routes `POST /api/auth/login` `{email, password}` → 204 + Set-Cookie, `POST /api/auth/logout` → 204, `GET /api/auth/me` → `{id, email}`; test fixture **`auth_client`** (client with a logged-in seeded user) added to conftest — all later API tests use `auth_client`.

- [ ] **Step 1: Write the failing tests**

`backend/tests/test_auth.py`:
```python
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
    resp = await client.post("/api/auth/login", json={"email": "lee@test.dev", "password": "pw123456"})
    assert resp.status_code == 204
    assert "session" in resp.cookies

    me = await client.get("/api/auth/me")
    assert me.status_code == 200
    assert me.json()["email"] == "lee@test.dev"


async def test_bad_password_rejected(client, db_session):
    await _seed_user(db_session)
    resp = await client.post("/api/auth/login", json={"email": "lee@test.dev", "password": "wrong"})
    assert resp.status_code == 401


async def test_me_requires_auth(client):
    resp = await client.get("/api/auth/me")
    assert resp.status_code == 401
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && pytest tests/test_auth.py -v`
Expected: FAIL — `ModuleNotFoundError: app.core.security`

- [ ] **Step 3: Implement security, deps, auth router, seed**

`backend/app/core/security.py`:
```python
import bcrypt
from itsdangerous import BadSignature, TimestampSigner

from app.core.config import settings

_signer = TimestampSigner(settings.secret_key)
SESSION_MAX_AGE_SECONDS = 60 * 60 * 24 * 30  # 30 days


def hash_password(pw: str) -> str:
    return bcrypt.hashpw(pw.encode(), bcrypt.gensalt()).decode()


def verify_password(pw: str, hashed: str) -> bool:
    return bcrypt.checkpw(pw.encode(), hashed.encode())


def sign_session(user_id: int) -> str:
    return _signer.sign(str(user_id)).decode()


def read_session(token: str) -> int | None:
    try:
        raw = _signer.unsign(token, max_age=SESSION_MAX_AGE_SECONDS)
        return int(raw)
    except (BadSignature, ValueError):
        return None
```

`backend/app/api/deps.py`:
```python
from typing import Annotated

from fastapi import Cookie, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session
from app.core.security import read_session
from app.models.user import User

SessionDep = Annotated[AsyncSession, Depends(get_session)]


async def get_current_user(
    db: SessionDep, session: Annotated[str | None, Cookie()] = None
) -> User:
    user_id = read_session(session) if session else None
    if user_id is None:
        raise HTTPException(status_code=401, detail="Not authenticated")
    user = await db.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=401, detail="Not authenticated")
    return user


CurrentUser = Annotated[User, Depends(get_current_user)]
```

`backend/app/api/auth.py`:
```python
from fastapi import APIRouter, HTTPException, Response
from pydantic import BaseModel
from sqlalchemy import select

from app.api.deps import CurrentUser, SessionDep
from app.core.security import SESSION_MAX_AGE_SECONDS, sign_session, verify_password
from app.models.user import User

router = APIRouter(prefix="/api/auth", tags=["auth"])


class LoginIn(BaseModel):
    email: str
    password: str


class MeOut(BaseModel):
    id: int
    email: str


@router.post("/login", status_code=204)
async def login(body: LoginIn, response: Response, db: SessionDep) -> None:
    result = await db.execute(select(User).where(User.email == body.email))
    user = result.scalar_one_or_none()
    if user is None or not verify_password(body.password, user.password_hash):
        raise HTTPException(status_code=401, detail="Invalid credentials")
    response.set_cookie(
        "session",
        sign_session(user.id),
        max_age=SESSION_MAX_AGE_SECONDS,
        httponly=True,
        samesite="lax",
    )


@router.post("/logout", status_code=204)
async def logout(response: Response) -> None:
    response.delete_cookie("session")


@router.get("/me", response_model=MeOut)
async def me(user: CurrentUser) -> MeOut:
    return MeOut(id=user.id, email=user.email)
```

Modify `backend/app/main.py`:
```python
from fastapi import FastAPI

from app.api.auth import router as auth_router


def create_app() -> FastAPI:
    app = FastAPI(title="Investment Guru")

    @app.get("/api/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    app.include_router(auth_router)
    return app


app = create_app()
```

`backend/app/seed.py`:
```python
"""Create the initial user from env config. Run: python -m app.seed"""
import asyncio

from sqlalchemy import select

from app.core.config import settings
from app.core.db import SessionLocal
from app.core.security import hash_password
from app.models.user import User


async def main() -> None:
    async with SessionLocal() as db:
        existing = await db.execute(select(User).where(User.email == settings.initial_user_email))
        if existing.scalar_one_or_none():
            print("User already exists")
            return
        db.add(
            User(
                email=settings.initial_user_email,
                password_hash=hash_password(settings.initial_user_password),
            )
        )
        await db.commit()
        print(f"Created {settings.initial_user_email}")


if __name__ == "__main__":
    asyncio.run(main())
```

- [ ] **Step 4: Add the `auth_client` fixture to conftest**

Append to `backend/tests/conftest.py`:
```python
from app.core.security import hash_password  # noqa: E402
from app.models.user import User  # noqa: E402


@pytest_asyncio.fixture
async def auth_client(client, db_session) -> httpx.AsyncClient:
    user = User(email="lee@test.dev", password_hash=hash_password("pw123456"))
    db_session.add(user)
    await db_session.commit()
    resp = await client.post(
        "/api/auth/login", json={"email": "lee@test.dev", "password": "pw123456"}
    )
    assert resp.status_code == 204
    return client
```

- [ ] **Step 5: Run tests + lint**

Run: `cd backend && pytest -v && ruff check .`
Expected: all PASS

- [ ] **Step 6: Commit**

```bash
git add -A
git commit -m "feat: auth-lite — bcrypt hashing, session-cookie login, current-user dep, seed script

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 4: CI — GitHub Actions backend job

**Files:**
- Create: `.github/workflows/ci.yml`

**Interfaces:**
- Consumes: backend test suite + ruff config.
- Produces: `Backend CI` job that later tasks keep green; Task 12 appends a `frontend` job to this same file.

- [ ] **Step 1: Write the workflow**

`.github/workflows/ci.yml`:
```yaml
name: CI

on:
  push:
    branches: [main]
  pull_request:

jobs:
  backend:
    runs-on: ubuntu-latest
    services:
      postgres:
        image: postgres:16
        env:
          POSTGRES_USER: guru
          POSTGRES_PASSWORD: guru
          POSTGRES_DB: guru_test
        ports:
          - 5433:5432
        options: >-
          --health-cmd pg_isready --health-interval 5s --health-timeout 5s --health-retries 10
    defaults:
      run:
        working-directory: backend
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"
          cache: pip
      - run: pip install -e ".[dev]"
      - run: ruff check .
      - run: pytest -v
```

- [ ] **Step 2: Commit and push, verify green**

```bash
git add .github/workflows/ci.yml
git commit -m "ci: backend job — ruff + pytest against Postgres 16 service

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
git push
```
Then verify: `gh run watch --repo ashmorel/investment-guru` and confirm with `gh run view --json conclusion` → `"success"`. (Never trust `gh run watch | tail; echo $?`.)

---

### Task 5: Domain models + migration — instruments, portfolios, positions, quote cache, price bars, FX rates

**Files:**
- Create: `backend/app/models/instrument.py`, `backend/app/models/portfolio.py`, `backend/app/models/market.py`
- Modify: `backend/app/models/__init__.py`
- Create: `backend/alembic/versions/0002_portfolio_core.py`
- Test: `backend/tests/test_models.py`

**Interfaces:**
- Consumes: `Base`, `TimestampMixin`, `User`.
- Produces (exact names used by all later tasks):
  - `Instrument`: id, symbol (unique), name, exchange, market (`"US"|"UK"|"HK"`), sector (nullable), industry (nullable), currency (native, e.g. `"USD"`, `"GBp"`, `"HKD"`)
  - `Portfolio`: id, user_id → users, name, kind (`"real"|"watchlist"`), base_currency (e.g. `"GBP"`), `positions` relationship
  - `Position`: id, portfolio_id → portfolios, instrument_id → instruments, quantity (Numeric(18,6), nullable), avg_cost (Numeric(18,4), nullable, in the instrument's native currency), notes (nullable), `instrument` relationship (eager `selectin`)
  - `QuoteCache`: symbol (PK), price Numeric(18,4), currency, previous_close Numeric(18,4) nullable, fetched_at datetime
  - `PriceBar`: id, instrument_id, date, open/high/low/close Numeric(18,4), volume BigInteger — unique (instrument_id, date)
  - `FxRate`: id, pair (e.g. `"USDGBP"`), date, rate Numeric(18,8) — unique (pair, date)

- [ ] **Step 1: Write the failing test**

`backend/tests/test_models.py`:
```python
from decimal import Decimal

import pytest

from app.models import Instrument, Portfolio, Position, User

pytestmark = pytest.mark.asyncio(loop_scope="session")


async def test_portfolio_with_positions(db_session):
    user = User(email="m@test.dev", password_hash="x")
    inst = Instrument(symbol="AAPL", name="Apple Inc.", exchange="NMS", market="US", currency="USD")
    db_session.add_all([user, inst])
    await db_session.flush()

    pf = Portfolio(user_id=user.id, name="Growth", kind="real", base_currency="GBP")
    db_session.add(pf)
    await db_session.flush()

    pos = Position(
        portfolio_id=pf.id, instrument_id=inst.id,
        quantity=Decimal("10.5"), avg_cost=Decimal("150.25"),
    )
    db_session.add(pos)
    await db_session.commit()

    loaded = await db_session.get(Portfolio, pf.id)
    await db_session.refresh(loaded, ["positions"])
    assert len(loaded.positions) == 1
    assert loaded.positions[0].quantity == Decimal("10.500000")


async def test_watchlist_position_allows_null_quantity(db_session):
    user = User(email="w@test.dev", password_hash="x")
    inst = Instrument(symbol="0700.HK", name="Tencent", exchange="HKG", market="HK", currency="HKD")
    db_session.add_all([user, inst])
    await db_session.flush()
    pf = Portfolio(user_id=user.id, name="Watch", kind="watchlist", base_currency="HKD")
    db_session.add(pf)
    await db_session.flush()
    db_session.add(Position(portfolio_id=pf.id, instrument_id=inst.id))
    await db_session.commit()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && pytest tests/test_models.py -v`
Expected: FAIL — ImportError (`Instrument` etc. not defined)

- [ ] **Step 3: Implement the models**

`backend/app/models/instrument.py`:
```python
from sqlalchemy import String
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base
from app.models.base import TimestampMixin


class Instrument(TimestampMixin, Base):
    __tablename__ = "instruments"

    id: Mapped[int] = mapped_column(primary_key=True)
    symbol: Mapped[str] = mapped_column(String(32), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(255))
    exchange: Mapped[str] = mapped_column(String(32))
    market: Mapped[str] = mapped_column(String(8))  # US | UK | HK
    sector: Mapped[str | None] = mapped_column(String(128))
    industry: Mapped[str | None] = mapped_column(String(128))
    currency: Mapped[str] = mapped_column(String(8))  # native listing ccy, may be "GBp"
```

`backend/app/models/portfolio.py`:
```python
from decimal import Decimal

from sqlalchemy import ForeignKey, Numeric, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.db import Base
from app.models.base import TimestampMixin
from app.models.instrument import Instrument


class Portfolio(TimestampMixin, Base):
    __tablename__ = "portfolios"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    name: Mapped[str] = mapped_column(String(120))
    kind: Mapped[str] = mapped_column(String(16))  # real | watchlist
    base_currency: Mapped[str] = mapped_column(String(8), default="GBP")

    positions: Mapped[list["Position"]] = relationship(
        back_populates="portfolio", cascade="all, delete-orphan", lazy="selectin"
    )


class Position(TimestampMixin, Base):
    __tablename__ = "positions"

    id: Mapped[int] = mapped_column(primary_key=True)
    portfolio_id: Mapped[int] = mapped_column(ForeignKey("portfolios.id"), index=True)
    instrument_id: Mapped[int] = mapped_column(ForeignKey("instruments.id"))
    quantity: Mapped[Decimal | None] = mapped_column(Numeric(18, 6))
    avg_cost: Mapped[Decimal | None] = mapped_column(Numeric(18, 4))  # native ccy
    notes: Mapped[str | None] = mapped_column(Text)

    portfolio: Mapped[Portfolio] = relationship(back_populates="positions")
    instrument: Mapped[Instrument] = relationship(lazy="selectin")
```

`backend/app/models/market.py`:
```python
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import BigInteger, Date, ForeignKey, Numeric, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base


class QuoteCache(Base):
    __tablename__ = "quote_cache"

    symbol: Mapped[str] = mapped_column(String(32), primary_key=True)
    price: Mapped[Decimal] = mapped_column(Numeric(18, 4))
    currency: Mapped[str] = mapped_column(String(8))
    previous_close: Mapped[Decimal | None] = mapped_column(Numeric(18, 4))
    fetched_at: Mapped[datetime] = mapped_column()


class PriceBar(Base):
    __tablename__ = "price_bars"
    __table_args__ = (UniqueConstraint("instrument_id", "date"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    instrument_id: Mapped[int] = mapped_column(ForeignKey("instruments.id"), index=True)
    date: Mapped[date] = mapped_column(Date)
    open: Mapped[Decimal] = mapped_column(Numeric(18, 4))
    high: Mapped[Decimal] = mapped_column(Numeric(18, 4))
    low: Mapped[Decimal] = mapped_column(Numeric(18, 4))
    close: Mapped[Decimal] = mapped_column(Numeric(18, 4))
    volume: Mapped[int | None] = mapped_column(BigInteger)


class FxRate(Base):
    __tablename__ = "fx_rates"
    __table_args__ = (UniqueConstraint("pair", "date"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    pair: Mapped[str] = mapped_column(String(8), index=True)  # e.g. USDGBP
    date: Mapped[date] = mapped_column(Date)
    rate: Mapped[Decimal] = mapped_column(Numeric(18, 8))
```

`backend/app/models/__init__.py`:
```python
from app.models.instrument import Instrument
from app.models.market import FxRate, PriceBar, QuoteCache
from app.models.portfolio import Portfolio, Position
from app.models.user import User

__all__ = ["FxRate", "Instrument", "Portfolio", "Position", "PriceBar", "QuoteCache", "User"]
```

- [ ] **Step 4: Write migration 0002**

`backend/alembic/versions/0002_portfolio_core.py` — hand-written, `revision = "0002"`, `down_revision = "0001"`, creating all five tables exactly as the models define (columns/types/uniques/indexes as above; use `sa.Numeric(18, 6)` etc.). Verify chain first: `alembic heads` → `0001`. Then run `alembic upgrade head`.
Expected: `Running upgrade 0001 -> 0002`

- [ ] **Step 5: Run tests + lint**

Run: `cd backend && pytest -v && ruff check .`
Expected: all PASS

- [ ] **Step 6: Commit**

```bash
git add -A
git commit -m "feat: domain models — instruments, portfolios, positions, quote cache, price bars, fx rates

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 6: Portfolio CRUD API

**Files:**
- Create: `backend/app/api/portfolios.py`, `backend/app/schemas/__init__.py`, `backend/app/schemas/portfolio.py`
- Modify: `backend/app/main.py`
- Test: `backend/tests/test_portfolios.py`

**Interfaces:**
- Consumes: `CurrentUser`, `SessionDep`, `Portfolio`.
- Produces routes (all require auth; all scoped to `user_id == current_user.id`, 404 on other users' rows):
  - `GET /api/portfolios` → `list[PortfolioOut]`
  - `POST /api/portfolios` `{name, kind, base_currency}` → `PortfolioOut` (201)
  - `PATCH /api/portfolios/{id}` `{name?, base_currency?}` → `PortfolioOut`
  - `DELETE /api/portfolios/{id}` → 204
  - `PortfolioOut = {id, name, kind, base_currency, position_count}`
- Produces schema module pattern (`app/schemas/*.py`) reused by Tasks 7–11.

- [ ] **Step 1: Write the failing tests**

`backend/tests/test_portfolios.py`:
```python
import pytest

pytestmark = pytest.mark.asyncio(loop_scope="session")


async def test_create_list_update_delete_portfolio(auth_client):
    created = await auth_client.post(
        "/api/portfolios", json={"name": "Growth", "kind": "real", "base_currency": "GBP"}
    )
    assert created.status_code == 201
    pid = created.json()["id"]

    listed = await auth_client.get("/api/portfolios")
    assert [p["name"] for p in listed.json()] == ["Growth"]

    patched = await auth_client.patch(f"/api/portfolios/{pid}", json={"name": "Core Growth"})
    assert patched.json()["name"] == "Core Growth"

    deleted = await auth_client.delete(f"/api/portfolios/{pid}")
    assert deleted.status_code == 204
    assert (await auth_client.get("/api/portfolios")).json() == []


async def test_invalid_kind_rejected(auth_client):
    resp = await auth_client.post(
        "/api/portfolios", json={"name": "X", "kind": "maybe", "base_currency": "GBP"}
    )
    assert resp.status_code == 422


async def test_requires_auth(client):
    assert (await client.get("/api/portfolios")).status_code == 401
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && pytest tests/test_portfolios.py -v`
Expected: FAIL — 404 (routes don't exist)

- [ ] **Step 3: Implement schemas + router**

`backend/app/schemas/portfolio.py`:
```python
from typing import Literal

from pydantic import BaseModel, Field


class PortfolioCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    kind: Literal["real", "watchlist"]
    base_currency: str = Field(pattern=r"^[A-Z]{3}$")


class PortfolioUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    base_currency: str | None = Field(default=None, pattern=r"^[A-Z]{3}$")


class PortfolioOut(BaseModel):
    id: int
    name: str
    kind: str
    base_currency: str
    position_count: int
```

`backend/app/api/portfolios.py`:
```python
from fastapi import APIRouter, HTTPException
from sqlalchemy import select

from app.api.deps import CurrentUser, SessionDep
from app.models import Portfolio
from app.schemas.portfolio import PortfolioCreate, PortfolioOut, PortfolioUpdate

router = APIRouter(prefix="/api/portfolios", tags=["portfolios"])


def _out(pf: Portfolio) -> PortfolioOut:
    return PortfolioOut(
        id=pf.id, name=pf.name, kind=pf.kind,
        base_currency=pf.base_currency, position_count=len(pf.positions),
    )


async def get_owned_portfolio(db: SessionDep, user: CurrentUser, portfolio_id: int) -> Portfolio:
    pf = await db.get(Portfolio, portfolio_id)
    if pf is None or pf.user_id != user.id:
        raise HTTPException(status_code=404, detail="Portfolio not found")
    return pf


@router.get("", response_model=list[PortfolioOut])
async def list_portfolios(db: SessionDep, user: CurrentUser) -> list[PortfolioOut]:
    result = await db.execute(
        select(Portfolio).where(Portfolio.user_id == user.id).order_by(Portfolio.id)
    )
    return [_out(p) for p in result.scalars().all()]


@router.post("", response_model=PortfolioOut, status_code=201)
async def create_portfolio(body: PortfolioCreate, db: SessionDep, user: CurrentUser) -> PortfolioOut:
    pf = Portfolio(user_id=user.id, **body.model_dump())
    db.add(pf)
    await db.commit()
    await db.refresh(pf)
    return _out(pf)


@router.patch("/{portfolio_id}", response_model=PortfolioOut)
async def update_portfolio(
    portfolio_id: int, body: PortfolioUpdate, db: SessionDep, user: CurrentUser
) -> PortfolioOut:
    pf = await get_owned_portfolio(db, user, portfolio_id)
    for field, value in body.model_dump(exclude_none=True).items():
        setattr(pf, field, value)
    await db.commit()
    await db.refresh(pf)
    return _out(pf)


@router.delete("/{portfolio_id}", status_code=204)
async def delete_portfolio(portfolio_id: int, db: SessionDep, user: CurrentUser) -> None:
    pf = await get_owned_portfolio(db, user, portfolio_id)
    await db.delete(pf)
    await db.commit()
```

In `backend/app/main.py`, add:
```python
from app.api.portfolios import router as portfolios_router
# inside create_app(), after auth router:
app.include_router(portfolios_router)
```
Create empty `backend/app/schemas/__init__.py`.

- [ ] **Step 4: Run tests + lint**

Run: `cd backend && pytest -v && ruff check .`
Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add -A
git commit -m "feat: portfolio CRUD API with per-user ownership scoping

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 7: Position CRUD API (nested under portfolios)

**Files:**
- Create: `backend/app/api/positions.py`, `backend/app/schemas/position.py`
- Modify: `backend/app/main.py`
- Test: `backend/tests/test_positions.py`

**Interfaces:**
- Consumes: `get_owned_portfolio` from `app.api.portfolios`, `Instrument`, `Position`.
- Produces routes:
  - `GET /api/portfolios/{id}/positions` → `list[PositionOut]`
  - `POST /api/portfolios/{id}/positions` `{symbol, quantity?, avg_cost?, notes?}` → `PositionOut` (201). Instrument row must already exist for the symbol (created by the lookup endpoint, Task 9) — 422 if unknown.
  - `PATCH /api/positions/{position_id}` `{quantity?, avg_cost?, notes?}` → `PositionOut`
  - `DELETE /api/positions/{position_id}` → 204
  - `PositionOut = {id, symbol, name, market, currency, quantity, avg_cost, notes}` (quantity/avg_cost serialised as strings via Decimal)
- Test helper `make_instrument(db_session, symbol, **overrides) -> Instrument` added to conftest, reused by Tasks 10–11 tests.

- [ ] **Step 1: Add the conftest helper**

Append to `backend/tests/conftest.py`:
```python
from app.models import Instrument  # noqa: E402


async def _make_instrument(db_session, symbol: str, **overrides) -> Instrument:
    defaults = dict(
        symbol=symbol, name=f"{symbol} Co", exchange="NMS", market="US", currency="USD"
    )
    inst = Instrument(**{**defaults, **overrides})
    db_session.add(inst)
    await db_session.commit()
    return inst


@pytest_asyncio.fixture
def make_instrument(db_session):
    async def _factory(symbol: str, **overrides) -> Instrument:
        return await _make_instrument(db_session, symbol, **overrides)

    return _factory
```

- [ ] **Step 2: Write the failing tests**

`backend/tests/test_positions.py`:
```python
import pytest

pytestmark = pytest.mark.asyncio(loop_scope="session")


async def _make_portfolio(auth_client, kind="real"):
    resp = await auth_client.post(
        "/api/portfolios", json={"name": "P", "kind": kind, "base_currency": "GBP"}
    )
    return resp.json()["id"]


async def test_position_crud(auth_client, make_instrument):
    await make_instrument("AAPL")
    pid = await _make_portfolio(auth_client)

    created = await auth_client.post(
        f"/api/portfolios/{pid}/positions",
        json={"symbol": "AAPL", "quantity": "10", "avg_cost": "150.25"},
    )
    assert created.status_code == 201
    pos_id = created.json()["id"]
    assert created.json()["symbol"] == "AAPL"

    patched = await auth_client.patch(f"/api/positions/{pos_id}", json={"quantity": "12"})
    assert patched.json()["quantity"] == "12.000000"

    listed = await auth_client.get(f"/api/portfolios/{pid}/positions")
    assert len(listed.json()) == 1

    assert (await auth_client.delete(f"/api/positions/{pos_id}")).status_code == 204


async def test_unknown_symbol_rejected(auth_client):
    pid = await _make_portfolio(auth_client)
    resp = await auth_client.post(
        f"/api/portfolios/{pid}/positions", json={"symbol": "NOPE", "quantity": "1"}
    )
    assert resp.status_code == 422


async def test_watchlist_entry_without_quantity(auth_client, make_instrument):
    await make_instrument("0700.HK", market="HK", currency="HKD")
    pid = await _make_portfolio(auth_client, kind="watchlist")
    resp = await auth_client.post(
        f"/api/portfolios/{pid}/positions", json={"symbol": "0700.HK"}
    )
    assert resp.status_code == 201
    assert resp.json()["quantity"] is None
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `cd backend && pytest tests/test_positions.py -v`
Expected: FAIL — 404/405 (routes don't exist)

- [ ] **Step 4: Implement schema + router**

`backend/app/schemas/position.py`:
```python
from decimal import Decimal

from pydantic import BaseModel, field_serializer


class PositionCreate(BaseModel):
    symbol: str
    quantity: Decimal | None = None
    avg_cost: Decimal | None = None
    notes: str | None = None


class PositionUpdate(BaseModel):
    quantity: Decimal | None = None
    avg_cost: Decimal | None = None
    notes: str | None = None


class PositionOut(BaseModel):
    id: int
    symbol: str
    name: str
    market: str
    currency: str
    quantity: Decimal | None
    avg_cost: Decimal | None
    notes: str | None

    @field_serializer("quantity", "avg_cost")
    def _ser_decimal(self, v: Decimal | None) -> str | None:
        return None if v is None else str(v)
```

`backend/app/api/positions.py`:
```python
from fastapi import APIRouter, HTTPException
from sqlalchemy import select

from app.api.deps import CurrentUser, SessionDep
from app.api.portfolios import get_owned_portfolio
from app.models import Instrument, Portfolio, Position
from app.schemas.position import PositionCreate, PositionOut, PositionUpdate

router = APIRouter(prefix="/api", tags=["positions"])


def _out(pos: Position) -> PositionOut:
    return PositionOut(
        id=pos.id, symbol=pos.instrument.symbol, name=pos.instrument.name,
        market=pos.instrument.market, currency=pos.instrument.currency,
        quantity=pos.quantity, avg_cost=pos.avg_cost, notes=pos.notes,
    )


async def _get_owned_position(db: SessionDep, user: CurrentUser, position_id: int) -> Position:
    pos = await db.get(Position, position_id)
    if pos is None:
        raise HTTPException(status_code=404, detail="Position not found")
    pf = await db.get(Portfolio, pos.portfolio_id)
    if pf is None or pf.user_id != user.id:
        raise HTTPException(status_code=404, detail="Position not found")
    return pos


@router.get("/portfolios/{portfolio_id}/positions", response_model=list[PositionOut])
async def list_positions(portfolio_id: int, db: SessionDep, user: CurrentUser):
    pf = await get_owned_portfolio(db, user, portfolio_id)
    return [_out(p) for p in pf.positions]


@router.post("/portfolios/{portfolio_id}/positions", response_model=PositionOut, status_code=201)
async def create_position(
    portfolio_id: int, body: PositionCreate, db: SessionDep, user: CurrentUser
):
    pf = await get_owned_portfolio(db, user, portfolio_id)
    inst = (
        await db.execute(select(Instrument).where(Instrument.symbol == body.symbol))
    ).scalar_one_or_none()
    if inst is None:
        raise HTTPException(status_code=422, detail=f"Unknown symbol {body.symbol}")
    pos = Position(
        portfolio_id=pf.id, instrument_id=inst.id,
        quantity=body.quantity, avg_cost=body.avg_cost, notes=body.notes,
    )
    db.add(pos)
    await db.commit()
    await db.refresh(pos)
    return _out(pos)


@router.patch("/positions/{position_id}", response_model=PositionOut)
async def update_position(
    position_id: int, body: PositionUpdate, db: SessionDep, user: CurrentUser
):
    pos = await _get_owned_position(db, user, position_id)
    for field, value in body.model_dump(exclude_unset=True).items():
        setattr(pos, field, value)
    await db.commit()
    await db.refresh(pos)
    return _out(pos)


@router.delete("/positions/{position_id}", status_code=204)
async def delete_position(position_id: int, db: SessionDep, user: CurrentUser) -> None:
    pos = await _get_owned_position(db, user, position_id)
    await db.delete(pos)
    await db.commit()
```

In `backend/app/main.py`: `from app.api.positions import router as positions_router` and `app.include_router(positions_router)`.

- [ ] **Step 5: Run tests + lint, commit**

Run: `cd backend && pytest -v && ruff check .`
Expected: all PASS

```bash
git add -A
git commit -m "feat: position CRUD API — holdings and watchlist entries

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 8: Market data provider — interface, Yahoo implementation, quote cache service

**Files:**
- Create: `backend/app/services/__init__.py`, `backend/app/services/market_data/__init__.py`, `backend/app/services/market_data/base.py`, `backend/app/services/market_data/yahoo.py`, `backend/app/services/market_data/quotes.py`
- Create: `backend/tests/fixtures/yahoo_quote_aapl.json`, `backend/tests/fixtures/yahoo_quote_hsba.json`
- Test: `backend/tests/test_yahoo_provider.py`, `backend/tests/test_quote_service.py`

**Interfaces:**
- Consumes: `QuoteCache` model, `get_session`.
- Produces:
  - `base.Quote` frozen dataclass: `symbol: str, price: Decimal, currency: str, previous_close: Decimal | None, as_of: datetime`
  - `base.InstrumentInfo` frozen dataclass: `symbol: str, name: str, exchange: str, market: str, currency: str, sector: str | None, industry: str | None`
  - `base.MarketDataProvider` Protocol: `async get_quotes(symbols: list[str]) -> dict[str, Quote]`, `async get_fx_rate(base: str, quote: str) -> Decimal`, `async lookup(symbol: str) -> InstrumentInfo | None`
  - `base.infer_market(symbol: str) -> str` — `.L` → `"UK"`, `.HK` → `"HK"`, else `"US"`
  - `yahoo.YahooProvider` implementing the protocol; pure parsing isolated in `yahoo.parse_quote(symbol: str, info: dict) -> Quote | None` and `yahoo.parse_instrument_info(symbol: str, info: dict) -> InstrumentInfo | None` (fixture-testable, no network)
  - `quotes.QuoteService(provider).get_quotes(db, symbols) -> dict[str, Quote]` — serves from `QuoteCache` when `fetched_at` < 15 min old; on provider failure returns stale cache entries (any age) and omits unknowns; upserts fresh quotes into cache
  - module-level singleton accessor `quotes.get_quote_service() -> QuoteService` (used by API tasks; tests construct their own with fake providers)

- [ ] **Step 1: Create recorded fixtures (synthetic, Yahoo `Ticker.info`-shaped)**

`backend/tests/fixtures/yahoo_quote_aapl.json`:
```json
{
  "symbol": "AAPL", "shortName": "Apple Inc.", "longName": "Apple Inc.",
  "exchange": "NMS", "currency": "USD",
  "regularMarketPrice": 231.55, "regularMarketPreviousClose": 229.10,
  "sector": "Technology", "industry": "Consumer Electronics"
}
```

`backend/tests/fixtures/yahoo_quote_hsba.json`:
```json
{
  "symbol": "HSBA.L", "shortName": "HSBC HOLDINGS PLC", "longName": "HSBC Holdings plc",
  "exchange": "LSE", "currency": "GBp",
  "regularMarketPrice": 702.30, "regularMarketPreviousClose": 698.50,
  "sector": "Financial Services", "industry": "Banks - Diversified"
}
```

- [ ] **Step 2: Write the failing parser tests**

`backend/tests/test_yahoo_provider.py`:
```python
import json
from decimal import Decimal
from pathlib import Path

from app.services.market_data.base import infer_market
from app.services.market_data.yahoo import parse_instrument_info, parse_quote

FIXTURES = Path(__file__).parent / "fixtures"


def _load(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text())


def test_parse_us_quote():
    q = parse_quote("AAPL", _load("yahoo_quote_aapl.json"))
    assert q.price == Decimal("231.55")
    assert q.currency == "USD"
    assert q.previous_close == Decimal("229.1")


def test_parse_uk_quote_keeps_pence_currency():
    q = parse_quote("HSBA.L", _load("yahoo_quote_hsba.json"))
    assert q.currency == "GBp"  # pence preserved; valuation layer converts


def test_parse_instrument_info_infers_market():
    info = parse_instrument_info("HSBA.L", _load("yahoo_quote_hsba.json"))
    assert info.market == "UK"
    assert info.sector == "Financial Services"


def test_parse_quote_missing_price_returns_none():
    assert parse_quote("AAPL", {"currency": "USD"}) is None


def test_infer_market():
    assert infer_market("AAPL") == "US"
    assert infer_market("HSBA.L") == "UK"
    assert infer_market("0700.HK") == "HK"
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `cd backend && pytest tests/test_yahoo_provider.py -v`
Expected: FAIL — ModuleNotFoundError

- [ ] **Step 4: Implement base + yahoo provider**

`backend/app/services/market_data/base.py`:
```python
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Protocol


@dataclass(frozen=True)
class Quote:
    symbol: str
    price: Decimal
    currency: str
    previous_close: Decimal | None
    as_of: datetime


@dataclass(frozen=True)
class InstrumentInfo:
    symbol: str
    name: str
    exchange: str
    market: str
    currency: str
    sector: str | None
    industry: str | None


def infer_market(symbol: str) -> str:
    if symbol.upper().endswith(".L"):
        return "UK"
    if symbol.upper().endswith(".HK"):
        return "HK"
    return "US"


class MarketDataProvider(Protocol):
    async def get_quotes(self, symbols: list[str]) -> dict[str, Quote]: ...
    async def get_fx_rate(self, base: str, quote: str) -> Decimal: ...
    async def lookup(self, symbol: str) -> InstrumentInfo | None: ...
```

`backend/app/services/market_data/yahoo.py`:
```python
import asyncio
from datetime import UTC, datetime
from decimal import Decimal

from app.services.market_data.base import InstrumentInfo, Quote, infer_market


def parse_quote(symbol: str, info: dict) -> Quote | None:
    price = info.get("regularMarketPrice")
    currency = info.get("currency")
    if price is None or currency is None:
        return None
    prev = info.get("regularMarketPreviousClose")
    return Quote(
        symbol=symbol,
        price=Decimal(str(price)),
        currency=currency,
        previous_close=None if prev is None else Decimal(str(prev)),
        as_of=datetime.now(UTC),
    )


def parse_instrument_info(symbol: str, info: dict) -> InstrumentInfo | None:
    name = info.get("longName") or info.get("shortName")
    currency = info.get("currency")
    if name is None or currency is None:
        return None
    return InstrumentInfo(
        symbol=symbol,
        name=name,
        exchange=info.get("exchange", ""),
        market=infer_market(symbol),
        currency=currency,
        sector=info.get("sector"),
        industry=info.get("industry"),
    )


class YahooProvider:
    """yfinance-backed provider. yfinance is sync — calls run in a thread."""

    def _fetch_info(self, symbol: str) -> dict:
        import yfinance as yf

        return yf.Ticker(symbol).info or {}

    async def get_quotes(self, symbols: list[str]) -> dict[str, Quote]:
        results: dict[str, Quote] = {}
        infos = await asyncio.gather(
            *(asyncio.to_thread(self._fetch_info, s) for s in symbols),
            return_exceptions=True,
        )
        for symbol, info in zip(symbols, infos, strict=True):
            if isinstance(info, BaseException):
                continue
            quote = parse_quote(symbol, info)
            if quote is not None:
                results[symbol] = quote
        return results

    async def get_fx_rate(self, base: str, quote: str) -> Decimal:
        if base == quote:
            return Decimal("1")
        info = await asyncio.to_thread(self._fetch_info, f"{base}{quote}=X")
        price = info.get("regularMarketPrice")
        if price is None:
            raise LookupError(f"No FX rate for {base}{quote}")
        return Decimal(str(price))

    async def lookup(self, symbol: str) -> InstrumentInfo | None:
        try:
            info = await asyncio.to_thread(self._fetch_info, symbol)
        except Exception:
            return None
        return parse_instrument_info(symbol, info)
```

- [ ] **Step 5: Write the failing quote-service tests (fake provider)**

`backend/tests/test_quote_service.py`:
```python
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from app.models import QuoteCache
from app.services.market_data.base import Quote
from app.services.market_data.quotes import QuoteService

pytestmark = pytest.mark.asyncio(loop_scope="session")


def _quote(symbol: str, price: str) -> Quote:
    return Quote(
        symbol=symbol, price=Decimal(price), currency="USD",
        previous_close=Decimal(price), as_of=datetime.now(UTC),
    )


class FakeProvider:
    def __init__(self, quotes: dict[str, Quote] | None = None, fail: bool = False):
        self.quotes = quotes or {}
        self.fail = fail
        self.calls = 0

    async def get_quotes(self, symbols):
        self.calls += 1
        if self.fail:
            raise RuntimeError("provider down")
        return {s: self.quotes[s] for s in symbols if s in self.quotes}

    async def get_fx_rate(self, base, quote):
        raise NotImplementedError

    async def lookup(self, symbol):
        return None


async def test_fresh_fetch_populates_cache(db_session):
    svc = QuoteService(FakeProvider({"AAPL": _quote("AAPL", "100")}))
    result = await svc.get_quotes(db_session, ["AAPL"])
    assert result["AAPL"].price == Decimal("100")
    assert await db_session.get(QuoteCache, "AAPL") is not None


async def test_cache_hit_skips_provider(db_session):
    db_session.add(QuoteCache(
        symbol="AAPL", price=Decimal("99"), currency="USD",
        previous_close=Decimal("98"), fetched_at=datetime.now(UTC).replace(tzinfo=None),
    ))
    await db_session.commit()
    provider = FakeProvider({"AAPL": _quote("AAPL", "100")})
    svc = QuoteService(provider)
    result = await svc.get_quotes(db_session, ["AAPL"])
    assert result["AAPL"].price == Decimal("99.0000")
    assert provider.calls == 0


async def test_provider_failure_serves_stale_cache(db_session):
    stale = datetime.now(UTC).replace(tzinfo=None) - timedelta(hours=6)
    db_session.add(QuoteCache(
        symbol="AAPL", price=Decimal("95"), currency="USD",
        previous_close=None, fetched_at=stale,
    ))
    await db_session.commit()
    svc = QuoteService(FakeProvider(fail=True))
    result = await svc.get_quotes(db_session, ["AAPL", "MSFT"])
    assert result["AAPL"].price == Decimal("95.0000")
    assert "MSFT" not in result
```

- [ ] **Step 6: Run tests to verify they fail, then implement QuoteService**

Run: `cd backend && pytest tests/test_quote_service.py -v` → FAIL (module missing)

`backend/app/services/market_data/quotes.py`:
```python
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import QuoteCache
from app.services.market_data.base import MarketDataProvider, Quote
from app.services.market_data.yahoo import YahooProvider

QUOTE_TTL = timedelta(minutes=15)


def _cache_to_quote(row: QuoteCache) -> Quote:
    return Quote(
        symbol=row.symbol, price=row.price, currency=row.currency,
        previous_close=row.previous_close,
        as_of=row.fetched_at.replace(tzinfo=UTC),
    )


class QuoteService:
    def __init__(self, provider: MarketDataProvider):
        self.provider = provider

    async def get_quotes(self, db: AsyncSession, symbols: list[str]) -> dict[str, Quote]:
        now = datetime.now(UTC).replace(tzinfo=None)
        rows = (
            await db.execute(select(QuoteCache).where(QuoteCache.symbol.in_(symbols)))
        ).scalars().all()
        cached = {r.symbol: r for r in rows}

        fresh = {s: _cache_to_quote(r) for s, r in cached.items() if now - r.fetched_at < QUOTE_TTL}
        missing = [s for s in symbols if s not in fresh]
        if not missing:
            return fresh

        try:
            fetched = await self.provider.get_quotes(missing)
        except Exception:
            fetched = {}

        for symbol, quote in fetched.items():
            row = cached.get(symbol)
            if row is None:
                row = QuoteCache(symbol=symbol, price=quote.price, currency=quote.currency,
                                 previous_close=quote.previous_close, fetched_at=now)
                db.add(row)
            else:
                row.price = quote.price
                row.currency = quote.currency
                row.previous_close = quote.previous_close
                row.fetched_at = now
        await db.commit()

        result = fresh | fetched
        for symbol in missing:  # stale-cache fallback for anything the provider missed
            if symbol not in result and symbol in cached:
                result[symbol] = _cache_to_quote(cached[symbol])
        return result


_service: QuoteService | None = None


def get_quote_service() -> QuoteService:
    global _service
    if _service is None:
        _service = QuoteService(YahooProvider())
    return _service
```

Create empty `backend/app/services/__init__.py` and `backend/app/services/market_data/__init__.py`.

- [ ] **Step 7: Run all tests + lint, commit**

Run: `cd backend && pytest -v && ruff check .`
Expected: all PASS

```bash
git add -A
git commit -m "feat: market data layer — provider interface, Yahoo parsing, TTL quote cache with stale fallback

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 9: Instrument lookup/search endpoint

**Files:**
- Create: `backend/app/api/instruments.py`
- Modify: `backend/app/main.py`
- Test: `backend/tests/test_instruments_api.py`

**Interfaces:**
- Consumes: `MarketDataProvider.lookup`, `Instrument` model, `get_quote_service`.
- Produces: `GET /api/instruments/lookup?symbol=AAPL` → `{symbol, name, exchange, market, currency, sector, industry, known: bool}`; side effect: creates the `Instrument` row if the provider recognises it (so `POST positions` succeeds afterwards). 404 if provider can't resolve. Provider is injected via FastAPI dependency `app.api.instruments.get_provider` — overridable in tests.

- [ ] **Step 1: Write the failing tests**

`backend/tests/test_instruments_api.py`:
```python
import pytest
from sqlalchemy import select

from app.api.instruments import get_provider
from app.models import Instrument
from app.services.market_data.base import InstrumentInfo

pytestmark = pytest.mark.asyncio(loop_scope="session")

TENCENT = InstrumentInfo(
    symbol="0700.HK", name="Tencent Holdings", exchange="HKG",
    market="HK", currency="HKD", sector="Communication Services", industry="Internet",
)


class FakeLookupProvider:
    async def lookup(self, symbol):
        return TENCENT if symbol == "0700.HK" else None

    async def get_quotes(self, symbols):
        return {}

    async def get_fx_rate(self, base, quote):
        raise NotImplementedError


def _override(client):
    # client fixture exposes the app via its transport
    app = client._transport.app  # httpx.ASGITransport
    app.dependency_overrides[get_provider] = lambda: FakeLookupProvider()


async def test_lookup_creates_instrument(auth_client, db_session):
    _override(auth_client)
    resp = await auth_client.get("/api/instruments/lookup", params={"symbol": "0700.HK"})
    assert resp.status_code == 200
    assert resp.json()["market"] == "HK"
    row = (
        await db_session.execute(select(Instrument).where(Instrument.symbol == "0700.HK"))
    ).scalar_one()
    assert row.name == "Tencent Holdings"


async def test_lookup_unknown_404(auth_client):
    _override(auth_client)
    resp = await auth_client.get("/api/instruments/lookup", params={"symbol": "NOPE"})
    assert resp.status_code == 404
```

- [ ] **Step 2: Run to verify fail, implement router**

Run: `cd backend && pytest tests/test_instruments_api.py -v` → FAIL

`backend/app/api/instruments.py`:
```python
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select

from app.api.deps import CurrentUser, SessionDep
from app.models import Instrument
from app.services.market_data.base import MarketDataProvider
from app.services.market_data.quotes import get_quote_service

router = APIRouter(prefix="/api/instruments", tags=["instruments"])


def get_provider() -> MarketDataProvider:
    return get_quote_service().provider


class InstrumentOut(BaseModel):
    symbol: str
    name: str
    exchange: str
    market: str
    currency: str
    sector: str | None
    industry: str | None
    known: bool


@router.get("/lookup", response_model=InstrumentOut)
async def lookup(
    symbol: str,
    db: SessionDep,
    user: CurrentUser,
    provider: MarketDataProvider = Depends(get_provider),
) -> InstrumentOut:
    existing = (
        await db.execute(select(Instrument).where(Instrument.symbol == symbol))
    ).scalar_one_or_none()
    if existing is not None:
        return InstrumentOut(
            symbol=existing.symbol, name=existing.name, exchange=existing.exchange,
            market=existing.market, currency=existing.currency,
            sector=existing.sector, industry=existing.industry, known=True,
        )
    info = await provider.lookup(symbol)
    if info is None:
        raise HTTPException(status_code=404, detail=f"Symbol {symbol} not found")
    inst = Instrument(
        symbol=info.symbol, name=info.name, exchange=info.exchange, market=info.market,
        currency=info.currency, sector=info.sector, industry=info.industry,
    )
    db.add(inst)
    await db.commit()
    return InstrumentOut(**info.__dict__, known=False)
```

In `backend/app/main.py`: include `instruments` router like the others.

- [ ] **Step 3: Run tests + lint, commit**

Run: `cd backend && pytest -v && ruff check .`
Expected: all PASS

```bash
git add -A
git commit -m "feat: instrument lookup endpoint — validates symbols via provider, caches metadata

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 10: Valuation service — FX conversion, GBp handling, P&L, portfolio summary + endpoints

**Files:**
- Create: `backend/app/services/valuation.py`, `backend/app/api/valuation.py`
- Modify: `backend/app/main.py`
- Test: `backend/tests/test_valuation.py`, `backend/tests/test_valuation_api.py`

**Interfaces:**
- Consumes: `Quote`, `QuoteService`, `Portfolio`/`Position`/`Instrument`, `FxRate` model, provider `get_fx_rate`.
- Produces:
  - `valuation.normalise(amount: Decimal, currency: str) -> tuple[Decimal, str]` — GBp→GBP (÷100), otherwise unchanged
  - `valuation.FxService(provider).get_rate(db, base: str, quote: str) -> Decimal` — daily-cached in `fx_rates` (today's row wins; provider fallback; on provider failure most-recent row of any date; raises `LookupError` if none)
  - `valuation.PositionValuation` dataclass: `position_id, symbol, name, market, quantity, avg_cost, native_currency, price, market_value_base, cost_basis_base, unrealized_pnl_base, unrealized_pnl_pct, day_change_base, quote_as_of` (all money Decimal, in portfolio base ccy; `None`s when quote missing or watchlist)
  - `valuation.PortfolioSummary` dataclass: `portfolio_id, base_currency, total_value, total_cost, total_pnl, total_pnl_pct, day_change, currency_exposure: dict[str, Decimal]` (exposure keyed by normalised native ccy, values = base-ccy market value), `positions: list[PositionValuation]`, `priced_positions: int, unpriced_positions: int`
  - `valuation.value_portfolio(db, portfolio: Portfolio, quote_service: QuoteService, fx: FxService) -> PortfolioSummary`
  - Routes: `GET /api/portfolios/{id}/valuation` → PortfolioSummary JSON (Decimals as strings); `GET /api/dashboard` → `{portfolios: [{id, name, kind, base_currency, total_value, day_change, total_pnl_pct}], as_of}` (each portfolio valued in its own base ccy; watchlists valued too, with null totals where no quantities)

- [ ] **Step 1: Write the failing unit tests**

`backend/tests/test_valuation.py`:
```python
from datetime import UTC, datetime
from decimal import Decimal

import pytest

from app.models import FxRate, Portfolio, Position, User
from app.services.market_data.base import Quote
from app.services.valuation import FxService, normalise, value_portfolio
from app.services.market_data.quotes import QuoteService

pytestmark = pytest.mark.asyncio(loop_scope="session")


def test_normalise_pence():
    amount, ccy = normalise(Decimal("702.30"), "GBp")
    assert (amount, ccy) == (Decimal("7.023"), "GBP")
    assert normalise(Decimal("5"), "USD") == (Decimal("5"), "USD")


class FakeFxProvider:
    async def get_fx_rate(self, base, quote):
        return {"USDGBP": Decimal("0.8"), "HKDGBP": Decimal("0.1")}[f"{base}{quote}"]

    async def get_quotes(self, symbols):
        return {}

    async def lookup(self, symbol):
        return None


class FakeQuoteProvider:
    def __init__(self, quotes):
        self._q = quotes

    async def get_quotes(self, symbols):
        return {s: q for s, q in self._q.items() if s in symbols}

    async def get_fx_rate(self, base, quote):
        raise NotImplementedError

    async def lookup(self, symbol):
        return None


def _quote(symbol, price, currency, prev):
    return Quote(symbol=symbol, price=Decimal(price), currency=currency,
                 previous_close=Decimal(prev), as_of=datetime.now(UTC))


async def test_fx_service_caches_daily(db_session):
    fx = FxService(FakeFxProvider())
    rate = await fx.get_rate(db_session, "USD", "GBP")
    assert rate == Decimal("0.8")
    row_count = len((await db_session.execute(
        __import__("sqlalchemy").select(FxRate)
    )).scalars().all())
    assert row_count == 1
    # same-currency shortcut
    assert await fx.get_rate(db_session, "GBP", "GBP") == Decimal("1")


async def test_value_portfolio_mixed_currencies(db_session, make_instrument):
    user = User(email="v@test.dev", password_hash="x")
    db_session.add(user)
    await db_session.flush()
    aapl = await make_instrument("AAPL")  # USD
    hsba = await make_instrument("HSBA.L", market="UK", currency="GBp", exchange="LSE")
    pf = Portfolio(user_id=user.id, name="Mix", kind="real", base_currency="GBP")
    db_session.add(pf)
    await db_session.flush()
    db_session.add_all([
        Position(portfolio_id=pf.id, instrument_id=aapl.id,
                 quantity=Decimal("10"), avg_cost=Decimal("100")),   # USD cost
        Position(portfolio_id=pf.id, instrument_id=hsba.id,
                 quantity=Decimal("200"), avg_cost=Decimal("650")),  # GBp cost
    ])
    await db_session.commit()
    await db_session.refresh(pf, ["positions"])

    quotes = QuoteService(FakeQuoteProvider({
        "AAPL": _quote("AAPL", "150", "USD", "148"),
        "HSBA.L": _quote("HSBA.L", "700", "GBp", "690"),
    }))
    summary = await value_portfolio(db_session, pf, quotes, FxService(FakeFxProvider()))

    # AAPL: 10 * 150 USD * 0.8 = 1200 GBP; HSBA: 200 * 7.00 GBP = 1400 GBP
    assert summary.total_value == Decimal("2600.00")
    # cost: 10*100*0.8 + 200*6.50 = 800 + 1300 = 2100
    assert summary.total_cost == Decimal("2100.00")
    assert summary.total_pnl == Decimal("500.00")
    assert summary.currency_exposure == {"USD": Decimal("1200.00"), "GBP": Decimal("1400.00")}
    assert summary.priced_positions == 2


async def test_missing_quote_degrades(db_session, make_instrument):
    user = User(email="v2@test.dev", password_hash="x")
    db_session.add(user)
    await db_session.flush()
    inst = await make_instrument("MYST")
    pf = Portfolio(user_id=user.id, name="M", kind="real", base_currency="GBP")
    db_session.add(pf)
    await db_session.flush()
    db_session.add(Position(portfolio_id=pf.id, instrument_id=inst.id,
                            quantity=Decimal("5"), avg_cost=Decimal("10")))
    await db_session.commit()
    await db_session.refresh(pf, ["positions"])

    summary = await value_portfolio(
        db_session, pf, QuoteService(FakeQuoteProvider({})), FxService(FakeFxProvider())
    )
    assert summary.unpriced_positions == 1
    assert summary.positions[0].market_value_base is None
```

- [ ] **Step 2: Run to verify fail, implement valuation service**

Run: `cd backend && pytest tests/test_valuation.py -v` → FAIL

`backend/app/services/valuation.py`:
```python
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from decimal import ROUND_HALF_UP, Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import FxRate, Portfolio
from app.services.market_data.base import MarketDataProvider
from app.services.market_data.quotes import QuoteService

TWO_DP = Decimal("0.01")


def normalise(amount: Decimal, currency: str) -> tuple[Decimal, str]:
    """Convert minor-unit listings to major units. GBp (LSE pence) -> GBP."""
    if currency == "GBp":
        return amount / Decimal("100"), "GBP"
    return amount, currency


class FxService:
    def __init__(self, provider: MarketDataProvider):
        self.provider = provider

    async def get_rate(self, db: AsyncSession, base: str, quote: str) -> Decimal:
        if base == quote:
            return Decimal("1")
        pair = f"{base}{quote}"
        today = date.today()
        row = (
            await db.execute(
                select(FxRate).where(FxRate.pair == pair, FxRate.date == today)
            )
        ).scalar_one_or_none()
        if row is not None:
            return row.rate
        try:
            rate = await self.provider.get_fx_rate(base, quote)
        except Exception:
            fallback = (
                await db.execute(
                    select(FxRate).where(FxRate.pair == pair).order_by(FxRate.date.desc()).limit(1)
                )
            ).scalar_one_or_none()
            if fallback is None:
                raise LookupError(f"No FX rate available for {pair}") from None
            return fallback.rate
        db.add(FxRate(pair=pair, date=today, rate=rate))
        await db.commit()
        return rate


@dataclass
class PositionValuation:
    position_id: int
    symbol: str
    name: str
    market: str
    quantity: Decimal | None
    avg_cost: Decimal | None
    native_currency: str
    price: Decimal | None
    market_value_base: Decimal | None
    cost_basis_base: Decimal | None
    unrealized_pnl_base: Decimal | None
    unrealized_pnl_pct: Decimal | None
    day_change_base: Decimal | None
    quote_as_of: datetime | None


@dataclass
class PortfolioSummary:
    portfolio_id: int
    base_currency: str
    total_value: Decimal | None
    total_cost: Decimal | None
    total_pnl: Decimal | None
    total_pnl_pct: Decimal | None
    day_change: Decimal | None
    currency_exposure: dict[str, Decimal] = field(default_factory=dict)
    positions: list[PositionValuation] = field(default_factory=list)
    priced_positions: int = 0
    unpriced_positions: int = 0


def _round(v: Decimal) -> Decimal:
    return v.quantize(TWO_DP, rounding=ROUND_HALF_UP)


async def value_portfolio(
    db: AsyncSession, portfolio: Portfolio, quote_service: QuoteService, fx: FxService
) -> PortfolioSummary:
    symbols = [p.instrument.symbol for p in portfolio.positions]
    quotes = await quote_service.get_quotes(db, symbols) if symbols else {}

    summary = PortfolioSummary(
        portfolio_id=portfolio.id, base_currency=portfolio.base_currency,
        total_value=None, total_cost=None, total_pnl=None,
        total_pnl_pct=None, day_change=None,
    )
    total_value = total_cost = day_change = Decimal("0")
    any_priced = False

    for pos in portfolio.positions:
        inst = pos.instrument
        quote = quotes.get(inst.symbol)
        pv = PositionValuation(
            position_id=pos.id, symbol=inst.symbol, name=inst.name, market=inst.market,
            quantity=pos.quantity, avg_cost=pos.avg_cost, native_currency=inst.currency,
            price=quote.price if quote else None,
            market_value_base=None, cost_basis_base=None, unrealized_pnl_base=None,
            unrealized_pnl_pct=None, day_change_base=None,
            quote_as_of=quote.as_of if quote else None,
        )
        if quote is not None and pos.quantity is not None:
            price_major, ccy = normalise(quote.price, quote.currency)
            rate = await fx.get_rate(db, ccy, portfolio.base_currency)
            value = _round(pos.quantity * price_major * rate)
            pv.market_value_base = value
            total_value += value
            any_priced = True

            exposure_key = ccy
            summary.currency_exposure[exposure_key] = (
                summary.currency_exposure.get(exposure_key, Decimal("0")) + value
            )

            if pos.avg_cost is not None:
                cost_major, cost_ccy = normalise(pos.avg_cost, inst.currency)
                cost_rate = await fx.get_rate(db, cost_ccy, portfolio.base_currency)
                cost = _round(pos.quantity * cost_major * cost_rate)
                pv.cost_basis_base = cost
                pv.unrealized_pnl_base = value - cost
                if cost != 0:
                    pv.unrealized_pnl_pct = _round((value - cost) / cost * 100)
                total_cost += cost

            if quote.previous_close is not None:
                prev_major, _ = normalise(quote.previous_close, quote.currency)
                pv.day_change_base = _round(pos.quantity * (price_major - prev_major) * rate)
                day_change += pv.day_change_base

            summary.priced_positions += 1
        elif pos.quantity is not None:
            summary.unpriced_positions += 1
        summary.positions.append(pv)

    if any_priced:
        summary.total_value = _round(total_value)
        summary.total_cost = _round(total_cost) if total_cost else None
        if summary.total_cost:
            summary.total_pnl = summary.total_value - summary.total_cost
            summary.total_pnl_pct = _round(summary.total_pnl / summary.total_cost * 100)
        summary.day_change = _round(day_change)
    return summary
```

Run: `cd backend && pytest tests/test_valuation.py -v` → PASS

- [ ] **Step 3: Write failing API tests, implement endpoints**

`backend/tests/test_valuation_api.py`:
```python
import pytest

pytestmark = pytest.mark.asyncio(loop_scope="session")


async def test_valuation_endpoint_shape(auth_client, make_instrument):
    await make_instrument("AAPL")
    pid = (await auth_client.post(
        "/api/portfolios", json={"name": "P", "kind": "real", "base_currency": "GBP"}
    )).json()["id"]
    await auth_client.post(
        f"/api/portfolios/{pid}/positions",
        json={"symbol": "AAPL", "quantity": "10", "avg_cost": "100"},
    )
    resp = await auth_client.get(f"/api/portfolios/{pid}/valuation")
    assert resp.status_code == 200
    body = resp.json()
    # no quote cache + fake-less provider will fail network-free: positions unpriced
    assert body["base_currency"] == "GBP"
    assert len(body["positions"]) == 1


async def test_dashboard_endpoint(auth_client):
    await auth_client.post(
        "/api/portfolios", json={"name": "P1", "kind": "real", "base_currency": "GBP"}
    )
    resp = await auth_client.get("/api/dashboard")
    assert resp.status_code == 200
    assert len(resp.json()["portfolios"]) == 1
```

Note: in tests the real `YahooProvider` would be hit by these endpoints. To keep tests network-free, the valuation router takes `QuoteService`/`FxService` via a dependency `get_services()` — and `conftest.py`'s `client` fixture overrides it with fake-provider-backed services. Append to conftest:
```python
from app.api.valuation import get_services  # noqa: E402
from app.services.market_data.quotes import QuoteService  # noqa: E402
from app.services.valuation import FxService  # noqa: E402


class _NullProvider:
    async def get_quotes(self, symbols):
        return {}

    async def get_fx_rate(self, base, quote):
        raise LookupError("no fx in tests")

    async def lookup(self, symbol):
        return None


def _test_services():
    provider = _NullProvider()
    return QuoteService(provider), FxService(provider)
```
…and inside the `client` fixture, after the session override: `app.dependency_overrides[get_services] = _test_services`.

`backend/app/api/valuation.py`:
```python
from datetime import UTC, datetime

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import select

from app.api.deps import CurrentUser, SessionDep
from app.api.portfolios import get_owned_portfolio
from app.models import Portfolio
from app.services.market_data.quotes import QuoteService, get_quote_service
from app.services.valuation import FxService, value_portfolio

router = APIRouter(prefix="/api", tags=["valuation"])


def get_services() -> tuple[QuoteService, FxService]:
    qs = get_quote_service()
    return qs, FxService(qs.provider)


class DashboardPortfolio(BaseModel):
    id: int
    name: str
    kind: str
    base_currency: str
    total_value: str | None
    day_change: str | None
    total_pnl_pct: str | None


class DashboardOut(BaseModel):
    portfolios: list[DashboardPortfolio]
    as_of: datetime


def _s(v) -> str | None:
    return None if v is None else str(v)


@router.get("/portfolios/{portfolio_id}/valuation")
async def portfolio_valuation(
    portfolio_id: int,
    db: SessionDep,
    user: CurrentUser,
    services: tuple[QuoteService, FxService] = Depends(get_services),
):
    pf = await get_owned_portfolio(db, user, portfolio_id)
    quote_service, fx = services
    summary = await value_portfolio(db, pf, quote_service, fx)
    return {
        "portfolio_id": summary.portfolio_id,
        "base_currency": summary.base_currency,
        "total_value": _s(summary.total_value),
        "total_cost": _s(summary.total_cost),
        "total_pnl": _s(summary.total_pnl),
        "total_pnl_pct": _s(summary.total_pnl_pct),
        "day_change": _s(summary.day_change),
        "currency_exposure": {k: str(v) for k, v in summary.currency_exposure.items()},
        "priced_positions": summary.priced_positions,
        "unpriced_positions": summary.unpriced_positions,
        "positions": [
            {
                "position_id": p.position_id, "symbol": p.symbol, "name": p.name,
                "market": p.market, "quantity": _s(p.quantity), "avg_cost": _s(p.avg_cost),
                "native_currency": p.native_currency, "price": _s(p.price),
                "market_value_base": _s(p.market_value_base),
                "cost_basis_base": _s(p.cost_basis_base),
                "unrealized_pnl_base": _s(p.unrealized_pnl_base),
                "unrealized_pnl_pct": _s(p.unrealized_pnl_pct),
                "day_change_base": _s(p.day_change_base),
                "quote_as_of": p.quote_as_of.isoformat() if p.quote_as_of else None,
            }
            for p in summary.positions
        ],
    }


@router.get("/dashboard", response_model=DashboardOut)
async def dashboard(
    db: SessionDep,
    user: CurrentUser,
    services: tuple[QuoteService, FxService] = Depends(get_services),
):
    quote_service, fx = services
    result = await db.execute(
        select(Portfolio).where(Portfolio.user_id == user.id).order_by(Portfolio.id)
    )
    out: list[DashboardPortfolio] = []
    for pf in result.scalars().all():
        summary = await value_portfolio(db, pf, quote_service, fx)
        out.append(
            DashboardPortfolio(
                id=pf.id, name=pf.name, kind=pf.kind, base_currency=pf.base_currency,
                total_value=_s(summary.total_value), day_change=_s(summary.day_change),
                total_pnl_pct=_s(summary.total_pnl_pct),
            )
        )
    return DashboardOut(portfolios=out, as_of=datetime.now(UTC))
```

In `backend/app/main.py`: include `valuation` router.

- [ ] **Step 4: Run all tests + lint, commit**

Run: `cd backend && pytest -v && ruff check .`
Expected: all PASS

```bash
git add -A
git commit -m "feat: valuation — GBp normalisation, daily-cached FX, position P&L, portfolio summary + dashboard endpoints

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 11: Yahoo CSV import — parser service + preview/commit endpoints

**Files:**
- Create: `backend/app/services/csv_import.py`, `backend/app/api/imports.py`, `backend/app/schemas/imports.py`
- Create: `backend/tests/fixtures/yahoo_portfolio_export.csv`
- Modify: `backend/app/main.py`
- Test: `backend/tests/test_csv_import.py`, `backend/tests/test_import_api.py`

**Interfaces:**
- Consumes: `Instrument`, `Position`, `get_owned_portfolio`, provider `lookup` (via `get_provider` from Task 9).
- Produces:
  - `csv_import.ParsedRow` dataclass: `symbol: str, quantity: Decimal | None, purchase_price: Decimal | None, comment: str | None`
  - `csv_import.parse_yahoo_csv(data: bytes) -> list[ParsedRow]` — raises `csv_import.CsvFormatError` if no `Symbol` column; skips blank/cash rows (`$$CASH`); tolerates missing Quantity/Purchase Price columns
  - `POST /api/imports/preview` (multipart file) → `{rows: [{symbol, quantity, purchase_price, comment, known}], errors: []}` — `known` = instrument resolvable (existing row or provider lookup, which creates the Instrument as in Task 9)
  - `POST /api/imports/commit` JSON `{portfolio_id: int | null, new_portfolio: {name, kind, base_currency} | null, merge: "update"|"skip"|"replace", rows: [{symbol, quantity?, avg_cost?}]}` → `{created: int, updated: int, skipped: int, portfolio_id: int}` — transactional: any unknown symbol → 422, nothing written
  - merge semantics on symbol clash in target portfolio: `update` = overwrite quantity/avg_cost; `skip` = leave existing; `replace` = delete existing position then insert

- [ ] **Step 1: Create the CSV fixture (synthetic data, real Yahoo export header)**

`backend/tests/fixtures/yahoo_portfolio_export.csv`:
```csv
Symbol,Current Price,Date,Time,Change,Open,High,Low,Volume,Trade Date,Purchase Price,Quantity,Commission,High Limit,Low Limit,Comment
AAPL,231.55,20260706,16:00,2.45,229.5,232.1,229.0,51234567,20250110,150.25,10,,,,"core holding"
HSBA.L,702.30,20260706,16:35,3.80,699.0,704.2,698.1,10234567,20240515,650.00,200,,,,
0700.HK,540.00,20260706,16:08,-2.00,541.0,545.0,538.5,9876543,,,,,,,
$$CASH,1.00,20260706,16:00,0.00,,,,,,,,,,,
```

- [ ] **Step 2: Write the failing parser tests**

`backend/tests/test_csv_import.py`:
```python
from decimal import Decimal
from pathlib import Path

import pytest

from app.services.csv_import import CsvFormatError, parse_yahoo_csv

FIXTURE = Path(__file__).parent / "fixtures" / "yahoo_portfolio_export.csv"


def test_parse_yahoo_export():
    rows = parse_yahoo_csv(FIXTURE.read_bytes())
    assert len(rows) == 3  # $$CASH skipped
    aapl = rows[0]
    assert (aapl.symbol, aapl.quantity, aapl.purchase_price) == (
        "AAPL", Decimal("10"), Decimal("150.25")
    )
    assert aapl.comment == "core holding"
    tencent = rows[2]
    assert tencent.symbol == "0700.HK"
    assert tencent.quantity is None  # watchlist-style row


def test_missing_symbol_column_raises():
    with pytest.raises(CsvFormatError):
        parse_yahoo_csv(b"Name,Price\nApple,100\n")
```

- [ ] **Step 3: Run to verify fail, implement parser**

Run: `cd backend && pytest tests/test_csv_import.py -v` → FAIL

`backend/app/services/csv_import.py`:
```python
import io
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

import pandas as pd


class CsvFormatError(Exception):
    pass


@dataclass(frozen=True)
class ParsedRow:
    symbol: str
    quantity: Decimal | None
    purchase_price: Decimal | None
    comment: str | None


def _dec(value) -> Decimal | None:
    if value is None or (isinstance(value, float) and pd.isna(value)) or value == "":
        return None
    try:
        return Decimal(str(value))
    except InvalidOperation:
        return None


def parse_yahoo_csv(data: bytes) -> list[ParsedRow]:
    try:
        df = pd.read_csv(io.BytesIO(data), dtype=str)
    except Exception as exc:
        raise CsvFormatError(f"Unreadable CSV: {exc}") from exc
    if "Symbol" not in df.columns:
        raise CsvFormatError("No 'Symbol' column — is this a Yahoo Finance portfolio export?")

    rows: list[ParsedRow] = []
    for _, r in df.iterrows():
        symbol = (r.get("Symbol") or "").strip()
        if not symbol or symbol.startswith("$$"):
            continue
        comment = r.get("Comment")
        rows.append(
            ParsedRow(
                symbol=symbol,
                quantity=_dec(r.get("Quantity")),
                purchase_price=_dec(r.get("Purchase Price")),
                comment=None if comment is None or pd.isna(comment) else str(comment).strip() or None,
            )
        )
    return rows
```

Run: `cd backend && pytest tests/test_csv_import.py -v` → PASS

- [ ] **Step 4: Write failing API tests**

`backend/tests/test_import_api.py`:
```python
from pathlib import Path

import pytest

from app.api.instruments import get_provider
from app.services.market_data.base import InstrumentInfo, infer_market

pytestmark = pytest.mark.asyncio(loop_scope="session")

FIXTURE = Path(__file__).parent / "fixtures" / "yahoo_portfolio_export.csv"


class AllKnownProvider:
    async def lookup(self, symbol):
        return InstrumentInfo(
            symbol=symbol, name=f"{symbol} Co", exchange="X",
            market=infer_market(symbol), currency="USD", sector=None, industry=None,
        )

    async def get_quotes(self, symbols):
        return {}

    async def get_fx_rate(self, base, quote):
        raise NotImplementedError


def _override(client):
    client._transport.app.dependency_overrides[get_provider] = lambda: AllKnownProvider()


async def test_preview_then_commit_new_portfolio(auth_client):
    _override(auth_client)
    preview = await auth_client.post(
        "/api/imports/preview",
        files={"file": ("pf.csv", FIXTURE.read_bytes(), "text/csv")},
    )
    assert preview.status_code == 200
    rows = preview.json()["rows"]
    assert len(rows) == 3
    assert all(r["known"] for r in rows)

    commit = await auth_client.post(
        "/api/imports/commit",
        json={
            "portfolio_id": None,
            "new_portfolio": {"name": "Imported", "kind": "real", "base_currency": "GBP"},
            "merge": "update",
            "rows": [
                {"symbol": "AAPL", "quantity": "10", "avg_cost": "150.25"},
                {"symbol": "HSBA.L", "quantity": "200", "avg_cost": "650.00"},
            ],
        },
    )
    assert commit.status_code == 200
    assert commit.json()["created"] == 2
    pid = commit.json()["portfolio_id"]
    positions = (await auth_client.get(f"/api/portfolios/{pid}/positions")).json()
    assert {p["symbol"] for p in positions} == {"AAPL", "HSBA.L"}


async def test_commit_merge_update_and_skip(auth_client, make_instrument):
    _override(auth_client)
    await make_instrument("AAPL")
    pid = (await auth_client.post(
        "/api/portfolios", json={"name": "P", "kind": "real", "base_currency": "GBP"}
    )).json()["id"]
    await auth_client.post(
        f"/api/portfolios/{pid}/positions",
        json={"symbol": "AAPL", "quantity": "5", "avg_cost": "90"},
    )

    resp = await auth_client.post(
        "/api/imports/commit",
        json={"portfolio_id": pid, "new_portfolio": None, "merge": "skip",
              "rows": [{"symbol": "AAPL", "quantity": "10", "avg_cost": "100"}]},
    )
    assert resp.json() == {"created": 0, "updated": 0, "skipped": 1, "portfolio_id": pid}

    resp = await auth_client.post(
        "/api/imports/commit",
        json={"portfolio_id": pid, "new_portfolio": None, "merge": "update",
              "rows": [{"symbol": "AAPL", "quantity": "10", "avg_cost": "100"}]},
    )
    assert resp.json()["updated"] == 1
    positions = (await auth_client.get(f"/api/portfolios/{pid}/positions")).json()
    assert positions[0]["quantity"] == "10.000000"
```

- [ ] **Step 5: Run to verify fail, implement import API**

Run: `cd backend && pytest tests/test_import_api.py -v` → FAIL

`backend/app/schemas/imports.py`:
```python
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel

from app.schemas.portfolio import PortfolioCreate


class ImportRowIn(BaseModel):
    symbol: str
    quantity: Decimal | None = None
    avg_cost: Decimal | None = None


class ImportCommitIn(BaseModel):
    portfolio_id: int | None = None
    new_portfolio: PortfolioCreate | None = None
    merge: Literal["update", "skip", "replace"] = "update"
    rows: list[ImportRowIn]


class ImportCommitOut(BaseModel):
    created: int
    updated: int
    skipped: int
    portfolio_id: int
```

`backend/app/api/imports.py`:
```python
from fastapi import APIRouter, Depends, HTTPException, UploadFile
from sqlalchemy import select

from app.api.deps import CurrentUser, SessionDep
from app.api.instruments import get_provider
from app.api.portfolios import get_owned_portfolio
from app.models import Instrument, Portfolio, Position
from app.schemas.imports import ImportCommitIn, ImportCommitOut
from app.services.csv_import import CsvFormatError, parse_yahoo_csv
from app.services.market_data.base import MarketDataProvider

router = APIRouter(prefix="/api/imports", tags=["imports"])


async def _resolve_instrument(db, provider, symbol: str) -> Instrument | None:
    inst = (
        await db.execute(select(Instrument).where(Instrument.symbol == symbol))
    ).scalar_one_or_none()
    if inst is not None:
        return inst
    info = await provider.lookup(symbol)
    if info is None:
        return None
    inst = Instrument(
        symbol=info.symbol, name=info.name, exchange=info.exchange, market=info.market,
        currency=info.currency, sector=info.sector, industry=info.industry,
    )
    db.add(inst)
    await db.flush()
    return inst


@router.post("/preview")
async def preview(
    file: UploadFile,
    db: SessionDep,
    user: CurrentUser,
    provider: MarketDataProvider = Depends(get_provider),
):
    data = await file.read()
    try:
        parsed = parse_yahoo_csv(data)
    except CsvFormatError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    rows = []
    for row in parsed:
        inst = await _resolve_instrument(db, provider, row.symbol)
        rows.append({
            "symbol": row.symbol,
            "quantity": None if row.quantity is None else str(row.quantity),
            "purchase_price": None if row.purchase_price is None else str(row.purchase_price),
            "comment": row.comment,
            "known": inst is not None,
        })
    await db.commit()
    return {"rows": rows, "errors": []}


@router.post("/commit", response_model=ImportCommitOut)
async def commit(
    body: ImportCommitIn,
    db: SessionDep,
    user: CurrentUser,
    provider: MarketDataProvider = Depends(get_provider),
):
    if body.portfolio_id is not None:
        pf = await get_owned_portfolio(db, user, body.portfolio_id)
    elif body.new_portfolio is not None:
        pf = Portfolio(user_id=user.id, **body.new_portfolio.model_dump())
        db.add(pf)
        await db.flush()
    else:
        raise HTTPException(status_code=422, detail="portfolio_id or new_portfolio required")

    # resolve all instruments first — all-or-nothing
    instruments: dict[str, Instrument] = {}
    for row in body.rows:
        inst = await _resolve_instrument(db, provider, row.symbol)
        if inst is None:
            await db.rollback()
            raise HTTPException(status_code=422, detail=f"Unknown symbol {row.symbol}")
        instruments[row.symbol] = inst

    existing = {
        p.instrument.symbol: p
        for p in (
            await db.execute(select(Position).where(Position.portfolio_id == pf.id))
        ).scalars().all()
    }

    created = updated = skipped = 0
    for row in body.rows:
        current = existing.get(row.symbol)
        if current is None:
            db.add(Position(
                portfolio_id=pf.id, instrument_id=instruments[row.symbol].id,
                quantity=row.quantity, avg_cost=row.avg_cost,
            ))
            created += 1
        elif body.merge == "skip":
            skipped += 1
        elif body.merge == "update":
            current.quantity = row.quantity
            current.avg_cost = row.avg_cost
            updated += 1
        else:  # replace
            await db.delete(current)
            await db.flush()
            db.add(Position(
                portfolio_id=pf.id, instrument_id=instruments[row.symbol].id,
                quantity=row.quantity, avg_cost=row.avg_cost,
            ))
            updated += 1

    await db.commit()
    return ImportCommitOut(created=created, updated=updated, skipped=skipped, portfolio_id=pf.id)
```

In `backend/app/main.py`: include `imports` router.

- [ ] **Step 6: Run all tests + lint, commit + push, verify CI green**

Run: `cd backend && pytest -v && ruff check .`
Expected: all PASS

```bash
git add -A
git commit -m "feat: Yahoo CSV import — parser, preview with symbol validation, transactional commit with merge rules

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
git push
gh run view --json conclusion  # expect "success"
```

---

### Task 12: Figma checkpoint + frontend scaffold (Vite, Tailwind, React Query, login) + CI frontend job

**Files:**
- Create: `frontend/` via Vite scaffold; `frontend/src/lib/api.ts`, `frontend/src/pages/LoginPage.tsx`, `frontend/src/App.tsx`, `frontend/src/main.tsx`, `frontend/src/index.css`, `frontend/vite.config.ts`, `frontend/vitest.config.ts`, `frontend/src/test/setup.ts`
- Modify: `.github/workflows/ci.yml`
- Test: `frontend/src/pages/LoginPage.test.tsx`

**Interfaces:**
- Produces: `api.apiFetch<T>(path: string, init?: RequestInit) -> Promise<T>` (throws `ApiError` with `.status` on non-2xx; sends cookies; JSON in/out) — the single HTTP entry point for Tasks 13–15; route skeleton `/login`, `/` (dashboard), `/portfolios`, `/portfolios/:id`, `/import`; `RequireAuth` wrapper redirecting to `/login` on 401 from `GET /api/auth/me`.

- [ ] **Step 0: FIGMA GATE — design tokens + key screens (HUMAN APPROVAL REQUIRED)**

Per the spec (§6) and the user's Figma-first standing rule: before building UI, produce Figma mockups for **Dashboard, Portfolio detail, Import wizard, Login** plus a token set (colours, type scale, spacing). Use the Figma MCP (`figma-generate-library` / `figma-generate-design` skills) in a NEW Figma file named "Investment Guru".
**STOP and get the user's approval of the mockups before proceeding to Step 1.** Record the approved file key in `docs/design/figma.md`. Mockup direction: modern, clean, data-dense-but-calm; light UI; restrained accent colour; tabular numerals for money columns.

- [ ] **Step 1: Scaffold the frontend**

```bash
cd /Users/leeashmore/investment-guru
npm create vite@latest frontend -- --template react-ts
cd frontend
npm install
npm install @tanstack/react-query react-router-dom
npm install -D tailwindcss @tailwindcss/vite vitest jsdom @testing-library/react @testing-library/user-event @testing-library/jest-dom
```

`frontend/vite.config.ts`:
```ts
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";
import { defineConfig } from "vite";

export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: {
    proxy: { "/api": "http://localhost:8000" },
  },
});
```

`frontend/src/index.css` (replace contents):
```css
@import "tailwindcss";
```

Add to `frontend/package.json` scripts:
```json
"test": "vitest run",
"check": "tsc -b && npm run lint && npm run test && npm run build"
```

`frontend/vitest.config.ts`:
```ts
import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  test: {
    environment: "jsdom",
    setupFiles: ["./src/test/setup.ts"],
    globals: true,
  },
});
```

`frontend/src/test/setup.ts`:
```ts
import "@testing-library/jest-dom/vitest";
```

- [ ] **Step 2: Write the failing login test**

`frontend/src/pages/LoginPage.test.tsx`:
```tsx
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";
import LoginPage from "./LoginPage";

describe("LoginPage", () => {
  it("submits credentials to the login endpoint", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(null, { status: 204 }),
    );
    render(
      <MemoryRouter>
        <LoginPage />
      </MemoryRouter>,
    );
    await userEvent.type(screen.getByLabelText(/email/i), "lee@test.dev");
    await userEvent.type(screen.getByLabelText(/password/i), "pw123456");
    await userEvent.click(screen.getByRole("button", { name: /sign in/i }));

    expect(fetchMock).toHaveBeenCalledWith(
      "/api/auth/login",
      expect.objectContaining({ method: "POST", credentials: "include" }),
    );
  });
});
```

- [ ] **Step 3: Run test to verify it fails**

Run: `cd frontend && npm run test`
Expected: FAIL — cannot resolve `./LoginPage`

- [ ] **Step 4: Implement api client, LoginPage, App shell**

`frontend/src/lib/api.ts`:
```ts
export class ApiError extends Error {
  constructor(public status: number, message: string) {
    super(message);
  }
}

export async function apiFetch<T>(path: string, init?: RequestInit): Promise<T> {
  const resp = await fetch(path, {
    credentials: "include",
    headers: { "Content-Type": "application/json", ...init?.headers },
    ...init,
  });
  if (!resp.ok) {
    const detail = await resp.text().catch(() => "");
    throw new ApiError(resp.status, detail || resp.statusText);
  }
  if (resp.status === 204) return undefined as T;
  return (await resp.json()) as T;
}
```

`frontend/src/pages/LoginPage.tsx`:
```tsx
import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { apiFetch, ApiError } from "../lib/api";

export default function LoginPage() {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const navigate = useNavigate();

  async function onSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    try {
      await apiFetch<void>("/api/auth/login", {
        method: "POST",
        body: JSON.stringify({ email, password }),
      });
      navigate("/");
    } catch (err) {
      setError(err instanceof ApiError && err.status === 401 ? "Invalid credentials" : "Login failed");
    }
  }

  return (
    <div className="flex min-h-screen items-center justify-center bg-slate-50">
      <form onSubmit={onSubmit} className="w-full max-w-sm space-y-4 rounded-xl bg-white p-8 shadow">
        <h1 className="text-xl font-semibold text-slate-900">Investment Guru</h1>
        <label className="block text-sm font-medium text-slate-700">
          Email
          <input
            type="email"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            className="mt-1 w-full rounded-md border border-slate-300 px-3 py-2"
            required
          />
        </label>
        <label className="block text-sm font-medium text-slate-700">
          Password
          <input
            type="password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            className="mt-1 w-full rounded-md border border-slate-300 px-3 py-2"
            required
          />
        </label>
        {error && <p className="text-sm text-red-600">{error}</p>}
        <button type="submit" className="w-full rounded-md bg-slate-900 px-4 py-2 text-white">
          Sign in
        </button>
      </form>
    </div>
  );
}
```

`frontend/src/App.tsx`:
```tsx
import { QueryClient, QueryClientProvider, useQuery } from "@tanstack/react-query";
import { BrowserRouter, Link, Navigate, Outlet, Route, Routes } from "react-router-dom";
import { apiFetch } from "./lib/api";
import LoginPage from "./pages/LoginPage";

const queryClient = new QueryClient();

function RequireAuth() {
  const me = useQuery({
    queryKey: ["me"],
    queryFn: () => apiFetch<{ id: number; email: string }>("/api/auth/me"),
    retry: false,
  });
  if (me.isPending) return <div className="p-8 text-slate-500">Loading…</div>;
  if (me.isError) return <Navigate to="/login" replace />;
  return (
    <div className="flex min-h-screen bg-slate-50">
      <nav className="w-56 shrink-0 border-r border-slate-200 bg-white p-4">
        <p className="mb-6 font-semibold text-slate-900">Investment Guru</p>
        <ul className="space-y-2 text-sm text-slate-700">
          <li><Link to="/">Dashboard</Link></li>
          <li><Link to="/portfolios">Portfolios</Link></li>
          <li><Link to="/import">Import CSV</Link></li>
        </ul>
      </nav>
      <main className="flex-1 p-8">
        <Outlet />
      </main>
    </div>
  );
}

export default function App() {
  return (
    <QueryClientProvider client={queryClient}>
      <BrowserRouter>
        <Routes>
          <Route path="/login" element={<LoginPage />} />
          <Route element={<RequireAuth />}>
            <Route path="/" element={<div>Dashboard (Task 15)</div>} />
            <Route path="/portfolios" element={<div>Portfolios (Task 13)</div>} />
            <Route path="/portfolios/:id" element={<div>Portfolio (Task 13)</div>} />
            <Route path="/import" element={<div>Import (Task 14)</div>} />
          </Route>
        </Routes>
      </BrowserRouter>
    </QueryClientProvider>
  );
}
```

`frontend/src/main.tsx` (replace scaffold default):
```tsx
import React from "react";
import ReactDOM from "react-dom/client";
import App from "./App";
import "./index.css";

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>,
);
```
Delete the Vite demo files `src/App.css` and `src/assets/react.svg` references.

- [ ] **Step 5: Run test + full check**

Run: `cd frontend && npm run test && npm run check`
Expected: test PASS; tsc/lint/build clean

- [ ] **Step 6: Add frontend CI job**

Append to `.github/workflows/ci.yml`:
```yaml
  frontend:
    runs-on: ubuntu-latest
    defaults:
      run:
        working-directory: frontend
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-node@v4
        with:
          node-version: 22
          cache: npm
          cache-dependency-path: frontend/package-lock.json
      - run: npm ci
      - run: npm run check
```

- [ ] **Step 7: Commit + push, verify CI green**

```bash
git add -A
git commit -m "feat: frontend scaffold — Vite/React/Tailwind, api client, login, authed app shell + frontend CI

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
git push
gh run view --json conclusion  # expect "success"
```

---

### Task 13: Portfolios UI — list/create, detail table with valuation, add/edit/delete positions

**Files:**
- Create: `frontend/src/pages/PortfoliosPage.tsx`, `frontend/src/pages/PortfolioDetailPage.tsx`, `frontend/src/components/Money.tsx`, `frontend/src/lib/types.ts`
- Modify: `frontend/src/App.tsx` (wire routes)
- Test: `frontend/src/pages/PortfoliosPage.test.tsx`

**Interfaces:**
- Consumes: `apiFetch`, backend routes from Tasks 6/7/9/10.
- Produces: `types.ts` mirrors of `PortfolioOut`, `PositionOut`, valuation payloads (all money fields `string | null`); `<Money value ccy signed? />` renderer (tabular numerals, red/green for signed values) reused by Task 15.

- [ ] **Step 1: Write the failing test**

`frontend/src/pages/PortfoliosPage.test.tsx`:
```tsx
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";
import PortfoliosPage from "./PortfoliosPage";

function renderPage() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter>
        <PortfoliosPage />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe("PortfoliosPage", () => {
  it("lists portfolios from the API", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(
        JSON.stringify([
          { id: 1, name: "Growth", kind: "real", base_currency: "GBP", position_count: 3 },
          { id: 2, name: "Watch", kind: "watchlist", base_currency: "HKD", position_count: 5 },
        ]),
        { status: 200, headers: { "Content-Type": "application/json" } },
      ),
    );
    renderPage();
    expect(await screen.findByText("Growth")).toBeInTheDocument();
    expect(screen.getByText("Watch")).toBeInTheDocument();
    expect(screen.getByText(/watchlist/i)).toBeInTheDocument();
  });
});
```

- [ ] **Step 2: Run to verify fail** — `cd frontend && npm run test` → FAIL (module missing)

- [ ] **Step 3: Implement types, Money, pages**

`frontend/src/lib/types.ts`:
```ts
export interface Portfolio {
  id: number;
  name: string;
  kind: "real" | "watchlist";
  base_currency: string;
  position_count: number;
}

export interface Position {
  id: number;
  symbol: string;
  name: string;
  market: string;
  currency: string;
  quantity: string | null;
  avg_cost: string | null;
  notes: string | null;
}

export interface PositionValuation {
  position_id: number;
  symbol: string;
  name: string;
  market: string;
  quantity: string | null;
  avg_cost: string | null;
  native_currency: string;
  price: string | null;
  market_value_base: string | null;
  cost_basis_base: string | null;
  unrealized_pnl_base: string | null;
  unrealized_pnl_pct: string | null;
  day_change_base: string | null;
  quote_as_of: string | null;
}

export interface PortfolioValuation {
  portfolio_id: number;
  base_currency: string;
  total_value: string | null;
  total_cost: string | null;
  total_pnl: string | null;
  total_pnl_pct: string | null;
  day_change: string | null;
  currency_exposure: Record<string, string>;
  priced_positions: number;
  unpriced_positions: number;
  positions: PositionValuation[];
}

export interface DashboardData {
  portfolios: Array<{
    id: number;
    name: string;
    kind: string;
    base_currency: string;
    total_value: string | null;
    day_change: string | null;
    total_pnl_pct: string | null;
  }>;
  as_of: string;
}
```

`frontend/src/components/Money.tsx`:
```tsx
export default function Money({
  value,
  ccy,
  signed = false,
}: {
  value: string | null;
  ccy?: string;
  signed?: boolean;
}) {
  if (value === null) return <span className="text-slate-400">—</span>;
  const n = Number(value);
  const cls = signed ? (n > 0 ? "text-emerald-600" : n < 0 ? "text-red-600" : "") : "";
  const formatted = n.toLocaleString(undefined, {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  });
  return (
    <span className={`tabular-nums ${cls}`}>
      {signed && n > 0 ? "+" : ""}
      {formatted}
      {ccy ? ` ${ccy}` : ""}
    </span>
  );
}
```

`frontend/src/pages/PortfoliosPage.tsx`:
```tsx
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Link } from "react-router-dom";
import { apiFetch } from "../lib/api";
import type { Portfolio } from "../lib/types";

export default function PortfoliosPage() {
  const qc = useQueryClient();
  const portfolios = useQuery({
    queryKey: ["portfolios"],
    queryFn: () => apiFetch<Portfolio[]>("/api/portfolios"),
  });
  const [name, setName] = useState("");
  const [kind, setKind] = useState<"real" | "watchlist">("real");
  const [ccy, setCcy] = useState("GBP");

  const create = useMutation({
    mutationFn: () =>
      apiFetch<Portfolio>("/api/portfolios", {
        method: "POST",
        body: JSON.stringify({ name, kind, base_currency: ccy }),
      }),
    onSuccess: () => {
      setName("");
      qc.invalidateQueries({ queryKey: ["portfolios"] });
    },
  });

  if (portfolios.isPending) return <p className="text-slate-500">Loading…</p>;
  if (portfolios.isError) return <p className="text-red-600">Failed to load portfolios.</p>;

  return (
    <div className="space-y-6">
      <h1 className="text-2xl font-semibold text-slate-900">Portfolios</h1>
      <ul className="divide-y divide-slate-200 rounded-xl bg-white shadow">
        {portfolios.data.map((p) => (
          <li key={p.id} className="flex items-center justify-between p-4">
            <Link to={`/portfolios/${p.id}`} className="font-medium text-slate-900">
              {p.name}
            </Link>
            <span className="text-sm text-slate-500">
              {p.kind} · {p.position_count} positions · {p.base_currency}
            </span>
          </li>
        ))}
        {portfolios.data.length === 0 && (
          <li className="p-4 text-slate-500">No portfolios yet — create one below.</li>
        )}
      </ul>
      <form
        onSubmit={(e) => {
          e.preventDefault();
          create.mutate();
        }}
        className="flex flex-wrap items-end gap-3 rounded-xl bg-white p-4 shadow"
      >
        <label className="text-sm text-slate-700">
          Name
          <input
            value={name}
            onChange={(e) => setName(e.target.value)}
            className="mt-1 block rounded-md border border-slate-300 px-3 py-2"
            required
          />
        </label>
        <label className="text-sm text-slate-700">
          Type
          <select
            value={kind}
            onChange={(e) => setKind(e.target.value as "real" | "watchlist")}
            className="mt-1 block rounded-md border border-slate-300 px-3 py-2"
          >
            <option value="real">Real</option>
            <option value="watchlist">Watchlist</option>
          </select>
        </label>
        <label className="text-sm text-slate-700">
          Base currency
          <select
            value={ccy}
            onChange={(e) => setCcy(e.target.value)}
            className="mt-1 block rounded-md border border-slate-300 px-3 py-2"
          >
            <option>GBP</option>
            <option>USD</option>
            <option>HKD</option>
          </select>
        </label>
        <button type="submit" className="rounded-md bg-slate-900 px-4 py-2 text-white">
          Create
        </button>
      </form>
    </div>
  );
}
```

`frontend/src/pages/PortfolioDetailPage.tsx`:
```tsx
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { useParams } from "react-router-dom";
import Money from "../components/Money";
import { apiFetch } from "../lib/api";
import type { PortfolioValuation, Position } from "../lib/types";

export default function PortfolioDetailPage() {
  const { id } = useParams();
  const qc = useQueryClient();
  const valuation = useQuery({
    queryKey: ["valuation", id],
    queryFn: () => apiFetch<PortfolioValuation>(`/api/portfolios/${id}/valuation`),
  });

  const [symbol, setSymbol] = useState("");
  const [quantity, setQuantity] = useState("");
  const [avgCost, setAvgCost] = useState("");
  const [addError, setAddError] = useState<string | null>(null);

  const addPosition = useMutation({
    mutationFn: async () => {
      await apiFetch(`/api/instruments/lookup?symbol=${encodeURIComponent(symbol)}`);
      return apiFetch<Position>(`/api/portfolios/${id}/positions`, {
        method: "POST",
        body: JSON.stringify({
          symbol,
          quantity: quantity || null,
          avg_cost: avgCost || null,
        }),
      });
    },
    onSuccess: () => {
      setSymbol("");
      setQuantity("");
      setAvgCost("");
      setAddError(null);
      qc.invalidateQueries({ queryKey: ["valuation", id] });
    },
    onError: () => setAddError(`Could not add ${symbol} — symbol not recognised.`),
  });

  const removePosition = useMutation({
    mutationFn: (positionId: number) =>
      apiFetch<void>(`/api/positions/${positionId}`, { method: "DELETE" }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["valuation", id] }),
  });

  if (valuation.isPending) return <p className="text-slate-500">Loading…</p>;
  if (valuation.isError) return <p className="text-red-600">Failed to load portfolio.</p>;
  const v = valuation.data;

  return (
    <div className="space-y-6">
      <div className="flex items-baseline justify-between">
        <h1 className="text-2xl font-semibold text-slate-900">Portfolio</h1>
        <div className="text-right">
          <p className="text-2xl font-semibold">
            <Money value={v.total_value} ccy={v.base_currency} />
          </p>
          <p className="text-sm">
            Day: <Money value={v.day_change} ccy={v.base_currency} signed />
            {" · "}P&L: <Money value={v.total_pnl} ccy={v.base_currency} signed /> (
            <Money value={v.total_pnl_pct} signed />
            %)
          </p>
        </div>
      </div>
      {v.unpriced_positions > 0 && (
        <p className="rounded-md bg-amber-50 p-3 text-sm text-amber-800">
          {v.unpriced_positions} position(s) missing live prices — values may be incomplete.
        </p>
      )}
      <table className="w-full rounded-xl bg-white text-sm shadow">
        <thead>
          <tr className="border-b border-slate-200 text-left text-slate-500">
            <th className="p-3">Symbol</th>
            <th className="p-3">Name</th>
            <th className="p-3 text-right">Qty</th>
            <th className="p-3 text-right">Price</th>
            <th className="p-3 text-right">Value ({v.base_currency})</th>
            <th className="p-3 text-right">Day</th>
            <th className="p-3 text-right">P&L</th>
            <th className="p-3" />
          </tr>
        </thead>
        <tbody>
          {v.positions.map((p) => (
            <tr key={p.position_id} className="border-b border-slate-100">
              <td className="p-3 font-medium">{p.symbol}</td>
              <td className="p-3 text-slate-600">{p.name}</td>
              <td className="p-3 text-right tabular-nums">{p.quantity ?? "—"}</td>
              <td className="p-3 text-right">
                <Money value={p.price} ccy={p.native_currency} />
              </td>
              <td className="p-3 text-right"><Money value={p.market_value_base} /></td>
              <td className="p-3 text-right"><Money value={p.day_change_base} signed /></td>
              <td className="p-3 text-right"><Money value={p.unrealized_pnl_base} signed /></td>
              <td className="p-3 text-right">
                <button
                  onClick={() => removePosition.mutate(p.position_id)}
                  className="text-xs text-red-600"
                >
                  Remove
                </button>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      <form
        onSubmit={(e) => {
          e.preventDefault();
          addPosition.mutate();
        }}
        className="flex flex-wrap items-end gap-3 rounded-xl bg-white p-4 shadow"
      >
        <label className="text-sm text-slate-700">
          Symbol
          <input
            value={symbol}
            onChange={(e) => setSymbol(e.target.value.toUpperCase())}
            placeholder="AAPL, HSBA.L, 0700.HK"
            className="mt-1 block rounded-md border border-slate-300 px-3 py-2"
            required
          />
        </label>
        <label className="text-sm text-slate-700">
          Quantity
          <input
            value={quantity}
            onChange={(e) => setQuantity(e.target.value)}
            className="mt-1 block rounded-md border border-slate-300 px-3 py-2"
            inputMode="decimal"
          />
        </label>
        <label className="text-sm text-slate-700">
          Avg cost (native ccy)
          <input
            value={avgCost}
            onChange={(e) => setAvgCost(e.target.value)}
            className="mt-1 block rounded-md border border-slate-300 px-3 py-2"
            inputMode="decimal"
          />
        </label>
        <button type="submit" className="rounded-md bg-slate-900 px-4 py-2 text-white">
          Add position
        </button>
        {addError && <p className="text-sm text-red-600">{addError}</p>}
      </form>
    </div>
  );
}
```

Wire routes in `frontend/src/App.tsx`: replace the placeholder `/portfolios` and `/portfolios/:id` elements with `<PortfoliosPage />` and `<PortfolioDetailPage />` (import at top).

- [ ] **Step 4: Run tests + check, commit**

Run: `cd frontend && npm run test && npm run check`
Expected: PASS

```bash
git add -A
git commit -m "feat: portfolios UI — list/create, valuation table, add/remove positions

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 14: CSV import wizard UI (3 steps)

**Files:**
- Create: `frontend/src/pages/ImportWizardPage.tsx`
- Modify: `frontend/src/App.tsx` (wire `/import`)
- Test: `frontend/src/pages/ImportWizardPage.test.tsx`

**Interfaces:**
- Consumes: `POST /api/imports/preview` (multipart), `POST /api/imports/commit`, `GET /api/portfolios`, types from Task 13.
- Produces: `/import` route — Step 1 file pick + upload; Step 2 preview table (unknown symbols highlighted + excluded) with target portfolio selector (existing or "create new" name/kind/ccy inline) and merge-strategy radio (update/skip/replace); Step 3 result summary with link to the portfolio.

- [ ] **Step 1: Write the failing test**

`frontend/src/pages/ImportWizardPage.test.tsx`:
```tsx
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";
import ImportWizardPage from "./ImportWizardPage";

function renderPage() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter>
        <ImportWizardPage />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe("ImportWizardPage", () => {
  it("uploads a CSV and shows the preview rows", async () => {
    vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
      const url = String(input);
      if (url.endsWith("/api/portfolios")) {
        return new Response(JSON.stringify([]), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        });
      }
      if (url.endsWith("/api/imports/preview")) {
        return new Response(
          JSON.stringify({
            rows: [
              { symbol: "AAPL", quantity: "10", purchase_price: "150.25", comment: null, known: true },
              { symbol: "BADX", quantity: "1", purchase_price: null, comment: null, known: false },
            ],
            errors: [],
          }),
          { status: 200, headers: { "Content-Type": "application/json" } },
        );
      }
      throw new Error(`Unexpected fetch: ${url}`);
    });

    renderPage();
    const file = new File(["Symbol,Quantity\nAAPL,10\n"], "pf.csv", { type: "text/csv" });
    await userEvent.upload(screen.getByLabelText(/csv file/i), file);
    await userEvent.click(screen.getByRole("button", { name: /upload/i }));

    expect(await screen.findByText("AAPL")).toBeInTheDocument();
    expect(screen.getByText(/not recognised/i)).toBeInTheDocument();
  });
});
```

- [ ] **Step 2: Run to verify fail** — `cd frontend && npm run test` → FAIL

- [ ] **Step 3: Implement the wizard**

`frontend/src/pages/ImportWizardPage.tsx`:
```tsx
import { useMutation, useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { Link } from "react-router-dom";
import { apiFetch } from "../lib/api";
import type { Portfolio } from "../lib/types";

interface PreviewRow {
  symbol: string;
  quantity: string | null;
  purchase_price: string | null;
  comment: string | null;
  known: boolean;
}

interface CommitResult {
  created: number;
  updated: number;
  skipped: number;
  portfolio_id: number;
}

export default function ImportWizardPage() {
  const [file, setFile] = useState<File | null>(null);
  const [rows, setRows] = useState<PreviewRow[] | null>(null);
  const [target, setTarget] = useState<string>("new");
  const [newName, setNewName] = useState("Imported");
  const [newKind, setNewKind] = useState<"real" | "watchlist">("real");
  const [newCcy, setNewCcy] = useState("GBP");
  const [merge, setMerge] = useState<"update" | "skip" | "replace">("update");
  const [result, setResult] = useState<CommitResult | null>(null);

  const portfolios = useQuery({
    queryKey: ["portfolios"],
    queryFn: () => apiFetch<Portfolio[]>("/api/portfolios"),
  });

  const preview = useMutation({
    mutationFn: async () => {
      const form = new FormData();
      form.append("file", file!);
      const resp = await fetch("/api/imports/preview", {
        method: "POST",
        credentials: "include",
        body: form,
      });
      if (!resp.ok) throw new Error(await resp.text());
      return (await resp.json()) as { rows: PreviewRow[] };
    },
    onSuccess: (data) => setRows(data.rows),
  });

  const commit = useMutation({
    mutationFn: () =>
      apiFetch<CommitResult>("/api/imports/commit", {
        method: "POST",
        body: JSON.stringify({
          portfolio_id: target === "new" ? null : Number(target),
          new_portfolio:
            target === "new"
              ? { name: newName, kind: newKind, base_currency: newCcy }
              : null,
          merge,
          rows: (rows ?? [])
            .filter((r) => r.known)
            .map((r) => ({ symbol: r.symbol, quantity: r.quantity, avg_cost: r.purchase_price })),
        }),
      }),
    onSuccess: setResult,
  });

  if (result) {
    return (
      <div className="space-y-4">
        <h1 className="text-2xl font-semibold text-slate-900">Import complete</h1>
        <p className="text-slate-700">
          Created {result.created}, updated {result.updated}, skipped {result.skipped}.
        </p>
        <Link to={`/portfolios/${result.portfolio_id}`} className="text-blue-600 underline">
          View portfolio
        </Link>
      </div>
    );
  }

  return (
    <div className="space-y-6">
      <h1 className="text-2xl font-semibold text-slate-900">Import from Yahoo Finance</h1>

      <section className="space-y-3 rounded-xl bg-white p-4 shadow">
        <h2 className="font-medium text-slate-900">1. Upload CSV</h2>
        <label className="block text-sm text-slate-700">
          CSV file
          <input
            type="file"
            accept=".csv,text/csv"
            onChange={(e) => setFile(e.target.files?.[0] ?? null)}
            className="mt-1 block"
          />
        </label>
        <button
          onClick={() => preview.mutate()}
          disabled={!file || preview.isPending}
          className="rounded-md bg-slate-900 px-4 py-2 text-white disabled:opacity-50"
        >
          Upload & preview
        </button>
        {preview.isError && (
          <p className="text-sm text-red-600">Could not parse that file as a Yahoo export.</p>
        )}
      </section>

      {rows && (
        <section className="space-y-3 rounded-xl bg-white p-4 shadow">
          <h2 className="font-medium text-slate-900">2. Review & assign</h2>
          <table className="w-full text-sm">
            <thead>
              <tr className="text-left text-slate-500">
                <th className="p-2">Symbol</th>
                <th className="p-2 text-right">Quantity</th>
                <th className="p-2 text-right">Purchase price</th>
                <th className="p-2">Status</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((r) => (
                <tr key={r.symbol} className={r.known ? "" : "bg-red-50"}>
                  <td className="p-2 font-medium">{r.symbol}</td>
                  <td className="p-2 text-right tabular-nums">{r.quantity ?? "—"}</td>
                  <td className="p-2 text-right tabular-nums">{r.purchase_price ?? "—"}</td>
                  <td className="p-2">
                    {r.known ? "OK" : <span className="text-red-600">not recognised — will be excluded</span>}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>

          <div className="flex flex-wrap items-end gap-3">
            <label className="text-sm text-slate-700">
              Target portfolio
              <select
                value={target}
                onChange={(e) => setTarget(e.target.value)}
                className="mt-1 block rounded-md border border-slate-300 px-3 py-2"
              >
                <option value="new">Create new…</option>
                {(portfolios.data ?? []).map((p) => (
                  <option key={p.id} value={p.id}>
                    {p.name} ({p.kind})
                  </option>
                ))}
              </select>
            </label>
            {target === "new" && (
              <>
                <label className="text-sm text-slate-700">
                  Name
                  <input
                    value={newName}
                    onChange={(e) => setNewName(e.target.value)}
                    className="mt-1 block rounded-md border border-slate-300 px-3 py-2"
                  />
                </label>
                <label className="text-sm text-slate-700">
                  Type
                  <select
                    value={newKind}
                    onChange={(e) => setNewKind(e.target.value as "real" | "watchlist")}
                    className="mt-1 block rounded-md border border-slate-300 px-3 py-2"
                  >
                    <option value="real">Real</option>
                    <option value="watchlist">Watchlist</option>
                  </select>
                </label>
                <label className="text-sm text-slate-700">
                  Base currency
                  <select
                    value={newCcy}
                    onChange={(e) => setNewCcy(e.target.value)}
                    className="mt-1 block rounded-md border border-slate-300 px-3 py-2"
                  >
                    <option>GBP</option>
                    <option>USD</option>
                    <option>HKD</option>
                  </select>
                </label>
              </>
            )}
            <fieldset className="text-sm text-slate-700">
              <legend>If a symbol already exists</legend>
              {(["update", "skip", "replace"] as const).map((m) => (
                <label key={m} className="mr-3">
                  <input
                    type="radio"
                    name="merge"
                    checked={merge === m}
                    onChange={() => setMerge(m)}
                    className="mr-1"
                  />
                  {m}
                </label>
              ))}
            </fieldset>
            <button
              onClick={() => commit.mutate()}
              disabled={commit.isPending}
              className="rounded-md bg-slate-900 px-4 py-2 text-white disabled:opacity-50"
            >
              3. Import {rows.filter((r) => r.known).length} rows
            </button>
          </div>
        </section>
      )}
    </div>
  );
}
```

Wire `/import` in `App.tsx` to `<ImportWizardPage />`.

- [ ] **Step 4: Run tests + check, commit**

Run: `cd frontend && npm run test && npm run check`
Expected: PASS

```bash
git add -A
git commit -m "feat: CSV import wizard — upload/preview/assign with merge strategies

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 15: Dashboard UI + end-to-end smoke + docs refresh

**Files:**
- Create: `frontend/src/pages/DashboardPage.tsx`
- Modify: `frontend/src/App.tsx`, `README.md`
- Test: `frontend/src/pages/DashboardPage.test.tsx`

**Interfaces:**
- Consumes: `GET /api/dashboard`, `DashboardData` type, `Money`.
- Produces: `/` route showing per-portfolio cards (name, kind, total value in base ccy, day change, P&L %) and an "as of" stamp; empty state links to `/portfolios` and `/import`.

- [ ] **Step 1: Write the failing test**

`frontend/src/pages/DashboardPage.test.tsx`:
```tsx
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";
import DashboardPage from "./DashboardPage";

describe("DashboardPage", () => {
  it("renders portfolio cards with values", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(
        JSON.stringify({
          portfolios: [
            {
              id: 1, name: "Growth", kind: "real", base_currency: "GBP",
              total_value: "2600.00", day_change: "31.20", total_pnl_pct: "23.81",
            },
          ],
          as_of: "2026-07-07T09:00:00Z",
        }),
        { status: 200, headers: { "Content-Type": "application/json" } },
      ),
    );
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(
      <QueryClientProvider client={qc}>
        <MemoryRouter>
          <DashboardPage />
        </MemoryRouter>
      </QueryClientProvider>,
    );
    expect(await screen.findByText("Growth")).toBeInTheDocument();
    expect(screen.getByText(/2,600\.00/)).toBeInTheDocument();
  });
});
```

- [ ] **Step 2: Run to verify fail, implement**

Run: `cd frontend && npm run test` → FAIL

`frontend/src/pages/DashboardPage.tsx`:
```tsx
import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import Money from "../components/Money";
import { apiFetch } from "../lib/api";
import type { DashboardData } from "../lib/types";

export default function DashboardPage() {
  const dash = useQuery({
    queryKey: ["dashboard"],
    queryFn: () => apiFetch<DashboardData>("/api/dashboard"),
  });

  if (dash.isPending) return <p className="text-slate-500">Loading…</p>;
  if (dash.isError) return <p className="text-red-600">Failed to load dashboard.</p>;
  const { portfolios, as_of } = dash.data;

  return (
    <div className="space-y-6">
      <div className="flex items-baseline justify-between">
        <h1 className="text-2xl font-semibold text-slate-900">Dashboard</h1>
        <p className="text-xs text-slate-400">as of {new Date(as_of).toLocaleString()}</p>
      </div>
      {portfolios.length === 0 ? (
        <p className="rounded-xl bg-white p-6 text-slate-600 shadow">
          No portfolios yet. <Link to="/portfolios" className="text-blue-600 underline">Create one</Link>{" "}
          or <Link to="/import" className="text-blue-600 underline">import a Yahoo CSV</Link>.
        </p>
      ) : (
        <div className="grid grid-cols-1 gap-4 md:grid-cols-2 xl:grid-cols-3">
          {portfolios.map((p) => (
            <Link
              key={p.id}
              to={`/portfolios/${p.id}`}
              className="rounded-xl bg-white p-5 shadow transition hover:shadow-md"
            >
              <div className="flex items-center justify-between">
                <h2 className="font-medium text-slate-900">{p.name}</h2>
                <span className="text-xs uppercase tracking-wide text-slate-400">{p.kind}</span>
              </div>
              <p className="mt-3 text-2xl font-semibold text-slate-900">
                <Money value={p.total_value} ccy={p.base_currency} />
              </p>
              <p className="mt-1 text-sm text-slate-600">
                Day <Money value={p.day_change} ccy={p.base_currency} signed /> · P&L{" "}
                <Money value={p.total_pnl_pct} signed />%
              </p>
            </Link>
          ))}
        </div>
      )}
    </div>
  );
}
```

Wire `/` in `App.tsx` to `<DashboardPage />`.

- [ ] **Step 3: End-to-end local smoke (manual, real services)**

```bash
docker compose up -d db
cd backend && source .venv/bin/activate && alembic upgrade head && python -m app.seed \
  && uvicorn app.main:app --reload &
cd frontend && npm run dev
```
In the browser at the Vite URL: log in with the seeded credentials → create a portfolio → add `AAPL`, `HSBA.L`, `0700.HK` → confirm live prices load, GBp is converted (HSBA value ≈ price÷100 × qty), day change shows, dashboard card totals render. Import `backend/tests/fixtures/yahoo_portfolio_export.csv` through the wizard into a new portfolio. Fix anything broken before committing.

- [ ] **Step 4: Update README status + run everything, commit + push, verify CI**

Add to `README.md` under a `## Status` heading: "Phase 1 (portfolio core) complete — portfolios/watchlists, Yahoo CSV import, live multi-currency valuation, dashboard. Next: Phase 2, the Guru (see spec)."

Run: `cd backend && pytest -v && ruff check . && cd ../frontend && npm run check`
Expected: all green

```bash
git add -A
git commit -m "feat: dashboard — portfolio cards with valuation summary; Phase 1 complete

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
git push
gh run view --json conclusion  # expect "success"
```

---

## Self-Review Notes

- **Spec coverage (Phase 1 scope):** repo/CI/auth-lite (Tasks 1–4), models (Task 5), portfolio+position CRUD (6–7), provider abstraction + cache + degrade-not-crash (8), symbol validation (9), quotes/FX/P&L incl. GBp pence handling (10), CSV wizard with merge rules + all-or-nothing commit (11), frontend + Figma gate (12), portfolio/import/dashboard UI (13–15). Phase 2+ items (profile, Guru, signals, digest, ORSO) intentionally out of scope per spec §8.
- **Known simplifications (deliberate, spec-compatible):** `price_bars` table is created in Task 5 but unpopulated until Phase 3 signals need history; no chart in the position drawer yet (needs price history — Phase 3); dashboard "attention flags" arrive with signals in Phase 3.
- **Type consistency check:** `Quote`/`InstrumentInfo`/`QuoteService`/`FxService`/`value_portfolio` names and signatures match across Tasks 8/9/10/11; `PositionOut` serialisation (Decimals as strings) matches frontend `types.ts`; `get_provider` override used by both Task 9 and Task 11 tests.
