# Phase 2b — The Guru — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship the Guru — investor profile, provider-agnostic LLM layer (Anthropic first), portfolio review reports, daily digest + dashboard "Guru's take" with a scheduler, per-position takes, and SSE chat — per spec `docs/superpowers/specs/2026-07-08-phase-2b-guru-design.md`.

**Architecture:** Structured-output pipeline: every non-chat mode calls `LLMProvider.generate_structured` with a mode-specific Pydantic schema and persists a versioned `guru_reports` row; a shared `ContextBuilder` assembles profile + valuations + signals; chat is the only free-text (streaming) path. Signals stay deterministic (Phase 2a). Tests never hit the real API — `FakeLLMProvider` is injected exactly like `_NullProvider` is today.

**Tech Stack:** FastAPI + SQLAlchemy 2 async + Alembic (backend), `anthropic` SDK (AsyncAnthropic, `messages.parse` + `messages.stream`), APScheduler, React/Vite/TS/Tailwind v4 + React Query (frontend), pytest-asyncio + vitest/RTL.

## Global Constraints

- Public repo: **never commit real holdings data** — synthetic fixtures only. Never read/modify `.env`.
- Money/quantity = `Numeric`/`Decimal`, never float. Every user-data table has `user_id`.
- DB change = hand-written chained Alembic migration (`alembic heads` first; new head chains on `0004`).
- Async tests: `pytestmark = pytest.mark.asyncio(loop_scope="session")` + shared `conftest.py` fixtures (`client`, `auth_client`, `db_session`, `make_instrument`). Never a raw `AsyncClient` on the app engine.
- Providers are fixture-mocked in tests; endpoints degrade on provider failure, **never 500**.
- TDD: failing test → minimal code → commit. Verify: `ruff check . && pytest` (backend, run from `backend/`, venv `backend/.venv`), `npm run check` (frontend).
- Models (config defaults): advice = `claude-opus-4-8` ($5/$25 per MTok), scan = `claude-haiku-4-5` ($1/$5 per MTok).
- Disclaimer string everywhere: `"The Guru is not regulated financial advice."`
- Commit messages end with `Co-Authored-By:` trailer per session convention.
- Update `.superpowers/sdd/progress.md` after each task (existing ledger convention).

**Execution notes:** Tasks 1–9 are backend (push at Task 9 seam is fine); Task 10 is a **user gate** (Figma approval — stop and wait); Tasks 11–13 frontend; Task 14 docs + live smoke (**needs the user's `ANTHROPIC_API_KEY` in `backend/.env` — ask the user to set it; never read the file**). Final whole-branch review on Opus per the model-mix rule. Implementer/reviewer subagents: cheap models (Sonnet) per [[feedback_model_selection]].

---

### Task 1: 2a-deferred cleanup

**Files:**
- Modify: `frontend/src/pages/DashboardPage.tsx`
- Modify: `frontend/src/pages/PortfolioDetailPage.tsx`
- Modify: `frontend/src/components/AttentionPanel.tsx`
- Modify: `frontend/src/components/AttentionPanel.test.tsx`
- Modify: `backend/app/services/market_data/fundamentals.py`
- Modify: `backend/app/services/market_data/news.py`
- Modify: `backend/app/services/signals/engine.py`
- Modify: `backend/pyproject.toml`
- Test: `frontend/src/pages/DashboardPage.test.tsx`, `backend/tests/test_signals_engine.py` (existing files — add cases)

**Interfaces:**
- Consumes: existing Phase 2a code only.
- Produces: no new interfaces; behavior fixes listed in spec §12.

- [ ] **Step 1: Frontend failing tests** — add to `frontend/src/pages/DashboardPage.test.tsx` (follow the file's existing mock/query-client setup):

```tsx
it("shows an error message when Run analysis fails", async () => {
  // arrange: mock apiFetch so POST /analyze rejects
  // (reuse the file's existing vi.mock("../lib/api") pattern)
  render(<DashboardPage />, { wrapper });
  await userEvent.click(await screen.findByRole("button", { name: /run analysis/i }));
  expect(await screen.findByText(/analysis failed/i)).toBeInTheDocument();
});
```

and to `AttentionPanel.test.tsx`:

```tsx
it("conveys severity as text, not colour alone", async () => {
  render(<AttentionPanel />, { wrapper });
  expect(await screen.findByText(/high/i)).toBeInTheDocument(); // sr-only label
});
```

- [ ] **Step 2: Run** `npm test -- DashboardPage AttentionPanel` — expect the two new cases to FAIL.
- [ ] **Step 3: Implement frontend fixes**

`DashboardPage.tsx` — (a) error state, (b) drop the redundant `/api/dashboard` refetch inside `mutationFn` (the query data is already in scope):

```tsx
const runAnalysis = useMutation({
  mutationFn: async () => {
    const portfolios = dash.data?.portfolios ?? [];
    const results = await Promise.all(
      portfolios.map((p) =>
        apiFetch<AnalyzeResponse>(`/api/portfolios/${p.id}/analyze`, { method: "POST" }),
      ),
    );
    return [...new Set(results.flatMap((r) => r.unavailable_inputs))];
  },
  onSuccess: (union) => {
    setUnavailable(union);
    qc.invalidateQueries({ queryKey: ["attention"] });
    qc.invalidateQueries({ queryKey: ["dashboard"] });
  },
});
```

and below the unavailable banner add:

```tsx
{runAnalysis.isError && (
  <p className="rounded-md bg-loss/10 p-3 text-sm text-loss">
    Analysis failed — provider may be down. Try again.
  </p>
)}
```

Apply the same `runAnalysis.isError` paragraph to `PortfolioDetailPage.tsx` (after its banner).

`AttentionPanel.tsx` — add a visually-hidden severity label next to the dot:

```tsx
<span className={`mt-1 h-2 w-2 shrink-0 rounded-full ${DOT[s.severity]}`} />
<span className="sr-only">{s.severity} severity</span>
```

- [ ] **Step 4: Backend failing tests** — add to `backend/tests/test_signals_engine.py`:

```python
async def test_fundamentals_down_with_stale_row_reports_unavailable(db_session, make_instrument):
    """A dead earnings feed must not be masked by a stale cached row."""
    # seed a stale InstrumentFundamentals row (fetched_at older than FUNDAMENTALS_TTL),
    # run engine with a provider whose get_earnings_date raises,
    # assert "earnings" in result.unavailable_inputs

async def test_news_freshness_keyed_on_published_at(db_session, make_instrument):
    """recent_news must filter on published_at (fallback fetched_at when null)."""
    # seed two NewsItems: published_at 10 days ago but fetched_at now → excluded;
    # published_at None + fetched_at now → included
```

Write these as real tests using the existing seeding helpers in that file (it already seeds `InstrumentFundamentals` and `NewsItem` rows for other cases — copy those patterns).

- [ ] **Step 5: Run** `pytest tests/test_signals_engine.py -q` — new cases FAIL.
- [ ] **Step 6: Implement backend fixes**

`fundamentals.py` — in `refresh`, when the provider call fails for an instrument that only has a **stale** row, do not count it as covered: return the set of instrument ids actually fresh (fresh row within TTL or successfully fetched), mirroring `HistoryService.refresh`; `engine.py` then appends `"earnings"` to `unavailable` when any id is missing from the returned set (replace the current read-back detection).

`news.py::recent_news` — filter on `published_at` with fetched fallback:

```python
.where(
    NewsItem.instrument_id == instrument_id,
    func.coalesce(NewsItem.published_at, NewsItem.fetched_at) >= cutoff,
)
```

`engine.py` — add module logger + per-rule logging in the rule loop:

```python
logger = logging.getLogger(__name__)
# inside the per-rule try/except:
except Exception:
    logger.exception("signal rule %s failed", rule.__name__)
```

`pyproject.toml` — under `[tool.pytest.ini_options]` add:

```toml
filterwarnings = ["error"]
```

Run the full suite; if third-party deprecation warnings fire, add targeted `ignore::DeprecationWarning:<module>` entries rather than removing the error default.

- [ ] **Step 7: Multi-portfolio union test** — add to `DashboardPage.test.tsx`: mock two portfolios whose analyze responses return `["news"]` and `["history"]`; assert the banner shows both.
- [ ] **Step 8: Verify** — backend: `ruff check . && pytest -q`; frontend: `npm run check`. All green.
- [ ] **Step 9: Commit** — `chore: close out Phase 2a deferred items (error UI, a11y, staleness, logging, warnings)`

---

### Task 2: Migration 0005 + models

**Files:**
- Create: `backend/app/models/guru.py`
- Create: `backend/alembic/versions/0005_guru.py`
- Modify: `backend/app/models/__init__.py`
- Test: `backend/tests/test_guru_models.py`

**Interfaces:**
- Produces: `InvestorProfile`, `GuruReport`, `ChatThread`, `ChatMessage`, `LlmUsage` ORM classes (fields below) — consumed by Tasks 3–9.

- [ ] **Step 1: Failing test** — `backend/tests/test_guru_models.py`:

```python
from datetime import UTC, datetime
from decimal import Decimal

import pytest

from app.models import ChatMessage, ChatThread, GuruReport, InvestorProfile, LlmUsage, User

pytestmark = pytest.mark.asyncio(loop_scope="session")


async def _user(db_session) -> User:
    u = User(email="guru@test.dev", password_hash="x")
    db_session.add(u)
    await db_session.commit()
    return u


async def test_guru_tables_roundtrip(db_session):
    u = await _user(db_session)
    now = datetime.now(UTC).replace(tzinfo=None)
    db_session.add(InvestorProfile(user_id=u.id, risk_appetite="balanced",
                                   horizon="long", sector_interests=["tech"], free_text="hi"))
    report = GuruReport(user_id=u.id, kind="digest", portfolio_id=None,
                        payload={"summary": "s"}, model="claude-haiku-4-5", created_at=now)
    db_session.add(report)
    thread = ChatThread(user_id=u.id, title="t", portfolio_id=None, seed_context=None)
    db_session.add(thread)
    await db_session.commit()
    db_session.add(ChatMessage(thread_id=thread.id, role="user", content="hello", created_at=now))
    db_session.add(LlmUsage(user_id=u.id, mode="digest", model="claude-haiku-4-5",
                            input_tokens=100, output_tokens=50,
                            est_cost_usd=Decimal("0.0004"), report_id=report.id, created_at=now))
    await db_session.commit()
    assert report.id and thread.id


async def test_investor_profile_unique_per_user(db_session):
    u = await _user(db_session)
    db_session.add(InvestorProfile(user_id=u.id))
    await db_session.commit()
    db_session.add(InvestorProfile(user_id=u.id))
    with pytest.raises(Exception):
        await db_session.commit()
```

- [ ] **Step 2: Run** `pytest tests/test_guru_models.py -q` — FAIL (ImportError).
- [ ] **Step 3: Implement** `backend/app/models/guru.py`:

```python
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import ForeignKey, Numeric, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base
from app.models.base import TimestampMixin


class InvestorProfile(TimestampMixin, Base):
    __tablename__ = "investor_profiles"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), unique=True, index=True)
    risk_appetite: Mapped[str] = mapped_column(String(16), default="balanced")
    horizon: Mapped[str] = mapped_column(String(16), default="medium")
    sector_interests: Mapped[list[str]] = mapped_column(JSONB, default=list)
    free_text: Mapped[str] = mapped_column(Text, default="")


class GuruReport(Base):
    __tablename__ = "guru_reports"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    kind: Mapped[str] = mapped_column(String(8))  # review | digest | take
    portfolio_id: Mapped[int | None] = mapped_column(ForeignKey("portfolios.id"))
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB)
    model: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column()


class ChatThread(TimestampMixin, Base):
    __tablename__ = "chat_threads"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    title: Mapped[str] = mapped_column(String(200))
    portfolio_id: Mapped[int | None] = mapped_column(ForeignKey("portfolios.id"))
    seed_context: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)


class ChatMessage(Base):
    __tablename__ = "chat_messages"

    id: Mapped[int] = mapped_column(primary_key=True)
    thread_id: Mapped[int] = mapped_column(ForeignKey("chat_threads.id"), index=True)
    role: Mapped[str] = mapped_column(String(9))  # user | assistant
    content: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column()


class LlmUsage(Base):
    __tablename__ = "llm_usage"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    mode: Mapped[str] = mapped_column(String(16))  # review | digest | take | chat
    model: Mapped[str] = mapped_column(String(64))
    input_tokens: Mapped[int] = mapped_column()
    output_tokens: Mapped[int] = mapped_column()
    est_cost_usd: Mapped[Decimal | None] = mapped_column(Numeric(10, 4), nullable=True)
    report_id: Mapped[int | None] = mapped_column(ForeignKey("guru_reports.id"))
    thread_id: Mapped[int | None] = mapped_column(ForeignKey("chat_threads.id"))
    created_at: Mapped[datetime] = mapped_column()
```

Register in `app/models/__init__.py` (imports + `__all__`, alphabetical, matching the existing style).

- [ ] **Step 4: Migration** — check head: `alembic heads` → `0004`. Create `backend/alembic/versions/0005_guru.py` mirroring `0004_signals.py` style: `revision = "0005"`, `down_revision = "0004"`, `upgrade()` creates the five tables with columns exactly as the models (JSONB via `postgresql.JSONB()`, `Numeric(10, 4)`, unique constraint on `investor_profiles.user_id`, indexes on every `user_id`/`thread_id` FK column named `ix_<table>_<col>`), `downgrade()` drops them in reverse order (`llm_usage`, `chat_messages`, `chat_threads`, `guru_reports`, `investor_profiles`).
- [ ] **Step 5: Verify migration chain** — `alembic upgrade head && alembic downgrade 0004 && alembic upgrade head` against the dev DB (docker compose db must be up). Expected: no errors.
- [ ] **Step 6: Run** `pytest tests/test_guru_models.py -q` — PASS. `ruff check .` clean.
- [ ] **Step 7: Commit** — `feat(guru): migration 0005 + models (profile, reports, chat, usage)`

---

### Task 3: LLM layer — base, fake, Anthropic provider, usage logging, config

**Files:**
- Create: `backend/app/services/guru/__init__.py` (empty), `backend/app/services/guru/llm/__init__.py` (empty)
- Create: `backend/app/services/guru/llm/base.py`
- Create: `backend/app/services/guru/llm/fake.py`
- Create: `backend/app/services/guru/llm/anthropic.py`
- Create: `backend/app/services/guru/usage.py`
- Modify: `backend/app/core/config.py`
- Modify: `backend/pyproject.toml` (dependencies)
- Test: `backend/tests/test_guru_llm.py`

**Interfaces:**
- Produces (consumed by Tasks 5–9):
  - `Usage(input_tokens: int, output_tokens: int)` dataclass
  - `LLMError(Exception)`, `LLMNotConfigured(LLMError)`
  - `class TextStream` — async-iterable of `str`; `.usage: Usage | None` populated after exhaustion
  - `class LLMProvider(ABC)`:
    - `async generate_structured(*, system: str, messages: list[dict], schema: type[BaseModel], model: str, max_tokens: int) -> tuple[BaseModel, Usage]`
    - `stream_text(*, system: str, messages: list[dict], model: str, max_tokens: int) -> TextStream`
  - `FakeLLMProvider` — `.structured_queue: list[BaseModel]` (popped per call, AssertionError when empty), `.stream_chunks: list[str]`, `.fail_structured: int` (raise `LLMError` for the first N calls), `.calls: list[dict]` (records system/messages/model)
  - `usage.estimate_cost(model: str, usage: Usage) -> Decimal | None`
  - `async usage.record_usage(db, *, user_id: int, mode: str, model: str, usage: Usage, report_id: int | None = None, thread_id: int | None = None) -> LlmUsage` (adds + flushes, no commit)
  - Settings: `anthropic_api_key: str = ""`, `guru_advice_model: str = "claude-opus-4-8"`, `guru_scan_model: str = "claude-haiku-4-5"`, `guru_digest_hour: int = 7`, `guru_timezone: str = "Europe/London"`

- [ ] **Step 1: Add dependencies** — in `backend/pyproject.toml` `dependencies`: `"anthropic>=0.80"`, `"apscheduler>=3.10"`. Then `pip install -e ".[dev]"` in the venv. Sanity: `python -c "from anthropic import AsyncAnthropic; AsyncAnthropic(api_key='x').messages.parse"` — must not AttributeError (if it does, `pip install -U anthropic` and raise the floor to the installed version).
- [ ] **Step 2: Failing tests** — `backend/tests/test_guru_llm.py`:

```python
from decimal import Decimal

import pytest
from pydantic import BaseModel

from app.services.guru.llm.base import LLMError, TextStream, Usage
from app.services.guru.llm.fake import FakeLLMProvider
from app.services.guru.usage import estimate_cost, record_usage

pytestmark = pytest.mark.asyncio(loop_scope="session")


class Out(BaseModel):
    answer: str


async def test_fake_structured_returns_queued_payload_and_usage():
    fake = FakeLLMProvider()
    fake.structured_queue.append(Out(answer="42"))
    result, usage = await fake.generate_structured(
        system="s", messages=[{"role": "user", "content": "q"}],
        schema=Out, model="m", max_tokens=100,
    )
    assert result.answer == "42"
    assert usage == Usage(input_tokens=100, output_tokens=50)
    assert fake.calls[0]["model"] == "m"


async def test_fake_structured_failure_injection():
    fake = FakeLLMProvider()
    fake.fail_structured = 1
    fake.structured_queue.append(Out(answer="ok"))
    with pytest.raises(LLMError):
        await fake.generate_structured(system="s", messages=[], schema=Out,
                                       model="m", max_tokens=10)
    result, _ = await fake.generate_structured(system="s", messages=[], schema=Out,
                                               model="m", max_tokens=10)
    assert result.answer == "ok"


async def test_fake_stream_yields_chunks_then_usage():
    fake = FakeLLMProvider()
    fake.stream_chunks = ["Hel", "lo"]
    stream = fake.stream_text(system="s", messages=[], model="m", max_tokens=10)
    assert isinstance(stream, TextStream)
    text = "".join([chunk async for chunk in stream])
    assert text == "Hello"
    assert stream.usage == Usage(input_tokens=100, output_tokens=50)


def test_estimate_cost_known_and_unknown_models():
    u = Usage(input_tokens=1_000_000, output_tokens=1_000_000)
    assert estimate_cost("claude-opus-4-8", u) == Decimal("30")
    assert estimate_cost("claude-haiku-4-5", u) == Decimal("6")
    assert estimate_cost("mystery-model", u) is None


async def test_record_usage_persists_row(db_session):
    from app.models import User
    user = User(email="u@test.dev", password_hash="x")
    db_session.add(user)
    await db_session.commit()
    row = await record_usage(db_session, user_id=user.id, mode="digest",
                             model="claude-haiku-4-5",
                             usage=Usage(input_tokens=10, output_tokens=5))
    await db_session.commit()
    assert row.id is not None
    assert row.est_cost_usd is not None
```

- [ ] **Step 3: Run** `pytest tests/test_guru_llm.py -q` — FAIL (ImportError).
- [ ] **Step 4: Implement** `base.py`:

```python
from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from dataclasses import dataclass

from pydantic import BaseModel


@dataclass(frozen=True)
class Usage:
    input_tokens: int
    output_tokens: int


class LLMError(Exception):
    """Provider failed or returned unusable output."""


class LLMNotConfigured(LLMError):
    """No API key configured — Guru features unavailable."""


class TextStream:
    """Async iterator of text chunks; .usage is set once the stream completes."""

    def __init__(self, gen: AsyncIterator[str]):
        self._gen = gen
        self.usage: Usage | None = None

    def __aiter__(self) -> AsyncIterator[str]:
        return self._gen.__aiter__()


class LLMProvider(ABC):
    @abstractmethod
    async def generate_structured(
        self, *, system: str, messages: list[dict], schema: type[BaseModel],
        model: str, max_tokens: int,
    ) -> tuple[BaseModel, Usage]: ...

    @abstractmethod
    def stream_text(
        self, *, system: str, messages: list[dict], model: str, max_tokens: int,
    ) -> TextStream: ...
```

`fake.py`:

```python
from pydantic import BaseModel

from app.services.guru.llm.base import LLMError, LLMProvider, TextStream, Usage

_FIXED_USAGE = Usage(input_tokens=100, output_tokens=50)


class FakeLLMProvider(LLMProvider):
    def __init__(self):
        self.structured_queue: list[BaseModel] = []
        self.stream_chunks: list[str] = ["Hello ", "from the Guru."]
        self.fail_structured = 0
        self.fail_stream = False
        self.calls: list[dict] = []

    async def generate_structured(self, *, system, messages, schema, model, max_tokens):
        self.calls.append({"kind": "structured", "system": system,
                           "messages": messages, "model": model})
        if self.fail_structured > 0:
            self.fail_structured -= 1
            raise LLMError("injected failure")
        assert self.structured_queue, "FakeLLMProvider queue empty — test forgot to seed it"
        result = self.structured_queue.pop(0)
        assert isinstance(result, schema), f"queued {type(result)} != requested {schema}"
        return result, _FIXED_USAGE

    def stream_text(self, *, system, messages, model, max_tokens) -> TextStream:
        self.calls.append({"kind": "stream", "system": system,
                           "messages": messages, "model": model})
        stream_holder: list[TextStream] = []

        async def gen():
            if self.fail_stream:
                raise LLMError("injected stream failure")
            for chunk in self.stream_chunks:
                yield chunk
            stream_holder[0].usage = _FIXED_USAGE

        stream = TextStream(gen())
        stream_holder.append(stream)
        return stream
```

`anthropic.py`:

```python
from anthropic import AsyncAnthropic

from app.services.guru.llm.base import LLMError, LLMProvider, TextStream, Usage


class AnthropicProvider(LLMProvider):
    def __init__(self, api_key: str):
        self._client = AsyncAnthropic(api_key=api_key)

    async def generate_structured(self, *, system, messages, schema, model, max_tokens):
        try:
            resp = await self._client.messages.parse(
                model=model, max_tokens=max_tokens, system=system,
                messages=messages, output_format=schema,
            )
        except Exception as exc:  # SDK/network/validation errors → uniform LLMError
            raise LLMError(str(exc)) from exc
        if resp.parsed_output is None:
            raise LLMError("model returned no parseable output")
        usage = Usage(resp.usage.input_tokens, resp.usage.output_tokens)
        return resp.parsed_output, usage

    def stream_text(self, *, system, messages, model, max_tokens) -> TextStream:
        stream_holder: list[TextStream] = []

        async def gen():
            try:
                async with self._client.messages.stream(
                    model=model, max_tokens=max_tokens, system=system, messages=messages,
                ) as s:
                    async for text in s.text_stream:
                        yield text
                    final = await s.get_final_message()
                    stream_holder[0].usage = Usage(
                        final.usage.input_tokens, final.usage.output_tokens
                    )
            except LLMError:
                raise
            except Exception as exc:
                raise LLMError(str(exc)) from exc

        stream = TextStream(gen())
        stream_holder.append(stream)
        return stream
```

`usage.py`:

```python
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy.ext.asyncio import AsyncSession

from app.models import LlmUsage
from app.services.guru.llm.base import Usage

# (input, output) USD per million tokens, keyed by model-id prefix.
_PRICES_PER_MTOK: dict[str, tuple[Decimal, Decimal]] = {
    "claude-opus-4": (Decimal("5"), Decimal("25")),
    "claude-haiku-4-5": (Decimal("1"), Decimal("5")),
}
_MTOK = Decimal("1000000")


def estimate_cost(model: str, usage: Usage) -> Decimal | None:
    for prefix, (in_price, out_price) in _PRICES_PER_MTOK.items():
        if model.startswith(prefix):
            return (usage.input_tokens * in_price + usage.output_tokens * out_price) / _MTOK
    return None


async def record_usage(
    db: AsyncSession, *, user_id: int, mode: str, model: str, usage: Usage,
    report_id: int | None = None, thread_id: int | None = None,
) -> LlmUsage:
    row = LlmUsage(
        user_id=user_id, mode=mode, model=model,
        input_tokens=usage.input_tokens, output_tokens=usage.output_tokens,
        est_cost_usd=estimate_cost(model, usage),
        report_id=report_id, thread_id=thread_id,
        created_at=datetime.now(UTC).replace(tzinfo=None),
    )
    db.add(row)
    await db.flush()
    return row
```

`app/core/config.py` — add to `Settings`:

```python
    anthropic_api_key: str = ""
    guru_advice_model: str = "claude-opus-4-8"
    guru_scan_model: str = "claude-haiku-4-5"
    guru_digest_hour: int = 7
    guru_timezone: str = "Europe/London"
```

- [ ] **Step 5: Run** `pytest tests/test_guru_llm.py -q` — PASS; `ruff check .` clean.
- [ ] **Step 6: Commit** — `feat(guru): LLM provider layer (base/fake/anthropic) + usage logging + config`

---

### Task 4: Investor profile API

**Files:**
- Create: `backend/app/api/guru.py` (router shell + profile endpoints)
- Modify: `backend/app/main.py` (include router)
- Test: `backend/tests/test_guru_profile_api.py`

**Interfaces:**
- Consumes: `InvestorProfile` (Task 2), `CurrentUser`/`SessionDep` from `app.api.deps`.
- Produces: `router = APIRouter(prefix="/api/guru", tags=["guru"])` in `app/api/guru.py` — later tasks add endpoints to this same router. `GET /api/guru/profile` → `{risk_appetite, horizon, sector_interests, free_text}` (defaults when no row); `PUT /api/guru/profile` upserts and returns the same shape. Pydantic models `ProfileIn`/`ProfileOut` (same four fields; `risk_appetite: Literal["cautious","balanced","adventurous"]`, `horizon: Literal["short","medium","long"]`).

- [ ] **Step 1: Failing tests** — `backend/tests/test_guru_profile_api.py`:

```python
import pytest

pytestmark = pytest.mark.asyncio(loop_scope="session")


async def test_profile_requires_auth(client):
    assert (await client.get("/api/guru/profile")).status_code == 401


async def test_profile_defaults_when_unset(auth_client):
    resp = await auth_client.get("/api/guru/profile")
    assert resp.status_code == 200
    assert resp.json() == {"risk_appetite": "balanced", "horizon": "medium",
                           "sector_interests": [], "free_text": ""}


async def test_profile_put_upserts_and_persists(auth_client):
    body = {"risk_appetite": "adventurous", "horizon": "long",
            "sector_interests": ["tech", "energy"], "free_text": "prefer dividends"}
    resp = await auth_client.put("/api/guru/profile", json=body)
    assert resp.status_code == 200 and resp.json() == body
    assert (await auth_client.get("/api/guru/profile")).json() == body
    body["horizon"] = "short"
    assert (await auth_client.put("/api/guru/profile", json=body)).json()["horizon"] == "short"


async def test_profile_rejects_invalid_enum(auth_client):
    resp = await auth_client.put("/api/guru/profile", json={
        "risk_appetite": "yolo", "horizon": "long", "sector_interests": [], "free_text": ""})
    assert resp.status_code == 422
```

- [ ] **Step 2: Run** — FAIL (404s).
- [ ] **Step 3: Implement** `app/api/guru.py`:

```python
from typing import Literal

from fastapi import APIRouter
from pydantic import BaseModel
from sqlalchemy import select

from app.api.deps import CurrentUser, SessionDep
from app.models import InvestorProfile

router = APIRouter(prefix="/api/guru", tags=["guru"])


class ProfileOut(BaseModel):
    risk_appetite: str
    horizon: str
    sector_interests: list[str]
    free_text: str


class ProfileIn(BaseModel):
    risk_appetite: Literal["cautious", "balanced", "adventurous"]
    horizon: Literal["short", "medium", "long"]
    sector_interests: list[str]
    free_text: str


async def get_profile_row(db, user) -> InvestorProfile | None:
    return (await db.execute(
        select(InvestorProfile).where(InvestorProfile.user_id == user.id)
    )).scalar_one_or_none()


@router.get("/profile", response_model=ProfileOut)
async def read_profile(db: SessionDep, user: CurrentUser):
    row = await get_profile_row(db, user)
    if row is None:
        return ProfileOut(risk_appetite="balanced", horizon="medium",
                          sector_interests=[], free_text="")
    return ProfileOut(risk_appetite=row.risk_appetite, horizon=row.horizon,
                      sector_interests=row.sector_interests, free_text=row.free_text)


@router.put("/profile", response_model=ProfileOut)
async def write_profile(body: ProfileIn, db: SessionDep, user: CurrentUser):
    row = await get_profile_row(db, user)
    if row is None:
        row = InvestorProfile(user_id=user.id)
        db.add(row)
    row.risk_appetite = body.risk_appetite
    row.horizon = body.horizon
    row.sector_interests = body.sector_interests
    row.free_text = body.free_text
    await db.commit()
    return ProfileOut(**body.model_dump())
```

In `app/main.py`: `from app.api.guru import router as guru_router` + `app.include_router(guru_router)` (alphabetical with the others).

- [ ] **Step 4: Run** — PASS; ruff clean.
- [ ] **Step 5: Commit** — `feat(guru): investor profile API (get/put upsert)`

---

### Task 5: ContextBuilder + persona + schemas

**Files:**
- Create: `backend/app/services/guru/persona.py`
- Create: `backend/app/services/guru/schemas.py`
- Create: `backend/app/services/guru/context.py`
- Test: `backend/tests/test_guru_context.py`

**Interfaces:**
- Consumes: `value_portfolio(db, portfolio, quote_service, fx) -> PortfolioSummary` (existing), `Signal` model, `InvestorProfile`.
- Produces:
  - `persona.PERSONA_V1: str`, `persona.DISCLAIMER = "The Guru is not regulated financial advice."`
  - `schemas.PositionVerdict(symbol: str, action: Literal["hold","increase","reduce","exit"], conviction: Literal["low","med","high"], rationale: str)`
  - `schemas.ReviewPayload(positions: list[PositionVerdict], observations: list[str], watch_next: list[str], disclaimer: str)`
  - `schemas.DigestPayload(earnings_this_week: list[EarningsItem], movers: list[MoverItem], news_flags: list[NewsFlag], summary: str, disclaimer: str)` with `EarningsItem(symbol: str, date: str | None, note: str)`, `MoverItem(symbol: str, note: str)`, `NewsFlag(symbol: str | None, headline: str, comment: str)`
  - `schemas.TakePayload(commentary: str, risks: list[RiskItem], ideas: list[IdeaItem], disclaimer: str)` with `RiskItem(kind: str, note: str)`, `IdeaItem(symbol: str | None, action: Literal["hold","increase","reduce","exit"], conviction: Literal["low","med","high"], rationale: str)`
  - `context.MAX_CONTEXT_CHARS = 60_000`
  - `async context.build_context(db, user, *, quote_service, fx, portfolios: list[Portfolio], profile: InvestorProfile | None) -> dict` — keys: `profile` (four fields, defaults when None), `portfolios` (per portfolio: `name`, `base_currency`, `total_value`, `total_pnl`, `day_change`, `integrity` (`{costed_positions, priced_positions, unpriced_positions, day_change_partial}`), `positions` (each: `symbol, name, market, quantity, market_value, unrealized_pnl_pct, day_change, currency, currency_mismatch, watchlist_entry` — `watchlist_entry = quantity is None`; Decimals rendered as `str`)), `signals` (latest stored rows per portfolio: `{portfolio, symbol, kind, severity, title, detail}`), `as_of` (ISO now), `context_truncated: bool`

- [ ] **Step 1: Failing tests** — `backend/tests/test_guru_context.py`. Seed via existing fixtures (`db_session`, `make_instrument`; create `User`, `Portfolio`, `Position` rows the way `tests/test_valuation.py` does — copy its seeding helpers). Cases:

```python
async def test_context_includes_profile_defaults_when_none(...):
    ctx = await build_context(db_session, user, quote_service=qs, fx=fx,
                              portfolios=[pf], profile=None)
    assert ctx["profile"]["risk_appetite"] == "balanced"
    assert ctx["context_truncated"] is False

async def test_context_positions_and_signals(...):
    # portfolio with 2 positions (one quantity=None → watchlist_entry True),
    # one stored Signal row → appears under ctx["signals"] with symbol resolved
    # Decimal fields are strings (json.dumps(ctx) must not raise)

async def test_context_truncates_largest_value_first(...):
    # monkeypatch context.MAX_CONTEXT_CHARS to something tiny (e.g. 500),
    # portfolio with 3 positions of differing market values →
    # ctx["context_truncated"] is True and the kept positions are the largest by value
```

- [ ] **Step 2: Run** — FAIL.
- [ ] **Step 3: Implement.** `persona.py`:

```python
DISCLAIMER = "The Guru is not regulated financial advice."

PERSONA_V1 = """You are "the Guru", a world-class investment adviser with deep expertise in the
US, UK and Hong Kong markets. You are measured and evidence-first: every judgment cites the
specific facts (signals, valuations, profile) it rests on. You always state a conviction level
and explain *why*. You relate every recommendation to the investor's stated risk appetite,
horizon and interests, and you explicitly flag any idea that sits outside that profile.
You never invent prices, news or events — you reason only from the data provided in the
context document. You are advising a single experienced adult investor.
""" + DISCLAIMER
```

`schemas.py` exactly per the Interfaces block (plain `BaseModel`s, every payload model has `disclaimer: str`). `context.py`: build the dict via `value_portfolio` per portfolio + one `select(Signal)` per portfolio (latest run: all rows, ordered severity then computed_at desc — same ordering idiom as `app/api/signals.py`), resolve symbols with the `_symbol_map` pattern; render every `Decimal` with `str(...)`; truncation loop:

```python
import json

def _truncate(ctx: dict) -> dict:
    while len(json.dumps(ctx)) > MAX_CONTEXT_CHARS:
        all_pos = [(pf, p) for pf in ctx["portfolios"] for p in pf["positions"]]
        if not all_pos:
            break
        pf, smallest = min(
            all_pos, key=lambda t: Decimal(t[1]["market_value"] or "0")
        )
        pf["positions"].remove(smallest)
        ctx["context_truncated"] = True
    return ctx
```

- [ ] **Step 4: Run** — PASS; ruff clean.
- [ ] **Step 5: Commit** — `feat(guru): context builder, persona, output schemas`

---

### Task 6: Review mode — GuruService core + review API

**Files:**
- Create: `backend/app/services/guru/service.py`
- Modify: `backend/app/api/guru.py` (review endpoints + error mapping)
- Modify: `backend/tests/conftest.py` (guru fixtures)
- Test: `backend/tests/test_guru_review.py`

**Interfaces:**
- Consumes: Tasks 3 & 5 interfaces; `get_owned_portfolio(db, user, portfolio_id)` from `app.api.portfolios`.
- Produces:
  - `service.GenerationInProgress(Exception)`
  - `class GuruService:` `__init__(self, provider: LLMProvider | None, quotes: QuoteService, fx: FxService)`; `async generate_review(db, user, portfolio) -> GuruReport`; `async generate_digest(db, user) -> GuruReport` and `async generate_take(db, user) -> GuruReport` (Task 7 fills these; stub raises `NotImplementedError` here); per-kind `asyncio.Lock` map — entered non-blocking, `GenerationInProgress` when already locked.
  - `service.get_guru_service() -> GuruService` — module singleton; provider is `AnthropicProvider(settings.anthropic_api_key)` when the key is non-empty else `None`.
  - `app/api/guru.py::get_guru` dependency (returns `get_guru_service()`) — **tests override this**, mirroring `get_analyzer`.
  - Endpoints: `POST /api/guru/reviews` body `{portfolio_id: int}` → 201 + report json; `GET /api/guru/reviews?portfolio_id=&limit=20` → `{reviews: [...]}` newest first; `GET /api/guru/reviews/{id}` → report json. Report json shape (shared `_report_out`): `{id, kind, portfolio_id, payload, model, created_at}`.
  - Error mapping (module-level helper used by all guru endpoints): `LLMNotConfigured → 503 {"detail": "llm_unconfigured"}`, `GenerationInProgress → 409 {"detail": "generation_in_progress"}`, `LLMError → 502 {"detail": "llm_error"}`.
- conftest additions:

```python
@pytest_asyncio.fixture
def fake_llm():
    from app.services.guru.llm.fake import FakeLLMProvider
    return FakeLLMProvider()


@pytest_asyncio.fixture
async def guru_client(auth_client, fake_llm) -> httpx.AsyncClient:
    from app.api.guru import get_guru
    from app.services.guru.service import GuruService
    svc = GuruService(fake_llm, *(_test_services()))
    auth_client.app.dependency_overrides[get_guru] = lambda: svc
    auth_client.fake_llm = fake_llm
    return auth_client
```

- [ ] **Step 1: Failing tests** — `backend/tests/test_guru_review.py` (seed a portfolio with two positions AAPL/MSFT using the same helpers as `test_guru_context.py`):

```python
def _review(symbols, extra=()):
    from app.services.guru.persona import DISCLAIMER
    from app.services.guru.schemas import PositionVerdict, ReviewPayload
    return ReviewPayload(
        positions=[PositionVerdict(symbol=s, action="hold", conviction="med",
                                   rationale="steady") for s in [*symbols, *extra]],
        observations=["concentrated in tech"], watch_next=["AAPL earnings"],
        disclaimer=DISCLAIMER)


async def test_review_generates_and_persists(guru_client, ...):
    guru_client.fake_llm.structured_queue.append(_review(["AAPL", "MSFT"]))
    resp = await guru_client.post("/api/guru/reviews", json={"portfolio_id": pf_id})
    assert resp.status_code == 201
    body = resp.json()
    assert body["kind"] == "review" and body["portfolio_id"] == pf_id
    assert {p["symbol"] for p in body["payload"]["positions"]} == {"AAPL", "MSFT"}
    # persisted + listable
    listed = (await guru_client.get(f"/api/guru/reviews?portfolio_id={pf_id}")).json()
    assert listed["reviews"][0]["id"] == body["id"]
    # usage row written
    ...assert one LlmUsage row with mode="review", report_id == body["id"]


async def test_review_missing_position_retries_then_succeeds(guru_client, ...):
    guru_client.fake_llm.structured_queue += [_review(["AAPL"]), _review(["AAPL", "MSFT"])]
    resp = await guru_client.post("/api/guru/reviews", json={"portfolio_id": pf_id})
    assert resp.status_code == 201
    assert len(guru_client.fake_llm.calls) == 2  # corrective retry happened


async def test_review_missing_position_twice_is_502(guru_client, ...):
    guru_client.fake_llm.structured_queue += [_review(["AAPL"]), _review(["AAPL"])]
    resp = await guru_client.post("/api/guru/reviews", json={"portfolio_id": pf_id})
    assert resp.status_code == 502 and resp.json()["detail"] == "llm_error"
    # nothing persisted
    assert (await guru_client.get(f"/api/guru/reviews?portfolio_id={pf_id}")).json()["reviews"] == []


async def test_review_unconfigured_503(auth_client, ...):
    # override get_guru with GuruService(None, ...) → 503 llm_unconfigured


async def test_review_other_users_portfolio_404(guru_client, ...):
    # portfolio owned by a second user → 404 (get_owned_portfolio behavior)
```

- [ ] **Step 2: Run** — FAIL.
- [ ] **Step 3: Implement** `service.py`:

```python
import asyncio
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models import GuruReport, Portfolio, User
from app.services.guru import usage as usage_mod
from app.services.guru.context import build_context
from app.services.guru.llm.anthropic import AnthropicProvider
from app.services.guru.llm.base import LLMError, LLMNotConfigured, LLMProvider
from app.services.guru.persona import PERSONA_V1
from app.services.guru.schemas import ReviewPayload
from app.services.market_data.quotes import QuoteService
from app.services.valuation import FxService


class GenerationInProgress(Exception):
    pass


def _now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


class GuruService:
    def __init__(self, provider: LLMProvider | None, quotes: QuoteService, fx: FxService):
        self.provider = provider
        self.quotes = quotes
        self.fx = fx
        self._locks: dict[str, asyncio.Lock] = {}

    def _lock(self, kind: str) -> asyncio.Lock:
        return self._locks.setdefault(kind, asyncio.Lock())

    def _require_provider(self) -> LLMProvider:
        if self.provider is None:
            raise LLMNotConfigured("anthropic_api_key not set")
        return self.provider

    async def _profile(self, db: AsyncSession, user: User):
        from app.api.guru import get_profile_row
        return await get_profile_row(db, user)

    async def generate_review(self, db: AsyncSession, user: User,
                              portfolio: Portfolio) -> GuruReport:
        provider = self._require_provider()
        lock = self._lock("review")
        if lock.locked():
            raise GenerationInProgress("review")
        async with lock:
            profile = await self._profile(db, user)
            ctx = await build_context(db, user, quote_service=self.quotes, fx=self.fx,
                                      portfolios=[portfolio], profile=profile)
            import json
            expected = {p.instrument.symbol for p in portfolio.positions}
            messages = [{"role": "user", "content":
                         "Review this portfolio. Give a verdict for EVERY position.\n\n"
                         + json.dumps(ctx)}]
            payload, usage = await provider.generate_structured(
                system=PERSONA_V1, messages=messages, schema=ReviewPayload,
                model=settings.guru_advice_model, max_tokens=4096)
            missing = expected - {p.symbol for p in payload.positions}
            if missing:
                messages += [
                    {"role": "assistant", "content": payload.model_dump_json()},
                    {"role": "user", "content":
                     f"You omitted these positions: {sorted(missing)}. "
                     "Return the complete review covering every position."},
                ]
                payload, usage = await provider.generate_structured(
                    system=PERSONA_V1, messages=messages, schema=ReviewPayload,
                    model=settings.guru_advice_model, max_tokens=4096)
                missing = expected - {p.symbol for p in payload.positions}
                if missing:
                    raise LLMError(f"review still missing positions: {sorted(missing)}")
            report = GuruReport(user_id=user.id, kind="review", portfolio_id=portfolio.id,
                                payload=payload.model_dump(),
                                model=settings.guru_advice_model, created_at=_now())
            db.add(report)
            await db.flush()
            await usage_mod.record_usage(db, user_id=user.id, mode="review",
                                         model=settings.guru_advice_model,
                                         usage=usage, report_id=report.id)
            await db.commit()
            return report

    async def generate_digest(self, db: AsyncSession, user: User) -> GuruReport:
        raise NotImplementedError  # Task 7

    async def generate_take(self, db: AsyncSession, user: User) -> GuruReport:
        raise NotImplementedError  # Task 7


_service: GuruService | None = None


def get_guru_service() -> GuruService:
    global _service
    if _service is None:
        from app.services.market_data.yahoo import get_provider as get_yahoo
        provider = (AnthropicProvider(settings.anthropic_api_key)
                    if settings.anthropic_api_key else None)
        yahoo = get_yahoo()
        _service = GuruService(provider, QuoteService(yahoo), FxService(yahoo))
    return _service
```

(If `app/services/market_data/yahoo.py` exposes its provider differently, mirror however `app/services/signals/engine.py::get_engine` obtains it — keep the two consistent.)

`app/api/guru.py` additions:

```python
from contextlib import contextmanager

from fastapi import Depends, HTTPException
from app.api.portfolios import get_owned_portfolio
from app.models import GuruReport
from app.services.guru.llm.base import LLMError, LLMNotConfigured
from app.services.guru.service import GenerationInProgress, GuruService, get_guru_service


def get_guru() -> GuruService:
    return get_guru_service()


GuruDep = Annotated[GuruService, Depends(get_guru)]


@contextmanager
def map_guru_errors():
    try:
        yield
    except LLMNotConfigured:
        raise HTTPException(status_code=503, detail="llm_unconfigured")
    except GenerationInProgress:
        raise HTTPException(status_code=409, detail="generation_in_progress")
    except LLMError:
        raise HTTPException(status_code=502, detail="llm_error")


class ReportOut(BaseModel):
    id: int
    kind: str
    portfolio_id: int | None
    payload: dict
    model: str
    created_at: str


def _report_out(r: GuruReport) -> ReportOut:
    return ReportOut(id=r.id, kind=r.kind, portfolio_id=r.portfolio_id,
                     payload=r.payload, model=r.model,
                     created_at=r.created_at.isoformat())


class ReviewRequest(BaseModel):
    portfolio_id: int


@router.post("/reviews", response_model=ReportOut, status_code=201)
async def create_review(body: ReviewRequest, db: SessionDep, user: CurrentUser, guru: GuruDep):
    pf = await get_owned_portfolio(db, user, body.portfolio_id)
    with map_guru_errors():
        report = await guru.generate_review(db, user, pf)
    return _report_out(report)


class ReviewList(BaseModel):
    reviews: list[ReportOut]


@router.get("/reviews", response_model=ReviewList)
async def list_reviews(db: SessionDep, user: CurrentUser,
                       portfolio_id: int | None = None, limit: int = 20):
    q = (select(GuruReport)
         .where(GuruReport.user_id == user.id, GuruReport.kind == "review")
         .order_by(GuruReport.created_at.desc(), GuruReport.id.desc()).limit(limit))
    if portfolio_id is not None:
        q = q.where(GuruReport.portfolio_id == portfolio_id)
    rows = (await db.execute(q)).scalars().all()
    return ReviewList(reviews=[_report_out(r) for r in rows])


@router.get("/reviews/{report_id}", response_model=ReportOut)
async def read_review(report_id: int, db: SessionDep, user: CurrentUser):
    r = (await db.execute(select(GuruReport).where(
        GuruReport.id == report_id, GuruReport.user_id == user.id,
        GuruReport.kind == "review"))).scalar_one_or_none()
    if r is None:
        raise HTTPException(status_code=404, detail="Not found")
    return _report_out(r)
```

On error paths (`map_guru_errors` firing after partial work), FastAPI raises before commit — the service only commits on success, so nothing persists. Rollback happens via session context.

- [ ] **Step 4: Run** `pytest tests/test_guru_review.py -q` then the full suite — PASS; ruff clean.
- [ ] **Step 5: Commit** — `feat(guru): portfolio review mode (service + API, coverage-checked, usage-logged)`

---

### Task 7: Digest + Guru's take modes + API

**Files:**
- Modify: `backend/app/services/guru/service.py`
- Modify: `backend/app/api/guru.py`
- Test: `backend/tests/test_guru_digest_take.py`

**Interfaces:**
- Produces:
  - `GuruService.generate_digest(db, user) -> GuruReport` — scan model, all portfolios context, `DigestPayload`, kind="digest", lock "digest".
  - `GuruService.generate_take(db, user) -> GuruReport` — advice model, all portfolios context **plus latest digest payload** appended to the user message, `TakePayload`, kind="take", lock "take".
  - `GuruService._all_portfolios(db, user) -> list[Portfolio]` (select by `user_id`, positions eager-loaded the way the dashboard endpoint does).
  - Endpoints: `GET /api/guru/digest/latest` → report json or 404; `POST /api/guru/digest` → 201; `GET /api/guru/take/latest` → report json or 404; `POST /api/guru/take` → 201.

- [ ] **Step 1: Failing tests** — `backend/tests/test_guru_digest_take.py`:

```python
async def test_digest_generates_with_scan_model(guru_client, ...):
    guru_client.fake_llm.structured_queue.append(_digest())  # helper builds DigestPayload
    resp = await guru_client.post("/api/guru/digest")
    assert resp.status_code == 201
    assert resp.json()["model"] == "claude-haiku-4-5"
    assert guru_client.fake_llm.calls[0]["model"] == "claude-haiku-4-5"
    latest = await guru_client.get("/api/guru/digest/latest")
    assert latest.json()["id"] == resp.json()["id"]

async def test_take_uses_advice_model_and_sees_latest_digest(guru_client, ...):
    # generate a digest first; then queue a TakePayload and POST /api/guru/take;
    # assert model == "claude-opus-4-8" and the take call's user message content
    # contains the digest summary text (context handoff)

async def test_latest_404_when_none(guru_client):
    assert (await guru_client.get("/api/guru/take/latest")).status_code == 404

async def test_digest_provider_failure_502_nothing_persisted(guru_client):
    guru_client.fake_llm.fail_structured = 2  # initial + retry? digest has no retry → 1 is enough
    ...
```

(Digest/take have **no** coverage-retry — that's review-only. `fail_structured = 1` → single call → 502.)

- [ ] **Step 2: Run** — FAIL (`NotImplementedError`).
- [ ] **Step 3: Implement.** Shared private helper in `service.py`:

```python
    async def _generate_global(self, db, user, *, kind: str, schema, model: str,
                               instruction: str, extra_context: str = "") -> GuruReport:
        provider = self._require_provider()
        lock = self._lock(kind)
        if lock.locked():
            raise GenerationInProgress(kind)
        async with lock:
            profile = await self._profile(db, user)
            portfolios = await self._all_portfolios(db, user)
            ctx = await build_context(db, user, quote_service=self.quotes, fx=self.fx,
                                      portfolios=portfolios, profile=profile)
            content = instruction + "\n\n" + json.dumps(ctx) + extra_context
            payload, usage = await provider.generate_structured(
                system=PERSONA_V1, messages=[{"role": "user", "content": content}],
                schema=schema, model=model, max_tokens=2048)
            report = GuruReport(user_id=user.id, kind=kind, portfolio_id=None,
                                payload=payload.model_dump(), model=model, created_at=_now())
            db.add(report)
            await db.flush()
            await usage_mod.record_usage(db, user_id=user.id, mode=kind, model=model,
                                         usage=usage, report_id=report.id)
            await db.commit()
            return report
```

`generate_digest` calls it with `kind="digest"`, `schema=DigestPayload`, `model=settings.guru_scan_model`, instruction "Produce this morning's digest: earnings this week, notable movers, flagged news. One-line commentary per item." `generate_take` first loads the latest digest row (`select(GuruReport).where(kind=="digest", user_id==...).order_by(created_at.desc()).limit(1)`) and passes `extra_context = "\n\nLatest daily digest:\n" + json.dumps(digest.payload)` when present, with `kind="take"`, `schema=TakePayload`, `model=settings.guru_advice_model`, instruction "Give your portfolio-level take: what moved and why, key risks vs the investor's profile, and rebalance ideas with conviction."

API: `_latest(db, user, kind)` helper + the four endpoints (POST endpoints wrap in `map_guru_errors()`, return 201).

- [ ] **Step 4: Run** — PASS; full suite + ruff clean.
- [ ] **Step 5: Commit** — `feat(guru): daily digest + Guru's take modes and endpoints`

---

### Task 8: Scheduler + startup catch-up

**Files:**
- Create: `backend/app/services/guru/scheduler.py`
- Modify: `backend/app/main.py` (lifespan)
- Test: `backend/tests/test_guru_scheduler.py`

**Interfaces:**
- Produces:
  - `scheduler.digest_exists_today(db, user_id: int, *, now: datetime | None = None) -> bool` (async; "today" = `guru_timezone` calendar day converted to naive-UTC window)
  - `async scheduler.run_daily_job(session_factory=None) -> None` — opens its own session (default `async_sessionmaker` over the app engine from `app.core.db`), picks the first user, runs digest then take via `get_guru_service()`; every failure path logs and returns (never raises); no API key → log "guru scheduler: no api key, skipping" and return.
  - `scheduler.create_scheduler() -> AsyncIOScheduler` — cron trigger `hour=settings.guru_digest_hour`, `timezone=settings.guru_timezone`, job=`run_daily_job`.
  - `async scheduler.catch_up() -> None` — if first user exists and not `digest_exists_today`, `await run_daily_job()`.
  - `app/main.py` gains a `lifespan` async context manager: start scheduler + `asyncio.create_task(catch_up())` on startup, `scheduler.shutdown(wait=False)` on exit. (httpx `ASGITransport` doesn't run lifespan, so tests are unaffected.)

- [ ] **Step 1: Failing tests** — `backend/tests/test_guru_scheduler.py`:

```python
async def test_digest_exists_today_respects_timezone(db_session):
    # insert digest row with created_at = today 06:00 UTC; with guru_timezone
    # Europe/London (summer, UTC+1) that is today → True.
    # insert only a row from yesterday 22:00 UTC → False.
    # Pass `now` explicitly — no real clock.

async def test_run_daily_job_generates_digest_then_take(db_session, fake_llm, monkeypatch):
    # monkeypatch app.services.guru.service._service to GuruService(fake_llm, *_test_services());
    # seed a user; queue DigestPayload + TakePayload;
    # await run_daily_job(session_factory=TestSession);
    # assert one kind="digest" and one kind="take" row exist.

async def test_run_daily_job_skips_without_key(db_session, monkeypatch, caplog):
    # _service with provider=None → no rows created, no exception, log contains "skipping"

async def test_run_daily_job_swallows_llm_failure(db_session, fake_llm, monkeypatch, caplog):
    # fake_llm.fail_structured = 1 → no rows, no raise, exception logged

async def test_catch_up_runs_only_when_missing(db_session, fake_llm, monkeypatch):
    # with today's digest present, catch_up() makes zero provider calls
```

- [ ] **Step 2: Run** — FAIL.
- [ ] **Step 3: Implement** `scheduler.py`:

```python
import asyncio
import logging
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from sqlalchemy import select

from app.core.config import settings
from app.core.db import engine
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.models import GuruReport, User
from app.services.guru.llm.base import LLMNotConfigured
from app.services.guru.service import get_guru_service

logger = logging.getLogger(__name__)
_default_session_factory = async_sessionmaker(engine, expire_on_commit=False)


def _today_start_utc(now: datetime | None = None) -> datetime:
    tz = ZoneInfo(settings.guru_timezone)
    local_now = (now or datetime.now(UTC)).astimezone(tz)
    local_start = local_now.replace(hour=0, minute=0, second=0, microsecond=0)
    return local_start.astimezone(UTC).replace(tzinfo=None)


async def digest_exists_today(db, user_id: int, *, now: datetime | None = None) -> bool:
    row = (await db.execute(
        select(GuruReport.id).where(
            GuruReport.user_id == user_id, GuruReport.kind == "digest",
            GuruReport.created_at >= _today_start_utc(now),
        ).limit(1)
    )).scalar_one_or_none()
    return row is not None


async def run_daily_job(session_factory=None) -> None:
    factory = session_factory or _default_session_factory
    svc = get_guru_service()
    async with factory() as db:
        user = (await db.execute(select(User).order_by(User.id).limit(1))).scalar_one_or_none()
        if user is None:
            logger.info("guru scheduler: no user, skipping")
            return
        try:
            await svc.generate_digest(db, user)
            await svc.generate_take(db, user)
            logger.info("guru scheduler: digest + take generated")
        except LLMNotConfigured:
            logger.info("guru scheduler: no api key, skipping")
        except Exception:
            logger.exception("guru scheduler: daily job failed")


async def catch_up(session_factory=None) -> None:
    factory = session_factory or _default_session_factory
    async with factory() as db:
        user = (await db.execute(select(User).order_by(User.id).limit(1))).scalar_one_or_none()
        if user is None or await digest_exists_today(db, user.id):
            return
    await run_daily_job(session_factory)


def create_scheduler() -> AsyncIOScheduler:
    sched = AsyncIOScheduler(timezone=settings.guru_timezone)
    sched.add_job(run_daily_job, CronTrigger(
        hour=settings.guru_digest_hour, timezone=settings.guru_timezone))
    return sched
```

(Import the engine/sessionmaker however `app/core/db.py` actually exposes them — if it already has a session factory, reuse it instead of creating `_default_session_factory`.)

`app/main.py`:

```python
import asyncio
from contextlib import asynccontextmanager


@asynccontextmanager
async def lifespan(app):
    from app.services.guru.scheduler import catch_up, create_scheduler
    sched = create_scheduler()
    sched.start()
    task = asyncio.create_task(catch_up())
    yield
    task.cancel()
    sched.shutdown(wait=False)


def create_app() -> FastAPI:
    app = FastAPI(title="Investment Guru", lifespan=lifespan)
    ...
```

- [ ] **Step 4: Run** — PASS; ruff clean. Also boot the dev server once (`uvicorn app.main:app`) and confirm the startup log shows the catch-up skip line (no key) and no crash.
- [ ] **Step 5: Commit** — `feat(guru): APScheduler daily digest→take with startup catch-up`

---

### Task 9: Chat backend — threads + SSE streaming + usage summary

**Files:**
- Create: `backend/app/services/guru/chat.py`
- Modify: `backend/app/api/guru.py`
- Test: `backend/tests/test_guru_chat.py`

**Interfaces:**
- Produces:
  - `class ChatService:` `__init__(self, guru: GuruService)` (reuses provider/quotes/fx via `guru`); `async stream_turn(db, user, thread: ChatThread, content: str) -> AsyncIterator[dict]` — persists+commits the user `ChatMessage` first, builds context (persona system prompt = `PERSONA_V1` + seed_context note; user-turn context = profile + thread's portfolio snapshot (or all portfolios when `portfolio_id` None) + latest signals + last 20 thread messages), then yields `{"event": "delta", "data": {"text": chunk}}` per chunk and finally `{"event": "done", "data": {"message_id": int, "input_tokens": int, "output_tokens": int}}`; on `LLMError` mid-stream yields `{"event": "error", "data": {"detail": "llm_error"}}` and persists nothing for the assistant turn.
  - Endpoints:
    - `GET /api/guru/chat/threads` → `{threads: [{id, title, portfolio_id, created_at}]}` newest first
    - `POST /api/guru/chat/threads` body `{title: str, portfolio_id: int | None = None, seed_context: dict | None = None}` → 201 thread json
    - `GET /api/guru/chat/threads/{id}` → thread json + `messages: [{id, role, content, created_at}]`
    - `POST /api/guru/chat/threads/{id}/messages` body `{content: str}` → `StreamingResponse` `text/event-stream`, frames `event: <name>\ndata: <json>\n\n`; pre-stream failures map via `map_guru_errors` (503 when unconfigured)
    - `GET /api/guru/usage/summary` → `{by_mode: [{mode, calls, input_tokens, output_tokens, est_cost_usd}], total_cost_30d: str | null}`

- [ ] **Step 1: Failing tests** — `backend/tests/test_guru_chat.py`:

```python
async def test_thread_crud_and_ownership(guru_client, auth_client_2?):
    # create → list → detail; other user's thread id → 404
    # (create a second user inline like test_guru_review does)

async def test_chat_turn_streams_and_persists(guru_client):
    t = (await guru_client.post("/api/guru/chat/threads", json={"title": "T"})).json()
    guru_client.fake_llm.stream_chunks = ["Buy ", "low."]
    async with guru_client.stream(
        "POST", f"/api/guru/chat/threads/{t['id']}/messages",
        json={"content": "thoughts?"},
    ) as resp:
        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("text/event-stream")
        body = "".join([chunk async for chunk in resp.aiter_text()])
    assert "Buy " in body and "event: done" in body
    detail = (await guru_client.get(f"/api/guru/chat/threads/{t['id']}")).json()
    roles = [m["role"] for m in detail["messages"]]
    assert roles == ["user", "assistant"]
    assert detail["messages"][1]["content"] == "Buy low."
    # + one LlmUsage row mode="chat" with thread_id

async def test_chat_stream_failure_keeps_user_message_only(guru_client):
    # fake_llm.fail_stream = True → body contains "event: error";
    # thread detail shows only the user message

async def test_chat_unconfigured_503(auth_client):
    # GuruService(None, ...) override → POST message returns 503 before streaming

async def test_usage_summary_aggregates(guru_client):
    # after one digest generation, /api/guru/usage/summary shows mode "digest",
    # calls == 1, tokens 100/50
```

- [ ] **Step 2: Run** — FAIL.
- [ ] **Step 3: Implement** `chat.py` (context assembly mirrors `service.py`; last-20 messages via `select(ChatMessage).where(thread_id==...).order_by(created_at.desc(), id.desc()).limit(20)` then reversed; anthropic `messages` list = prior turns + new user turn, with the context JSON prepended to the first user turn's content). SSE endpoint:

```python
import json as _json
from fastapi.responses import StreamingResponse


def _sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {_json.dumps(data)}\n\n"


@router.post("/chat/threads/{thread_id}/messages")
async def post_chat_message(thread_id: int, body: ChatMessageIn, db: SessionDep,
                            user: CurrentUser, guru: GuruDep):
    thread = await _get_owned_thread(db, user, thread_id)
    with map_guru_errors():
        guru_chat = ChatService(guru)
        # raises LLMNotConfigured here, before streaming starts
        gen = guru_chat.stream_turn(db, user, thread, body.content)

    async def event_source():
        async for frame in gen:
            yield _sse(frame["event"], frame["data"])

    return StreamingResponse(event_source(), media_type="text/event-stream")
```

`stream_turn` shape:

```python
    async def stream_turn(self, db, user, thread, content):
        provider = self.guru._require_provider()
        user_msg = ChatMessage(thread_id=thread.id, role="user", content=content,
                               created_at=_now())
        db.add(user_msg)
        await db.commit()
        system, messages = await self._build_messages(db, user, thread)
        stream = provider.stream_text(system=system, messages=messages,
                                      model=settings.guru_advice_model, max_tokens=2048)
        parts: list[str] = []
        try:
            async for chunk in stream:
                parts.append(chunk)
                yield {"event": "delta", "data": {"text": chunk}}
        except LLMError:
            yield {"event": "error", "data": {"detail": "llm_error"}}
            return
        assistant = ChatMessage(thread_id=thread.id, role="assistant",
                                content="".join(parts), created_at=_now())
        db.add(assistant)
        await db.flush()
        usage = stream.usage or Usage(0, 0)
        await usage_mod.record_usage(db, user_id=user.id, mode="chat",
                                     model=settings.guru_advice_model, usage=usage,
                                     thread_id=thread.id)
        await db.commit()
        yield {"event": "done", "data": {"message_id": assistant.id,
                                         "input_tokens": usage.input_tokens,
                                         "output_tokens": usage.output_tokens}}
```

Usage summary endpoint: `select(LlmUsage.mode, func.count(), func.sum(...))` grouped by mode + a 30-day `func.sum(est_cost_usd)`; Decimals serialized as strings.

- [ ] **Step 4: Run** full backend suite + ruff — green.
- [ ] **Step 5: Commit** — `feat(guru): chat threads + SSE streaming turns + usage summary` — then **push and confirm CI green** (backend seam; use `gh run view --json conclusion,jobs` per convention).

---

### Task 10: Figma pass (USER GATE)

**Files:** none (Figma only).

- [ ] **Step 1:** Load the `figma:figma-generate-design` + `figma:figma-use` skills. Target file key `0gU58wfjttdZS0NXQeEtuD`; use the existing tokens/styles (accent `4F46E5`, gain `059669`, loss `DC2626`, Inter + tabular-nums).
- [ ] **Step 2:** Mock three screens on a new "Phase 2b — Guru" page in that file: (a) **Guru page** — Guru's-take card at top, digest card, review history list + one expanded review with verdict chips (`hold/increase/reduce/exit` + conviction), chat panel on the right with a streaming message; (b) **Settings** — investor profile form (risk appetite segmented control, horizon select, sector-interest chips, free-text area) + usage summary card; (c) **Dashboard Guru's-take panel** state in situ (filling the reserved slot), including the "Guru not configured" banner variant.
- [ ] **Step 3:** Post the Figma link and **STOP — wait for the user's approval** before any frontend task. Iterate on feedback in Figma, not in code.

---

### Task 11: Frontend — routes, types, Settings (profile + usage)

**Files:**
- Modify: `frontend/src/App.tsx` (enable Guru + Settings nav/routes)
- Modify: `frontend/src/lib/types.ts`
- Create: `frontend/src/pages/SettingsPage.tsx`
- Create: `frontend/src/pages/GuruPage.tsx` (shell only — filled in Task 12)
- Test: `frontend/src/pages/SettingsPage.test.tsx`

**Interfaces:**
- Consumes: Task 4 + Task 9 endpoints.
- Produces: `types.ts` additions used by Tasks 12–13:

```ts
export type GuruAction = "hold" | "increase" | "reduce" | "exit";
export type Conviction = "low" | "med" | "high";
export interface PositionVerdict { symbol: string; action: GuruAction; conviction: Conviction; rationale: string; }
export interface ReviewPayload { positions: PositionVerdict[]; observations: string[]; watch_next: string[]; disclaimer: string; }
export interface DigestPayload { earnings_this_week: { symbol: string; date: string | null; note: string }[]; movers: { symbol: string; note: string }[]; news_flags: { symbol: string | null; headline: string; comment: string }[]; summary: string; disclaimer: string; }
export interface TakePayload { commentary: string; risks: { kind: string; note: string }[]; ideas: { symbol: string | null; action: GuruAction; conviction: Conviction; rationale: string }[]; disclaimer: string; }
export interface GuruReport<P = unknown> { id: number; kind: "review" | "digest" | "take"; portfolio_id: number | null; payload: P; model: string; created_at: string; }
export interface InvestorProfile { risk_appetite: "cautious" | "balanced" | "adventurous"; horizon: "short" | "medium" | "long"; sector_interests: string[]; free_text: string; }
export interface UsageSummary { by_mode: { mode: string; calls: number; input_tokens: number; output_tokens: number; est_cost_usd: string | null }[]; total_cost_30d: string | null; }
export interface ChatThread { id: number; title: string; portfolio_id: number | null; created_at: string; }
export interface ChatMessage { id: number; role: "user" | "assistant"; content: string; created_at: string; }
```

- [ ] **Step 1: Failing tests** — `SettingsPage.test.tsx` (mock `apiFetch` per the repo's existing pattern): renders fetched profile; submitting the form PUTs the edited body and shows a saved confirmation; usage table renders `by_mode` rows and 30-day total.
- [ ] **Step 2: Run** — FAIL.
- [ ] **Step 3: Implement.** `App.tsx`: replace `DisabledNavItem "Guru"`/`"Settings"` with `NavItem to="/guru"` / `to="/settings"`; add `<Route path="/guru" element={<GuruPage />} />` and `<Route path="/settings" element={<SettingsPage />} />`. `GuruPage.tsx` shell: `<h1>Guru</h1>` placeholder (Task 12 fills). `SettingsPage.tsx`: React Query `["guru","profile"]` + `["guru","usage"]`; controlled form matching the approved Figma (segmented control = radio group, chips = toggleable buttons with an add-input, textarea); `useMutation` PUT → invalidate `["guru","profile"]`; usage card. Include `vitest-axe` check in the test (`expect(await axe(container)).toHaveNoViolations()`), matching the repo's existing a11y test usage.
- [ ] **Step 4: Run** `npm run check` — green.
- [ ] **Step 5: Commit** — `feat(web): Guru/Settings routes + investor profile & usage settings page`

---

### Task 12: Frontend — Guru page (reports), dashboard take panel, drawer take

**Files:**
- Create: `frontend/src/components/GuruTakePanel.tsx`
- Create: `frontend/src/components/VerdictChip.tsx`
- Modify: `frontend/src/pages/GuruPage.tsx`
- Modify: `frontend/src/pages/DashboardPage.tsx` (fill the reserved Guru's-take slot — search for the existing placeholder section labelled "Guru" and replace it with `<GuruTakePanel />`)
- Modify: `frontend/src/pages/PortfolioDetailPage.tsx` (position drawer take section)
- Test: `frontend/src/components/GuruTakePanel.test.tsx`, `frontend/src/pages/GuruPage.test.tsx`

**Interfaces:**
- Consumes: Task 6/7 endpoints + Task 11 types.
- Produces: `<GuruTakePanel />` (self-fetching, like `AttentionPanel`); `<VerdictChip action conviction />`; GuruPage sections consumed by Task 13 (chat panel slot: GuruPage renders a two-column layout, right column is `<ChatPanel />` placeholder div until Task 13).

- [ ] **Step 1: Failing tests.** `GuruTakePanel.test.tsx`: renders commentary/risks/ideas from a mocked `GET /api/guru/take/latest`; 404 → "No take yet — refresh to generate"; 503-shape `ApiError` from refresh POST → "Guru isn't configured yet" banner; refresh button fires `POST /api/guru/take` then refetches; each idea renders a "Discuss" link. `GuruPage.test.tsx`: digest card renders sections from `GET /api/guru/digest/latest`; reviews list from `GET /api/guru/reviews`; clicking a review shows verdict chips with action + conviction **as text**; "Run review" button per portfolio POSTs `/api/guru/reviews`; disclaimer text visible on every rendered report; axe clean.
- [ ] **Step 2: Run** — FAIL.
- [ ] **Step 3: Implement.**
  - `VerdictChip.tsx`: colored chip (`increase` gain, `reduce`/`exit` loss, `hold` muted) with text label `{action} · {conviction}` (never color-only).
  - `GuruTakePanel.tsx`: queries `["guru","take"]` (`apiFetch<GuruReport<TakePayload>>("/api/guru/take/latest")`, `retry: false`, treat `ApiError.status === 404` as empty state); staleness line `Generated {toLocaleString(created_at)}`; refresh `useMutation` with pending/error states (409 → "already generating"; 503 → unconfigured banner); ideas list with `VerdictChip` + `Link to={"/guru?discuss=" + encodeURIComponent(JSON.stringify(idea))}`; disclaimer footer.
  - `GuruPage.tsx`: left column — take card (reuse `GuruTakePanel`), digest card (`["guru","digest"]`, manual "Generate digest" button), review section (portfolio picker from `["dashboard"]` data, "Run review" mutation, history list `["guru","reviews", portfolioId]`, expandable detail with chips/observations/watch-next). Right column — `<div data-testid="chat-slot" />` placeholder.
  - `PortfolioDetailPage.tsx`: in the position drawer add a "Guru's take" block — query `["guru","reviews", id]` (limit 1), slice `payload.positions` by the drawer's symbol; render chip + rationale + `Generated …` staleness label + "Ask in chat" link (`/guru?discuss=…`); if no review or symbol missing, "No take yet — run a review."
- [ ] **Step 4: Run** `npm run check` — green.
- [ ] **Step 5: Commit** — `feat(web): Guru report reader, dashboard Guru's-take panel, per-position take`

---

### Task 13: Frontend — chat panel (SSE)

**Files:**
- Create: `frontend/src/lib/sse.ts`
- Create: `frontend/src/components/ChatPanel.tsx`
- Modify: `frontend/src/pages/GuruPage.tsx` (mount ChatPanel in the chat slot; consume `?discuss=` param)
- Test: `frontend/src/lib/sse.test.ts`, `frontend/src/components/ChatPanel.test.tsx`

**Interfaces:**
- Consumes: Task 9 endpoints.
- Produces: `streamSSE(path: string, body: unknown, handlers: { onDelta(text: string): void; onDone(d: { message_id: number }): void; onError(detail: string): void }): Promise<void>` — POST via `fetch` (credentials include), reads `response.body` with `TextDecoder`, parses `event:`/`data:` frames split on `\n\n`; non-2xx → throw `ApiError` before streaming.

- [ ] **Step 1: Failing tests.** `sse.test.ts`: feed a mocked `fetch` returning a `ReadableStream` that enqueues `event: delta\ndata: {"text":"Hi"}\n\n` then a `done` frame — handlers called in order; a frame split across two chunks still parses (buffer test); 503 response throws `ApiError(503)`. `ChatPanel.test.tsx`: thread list renders; sending a message appends the user bubble immediately, then assistant text grows as mocked `streamSSE` emits deltas; `error` event shows a retry affordance keeping the user message; new-thread button POSTs `/api/guru/chat/threads`; axe clean.
- [ ] **Step 2: Run** — FAIL.
- [ ] **Step 3: Implement.** `sse.ts`:

```ts
export async function streamSSE(path: string, body: unknown, handlers: {
  onDelta: (text: string) => void;
  onDone: (d: { message_id: number }) => void;
  onError: (detail: string) => void;
}): Promise<void> {
  const resp = await fetch(path, {
    method: "POST", credentials: "include",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!resp.ok || !resp.body) {
    throw new ApiError(resp.status, await resp.text().catch(() => resp.statusText));
  }
  const reader = resp.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    let idx;
    while ((idx = buffer.indexOf("\n\n")) >= 0) {
      const frame = buffer.slice(0, idx);
      buffer = buffer.slice(idx + 2);
      const event = /^event: (.*)$/m.exec(frame)?.[1];
      const data = /^data: (.*)$/m.exec(frame)?.[1];
      if (!event || data === undefined) continue;
      const parsed = JSON.parse(data);
      if (event === "delta") handlers.onDelta(parsed.text);
      else if (event === "done") handlers.onDone(parsed);
      else if (event === "error") handlers.onError(parsed.detail);
    }
  }
}
```

`ChatPanel.tsx`: thread list (`["guru","chat","threads"]`) + active thread messages (`["guru","chat","thread", id]`); local `streamingText` state appended via `onDelta`; on `onDone` invalidate the thread query and clear `streamingText`; send disabled while streaming; "New thread" (optionally seeded from the `?discuss=` param passed down by GuruPage — creates the thread with `seed_context: JSON.parse(param)` and a title from the idea's symbol/action). GuruPage mounts `<ChatPanel discuss={searchParams.get("discuss")} />` in the chat slot.

- [ ] **Step 4: Run** `npm run check` — green.
- [ ] **Step 5: Commit** — `feat(web): Guru chat panel with SSE streaming` — then **push, confirm CI green**.

---

### Task 14: Docs + live smoke (needs user's API key)

**Files:**
- Modify: `README.md` (Status), `docs/PROGRESS.md` (Phase 2b section)
- No code changes expected; fix-forward anything the smoke finds (each fix = its own TDD micro-cycle + commit).

- [ ] **Step 1:** Ask the user to put `ANTHROPIC_API_KEY=...` in `backend/.env` (never read the file; just confirm they've done it).
- [ ] **Step 2: Live smoke** against dev stack (`docker compose up -d db`, backend, frontend): (a) Settings → save a real profile; (b) portfolio review on the demo portfolio — verdicts cover every position, disclaimer present; (c) manual digest + take — dashboard panel fills, ideas link to chat; (d) chat turn streams token-by-token; (e) restart backend → startup catch-up does **not** regenerate (digest exists today); (f) `/api/guru/usage/summary` shows rows and plausible cost; cross-check spend in the Anthropic console.
- [ ] **Step 3:** Update `README.md` Status + `docs/PROGRESS.md` (endpoints, modes, scheduler behavior, how-to-run incl. `ANTHROPIC_API_KEY`, verified-e2e note). Update `.superpowers/sdd/progress.md` ledger.
- [ ] **Step 4:** Commit `feat: Phase 2b smoke verified + docs; the Guru is live` — push, confirm CI green (`gh run view --json conclusion,jobs`).
- [ ] **Step 5:** **Final whole-branch review on Opus** (per model-mix rule): full diff from the pre-2b base; triage findings; fix + re-review until clean.

---

## Self-review notes (completed)

- **Spec coverage:** §2→T2, §3→T3, §4/§5(persona+schemas)→T5, §5(review/take-derivation)→T6+T12, §5(digest/take)→T7, §6→T8, §7→T9+T13, §8→T4/T6/T7/T9, §9→T10–T13, §10 error table→T3/T6/T9 tests, §11→per-task tests + T14 smoke, §12→T1, §14 order preserved. Per-position take (spec: derived, no LLM call) is frontend-only slicing — T12, no backend endpoint, matching spec.
- **Type consistency:** `Usage`, `TextStream`, `LLMProvider`, `FakeLLMProvider` fields, `GuruService` method names, `map_guru_errors`, payload schema field names, and TS types cross-checked across tasks.
- **Known judgment calls for implementers:** exact provider-singleton wiring in `get_guru_service` mirrors `signals.engine.get_engine` (verify at T6); `app/core/db.py` session-factory reuse at T8; if `anthropic>=0.80` floor is wrong for `messages.parse`, raise to the installed version at T3 Step 1.
