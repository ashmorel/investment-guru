# Project 1 — Multi-user + Encryption at Rest + Admin Role — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn the single-user app into a real multi-user product per spec `docs/superpowers/specs/2026-07-09-multiuser-encryption-design.md` — open registration, encrypt-at-rest with a server-held key, an email-allowlisted admin role + area shell, a per-user daily LLM budget, and an opt-in (multi-user) daily digest.

**Architecture:** A transparent SQLAlchemy `TypeDecorator` layer (Fernet, versioned tokens) encrypts sensitive value columns; the app keeps working in plaintext Python types. Registration + an admin allowlist + a per-user budget check + a scheduler that iterates opted-in users layer onto the existing session/ownership foundation. No change to what any existing feature *does*.

**Tech Stack:** FastAPI + SQLAlchemy 2 async + Alembic (head → 0007) + Postgres; `cryptography` (Fernet); React/Vite/TS/Tailwind v4. No new frontend deps.

## Global Constraints

- Public repo: never commit real holdings data or secrets. Never read/modify `.env`.
- Money/quantity = `Numeric`/`Decimal`, never float. Every user-data table has `user_id`.
- DB change = hand-written chained Alembic migration (`alembic heads` first; new head **0007** on `0006`).
- Async tests: `pytestmark = pytest.mark.asyncio(loop_scope="session")` **only when all tests in the file are async** (`filterwarnings=["error"]` breaks it on sync tests — mark async tests individually otherwise); conftest fixtures (`client`, `auth_client`, `guru_client`, `orso_client`, `db_session`, `make_instrument`, `fake_llm`).
- Providers fixture-mocked in tests; endpoints degrade on provider failure, never 500.
- TDD. Verify: `ruff check . && pytest -q` (backend, venv `backend/.venv`, needs `docker compose up -d db`), `npm run check` (frontend). Frontend tests mock `globalThis.fetch` via `vi.spyOn`; vitest-axe on new UI.
- Exact values (spec §§2–5): password min length **8**; per-user daily budget default **`Decimal("1.00")`** USD; admin allowlist default **`["lee_ashmore@hotmail.co.uk"]`**; encrypted-token format **`v1:<fernet-token>`**; digest **off by default** (`digest_enabled=False`); budget-exhausted → **429 `budget_exhausted`**; duplicate email → **409 `email_taken`**; non-admin → **403 `admin_only`**.
- `DATA_ENCRYPTION_KEY` is a NEW env var, distinct from `SECRET_KEY`; fail-hard in production if absent; fixed test key in dev/tests.
- Commits end `Co-Authored-By:` trailer. Update `.superpowers/sdd/progress.md` after each task. **After the prod push: refresh `AGENTS.md` + `docs/PROGRESS.md`** (standing rule).
- Push seams: after Task 7 (backend complete) and after Task 9 (frontend). Tasks 8 (Figma) and 10 (smoke/review) gate on the user.

---

### Task 1: Crypto core — Fernet, versioned tokens, TypeDecorators, config + fail-hard

**Files:**
- Create: `backend/app/core/crypto.py`
- Modify: `backend/app/core/config.py`, `backend/app/core/hardening.py`, `backend/pyproject.toml`, `backend/tests/conftest.py`
- Test: `backend/tests/test_crypto.py`

**Interfaces:**
- Produces: `crypto.encrypt(str) -> str` (returns `v1:<token>`), `crypto.decrypt(str) -> str` (dispatches on version prefix; raises `crypto.DecryptError` on failure); `crypto.EncryptedText` / `crypto.EncryptedDecimal` / `crypto.EncryptedJSON` (SQLAlchemy `TypeDecorator`s, `cache_ok = True`, `impl = Text`); `settings.data_encryption_key: str` (env `DATA_ENCRYPTION_KEY`, empty default); `validate_production_settings` also raises when the key is empty/invalid in production.
- Consumes: existing `Settings`, `validate_production_settings`.

- [ ] **Step 1: Add dependency** — `backend/pyproject.toml` dependencies: `"cryptography>=43"`. `pip install -e ".[dev]"` in the venv.
- [ ] **Step 2: Failing tests** — `backend/tests/test_crypto.py`:

```python
from decimal import Decimal

import pytest

from app.core import crypto
from app.core.config import Settings
from app.core.hardening import validate_production_settings


def test_encrypt_roundtrip_and_versioned_token():
    tok = crypto.encrypt("hello")
    assert tok.startswith("v1:")
    assert tok != "hello"
    assert crypto.decrypt(tok) == "hello"


def test_encrypt_is_nondeterministic_but_decrypts():
    a, b = crypto.encrypt("x"), crypto.encrypt("x")
    assert a != b  # Fernet includes an IV
    assert crypto.decrypt(a) == crypto.decrypt(b) == "x"


def test_decrypt_rejects_garbage():
    with pytest.raises(crypto.DecryptError):
        crypto.decrypt("v1:not-a-real-token")
    with pytest.raises(crypto.DecryptError):
        crypto.decrypt("no-version-prefix")


def test_key_rotation_dispatch():
    # a token made with the current key still decrypts after a new primary key is prepended
    tok = crypto.encrypt("rotate-me")
    from cryptography.fernet import Fernet
    rotated = crypto.Crypto([Fernet.generate_key().decode(), crypto._active_key()])
    assert rotated.decrypt(tok) == "rotate-me"


def test_decimal_typedecorator_bind_and_result():
    ed = crypto.EncryptedDecimal()
    bound = ed.process_bind_param(Decimal("123.4567"), dialect=None)
    assert bound.startswith("v1:")
    assert ed.process_result_value(bound, dialect=None) == Decimal("123.4567")
    assert ed.process_bind_param(None, dialect=None) is None
    assert ed.process_result_value(None, dialect=None) is None


def test_json_typedecorator_roundtrip():
    ej = crypto.EncryptedJSON()
    payload = {"a": [1, 2], "b": "x"}
    bound = ej.process_bind_param(payload, dialect=None)
    assert bound.startswith("v1:")
    assert ej.process_result_value(bound, dialect=None) == payload


def test_production_requires_encryption_key():
    ok = Settings(env="production", secret_key="x" * 32, data_encryption_key=crypto._active_key())
    validate_production_settings(ok)  # no raise
    with pytest.raises(RuntimeError):
        validate_production_settings(Settings(env="production", secret_key="x" * 32, data_encryption_key=""))
```

- [ ] **Step 3: Run** `pytest tests/test_crypto.py -q` — FAIL (ImportError).
- [ ] **Step 4: Implement** `backend/app/core/crypto.py`:

```python
import json
from decimal import Decimal

from cryptography.fernet import Fernet, InvalidToken, MultiFernet
from sqlalchemy import Text
from sqlalchemy.types import TypeDecorator

from app.core.config import settings

_VERSION = "v1"
# Fixed dev/test key so the suite runs without secrets. Never used in production
# (validate_production_settings fails hard on an empty real key).
_DEV_KEY = "zZ8h1n0kМust_be_32_urlsafe_b64=="  # replaced below with a real generated constant


class DecryptError(Exception):
    pass


def _active_key() -> str:
    return settings.data_encryption_key or _DEV_KEY_REAL


class Crypto:
    def __init__(self, keys: list[str]):
        self._mf = MultiFernet([Fernet(k.encode()) for k in keys])

    def encrypt(self, plaintext: str) -> str:
        return f"{_VERSION}:{self._mf.encrypt(plaintext.encode()).decode()}"

    def decrypt(self, token: str) -> str:
        if not token.startswith(_VERSION + ":"):
            raise DecryptError("missing version prefix")
        try:
            return self._mf.decrypt(token[len(_VERSION) + 1:].encode()).decode()
        except (InvalidToken, ValueError) as exc:
            raise DecryptError(str(exc)) from exc


def _default() -> "Crypto":
    return Crypto([_active_key()])


def encrypt(plaintext: str) -> str:
    return _default().encrypt(plaintext)


def decrypt(token: str) -> str:
    return _default().decrypt(token)


class EncryptedText(TypeDecorator):
    impl = Text
    cache_ok = True

    def process_bind_param(self, value, dialect):
        return None if value is None else encrypt(value)

    def process_result_value(self, value, dialect):
        return None if value is None else decrypt(value)


class EncryptedDecimal(TypeDecorator):
    impl = Text
    cache_ok = True

    def process_bind_param(self, value, dialect):
        return None if value is None else encrypt(str(value))

    def process_result_value(self, value, dialect):
        return None if value is None else Decimal(decrypt(value))


class EncryptedJSON(TypeDecorator):
    impl = Text
    cache_ok = True

    def process_bind_param(self, value, dialect):
        return None if value is None else encrypt(json.dumps(value))

    def process_result_value(self, value, dialect):
        return None if value is None else json.loads(decrypt(value))
```

Generate a real dev key: run `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"` and set `_DEV_KEY_REAL = "<that value>"` as a module constant (a valid Fernet key; it is non-secret dev scaffolding, safe to commit). Remove the placeholder `_DEV_KEY` line.

`config.py` — add `data_encryption_key: str = ""` (env `DATA_ENCRYPTION_KEY`). `hardening.py` — in `validate_production_settings`, after the secret-key check, add: `if not settings.data_encryption_key: raise RuntimeError("DATA_ENCRYPTION_KEY must be set in production")`. `conftest.py` — nothing required (empty key → dev key path), but add a module note that tests use the dev key.

- [ ] **Step 5: Run** `pytest tests/test_crypto.py -q` + full suite + `ruff check .` — green. **Step 6: Commit** — `feat(crypto): Fernet encryption core + versioned tokens + SQLAlchemy type decorators`

---

### Task 2: Migration 0007 — switch columns to encrypted types + digest_enabled + in-place encryption

**Files:**
- Modify: `backend/app/models/portfolio.py` (Position), `backend/app/models/orso.py` (OrsoAllocation, OrsoSwitchLog), `backend/app/models/guru.py` (GuruReport, ChatMessage, InvestorProfile)
- Create: `backend/alembic/versions/0007_encrypt_and_digest.py`
- Test: `backend/tests/test_encrypted_columns.py`

**Interfaces:**
- Consumes: Task 1 type decorators.
- Produces: `Position.quantity`/`avg_cost` use `EncryptedDecimal`; `OrsoAllocation.units`/`contribution_pct` `EncryptedDecimal`; `OrsoSwitchLog.old_state`/`new_state` `EncryptedJSON`; `GuruReport.payload` `EncryptedJSON`; `ChatMessage.content` `EncryptedText`; `InvestorProfile.free_text` `EncryptedText`; `InvestorProfile.digest_enabled: Mapped[bool]` (default False, server_default false).

- [ ] **Step 1: Failing test** — `backend/tests/test_encrypted_columns.py` (uses `db_session`, `make_instrument`, and a raw connection to inspect ciphertext):

```python
from decimal import Decimal

import pytest
from sqlalchemy import text

pytestmark = pytest.mark.asyncio(loop_scope="session")


async def test_position_amounts_encrypted_at_rest(db_session, make_instrument):
    from app.models import Portfolio, Position, User
    u = User(email="enc@test.dev", password_hash="x"); db_session.add(u); await db_session.commit()
    pf = Portfolio(user_id=u.id, name="P", base_currency="GBP"); db_session.add(pf); await db_session.commit()
    inst = await make_instrument("AAPL")
    db_session.add(Position(portfolio_id=pf.id, instrument_id=inst.id,
                            quantity=Decimal("10.5"), avg_cost=Decimal("123.4567")))
    await db_session.commit()
    # ORM round-trips as Decimal
    pos = (await db_session.execute(text("SELECT quantity, avg_cost FROM positions"))).one()
    assert pos.quantity.startswith("v1:") and pos.avg_cost.startswith("v1:")  # raw = ciphertext
    from sqlalchemy import select
    orm_pos = (await db_session.execute(select(Position))).scalar_one()
    assert orm_pos.quantity == Decimal("10.5") and orm_pos.avg_cost == Decimal("123.4567")


async def test_digest_enabled_defaults_false(db_session):
    from app.models import InvestorProfile, User
    u = User(email="dig@test.dev", password_hash="x"); db_session.add(u); await db_session.commit()
    p = InvestorProfile(user_id=u.id); db_session.add(p); await db_session.commit()
    await db_session.refresh(p)
    assert p.digest_enabled is False
```

- [ ] **Step 2: Run** — FAIL. **Step 3: Implement** the model column type swaps (import the decorators from `app.core.crypto`; keep `Mapped[Decimal | None]` etc. Python types unchanged — only the `mapped_column(...)` type changes to `EncryptedDecimal()`/`EncryptedJSON()`/`EncryptedText()`); add `digest_enabled`.
- [ ] **Step 4: Migration** — `alembic heads` → `0006`. Write `0007_encrypt_and_digest.py` (`revision="0007"`, `down_revision="0006"`). Upgrade: for each encrypted column, `ALTER COLUMN ... TYPE TEXT` and encrypt any existing rows in place. Because the source types differ (Numeric → Text, JSONB → Text), do it per column as: read rows via a transient connection, compute `crypto.encrypt(...)` in Python, then `ALTER COLUMN TYPE TEXT USING NULL` + `UPDATE` the encrypted values keyed by pk. Concretely, for each `(table, col, kind)`:

```python
from app.core import crypto
conn = op.get_bind()
rows = conn.execute(sa.text(f"SELECT id, {col} FROM {table} WHERE {col} IS NOT NULL")).fetchall()
op.alter_column(table, col, type_=sa.Text(), postgresql_using="NULL")  # drop old typed data; we re-insert encrypted
for pk, val in rows:
    enc = crypto.encrypt(str(val)) if kind == "decimal" else crypto.encrypt(json.dumps(val)) if kind == "json" else crypto.encrypt(val)
    conn.execute(sa.text(f"UPDATE {table} SET {col} = :v WHERE id = :id"), {"v": enc, "id": pk})
```

(Prod currently has **no** positions/allocations/reports/chat rows — only the plaintext ORSO fund menu, which is untouched — so the row loop is a near-no-op today; it must still be correct.) Then `op.add_column("investor_profiles", sa.Column("digest_enabled", sa.Boolean(), nullable=False, server_default=sa.false()))`. Downgrade: reverse (decrypt each, alter column back to its original type, drop `digest_enabled`).
- [ ] **Step 5: Verify chain** — `alembic upgrade head && alembic downgrade 0006 && alembic upgrade head` clean (dev DB up).
- [ ] **Step 6: Run** `pytest tests/test_encrypted_columns.py -q` + full suite + ruff — green. **Step 7: Commit** — `feat(crypto): migration 0007 — encrypt sensitive columns at rest + digest_enabled`

---

### Task 3: Registration endpoint + rate limit + me.is_admin

**Files:**
- Modify: `backend/app/api/auth.py`, `backend/app/core/hardening.py` (register throttle)
- Test: `backend/tests/test_registration.py`

**Interfaces:**
- Consumes: existing `hash_password`, `sign_session`, `login_throttle` pattern, `settings.admin_emails` (Task 4 adds the setting — for THIS task, add `me.is_admin` computed from `settings.admin_emails` and add the setting here with its default so `me` works; Task 4 adds the dependency + gate).
- Produces: `POST /api/auth/register {email: str, password: str}` → 204 + session cookie (same as login); 409 `email_taken`; 422 on invalid email / password < 8; 429 when rate-limited. `GET /api/auth/me` → `{id, email, is_admin}`. `register_throttle` in hardening.py (fresh `LoginThrottle` instance keyed per client IP; 10 attempts → 60s).

- [ ] **Step 1: Failing tests** — `backend/tests/test_registration.py`:

```python
import pytest

pytestmark = pytest.mark.asyncio(loop_scope="session")


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
    await client.post("/api/auth/register", json={"email": "boss@test.dev", "password": "goodpass1"})
    assert (await client.get("/api/auth/me")).json()["is_admin"] is True
```

- [ ] **Step 2: Run** — FAIL. **Step 3: Implement.** `config.py`: `admin_emails: list[str] = ["lee_ashmore@hotmail.co.uk"]` (pydantic-settings parses a comma/JSON env list). `RegisterIn(BaseModel)` with `email: EmailStr` (pydantic `EmailStr` — check it's available; `email-validator` ships with pydantic[email]; if not, add `"pydantic[email]"` to deps) and `password: str = Field(min_length=8)`. Handler: throttle by IP (`request.client.host`), `select` existing email → 409, else create + `set_cookie` (mirror login's cookie incl. `secure=settings.is_production`). `me`: add `is_admin = user.email.lower() in {e.lower() for e in settings.admin_emails}` to the response.
- [ ] **Step 4: Run** — full suite + ruff green. **Step 5: Commit** — `feat(auth): open registration + rate limit + me.is_admin`

---

### Task 4: Admin allowlist + AdminUser dependency + /api/admin/ping

**Files:**
- Modify: `backend/app/api/deps.py`
- Create: `backend/app/api/admin.py`
- Modify: `backend/app/main.py`
- Test: `backend/tests/test_admin.py`

**Interfaces:**
- Consumes: `CurrentUser`, `settings.admin_emails`.
- Produces: `deps.is_admin(user) -> bool`; `deps.AdminUser` (Annotated dependency → 403 `admin_only` when not admin); `router = APIRouter(prefix="/api/admin", tags=["admin"])` with `GET /ping` → `{"ok": true}` (admins only). Project 2 adds the provider-config endpoints to this router.

- [ ] **Step 1: Failing tests** — `backend/tests/test_admin.py`: allowlisted user (monkeypatch `settings.admin_emails` to include the auth_client user `lee@test.dev`) gets 200 on `/api/admin/ping`; a non-allowlisted authed user gets 403 `admin_only`; unauth gets 401.
- [ ] **Step 2: Run** — FAIL. **Step 3: Implement** `deps.py` (`is_admin` + `AdminUser` dependency raising `HTTPException(403, "admin_only")`), `admin.py` (router + ping), register in `main.py` alphabetically (after `admin`? place first — `admin_router` before `auth_router`). Note `auth_client`'s user is `lee@test.dev`; the default allowlist is the real email, so tests must monkeypatch `settings.admin_emails`.
- [ ] **Step 4: Run** + ruff — green. **Step 5: Commit** — `feat(admin): email-allowlist admin role + gate + ping`

---

### Task 5: Per-user daily LLM budget

**Files:**
- Create: `backend/app/services/guru/budget.py`
- Modify: `backend/app/core/config.py`, `backend/app/services/guru/service.py`, `backend/app/services/guru/chat.py`, `backend/app/api/guru.py` (map 429)
- Test: `backend/tests/test_budget.py`

**Interfaces:**
- Consumes: `LlmUsage`, `settings.guru_timezone`.
- Produces: `budget.BudgetExhausted(Exception)`; `async budget.check_budget(db, user_id, *, now=None) -> None` (sums `LlmUsage.est_cost_usd` for the user since local-midnight in `guru_timezone`; raises `BudgetExhausted` when `>= settings.guru_daily_budget_usd`; null costs count 0); `settings.guru_daily_budget_usd: Decimal = Decimal("1.00")`. `map_guru_errors` maps `BudgetExhausted` → 429 `budget_exhausted`. `check_budget` is called at the top of every `GuruService.generate_*` and `ChatService.stream_turn` before the provider call (inside the existing lock, before context build is fine).

- [ ] **Step 1: Failing tests** — `backend/tests/test_budget.py`: with `llm_usage` rows summing just under the cap for a user → `check_budget` returns (no raise); at/over cap → raises `BudgetExhausted`; rows from *yesterday* (via injected `now`) don't count; another user's usage doesn't count. Plus an API-level test: a `guru_client` whose user is at cap → `POST /api/guru/reviews` returns 429 `budget_exhausted` and nothing persists.
- [ ] **Step 2: Run** — FAIL. **Step 3: Implement** `budget.py` (local-midnight window via `ZoneInfo(settings.guru_timezone)` → naive-UTC, mirroring `scheduler._today_start_utc`); add the config default; call `check_budget` in each generate path + chat; extend `map_guru_errors`. Reuse `scheduler._today_start_utc` if importable, else duplicate the small helper.
- [ ] **Step 4: Run** full suite + ruff — green. **Step 5: Commit** — `feat(guru): per-user daily LLM budget cap (429 budget_exhausted)`

---

### Task 6: Opt-in digest + multi-user scheduler

**Files:**
- Modify: `backend/app/services/guru/scheduler.py`, `backend/app/api/guru.py` (profile GET/PUT gains digest_enabled)
- Test: `backend/tests/test_guru_scheduler.py` (extend), `backend/tests/test_guru_profile_api.py` (extend)

**Interfaces:**
- Consumes: Task 2 `InvestorProfile.digest_enabled`, Task 5 `check_budget`.
- Produces: `run_daily_job` and `catch_up` iterate **all users with an `InvestorProfile.digest_enabled = true`** (was: first user); each user's digest→take wrapped in its own try/except (one failure never aborts the loop); a user at/over budget is skipped (catch `BudgetExhausted`, log, continue). Profile API `GET/PUT /api/guru/profile` round-trips `digest_enabled` (default false).

- [ ] **Step 1: Failing tests** — extend `test_guru_scheduler.py`: two users, only one `digest_enabled=true` → after `run_daily_job` only that user has digest+take rows; the opted-in user being over budget → skipped (no rows), no raise; a user whose LLM call fails → other opted-in user still gets theirs. Extend `test_guru_profile_api.py`: PUT `digest_enabled=true` persists and GET returns it; default is false.
- [ ] **Step 2: Run** — FAIL. **Step 3: Implement** — replace the "first user" query with `select(User).join(InvestorProfile).where(InvestorProfile.digest_enabled.is_(True))`; loop with per-user try/except wrapping `generate_digest`→`generate_take`, catching `BudgetExhausted`/`LLMError`/`Exception` and logging; add `digest_enabled` to the profile Pydantic in/out models.
- [ ] **Step 4: Run** full suite + ruff — green. **Step 5: Commit** — `feat(guru): opt-in per-user daily digest; scheduler iterates opted-in users`

---

### Task 7: Cross-user isolation test sweep (backend push seam)

**Files:**
- Test: `backend/tests/test_isolation.py`

**Interfaces:**
- Consumes: every user-scoped route. Produces: no code — a central regression guard proving user B cannot read/mutate user A's data.

- [ ] **Step 1: Write the sweep** — `backend/tests/test_isolation.py`: create user A (via register) with a portfolio + position + guru review + chat thread + ORSO fund + allocation; create user B (separate client/cookie). Parametrise over the read/mutate routes that take an owned id — `GET/PUT /api/portfolios/{A_pf}`, `GET /api/portfolios/{A_pf}/valuation`, `GET /api/portfolios/{A_pf}/signals`, `POST /api/portfolios/{A_pf}/analyze`, `GET /api/guru/reviews/{A_report}`, `GET /api/guru/chat/threads/{A_thread}`, `POST /api/guru/chat/threads/{A_thread}/messages`, `PATCH /api/orso/funds/{A_fund}`, `PUT /api/orso/allocation` (referencing A's fund_id) — and assert each returns **404** (not 403, not 200 with data) for user B. (Use `guru_client`/`orso_client` fixtures or build the LLM override inline; where an LLM call would fire, seed the report/thread directly via `db_session` so the test needs no provider.)
- [ ] **Step 2: Run** `pytest tests/test_isolation.py -q` — it must PASS immediately (isolation already enforced by `get_owned_*`); if any case leaks, that's a real bug — fix the offending endpoint's ownership check, note it in the report.
- [ ] **Step 3: Run** full suite + ruff — green. **Step 4: Commit** — `test: cross-user isolation sweep across all owned-resource routes` — then **push + confirm CI green** (`gh run view --json conclusion,jobs`; Railway auto-deploys the migration on green — confirm the deploy succeeds and `/api/health` is ok, since 0007 runs at deploy).

**Note:** `DATA_ENCRYPTION_KEY` must be set in Railway **before** this push deploys (0007 needs it, and prod is `ENV=production` which fails hard without it). The controller sets this as an operator step at the push, coordinating with the user.

---

### Task 8: Figma pass — registration screen (USER GATE)

**Files:** none (Figma only).

- [ ] **Step 1:** Load `figma:figma-generate-design` + `figma:figma-use`. File `0gU58wfjttdZS0NXQeEtuD`; match the existing Login screen (frame 01) language.
- [ ] **Step 2:** Mock a "09 Register" screen: the login card with a "Create account" mode — email, password, confirm-password fields, primary "Create account" button, "already have an account? Log in" toggle, and inline error states (email taken, password too short, mismatch). Small; reuse the login card exactly.
- [ ] **Step 3:** Post the link, **STOP — wait for user approval**. (Admin shell + digest toggle are trivial and build directly against existing patterns — no Figma needed.)

---

### Task 9: Frontend — registration, admin shell + nav, digest toggle, budget state (frontend push seam)

**Files:**
- Modify: `frontend/src/pages/LoginPage.tsx`, `frontend/src/App.tsx`, `frontend/src/pages/SettingsPage.tsx`, `frontend/src/lib/types.ts`, `frontend/src/components/GuruTakePanel.tsx` (+ other Guru action surfaces for the 429 state)
- Create: `frontend/src/pages/AdminPage.tsx`
- Test: `frontend/src/pages/LoginPage.test.tsx` (extend), `frontend/src/pages/AdminPage.test.tsx`, `frontend/src/pages/SettingsPage.test.tsx` (extend)

**Interfaces:**
- Consumes: Tasks 3–6 endpoints. Produces: `me` type gains `is_admin: boolean`; registration UI; `/admin` route + nav (visible only when `me.is_admin`); Settings digest toggle; a shared "daily AI limit reached — resets tomorrow" state when a Guru action returns `ApiError.status === 429 && detail === "budget_exhausted"`.

- [ ] **Step 1: Failing tests** — LoginPage: "Create account" toggle shows the register form; submitting posts `/api/auth/register` and routes in on 204; 409 shows "email already registered"; password mismatch blocks submit. AdminPage: renders admin landing; nav "Admin" item present only when `me.is_admin` (test both). Settings: digest toggle PUTs `digest_enabled`; renders the daily-budget note. Budget: a Guru action mocked to 429 `budget_exhausted` shows the limit-reached copy (distinct from unconfigured/error). Match the approved Figma. Build to the existing `apiFetch`/`ApiError` + card/token idioms; vitest-axe on the register form + admin page.
- [ ] **Step 2: Run** — FAIL. **Step 3: Implement** to the approved Figma. `App.tsx`: add `/admin` route + conditional nav item from the `["me"]` query. **Step 4: Run** `npm run check` — green. **Step 5: Commit** — `feat(web): registration, admin area shell, opt-in digest toggle, budget-limit state` — then **push + confirm CI green**.

---

### Task 10: Docs + live smoke + final review

**Files:**
- Modify: `README.md`, `docs/PROGRESS.md`, `AGENTS.md`, `docs/deployment.md`

- [ ] **Step 1: Runbook** — `docs/deployment.md`: add `DATA_ENCRYPTION_KEY` to the env-var table (generate: `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"`; MUST be set in Railway before the 0007 deploy; losing it makes encrypted data unrecoverable — treat as a durable secret, back it up).
- [ ] **Step 2: Live smoke** (prod, after the deploy): register a **throwaway** second user via the live UI; confirm they see none of your data and you see none of theirs (isolation); inspect the prod DB (via `DATABASE_PUBLIC_URL`) to confirm a written encrypted column is `v1:` ciphertext; verify `/admin` 403s the throwaway user and loads for your account; toggle the digest on for the throwaway user, confirm the profile persists; **purge the throwaway user + all its rows afterward** (reuse the smoke-purge script pattern).
- [ ] **Step 3: Docs** — README Status (multi-user + encryption), PROGRESS.md "Enhancement Project 1" section, AGENTS.md (multi-user now live; `DATA_ENCRYPTION_KEY` in the prod facts; admin allowlist), ledger closed.
- [ ] **Step 4: Commit + push + CI green.**
- [ ] **Step 5: Final whole-branch review on Opus** (base = pre-project-1 commit `89b763e`'s parent, i.e. the tip before Task 1 — record it at start). Security-focused given the crypto + auth + isolation surface. Fix wave → re-review to merge-clean; push fixes; re-run docs refresh if anything changed.

---

## Self-review notes (completed)

- **Spec coverage:** §2 registration→T3; §3 admin→T4+T9; §4 encryption→T1+T2; §5 budget+opt-in digest→T5+T6; §6 migration→T2; §7 frontend→T8+T9; §8 error table→T3–T5 tests; §9 testing incl. isolation sweep→T7 + per-task; §10 out-of-scope honoured (no multi-provider, no password reset); §11 order preserved.
- **Type consistency:** `crypto.encrypt/decrypt/EncryptedDecimal/EncryptedJSON/EncryptedText`, `budget.check_budget`/`BudgetExhausted`, `deps.AdminUser`/`is_admin`, `admin_emails`, `digest_enabled`, `guru_daily_budget_usd` — names consistent across tasks.
- **Judgment calls for implementers:** the real `_DEV_KEY_REAL` Fernet constant is generated in T1 Step 4 (non-secret dev scaffold, safe to commit); `EmailStr` may need `pydantic[email]` dep (T3); migration column-type swap uses `USING NULL` + re-insert of encrypted values because source types differ (T2) — verify the per-column loop on the dev DB before pushing since 0007 runs at deploy; `DATA_ENCRYPTION_KEY` must be in Railway before the T7/T9 push deploys (operator step, coordinate with user).
