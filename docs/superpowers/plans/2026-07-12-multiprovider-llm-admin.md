# Multi-provider LLM + Admin Config Panel Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let the admin choose the LLM provider (Anthropic / OpenAI / Google Gemini), models, and API key from an admin panel — powering every Guru AI feature without a redeploy, with the key encrypted at rest and never returned to the browser.

**Architecture:** A single active `llm_config` DB row (key `EncryptedText`) drives a config-aware, rebuildable `get_guru_service()`. Three `LLMProvider` adapters share one ABC; Anthropic's message shape is canonical and the OpenAI/Google adapters translate from it (Approach A). Per-role models (`advice`/`scan`) and optional per-role pricing come from the config; with no row, the app falls back to today's env-based behaviour.

**Tech Stack:** FastAPI + SQLAlchemy 2 async + Alembic (head **0009** → new **0010**) + Postgres; `anthropic`, `openai`, `google-genai` SDKs; React 18 + Vite + TS + Tailwind + TanStack Query.

## Global Constraints

- Public repo — **never commit real API keys/secrets**. The admin key lives only in the `EncryptedText` `llm_config.api_key` column, entered by the user in the panel. Tests use fake/mocked SDKs — no real keys, no network.
- Money = `Decimal`, never float. DB change = ONE hand-written chained Alembic migration; `alembic heads` must be a single head. New head is `0010` on down_revision `0009`.
- Providers are fixture/mock-backed in tests; endpoints **degrade, never 500**: `LLMNotConfigured`→503, `GenerationInProgress`→409, `LLMError`→502, `BudgetExhausted`→429 (via `map_guru_errors`).
- The API key is **never** logged or returned in any response — the admin `GET` exposes only `key_set: bool`.
- Admin endpoints are gated by the existing `AdminUser` dependency (403 `admin_only`); only `lee_ashmore@hotmail.co.uk` (the `admin_emails` allowlist) reaches them.
- `EncryptedText` / `EncryptedDecimal` columns stay encrypted; `provider`/model/price columns are plaintext.
- Async tests: `pytestmark = pytest.mark.asyncio(loop_scope="session")` + conftest fixtures (`client`, `auth_client`, `guru_client`, `db_session`, `fake_llm`). Postgres :5433 via `docker compose up -d db`. **Run tests in the FOREGROUND; never background a pytest run (it poisons the local DB). If a run shows mass IntegrityErrors/hangs, `docker compose down db && docker compose up -d db` and re-run.**
- Backend verify: `cd backend && .venv/bin/ruff check . && .venv/bin/pytest -q`. Frontend: `cd frontend && npm run check`.
- Commit to `main`; co-author trailer `Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>`.

---

## File Structure

**Backend**
- `backend/alembic/versions/0010_llm_config.py` — CREATE: `llm_config` table migration.
- `backend/app/models/guru.py` — MODIFY: add `LlmConfig` model.
- `backend/app/models/__init__.py` — MODIFY: export `LlmConfig`.
- `backend/app/services/guru/config.py` — CREATE: `ResolvedLlmConfig` + `load_active_config(db)`.
- `backend/app/services/guru/usage.py` — MODIFY: `estimate_cost`/`record_usage` price override + OpenAI/Gemini table entries.
- `backend/app/services/guru/llm/openai.py` — CREATE: `OpenAIProvider`.
- `backend/app/services/guru/llm/google.py` — CREATE: `GoogleProvider`.
- `backend/app/services/guru/llm/factory.py` — CREATE: `build_provider(provider, api_key)`.
- `backend/app/services/guru/service.py` — MODIFY: config-aware async `get_guru_service(db)` + `invalidate_guru_service()`; `GuruService` gains `advice_model`/`scan_model`/`advice_price`/`scan_price`; replace `settings.guru_*_model` reads.
- `backend/app/services/guru/chat.py`, `backend/app/services/orso/vision.py` — MODIFY: use the service's model/price instead of `settings.guru_advice_model`.
- `backend/app/api/guru.py` — MODIFY: `get_guru` becomes async `(db)` → `await get_guru_service(db)`.
- `backend/app/services/guru/scheduler.py` — MODIFY: `await get_guru_service(db)`.
- `backend/app/api/admin.py` — MODIFY: `GET`/`PUT`/`test` `llm-config` endpoints.
- `backend/pyproject.toml` — MODIFY: add `openai`, `google-genai` deps.
- Tests: `backend/tests/test_llm_config.py`, `test_llm_cost.py`, `test_openai_provider.py`, `test_google_provider.py`, `test_guru_factory_config.py`, `test_admin_llm_config.py`.

**Frontend**
- `frontend/src/lib/api.ts`, `frontend/src/lib/types.ts` — MODIFY: llm-config client + types.
- `frontend/src/pages/AdminPage.tsx` — MODIFY: the provider/model/key panel + Test button.
- Test: `frontend/src/pages/AdminPage.test.tsx`.

---

## Task 1: Migration 0010 + LlmConfig model + load_active_config

**Files:**
- Create: `backend/alembic/versions/0010_llm_config.py`, `backend/app/services/guru/config.py`
- Modify: `backend/app/models/guru.py`, `backend/app/models/__init__.py`
- Test: `backend/tests/test_llm_config.py`

**Interfaces:**
- Produces: `LlmConfig` model (table `llm_config`); `ResolvedLlmConfig` dataclass with fields `provider: str`, `api_key: str`, `advice_model: str`, `scan_model: str`, `advice_price: tuple[Decimal,Decimal]|None`, `scan_price: tuple[Decimal,Decimal]|None`; `async load_active_config(db) -> ResolvedLlmConfig`.

- [ ] **Step 1: Write the failing tests**

```python
# backend/tests/test_llm_config.py
from datetime import UTC, datetime
from decimal import Decimal

import pytest

from app.models import LlmConfig
from app.services.guru.config import load_active_config

pytestmark = pytest.mark.asyncio(loop_scope="session")


async def test_load_config_env_fallback_when_no_row(db_session, monkeypatch):
    from app.core.config import settings
    monkeypatch.setattr(settings, "anthropic_api_key", "env-key")
    monkeypatch.setattr(settings, "guru_advice_model", "claude-opus-4-8")
    monkeypatch.setattr(settings, "guru_scan_model", "claude-haiku-4-5")
    cfg = await load_active_config(db_session)
    assert cfg.provider == "anthropic"
    assert cfg.api_key == "env-key"
    assert cfg.advice_model == "claude-opus-4-8"
    assert cfg.scan_model == "claude-haiku-4-5"
    assert cfg.advice_price is None and cfg.scan_price is None


async def test_load_config_row_is_authoritative_and_key_encrypted(db_session):
    db_session.add(LlmConfig(
        provider="openai", advice_model="gpt-4o", scan_model="gpt-4o-mini",
        api_key="sk-secret", advice_input_price=Decimal("2.5"),
        advice_output_price=Decimal("10"), updated_at=datetime.now(UTC).replace(tzinfo=None)))
    await db_session.commit()

    cfg = await load_active_config(db_session)
    assert cfg.provider == "openai" and cfg.api_key == "sk-secret"
    assert cfg.advice_model == "gpt-4o" and cfg.scan_model == "gpt-4o-mini"
    assert cfg.advice_price == (Decimal("2.5"), Decimal("10"))
    assert cfg.scan_price is None   # only one side priced -> None

    # api_key is ciphertext at rest, not plaintext
    from sqlalchemy import text
    raw = (await db_session.execute(text("SELECT api_key FROM llm_config LIMIT 1"))).scalar_one()
    assert raw.startswith("v1:") and "sk-secret" not in raw
```

- [ ] **Step 2: Run to verify failure**

Run: `cd backend && .venv/bin/pytest tests/test_llm_config.py -q`
Expected: FAIL (`LlmConfig`/`load_active_config` missing).

- [ ] **Step 3: Add the model**

In `backend/app/models/guru.py` add (imports at top already include `Numeric`, `String`, `datetime`, `Decimal`, `Mapped`, `mapped_column`, `EncryptedText`; add any missing):

```python
class LlmConfig(Base):
    __tablename__ = "llm_config"

    id: Mapped[int] = mapped_column(primary_key=True)
    provider: Mapped[str] = mapped_column(String(16), default="anthropic")
    advice_model: Mapped[str] = mapped_column(String(64))
    scan_model: Mapped[str] = mapped_column(String(64))
    api_key: Mapped[str] = mapped_column(EncryptedText(), default="")
    advice_input_price: Mapped[Decimal | None] = mapped_column(Numeric(10, 4), nullable=True)
    advice_output_price: Mapped[Decimal | None] = mapped_column(Numeric(10, 4), nullable=True)
    scan_input_price: Mapped[Decimal | None] = mapped_column(Numeric(10, 4), nullable=True)
    scan_output_price: Mapped[Decimal | None] = mapped_column(Numeric(10, 4), nullable=True)
    updated_at: Mapped[datetime] = mapped_column()
    updated_by: Mapped[str | None] = mapped_column(String(255), nullable=True)
```

In `backend/app/models/__init__.py` add `LlmConfig` to the `from app.models.guru import (...)` line and to `__all__`.

- [ ] **Step 4: Add config loader**

```python
# backend/app/services/guru/config.py
"""Resolve the active LLM config: the single llm_config row if present, else a
fallback synthesised from env settings (so the app runs before the panel is
ever saved)."""
from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models import LlmConfig


@dataclass(frozen=True)
class ResolvedLlmConfig:
    provider: str
    api_key: str
    advice_model: str
    scan_model: str
    advice_price: tuple[Decimal, Decimal] | None
    scan_price: tuple[Decimal, Decimal] | None


def _price(in_p: Decimal | None, out_p: Decimal | None) -> tuple[Decimal, Decimal] | None:
    return (in_p, out_p) if in_p is not None and out_p is not None else None


async def load_active_config(db: AsyncSession) -> ResolvedLlmConfig:
    row = (await db.execute(select(LlmConfig).order_by(LlmConfig.id).limit(1))).scalar_one_or_none()
    if row is None:
        return ResolvedLlmConfig(
            provider="anthropic", api_key=settings.anthropic_api_key,
            advice_model=settings.guru_advice_model, scan_model=settings.guru_scan_model,
            advice_price=None, scan_price=None)
    return ResolvedLlmConfig(
        provider=row.provider, api_key=row.api_key or "",
        advice_model=row.advice_model, scan_model=row.scan_model,
        advice_price=_price(row.advice_input_price, row.advice_output_price),
        scan_price=_price(row.scan_input_price, row.scan_output_price))
```

- [ ] **Step 5: Write the migration**

```python
# backend/alembic/versions/0010_llm_config.py
"""llm_config table (admin provider/model/key config)

Additive, forward-only. Single-row admin config for the active LLM provider,
models, encrypted API key, and optional per-role pricing.

Revision ID: 0010
Revises: 0009
"""
import sqlalchemy as sa

from alembic import op

revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "llm_config",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("provider", sa.String(16), nullable=False, server_default="anthropic"),
        sa.Column("advice_model", sa.String(64), nullable=False),
        sa.Column("scan_model", sa.String(64), nullable=False),
        sa.Column("api_key", sa.Text(), nullable=False, server_default=""),
        sa.Column("advice_input_price", sa.Numeric(10, 4), nullable=True),
        sa.Column("advice_output_price", sa.Numeric(10, 4), nullable=True),
        sa.Column("scan_input_price", sa.Numeric(10, 4), nullable=True),
        sa.Column("scan_output_price", sa.Numeric(10, 4), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("updated_by", sa.String(255), nullable=True),
    )


def downgrade() -> None:
    op.drop_table("llm_config")
```

- [ ] **Step 6: Run tests + migration check**

Run: `cd backend && .venv/bin/pytest tests/test_llm_config.py -q` → PASS (2).
Run: `.venv/bin/alembic heads` → single head `0010`.
Run: `.venv/bin/ruff check . && .venv/bin/pytest -q` → green.

- [ ] **Step 7: Commit**

```bash
git add backend/alembic/versions/0010_llm_config.py backend/app/models/guru.py backend/app/models/__init__.py backend/app/services/guru/config.py backend/tests/test_llm_config.py
git commit -m "feat(llm): llm_config table + load_active_config (env fallback) (0010)"
```

---

## Task 2: Cost/pricing resolution

**Files:**
- Modify: `backend/app/services/guru/usage.py`
- Test: `backend/tests/test_llm_cost.py`

**Interfaces:**
- Consumes: `Usage` (`app/services/guru/llm/base.py`).
- Produces: `estimate_cost(model, usage, price: tuple[Decimal,Decimal]|None = None) -> Decimal|None`; `record_usage(..., price: tuple[Decimal,Decimal]|None = None)`.

- [ ] **Step 1: Write the failing tests**

```python
# backend/tests/test_llm_cost.py
from decimal import Decimal

from app.services.guru.llm.base import Usage
from app.services.guru.usage import estimate_cost


def test_config_price_override_wins():
    # 1M in @ $3, 1M out @ $6  -> 9
    c = estimate_cost("any-model", Usage(1_000_000, 1_000_000),
                      price=(Decimal("3"), Decimal("6")))
    assert c == Decimal("9")


def test_builtin_table_used_when_no_override():
    c = estimate_cost("gpt-4o-mini", Usage(1_000_000, 0))
    assert c is not None and c > 0


def test_unknown_model_uncosted():
    assert estimate_cost("some-brand-new-model", Usage(1000, 1000)) is None
```

- [ ] **Step 2: Run to verify failure**

Run: `cd backend && .venv/bin/pytest tests/test_llm_cost.py -q`
Expected: FAIL (`estimate_cost` has no `price` kwarg; gpt-4o-mini not in table).

- [ ] **Step 3: Implement**

In `backend/app/services/guru/usage.py`, extend the table and signatures:

```python
# add to _PRICES_PER_MTOK (approximate public rates — the panel's optional
# per-role prices override these exactly for any model):
    "gpt-4o-mini": (Decimal("0.15"), Decimal("0.60")),
    "gpt-4o": (Decimal("2.5"), Decimal("10")),
    "gpt-4.1-mini": (Decimal("0.40"), Decimal("1.60")),
    "gpt-4.1": (Decimal("2"), Decimal("8")),
    "o1": (Decimal("15"), Decimal("60")),
    "gemini-2.5-flash": (Decimal("0.30"), Decimal("2.50")),
    "gemini-2.5-pro": (Decimal("1.25"), Decimal("10")),
    "gemini-1.5-flash": (Decimal("0.075"), Decimal("0.30")),
    "gemini-1.5-pro": (Decimal("1.25"), Decimal("5")),
```

```python
def estimate_cost(
    model: str, usage: Usage,
    price: tuple[Decimal, Decimal] | None = None,
) -> Decimal | None:
    if price is not None:
        in_price, out_price = price
        return (usage.input_tokens * in_price + usage.output_tokens * out_price) / _MTOK
    for prefix, (in_price, out_price) in _PRICES_PER_MTOK.items():
        if model.startswith(prefix):
            return (usage.input_tokens * in_price + usage.output_tokens * out_price) / _MTOK
    return None


async def record_usage(
    db: AsyncSession, *, user_id: int, mode: str, model: str, usage: Usage,
    report_id: int | None = None, thread_id: int | None = None,
    price: tuple[Decimal, Decimal] | None = None,
) -> LlmUsage:
    est = estimate_cost(model, usage, price)
    if est is None:
        import logging
        logging.getLogger(__name__).warning("uncosted model %r — usage not budget-counted", model)
    row = LlmUsage(
        user_id=user_id, mode=mode, model=model,
        input_tokens=usage.input_tokens, output_tokens=usage.output_tokens,
        est_cost_usd=est, report_id=report_id, thread_id=thread_id,
        created_at=datetime.now(UTC).replace(tzinfo=None),
    )
    db.add(row)
    await db.flush()
    return row
```

- [ ] **Step 4: Run tests**

Run: `cd backend && .venv/bin/pytest tests/test_llm_cost.py -q` → PASS (3). Then `.venv/bin/pytest -q` → green (existing `record_usage` callers unaffected — `price` defaults None).

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/guru/usage.py backend/tests/test_llm_cost.py
git commit -m "feat(llm): per-role price override + OpenAI/Gemini pricing table; log uncosted models"
```

---

## Task 3: OpenAIProvider adapter

**Files:**
- Create: `backend/app/services/guru/llm/openai.py`
- Modify: `backend/pyproject.toml` (add `openai>=1.50`)
- Test: `backend/tests/test_openai_provider.py`

**Interfaces:**
- Consumes: `LLMProvider`, `Usage`, `LLMError`, `TextStream` (`app/services/guru/llm/base.py`).
- Produces: `OpenAIProvider(api_key)` implementing `generate_structured` + `stream_text`; translates Anthropic-style messages → OpenAI format.

**Implementer note:** confirm the current `openai` SDK method names via context7 (`client.beta.chat.completions.parse`, `.chat.completions.create(stream=True)`) before finalizing — the tests mock exactly what the adapter calls, so a wrong method name passes tests but fails live. Keep the adapter thin.

- [ ] **Step 1: Write the failing tests (mocked SDK — no network/keys)**

```python
# backend/tests/test_openai_provider.py
import pytest
from pydantic import BaseModel

from app.services.guru.llm.base import LLMError
from app.services.guru.llm.openai import OpenAIProvider, _to_openai_messages


class _Schema(BaseModel):
    verdict: str


def test_translates_text_and_image_blocks():
    msgs = [{"role": "user", "content": [
        {"type": "text", "text": "hi"},
        {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": "AAA"}},
    ]}]
    out = _to_openai_messages("SYS", msgs)
    assert out[0] == {"role": "system", "content": "SYS"}
    parts = out[1]["content"]
    assert {"type": "text", "text": "hi"} in parts
    img = next(p for p in parts if p["type"] == "image_url")
    assert img["image_url"]["url"] == "data:image/png;base64,AAA"


@pytest.mark.asyncio(loop_scope="session")
async def test_generate_structured_parses_and_reports_usage(monkeypatch):
    prov = OpenAIProvider("sk-test")

    class _Msg: parsed = _Schema(verdict="ok"); refusal = None
    class _Choice: message = _Msg()
    class _Usage: prompt_tokens = 11; completion_tokens = 7
    class _Resp: choices = [_Choice()]; usage = _Usage()

    async def fake_parse(**kwargs):
        assert kwargs["model"] == "gpt-4o" and kwargs["response_format"] is _Schema
        return _Resp()
    monkeypatch.setattr(prov._client.beta.chat.completions, "parse", fake_parse)

    payload, usage = await prov.generate_structured(
        system="s", messages=[{"role": "user", "content": "x"}],
        schema=_Schema, model="gpt-4o", max_tokens=100)
    assert payload.verdict == "ok"
    assert usage.input_tokens == 11 and usage.output_tokens == 7


@pytest.mark.asyncio(loop_scope="session")
async def test_generate_structured_wraps_errors(monkeypatch):
    prov = OpenAIProvider("sk-test")

    async def boom(**kwargs):
        raise RuntimeError("api down")
    monkeypatch.setattr(prov._client.beta.chat.completions, "parse", boom)
    with pytest.raises(LLMError):
        await prov.generate_structured(system="s", messages=[{"role": "user", "content": "x"}],
                                       schema=_Schema, model="gpt-4o", max_tokens=100)
```

- [ ] **Step 2: Run to verify failure**

Run: `cd backend && .venv/bin/pytest tests/test_openai_provider.py -q`
Expected: FAIL (module missing). (Add `openai>=1.50` to `pyproject.toml` `dependencies` and `pip install -e .` in the venv first if the import errors.)

- [ ] **Step 3: Implement the adapter**

```python
# backend/app/services/guru/llm/openai.py
from openai import AsyncOpenAI

from app.services.guru.llm.base import LLMError, LLMProvider, TextStream, Usage


def _to_openai_messages(system: str, messages: list[dict]) -> list[dict]:
    """Translate Anthropic-style messages (+ separate system string) into
    OpenAI chat messages. Text blocks pass through; Anthropic base64 image
    blocks become data-URL image_url parts."""
    out: list[dict] = [{"role": "system", "content": system}]
    for m in messages:
        content = m["content"]
        if isinstance(content, str):
            out.append({"role": m["role"], "content": content})
            continue
        parts = []
        for block in content:
            if block.get("type") == "text":
                parts.append({"type": "text", "text": block["text"]})
            elif block.get("type") == "image":
                src = block["source"]
                url = f"data:{src['media_type']};base64,{src['data']}"
                parts.append({"type": "image_url", "image_url": {"url": url}})
        out.append({"role": m["role"], "content": parts})
    return out


class OpenAIProvider(LLMProvider):
    def __init__(self, api_key: str):
        self._client = AsyncOpenAI(api_key=api_key)

    async def generate_structured(self, *, system, messages, schema, model, max_tokens):
        try:
            resp = await self._client.beta.chat.completions.parse(
                model=model, max_tokens=max_tokens,
                messages=_to_openai_messages(system, messages),
                response_format=schema,
            )
        except Exception as exc:
            raise LLMError(str(exc)) from exc
        msg = resp.choices[0].message
        if getattr(msg, "refusal", None):
            raise LLMError(f"model refused: {msg.refusal}")
        if msg.parsed is None:
            raise LLMError("model returned no parseable output")
        u = resp.usage
        return msg.parsed, Usage(u.prompt_tokens, u.completion_tokens)

    def stream_text(self, *, system, messages, model, max_tokens) -> TextStream:
        client, translate = self._client, _to_openai_messages
        stream_holder: list[TextStream] = []

        async def gen():
            try:
                stream = await client.chat.completions.create(
                    model=model, max_tokens=max_tokens,
                    messages=translate(system, messages), stream=True,
                    stream_options={"include_usage": True},
                )
                usage = None
                async for chunk in stream:
                    if chunk.usage is not None:
                        usage = chunk.usage
                    if chunk.choices and chunk.choices[0].delta.content:
                        yield chunk.choices[0].delta.content
                if usage is not None:
                    stream_holder[0].usage = Usage(usage.prompt_tokens, usage.completion_tokens)
            except LLMError:
                raise
            except Exception as exc:
                raise LLMError(str(exc)) from exc

        stream = TextStream(gen())
        stream_holder.append(stream)
        return stream
```

- [ ] **Step 4: Run tests**

Run: `cd backend && .venv/bin/pytest tests/test_openai_provider.py -q` → PASS (3). `.venv/bin/ruff check app/services/guru/llm/openai.py` → clean.

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/guru/llm/openai.py backend/pyproject.toml backend/tests/test_openai_provider.py
git commit -m "feat(llm): OpenAIProvider adapter (message/image translation + structured parse + streaming)"
```

---

## Task 4: GoogleProvider adapter

**Files:**
- Create: `backend/app/services/guru/llm/google.py`
- Modify: `backend/pyproject.toml` (add `google-genai>=1.0`)
- Test: `backend/tests/test_google_provider.py`

**Interfaces:**
- Produces: `GoogleProvider(api_key)` implementing the ABC; `_to_google_contents(messages) -> list` translating text + image blocks to genai parts.

**Implementer note:** confirm current `google-genai` API via context7 (`genai.Client(api_key=...)`, `client.aio.models.generate_content(model, contents, config=types.GenerateContentConfig(system_instruction, response_mime_type, response_schema, max_output_tokens))`, `.parsed`, `usage_metadata.prompt_token_count`/`.candidates_token_count`, and `generate_content_stream`). Adjust the adapter to the installed version; keep tests mocking exactly what the adapter calls.

- [ ] **Step 1: Write the failing tests (mocked client)**

```python
# backend/tests/test_google_provider.py
import pytest
from pydantic import BaseModel

from app.services.guru.llm.base import LLMError
from app.services.guru.llm.google import GoogleProvider, _to_google_contents


class _Schema(BaseModel):
    verdict: str


def test_translates_text_and_image_parts():
    msgs = [{"role": "user", "content": [
        {"type": "text", "text": "hi"},
        {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": "QUFB"}},
    ]}]
    parts = _to_google_contents(msgs)
    kinds = {p["kind"] for p in parts}          # helper returns a neutral, inspectable shape
    assert kinds == {"text", "image"}
    img = next(p for p in parts if p["kind"] == "image")
    assert img["mime_type"] == "image/png" and img["data"] == b"AAA"   # base64 QUFB -> b"AAA"


@pytest.mark.asyncio(loop_scope="session")
async def test_generate_structured_parses_and_usage(monkeypatch):
    prov = GoogleProvider("g-key")

    class _UM: prompt_token_count = 9; candidates_token_count = 4
    class _Resp: parsed = _Schema(verdict="ok"); usage_metadata = _UM()

    async def fake_gen(**kwargs):
        assert kwargs["model"] == "gemini-2.5-pro"
        return _Resp()
    monkeypatch.setattr(prov._client.aio.models, "generate_content", fake_gen)

    payload, usage = await prov.generate_structured(
        system="s", messages=[{"role": "user", "content": "x"}],
        schema=_Schema, model="gemini-2.5-pro", max_tokens=100)
    assert payload.verdict == "ok"
    assert usage.input_tokens == 9 and usage.output_tokens == 4


@pytest.mark.asyncio(loop_scope="session")
async def test_wraps_errors(monkeypatch):
    prov = GoogleProvider("g-key")

    async def boom(**kwargs):
        raise RuntimeError("quota")
    monkeypatch.setattr(prov._client.aio.models, "generate_content", boom)
    with pytest.raises(LLMError):
        await prov.generate_structured(system="s", messages=[{"role": "user", "content": "x"}],
                                       schema=_Schema, model="gemini-2.5-pro", max_tokens=100)
```

- [ ] **Step 2: Run to verify failure**

Run: `cd backend && .venv/bin/pytest tests/test_google_provider.py -q`
Expected: FAIL (module missing). Add `google-genai>=1.0` to deps + `pip install -e .` if import errors.

- [ ] **Step 3: Implement the adapter**

`_to_google_contents` returns a neutral inspectable list (text/image dicts with decoded image bytes) that the adapter maps to `types.Part`; this keeps the translation unit-testable without constructing SDK objects. The adapter builds real `types.Part` from it.

```python
# backend/app/services/guru/llm/google.py
import base64

from google import genai
from google.genai import types

from app.services.guru.llm.base import LLMError, LLMProvider, TextStream, Usage


def _to_google_contents(messages: list[dict]) -> list[dict]:
    """Flatten Anthropic-style user content into a neutral part list:
    {"kind":"text","text":...} / {"kind":"image","mime_type":...,"data":<bytes>}."""
    parts: list[dict] = []
    for m in messages:
        content = m["content"]
        if isinstance(content, str):
            parts.append({"kind": "text", "text": content})
            continue
        for block in content:
            if block.get("type") == "text":
                parts.append({"kind": "text", "text": block["text"]})
            elif block.get("type") == "image":
                src = block["source"]
                parts.append({"kind": "image", "mime_type": src["media_type"],
                              "data": base64.b64decode(src["data"])})
    return parts


def _to_parts(neutral: list[dict]) -> list:
    out = []
    for p in neutral:
        if p["kind"] == "text":
            out.append(types.Part.from_text(text=p["text"]))
        else:
            out.append(types.Part.from_bytes(data=p["data"], mime_type=p["mime_type"]))
    return out


class GoogleProvider(LLMProvider):
    def __init__(self, api_key: str):
        self._client = genai.Client(api_key=api_key)

    async def generate_structured(self, *, system, messages, schema, model, max_tokens):
        try:
            resp = await self._client.aio.models.generate_content(
                model=model, contents=_to_parts(_to_google_contents(messages)),
                config=types.GenerateContentConfig(
                    system_instruction=system, max_output_tokens=max_tokens,
                    response_mime_type="application/json", response_schema=schema),
            )
        except Exception as exc:
            raise LLMError(str(exc)) from exc
        if resp.parsed is None:
            raise LLMError("model returned no parseable output")
        um = resp.usage_metadata
        return resp.parsed, Usage(um.prompt_token_count or 0, um.candidates_token_count or 0)

    def stream_text(self, *, system, messages, model, max_tokens) -> TextStream:
        client = self._client
        stream_holder: list[TextStream] = []

        async def gen():
            try:
                stream = await client.aio.models.generate_content_stream(
                    model=model, contents=_to_parts(_to_google_contents(messages)),
                    config=types.GenerateContentConfig(
                        system_instruction=system, max_output_tokens=max_tokens),
                )
                usage = None
                async for chunk in stream:
                    if getattr(chunk, "usage_metadata", None):
                        usage = chunk.usage_metadata
                    if chunk.text:
                        yield chunk.text
                if usage is not None:
                    stream_holder[0].usage = Usage(
                        usage.prompt_token_count or 0, usage.candidates_token_count or 0)
            except LLMError:
                raise
            except Exception as exc:
                raise LLMError(str(exc)) from exc

        stream = TextStream(gen())
        stream_holder.append(stream)
        return stream
```

- [ ] **Step 4: Run tests**

Run: `cd backend && .venv/bin/pytest tests/test_google_provider.py -q` → PASS (3). Ruff clean.

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/guru/llm/google.py backend/pyproject.toml backend/tests/test_google_provider.py
git commit -m "feat(llm): GoogleProvider (Gemini) adapter (contents/image translation + structured + streaming)"
```

---

## Task 5: Config-aware rebuildable factory + role→model wiring

**Files:**
- Create: `backend/app/services/guru/llm/factory.py`
- Modify: `backend/app/services/guru/service.py`, `backend/app/api/guru.py`, `backend/app/services/guru/chat.py`, `backend/app/services/orso/vision.py`, `backend/app/services/guru/scheduler.py`
- Test: `backend/tests/test_guru_factory_config.py`

**Interfaces:**
- Consumes: `load_active_config` (Task 1), the three adapters (Tasks 3-4), `AnthropicProvider`.
- Produces: `build_provider(provider, api_key) -> LLMProvider`; `async get_guru_service(db) -> GuruService`; `invalidate_guru_service()`; `GuruService.__init__(provider, quotes, fx, *, advice_model, scan_model, advice_price=None, scan_price=None)` with attributes of the same names.

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_guru_factory_config.py
from datetime import UTC, datetime

import pytest

from app.models import LlmConfig
from app.services.guru import service as service_mod

pytestmark = pytest.mark.asyncio(loop_scope="session")


async def test_factory_rebuilds_provider_from_config_on_invalidate(db_session, monkeypatch):
    from app.core.config import settings
    monkeypatch.setattr(settings, "anthropic_api_key", "env-key")
    service_mod.invalidate_guru_service()

    svc1 = await service_mod.get_guru_service(db_session)
    from app.services.guru.llm.anthropic import AnthropicProvider
    assert isinstance(svc1.provider, AnthropicProvider)  # env fallback
    assert svc1.advice_model == settings.guru_advice_model

    db_session.add(LlmConfig(provider="openai", advice_model="gpt-4o", scan_model="gpt-4o-mini",
                             api_key="sk-x", updated_at=datetime.now(UTC).replace(tzinfo=None)))
    await db_session.commit()
    service_mod.invalidate_guru_service()

    svc2 = await service_mod.get_guru_service(db_session)
    from app.services.guru.llm.openai import OpenAIProvider
    assert isinstance(svc2.provider, OpenAIProvider)
    assert svc2.advice_model == "gpt-4o" and svc2.scan_model == "gpt-4o-mini"
```

- [ ] **Step 2: Run to verify failure**

Run: `cd backend && .venv/bin/pytest tests/test_guru_factory_config.py -q`
Expected: FAIL (`get_guru_service` is sync/no-db; no `invalidate_guru_service`; `GuruService` has no `advice_model`).

- [ ] **Step 3: Add the provider factory**

```python
# backend/app/services/guru/llm/factory.py
from app.services.guru.llm.anthropic import AnthropicProvider
from app.services.guru.llm.base import LLMProvider
from app.services.guru.llm.google import GoogleProvider
from app.services.guru.llm.openai import OpenAIProvider


def build_provider(provider: str, api_key: str) -> LLMProvider:
    if provider == "anthropic":
        return AnthropicProvider(api_key)
    if provider == "openai":
        return OpenAIProvider(api_key)
    if provider == "google":
        return GoogleProvider(api_key)
    raise ValueError(f"unknown LLM provider: {provider!r}")
```

- [ ] **Step 4: Rewire `GuruService` + the factory in `service.py`**

Change `GuruService.__init__` to accept and store the models/prices:

```python
    def __init__(self, provider, quotes, fx, *, advice_model, scan_model,
                 advice_price=None, scan_price=None):
        self.provider = provider
        self.quotes = quotes
        self.fx = fx
        self.advice_model = advice_model
        self.scan_model = scan_model
        self.advice_price = advice_price
        self.scan_price = scan_price
        self._locks = {}
```

Replace the module-bottom factory:

```python
_service: GuruService | None = None


async def get_guru_service(db) -> GuruService:
    global _service
    if _service is None:
        from app.services.guru.config import load_active_config
        from app.services.guru.llm.factory import build_provider
        from app.services.market_data.quotes import get_quote_service
        cfg = await load_active_config(db)
        provider = build_provider(cfg.provider, cfg.api_key) if cfg.api_key else None
        qs = get_quote_service()
        _service = GuruService(
            provider, qs, FxService(qs.provider),
            advice_model=cfg.advice_model, scan_model=cfg.scan_model,
            advice_price=cfg.advice_price, scan_price=cfg.scan_price)
    return _service


def invalidate_guru_service() -> None:
    global _service
    _service = None
```

Then replace model references throughout `service.py` (sites confirmed by
`grep -n "settings.guru_advice_model\|settings.guru_scan_model" app/services/guru/service.py`):
`settings.guru_advice_model` → `self.advice_model`, `settings.guru_scan_model` → `self.scan_model`. For each `record_usage(...)` call add the matching price: advice paths get `price=self.advice_price`, the digest (scan) path gets `price=self.scan_price`. Remove the now-unused `from app.core.config import settings` import only if nothing else in the file uses it (it still may — check).

- [ ] **Step 5: Rewire the callers**

`backend/app/api/guru.py` — make the dependency async + db-bound:

```python
async def get_guru(db: SessionDep) -> GuruService:
    return await get_guru_service(db)
```

(Import `SessionDep` from `app.api.deps` if not already imported; `GuruDep` stays `Annotated[GuruService, Depends(get_guru)]`.)

`backend/app/services/guru/scheduler.py` — replace `svc = get_guru_service()` with `svc = await get_guru_service(db)` using the session already open in that scope (both `run_daily_job` and `catch_up` open `async with factory() as db:` — build the service inside that block).

`backend/app/services/guru/chat.py` (lines 55, 71 use `settings.guru_advice_model`) — the chat function runs on a `GuruService`; use `self.advice_model` and `price=self.advice_price` (if chat.py is a free function taking the service/provider, thread the model + price through its signature from the caller in `service.py`/`api/guru.py`). Match the existing call shape; do not change chat behaviour otherwise.

`backend/app/services/orso/vision.py` — `extract_statement` currently reads `settings.guru_advice_model`. Add params `*, model: str, price=None`; use them for `generate_structured(model=model, ...)` and `record_usage(..., price=price)`. Update the caller in `backend/app/api/orso.py` (`ingest_screenshot`) to pass `model=guru.advice_model, price=guru.advice_price`.

- [ ] **Step 6: Run tests**

Run: `cd backend && .venv/bin/pytest tests/test_guru_factory_config.py -q` → PASS. Then the full suite `.venv/bin/pytest -q` — the `guru_client`/`fake_llm` fixtures must still inject the fake provider; if conftest builds `GuruService(...)` directly it needs the new keyword args, so update that fixture to pass `advice_model="test-advice", scan_model="test-scan"`. Fix any call sites the compiler/tests surface. Ruff clean.

- [ ] **Step 7: Commit**

```bash
git add backend/app/services/guru/ backend/app/api/guru.py backend/app/services/orso/vision.py backend/app/api/orso.py backend/tests/test_guru_factory_config.py
git commit -m "feat(llm): config-aware rebuildable get_guru_service + role->model wiring + invalidate"
```

---

## Task 6: Admin API — GET / PUT / test

**Files:**
- Modify: `backend/app/api/admin.py`
- Test: `backend/tests/test_admin_llm_config.py`

**Interfaces:**
- Consumes: `AdminUser`, `SessionDep`, `LlmConfig`, `load_active_config`, `invalidate_guru_service`, `build_provider`.
- Produces: `GET /api/admin/llm-config`, `PUT /api/admin/llm-config`, `POST /api/admin/llm-config/test`.

- [ ] **Step 1: Write the failing tests**

```python
# backend/tests/test_admin_llm_config.py
import pytest

pytestmark = pytest.mark.asyncio(loop_scope="session")


async def _make_admin(guru_client, monkeypatch):
    # guru_client's user is lee@test.dev; put it on the admin allowlist
    from app.core.config import settings
    monkeypatch.setattr(settings, "admin_emails", ["lee@test.dev"])


async def test_non_admin_forbidden(guru_client):
    r = await guru_client.get("/api/admin/llm-config")
    assert r.status_code == 403


async def test_put_then_get_never_returns_key(guru_client, monkeypatch):
    await _make_admin(guru_client, monkeypatch)
    r = await guru_client.put("/api/admin/llm-config", json={
        "provider": "openai", "advice_model": "gpt-4o", "scan_model": "gpt-4o-mini",
        "api_key": "sk-secret"})
    assert r.status_code == 200
    got = (await guru_client.get("/api/admin/llm-config")).json()
    assert got["provider"] == "openai" and got["advice_model"] == "gpt-4o"
    assert got["key_set"] is True
    assert "api_key" not in got and "sk-secret" not in str(got)


async def test_put_omitting_key_preserves_stored_key(guru_client, monkeypatch):
    await _make_admin(guru_client, monkeypatch)
    await guru_client.put("/api/admin/llm-config", json={
        "provider": "openai", "advice_model": "gpt-4o", "scan_model": "gpt-4o-mini",
        "api_key": "sk-first"})
    # edit models, omit api_key -> key stays set
    await guru_client.put("/api/admin/llm-config", json={
        "provider": "openai", "advice_model": "gpt-4.1", "scan_model": "gpt-4o-mini"})
    got = (await guru_client.get("/api/admin/llm-config")).json()
    assert got["advice_model"] == "gpt-4.1" and got["key_set"] is True


async def test_test_endpoint_reports_failure_not_500(guru_client, monkeypatch):
    await _make_admin(guru_client, monkeypatch)

    async def boom(*a, **k):
        raise RuntimeError("bad key")
    # force the test call to fail inside the provider
    import app.api.admin as admin_mod
    monkeypatch.setattr(admin_mod, "_run_test_call", boom)
    r = await guru_client.post("/api/admin/llm-config/test", json={
        "provider": "openai", "advice_model": "gpt-4o", "scan_model": "gpt-4o-mini",
        "api_key": "sk-bad"})
    assert r.status_code == 200
    assert r.json()["ok"] is False
```

- [ ] **Step 2: Run to verify failure**

Run: `cd backend && .venv/bin/pytest tests/test_admin_llm_config.py -q`
Expected: FAIL (endpoints missing).

- [ ] **Step 3: Implement the endpoints**

```python
# backend/app/api/admin.py
from datetime import UTC, datetime

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.api.deps import AdminUser, SessionDep
from app.models import LlmConfig
from app.services.guru.config import load_active_config
from app.services.guru.llm.base import Usage  # noqa: F401 (documents return shape)
from app.services.guru.llm.factory import build_provider
from app.services.guru.service import invalidate_guru_service

router = APIRouter(prefix="/api/admin", tags=["admin"])

_PROVIDERS = {"anthropic", "openai", "google"}


@router.get("/ping")
async def ping(user: AdminUser) -> dict[str, bool]:
    return {"ok": True}


class LlmConfigOut(BaseModel):
    provider: str
    advice_model: str
    scan_model: str
    advice_input_price: str | None
    advice_output_price: str | None
    scan_input_price: str | None
    scan_output_price: str | None
    key_set: bool
    updated_at: str | None
    updated_by: str | None


class LlmConfigIn(BaseModel):
    provider: str
    advice_model: str = Field(min_length=1, max_length=64)
    scan_model: str = Field(min_length=1, max_length=64)
    api_key: str | None = None
    advice_input_price: str | None = None
    advice_output_price: str | None = None
    scan_input_price: str | None = None
    scan_output_price: str | None = None


async def _get_row(db) -> LlmConfig | None:
    return (await db.execute(select(LlmConfig).order_by(LlmConfig.id).limit(1))).scalar_one_or_none()


def _dec(v):
    from decimal import Decimal
    return None if v in (None, "") else Decimal(v)


@router.get("/llm-config", response_model=LlmConfigOut)
async def get_llm_config(db: SessionDep, user: AdminUser):
    row = await _get_row(db)
    if row is None:
        cfg = await load_active_config(db)  # env fallback view
        return LlmConfigOut(provider=cfg.provider, advice_model=cfg.advice_model,
                            scan_model=cfg.scan_model, advice_input_price=None,
                            advice_output_price=None, scan_input_price=None,
                            scan_output_price=None, key_set=bool(cfg.api_key),
                            updated_at=None, updated_by=None)
    return LlmConfigOut(
        provider=row.provider, advice_model=row.advice_model, scan_model=row.scan_model,
        advice_input_price=(None if row.advice_input_price is None else str(row.advice_input_price)),
        advice_output_price=(None if row.advice_output_price is None else str(row.advice_output_price)),
        scan_input_price=(None if row.scan_input_price is None else str(row.scan_input_price)),
        scan_output_price=(None if row.scan_output_price is None else str(row.scan_output_price)),
        key_set=bool(row.api_key), updated_at=row.updated_at.isoformat(), updated_by=row.updated_by)


@router.put("/llm-config", response_model=LlmConfigOut)
async def put_llm_config(body: LlmConfigIn, db: SessionDep, user: AdminUser):
    if body.provider not in _PROVIDERS:
        raise HTTPException(status_code=422, detail="unknown_provider")
    row = await _get_row(db)
    if row is None:
        row = LlmConfig(api_key="")
        db.add(row)
    row.provider = body.provider
    row.advice_model = body.advice_model
    row.scan_model = body.scan_model
    if body.api_key:                       # blank/None -> keep the stored key
        row.api_key = body.api_key
    row.advice_input_price = _dec(body.advice_input_price)
    row.advice_output_price = _dec(body.advice_output_price)
    row.scan_input_price = _dec(body.scan_input_price)
    row.scan_output_price = _dec(body.scan_output_price)
    row.updated_at = datetime.now(UTC).replace(tzinfo=None)
    row.updated_by = user.email
    await db.commit()
    invalidate_guru_service()              # next request rebuilds with the new config
    return await get_llm_config(db, user)


class _Probe(BaseModel):
    ok: bool


async def _run_test_call(provider: str, api_key: str, model: str) -> None:
    """One minimal structured call to validate provider+key+model. Raises on failure."""
    prov = build_provider(provider, api_key)
    await prov.generate_structured(
        system="Reply with ok=true.", messages=[{"role": "user", "content": "ping"}],
        schema=_Probe, model=model, max_tokens=16)


@router.post("/llm-config/test")
async def test_llm_config(body: LlmConfigIn, db: SessionDep, user: AdminUser) -> dict:
    api_key = body.api_key
    if not api_key:
        row = await _get_row(db)
        api_key = (row.api_key if row else "") or ""
    if not api_key:
        return {"ok": False, "detail": "no api key configured"}
    try:
        await _run_test_call(body.provider, api_key, body.advice_model)
    except Exception as exc:  # provider/auth/network failure -> clean report, never 500
        return {"ok": False, "detail": str(exc)[:200]}
    return {"ok": True, "detail": "connection ok"}
```

- [ ] **Step 4: Run tests**

Run: `cd backend && .venv/bin/pytest tests/test_admin_llm_config.py -q` → PASS (4). Full suite green. Ruff clean.

- [ ] **Step 5: Commit**

```bash
git add backend/app/api/admin.py backend/tests/test_admin_llm_config.py
git commit -m "feat(admin): llm-config GET/PUT/test endpoints (admin-only; key write-only, encrypted)"
```

---

## Task 7: Figma gate (USER GATE)

**Files:** none (Figma design artifacts).

- [ ] **Step 1: Produce Figma frames** for the `/admin` "AI provider" panel: provider `<select>`; advice + scan model text inputs; a collapsible "Budget (optional)" group with the four price inputs; a write-only API-key input showing a "configured" pill when `key_set`; a **Test** button with ✓/✗ result; Save. Match the existing admin-shell styling (file key `0gU58wfjttdZS0NXQeEtuD`).
- [ ] **Step 2: Present to the user (inline PNGs) and get explicit approval before Task 8.** Incorporate feedback and re-present until approved.

---

## Task 8: Admin panel frontend (push seam)

**Files:**
- Modify: `frontend/src/lib/api.ts`, `frontend/src/lib/types.ts`, `frontend/src/pages/AdminPage.tsx`
- Test: `frontend/src/pages/AdminPage.test.tsx`

**Interfaces:**
- Consumes: `GET/PUT /api/admin/llm-config`, `POST /api/admin/llm-config/test`.

- [ ] **Step 1: Types + API client** — add `LlmConfig` type (mirrors `LlmConfigOut`, incl. `key_set`), and `getLlmConfig()`, `putLlmConfig(body)`, `testLlmConfig(body)` in `lib/api.ts` following the existing fetch/`ApiError` pattern.
- [ ] **Step 2: Panel (TDD w/ vitest-axe)** — write a failing `AdminPage.test.tsx` that (mocking fetch) renders the panel with an existing config (`key_set:true`, provider openai), asserts the key field shows "configured" and is empty, changes the advice model + clicks Save → asserts `putLlmConfig` called WITHOUT `api_key` (so the stored key is preserved), and clicks Test → asserts the ✓/✗ result renders. Then implement the panel in `AdminPage.tsx` (replacing the "Coming soon" block), gated on the existing admin/forbidden states. Provider `<select>`, model inputs, collapsible price inputs, write-only key input (placeholder "•••• configured — leave blank to keep"), Test button wired to `testLlmConfig`, Save wired to `putLlmConfig`. axe assertion on the populated form.
- [ ] **Step 3: Verify** — `cd frontend && npm run check` (tsc + lint + vitest incl. axe + build) → green.
- [ ] **Step 4: Commit + push (push seam — reaches prod)**

```bash
git add frontend/src
git commit -m "feat(admin): LLM provider/model/key config panel + test-connection (frontend)"
git push origin main
```

Confirm CI green (`gh run view <id> --json conclusion,jobs`); Railway deploys the backend on green CI (migration 0010 runs), Vercel the frontend.

---

## Task 9: Docs + live smoke + final Opus review

- [ ] **Step 1: Live smoke** on prod — as admin, open `/admin`, GET returns current config with `key_set` (no key value); PUT switches provider/models (with a real key you enter — never committed); Test button validates; regenerate a Guru take / ORSO advice and confirm it runs on the new provider; confirm a non-admin gets 403. Confirm migration 0010 ran (`railway logs … | grep 0010`), health 200.
- [ ] **Step 2: Docs** — AGENTS.md (head → 0010; the multi-provider LLM + admin config surface; note the key is DB-encrypted, panel-entered), `docs/PROGRESS.md` (new section), README, and `docs/deployment.md` (note: `ANTHROPIC_API_KEY` env is now only the pre-panel fallback; the active provider/key live in `llm_config`).
- [ ] **Step 3: Final whole-branch review on Opus** — base = the pre-Task-1 tip. Security focus: key never logged/returned, encrypted at rest, admin-only; degrade-never-500 across all three adapters; rebuild-on-save correctness. Fix wave → re-review to merge-clean; push fixes; refresh docs if anything changed.
- [ ] **Step 4: Commit doc/fix changes + push.**

---

## Self-Review (completed by the plan author)

**1. Spec coverage:** llm_config table + encrypted key + precedence → Task 1. Cost/pricing order + logged-uncosted → Task 2. OpenAI adapter → Task 3. Google adapter → Task 4. Config-aware rebuildable factory + role→model + invalidate → Task 5. Admin GET/PUT(key-preserve)/test → Task 6. Figma gate → Task 7. Panel → Task 8. Docs+smoke+Opus → Task 9. All spec §1–§7 requirements map to a task. Message-translation Approach A is realized in Tasks 3-4 (`_to_openai_messages`/`_to_google_contents`), Anthropic unchanged.

**2. Placeholder scan:** no `TBD`/vague-error directives. The built-in prices are concrete values flagged "approximate — panel prices override," which is a real, working default (not a placeholder). The two "confirm SDK via context7" implementer notes are risk callouts, not missing content (full adapter code is given).

**3. Type consistency:** `ResolvedLlmConfig` fields (Task 1) are consumed unchanged in Task 5's factory. `GuruService.__init__(*, advice_model, scan_model, advice_price, scan_price)` (Task 5) matches the attributes read in the rewired `service.py`/`chat.py`/`vision.py` and the conftest fixture update. `estimate_cost(model, usage, price=None)` (Task 2) matches the `price=self.advice_price/scan_price` calls (Task 5). `build_provider(provider, api_key)` (Task 5) is used by Task 6's test endpoint. `LlmConfigOut.key_set` (Task 6) matches the frontend type + test (Task 8).

**Fixtures note for executors (confirmed):** `backend/tests/conftest.py:161` builds `svc = GuruService(fake_llm, *(_test_services()))` and overrides the dependency with `dependency_overrides[get_guru] = lambda: svc` (line 162). After Task 5:
- Update line 161 to `GuruService(fake_llm, *(_test_services()), advice_model="test-advice", scan_model="test-scan")` (the new `__init__` makes those keyword-only).
- **Leave the `lambda: svc` override as-is** — a FastAPI dependency override replaces the original wholesale, so a sync no-arg lambda keeps working even though `get_guru` becomes `async def get_guru(db)`. This is what keeps the fake provider injected for all Guru/ORSO tests; the real `get_guru_service(db)` config path is exercised only by `test_guru_factory_config.py` (which calls it directly with `db_session`).
