# Dashboard / Stock-News UX Restructure Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give each holding a readable news surface — de-duplicated headlines grouped by stock on a dashboard News panel + per-position list, plus an on-demand, saved, budget-gated Guru summary with a sentiment tag.

**Architecture:** Expose the already-collected `NewsItem` data through a new `/api/news` router (TTL-gated refresh reusing `NewsService`, dedupe + rank helpers). Per-stock summaries are `GuruReport`s (`kind="news"`, new nullable `instrument_id`) generated on the cheap scan model, budget-gated. Headlines always render even when summaries fail.

**Tech Stack:** FastAPI + SQLAlchemy 2 async + Alembic (head **0010** → new **0011**) + Postgres; the Guru LLM layer (admin-selected provider from Project 2); React 18 + Vite + Tailwind + TanStack Query.

## Global Constraints

- Money = `Decimal`. Every user-data table has `user_id`; every route 404s on another user's data. News/summary are scoped to instruments the user actually holds (referenced by the user's positions).
- DB change = ONE hand-written chained Alembic migration; `alembic heads` must be a single head. New head `0011` on down_revision `0010`.
- Endpoints **degrade, never 500**: RSS/feed failure → serve cache + per-instrument `unavailable`; LLM failures → `LLMNotConfigured`→503, `GenerationInProgress`→409, `LLMError`→502, `BudgetExhausted`→429 (via `map_guru_errors`). Headlines always render even when summaries fail.
- LLM summary output is schema-validated (`NewsSummaryPayload`) and stored **encrypted** (`GuruReport.payload` is `EncryptedJSON`); summaries are **budget-gated** (`check_budget`) and run on the **scan model** (`self.scan_model` + `self.scan_price`).
- Providers are fixture/mock-backed in tests (`FakeLLMProvider`; a fake news provider). **Run pytest in the FOREGROUND only** (backgrounding poisons the local DB); if a run shows mass IntegrityErrors/hangs, `docker compose down db && docker compose up -d db` and re-run.
- Async tests: `pytestmark = pytest.mark.asyncio(loop_scope="session")` + conftest fixtures (`client`, `auth_client`, `guru_client`, `db_session`, `fake_llm`, `make_instrument`). Postgres :5433 via `docker compose up -d db`.
- Backend verify: `cd backend && .venv/bin/ruff check . && .venv/bin/pytest -q`. Frontend: `cd frontend && npm run check`.
- Commit to `main`; co-author trailer `Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>`.

---

## File Structure

**Backend**
- `backend/alembic/versions/0011_guru_report_instrument.py` — CREATE: add `guru_reports.instrument_id` nullable FK + index.
- `backend/app/models/guru.py` — MODIFY: `GuruReport.instrument_id`.
- `backend/app/services/guru/schemas.py` — MODIFY: add `NewsSummaryPayload`.
- `backend/app/services/market_data/news_read.py` — CREATE: `norm_title`, `dedupe`, `rank_groups` pure helpers.
- `backend/app/api/news.py` — CREATE: `get_news_service` dep + `user_instruments` helper + read endpoints (`GET /api/news`, `GET /api/news/{symbol}`, `POST /api/news/refresh`) + summary endpoints (`POST`/`GET /api/news/{symbol}/summary`).
- `backend/app/main.py` — MODIFY: register `news_router`.
- `backend/app/services/guru/service.py` — MODIFY: add `generate_news_summary`.
- Tests: `backend/tests/test_news_read.py`, `test_news_api.py`, `test_news_summary.py`.

**Frontend**
- `frontend/src/lib/api.ts`, `frontend/src/lib/types.ts` — MODIFY: news + summary clients/types.
- `frontend/src/components/NewsPanel.tsx` — CREATE: dashboard news panel.
- `frontend/src/pages/DashboardPage.tsx` — MODIFY: mount `NewsPanel`.
- `frontend/src/pages/PortfolioDetailPage.tsx` — MODIFY: per-stock news list + summarize.
- Tests: `NewsPanel.test.tsx` (+ detail additions), vitest-axe.

---

## Task 1: Migration 0011 + GuruReport.instrument_id + NewsSummaryPayload

**Files:**
- Create: `backend/alembic/versions/0011_guru_report_instrument.py`
- Modify: `backend/app/models/guru.py`, `backend/app/services/guru/schemas.py`
- Test: `backend/tests/test_news_summary.py` (Step-1 subset — schema + model)

**Interfaces:**
- Produces: `GuruReport.instrument_id: int | None` (FK `instruments.id`, indexed); `NewsSummaryPayload` with `summary: str`, `sentiment: Literal["positive","negative","neutral","watch"]`, `key_points: list[str]`, `disclaimer: str`.

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_news_summary.py
from datetime import UTC, datetime

import pytest

from app.models import GuruReport
from app.services.guru.schemas import NewsSummaryPayload

pytestmark = pytest.mark.asyncio(loop_scope="session")


def test_news_summary_payload_schema():
    p = NewsSummaryPayload(summary="Up on earnings.", sentiment="positive",
                           key_points=["Beat estimates"], disclaimer="Not advice.")
    assert p.sentiment == "positive"
    with pytest.raises(Exception):
        NewsSummaryPayload(summary="x", sentiment="bullish", key_points=[], disclaimer="d")


async def test_guru_report_accepts_instrument_id_and_news_kind(db_session, make_instrument):
    inst = await make_instrument("AAPL")
    r = GuruReport(user_id=1, kind="news", instrument_id=inst.id,
                   payload={"summary": "s", "sentiment": "neutral", "key_points": [],
                            "disclaimer": "d"},
                   model="m", created_at=datetime.now(UTC).replace(tzinfo=None))
    db_session.add(r)
    await db_session.commit()
    await db_session.refresh(r)
    assert r.instrument_id == inst.id and r.kind == "news"
```

(If `make_instrument` requires a user for FK, this test only needs the instrument; `user_id=1` need not exist for the insert since there's no enforced FK check failure in the test DB — if it does fail, create a user first via the `db_session` + `User` model.)

- [ ] **Step 2: Run to verify failure**

Run: `cd backend && .venv/bin/pytest tests/test_news_summary.py -q`
Expected: FAIL (`NewsSummaryPayload` missing; `instrument_id` unknown column).

- [ ] **Step 3: Add the model column**

In `backend/app/models/guru.py`, add to `GuruReport` (after `portfolio_id`):

```python
    instrument_id: Mapped[int | None] = mapped_column(ForeignKey("instruments.id"), index=True)
```

Update the `kind` comment to `# review | digest | take | orso | news`.

- [ ] **Step 4: Add the schema**

In `backend/app/services/guru/schemas.py` (which already imports `Literal`, `BaseModel`):

```python
class NewsSummaryPayload(BaseModel):
    summary: str
    sentiment: Literal["positive", "negative", "neutral", "watch"]
    key_points: list[str]
    disclaimer: str
```

- [ ] **Step 5: Write the migration**

```python
# backend/alembic/versions/0011_guru_report_instrument.py
"""guru_reports.instrument_id (for kind=news per-stock summaries)

Additive, forward-only. Nullable FK so existing review/digest/take/orso rows are
unaffected.

Revision ID: 0011
Revises: 0010
"""
import sqlalchemy as sa

from alembic import op

revision = "0011"
down_revision = "0010"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("guru_reports", sa.Column("instrument_id", sa.Integer(), nullable=True))
    op.create_foreign_key(
        "fk_guru_reports_instrument_id", "guru_reports", "instruments",
        ["instrument_id"], ["id"])
    op.create_index("ix_guru_reports_instrument_id", "guru_reports", ["instrument_id"])


def downgrade() -> None:
    op.drop_index("ix_guru_reports_instrument_id", table_name="guru_reports")
    op.drop_constraint("fk_guru_reports_instrument_id", "guru_reports", type_="foreignkey")
    op.drop_column("guru_reports", "instrument_id")
```

- [ ] **Step 6: Run tests + migration check**

Run: `cd backend && .venv/bin/pytest tests/test_news_summary.py -q` → PASS (2).
Run: `.venv/bin/alembic heads` → single head `0011`.
Run: `.venv/bin/ruff check . && .venv/bin/pytest -q` → green.

- [ ] **Step 7: Commit**

```bash
git add backend/alembic/versions/0011_guru_report_instrument.py backend/app/models/guru.py backend/app/services/guru/schemas.py backend/tests/test_news_summary.py
git commit -m "feat(news): GuruReport.instrument_id + kind=news + NewsSummaryPayload (0011)"
```

---

## Task 2: News read helpers + read endpoints

**Files:**
- Create: `backend/app/services/market_data/news_read.py`, `backend/app/api/news.py`
- Modify: `backend/app/main.py` (register router)
- Test: `backend/tests/test_news_read.py`, `backend/tests/test_news_api.py`

**Interfaces:**
- Consumes: `NewsService.refresh(db, instruments) -> set[int]`, `NewsItem`, `Instrument`, `Position`, `Portfolio`.
- Produces: `norm_title(str) -> str`; `dedupe(list[NewsItem]) -> list[NewsItem]`; `rank_groups(list[dict]) -> list[dict]`; `get_news_service()` dep; `user_instruments(db, user_id) -> list[Instrument]`; routes `GET /api/news`, `GET /api/news/{symbol}`, `POST /api/news/refresh`.

- [ ] **Step 1: Write the failing helper tests**

```python
# backend/tests/test_news_read.py
from datetime import datetime

from app.models import NewsItem
from app.services.market_data.news_read import dedupe, norm_title, rank_groups


def _n(title, when):
    return NewsItem(instrument_id=1, title=title, source="Yahoo", url=title,
                    published_at=datetime(2026, 1, when), fetched_at=datetime(2026, 1, when))


def test_norm_title_folds_case_ws_punct():
    assert norm_title("Apple  Beats!  Estimates.") == norm_title("apple beats estimates")


def test_dedupe_keeps_earliest_returns_newest_first():
    items = [_n("Apple beats estimates", 3), _n("APPLE  beats estimates!", 1),
             _n("Rival launches phone", 2)]
    out = dedupe(items)
    # duplicate collapsed to one (earliest published kept), newest-first order
    assert len(out) == 2
    assert out[0].title == "Rival launches phone"      # day 2 newest of the survivors
    assert out[1].published_at.day == 1                # the kept dup is the earliest (day 1)


def test_rank_groups_by_count_then_recency():
    groups = [
        {"symbol": "A", "latest_published_at": "2026-01-05", "items": [1, 2]},
        {"symbol": "B", "latest_published_at": "2026-01-09", "items": [1, 2]},
        {"symbol": "C", "latest_published_at": "2026-01-01", "items": [1]},
    ]
    ranked = [g["symbol"] for g in rank_groups(groups)]
    assert ranked == ["B", "A", "C"]   # A,B tie on count(2) -> B newer first; C fewer
```

- [ ] **Step 2: Run to verify failure**

Run: `cd backend && .venv/bin/pytest tests/test_news_read.py -q`
Expected: FAIL (module missing).

- [ ] **Step 3: Implement the helpers**

```python
# backend/app/services/market_data/news_read.py
"""Read-side news helpers: normalize/dedupe headlines and rank holdings. Pure
functions (no DB/IO) so they're trivially unit-tested."""
import re

from app.models import NewsItem

_PUNCT = re.compile(r"[^0-9a-z ]+")
_WS = re.compile(r"\s+")


def norm_title(title: str) -> str:
    t = _PUNCT.sub(" ", title.lower())
    return _WS.sub(" ", t).strip()


def dedupe(items: list[NewsItem]) -> list[NewsItem]:
    """Collapse near-duplicate headlines by normalized title (keep the
    earliest-published of a duplicate set), return newest-first."""
    def sort_key(n: NewsItem):
        return (n.published_at or n.fetched_at)

    best: dict[str, NewsItem] = {}
    for n in items:
        k = norm_title(n.title)
        cur = best.get(k)
        if cur is None or sort_key(n) < sort_key(cur):
            best[k] = n
    return sorted(best.values(), key=sort_key, reverse=True)


def rank_groups(groups: list[dict]) -> list[dict]:
    """Order holdings by recent-headline count desc, then latest_published_at desc."""
    return sorted(
        groups,
        key=lambda g: (len(g["items"]), g.get("latest_published_at") or ""),
        reverse=True,
    )
```

- [ ] **Step 4: Write the failing API tests**

```python
# backend/tests/test_news_api.py
from datetime import UTC, datetime, timedelta

import pytest

from app.models import NewsItem
from app.services.market_data.news import NewsService

pytestmark = pytest.mark.asyncio(loop_scope="session")


class _NoFetchNews:
    """News provider that never returns anything — cache-only tests seed rows
    with fresh fetched_at so refresh() is a no-op skip; this guards against any
    accidental network call."""
    async def get_news(self, symbol):
        return []


def _override_news(client):
    from app.api.news import get_news_service
    client.app.dependency_overrides[get_news_service] = lambda: NewsService(_NoFetchNews())


async def _seed_news(db, instrument_id, titles, *, fresh=True):
    now = datetime.now(UTC).replace(tzinfo=None)
    for i, t in enumerate(titles):
        db.add(NewsItem(instrument_id=instrument_id, title=t, source="Yahoo",
                        url=f"http://x/{instrument_id}/{i}", published_at=now - timedelta(hours=i),
                        fetched_at=now))
    await db.commit()


async def _add_position(auth_client, symbol):
    pid = (await auth_client.post("/api/portfolios",
           json={"name": "P", "kind": "real", "base_currency": "GBP"})).json()["id"]
    await auth_client.post(f"/api/portfolios/{pid}/positions",
                           json={"symbol": symbol, "quantity": "1"})


async def test_get_news_groups_and_ranks(auth_client, db_session, make_instrument):
    _override_news(auth_client)
    a = await make_instrument("AAPL")
    b = await make_instrument("MSFT")
    await _add_position(auth_client, "AAPL")
    await _add_position(auth_client, "MSFT")
    await _seed_news(db_session, a.id, ["Apple beats", "Apple beats!", "Apple ships"])  # 2 after dedupe
    await _seed_news(db_session, b.id, ["MSFT cloud grows"])  # 1

    body = (await auth_client.get("/api/news")).json()
    syms = [g["symbol"] for g in body["groups"]]
    assert syms == ["AAPL", "MSFT"]                 # AAPL more headlines -> first
    aapl = body["groups"][0]
    assert len(aapl["items"]) == 2                  # deduped
    assert aapl["summary_available"] is False


async def test_get_stock_news_404_when_not_held(auth_client):
    _override_news(auth_client)
    r = await auth_client.get("/api/news/NVDA")
    assert r.status_code == 404


async def test_news_excludes_other_users(auth_client, client, db_session, make_instrument):
    _override_news(auth_client)
    a = await make_instrument("AAPL")
    await _add_position(auth_client, "AAPL")
    await _seed_news(db_session, a.id, ["Apple news"])
    # second user sees no groups
    from app.core.security import hash_password
    from app.models.user import User
    db_session.add(User(email="bnews@test.dev", password_hash=hash_password("pw123456")))
    await db_session.commit()
    _override_news(client)
    await client.post("/api/auth/login", json={"email": "bnews@test.dev", "password": "pw123456"})
    body = (await client.get("/api/news")).json()
    assert body["groups"] == []
```

- [ ] **Step 5: Run to verify failure**

Run: `cd backend && .venv/bin/pytest tests/test_news_api.py -q`
Expected: FAIL (routes missing).

- [ ] **Step 6: Implement the router**

```python
# backend/app/api/news.py
from datetime import UTC, datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select

from app.api.deps import CurrentUser, SessionDep
from app.models import GuruReport, Instrument, NewsItem, Portfolio, Position
from app.services.market_data.news import NewsService, YahooRssProvider
from app.services.market_data.news_read import dedupe, rank_groups

router = APIRouter(prefix="/api/news", tags=["news"])

_PER_STOCK_DASH = 8      # headlines per stock on the dashboard panel
_PER_STOCK_FULL = 30     # headlines on the per-stock page
_WINDOW = timedelta(days=14)

_news_service: NewsService | None = None


def get_news_service() -> NewsService:
    global _news_service
    if _news_service is None:
        _news_service = NewsService(YahooRssProvider())
    return _news_service


NewsServiceDep = Annotated[NewsService, Depends(get_news_service)]


async def user_instruments(db, user_id: int) -> list[Instrument]:
    return list((await db.execute(
        select(Instrument).distinct()
        .join(Position, Position.instrument_id == Instrument.id)
        .join(Portfolio, Portfolio.id == Position.portfolio_id)
        .where(Portfolio.user_id == user_id)
    )).scalars().all())


async def _instrument_for_symbol(db, user_id: int, symbol: str) -> Instrument:
    inst = (await db.execute(
        select(Instrument).distinct()
        .join(Position, Position.instrument_id == Instrument.id)
        .join(Portfolio, Portfolio.id == Position.portfolio_id)
        .where(Portfolio.user_id == user_id, Instrument.symbol == symbol.upper())
    )).scalar_one_or_none()
    if inst is None:
        raise HTTPException(status_code=404, detail="not_held")
    return inst


async def _recent(db, instrument_id: int) -> list[NewsItem]:
    cutoff = datetime.now(UTC).replace(tzinfo=None) - _WINDOW
    from sqlalchemy import func
    return list((await db.execute(
        select(NewsItem).where(
            NewsItem.instrument_id == instrument_id,
            func.coalesce(NewsItem.published_at, NewsItem.fetched_at) >= cutoff,
        )
    )).scalars().all())


def _item_out(n: NewsItem) -> dict:
    return {"title": n.title, "source": n.source, "url": n.url,
            "published_at": (n.published_at or n.fetched_at).isoformat()}


class NewsItemOut(BaseModel):
    title: str
    source: str
    url: str
    published_at: str


class NewsGroup(BaseModel):
    symbol: str
    name: str
    latest_published_at: str | None
    items: list[NewsItemOut]
    summary_available: bool


class NewsResponse(BaseModel):
    groups: list[NewsGroup]
    unavailable: list[str]
    as_of: str


@router.get("", response_model=NewsResponse)
async def get_news(db: SessionDep, user: CurrentUser, news: NewsServiceDep):
    insts = await user_instruments(db, user.id)
    refreshed = await news.refresh(db, insts)     # TTL-gated; failure-isolated
    await db.commit()
    unavailable = [i.symbol for i in insts if i.id not in refreshed]

    summarized = {iid for (iid,) in (await db.execute(
        select(GuruReport.instrument_id).where(
            GuruReport.user_id == user.id, GuruReport.kind == "news",
            GuruReport.instrument_id.isnot(None))
    )).all()}

    groups: list[dict] = []
    for inst in insts:
        items = dedupe(await _recent(db, inst.id))[:_PER_STOCK_DASH]
        if not items:
            continue
        groups.append({
            "symbol": inst.symbol, "name": inst.name,
            "latest_published_at": _item_out(items[0])["published_at"],
            "items": [_item_out(n) for n in items],
            "summary_available": inst.id in summarized,
        })
    groups = rank_groups(groups)
    return NewsResponse(groups=groups, unavailable=unavailable,
                        as_of=datetime.now(UTC).isoformat())


class StockNews(BaseModel):
    symbol: str
    name: str
    items: list[NewsItemOut]
    as_of: str


@router.get("/{symbol}", response_model=StockNews)
async def get_stock_news(symbol: str, db: SessionDep, user: CurrentUser, news: NewsServiceDep):
    inst = await _instrument_for_symbol(db, user.id, symbol)
    await news.refresh(db, [inst])
    await db.commit()
    items = dedupe(await _recent(db, inst.id))[:_PER_STOCK_FULL]
    return StockNews(symbol=inst.symbol, name=inst.name,
                     items=[NewsItemOut(**_item_out(n)) for n in items],
                     as_of=datetime.now(UTC).isoformat())


class RefreshOut(BaseModel):
    refreshed: list[str]
    unavailable: list[str]


@router.post("/refresh", response_model=RefreshOut)
async def refresh_news(db: SessionDep, user: CurrentUser, news: NewsServiceDep):
    insts = await user_instruments(db, user.id)
    refreshed = await news.refresh(db, insts)
    await db.commit()
    return RefreshOut(
        refreshed=[i.symbol for i in insts if i.id in refreshed],
        unavailable=[i.symbol for i in insts if i.id not in refreshed])
```

Register in `backend/app/main.py`: `from app.api.news import router as news_router` (alphabetical) and `app.include_router(news_router)` alongside the others.

- [ ] **Step 7: Run tests**

Run: `cd backend && .venv/bin/pytest tests/test_news_read.py tests/test_news_api.py -q` → PASS. Then `.venv/bin/ruff check . && .venv/bin/pytest -q` → green.

- [ ] **Step 8: Commit**

```bash
git add backend/app/services/market_data/news_read.py backend/app/api/news.py backend/app/main.py backend/tests/test_news_read.py backend/tests/test_news_api.py
git commit -m "feat(news): read API (dedupe + rank + TTL refresh) — GET /api/news, /{symbol}, POST /refresh"
```

---

## Task 3: On-demand per-stock summary

**Files:**
- Modify: `backend/app/services/guru/service.py` (`generate_news_summary`), `backend/app/api/news.py` (summary endpoints)
- Test: `backend/tests/test_news_summary.py` (extend)

**Interfaces:**
- Consumes: `GuruService` (advice/scan model attrs from Project 2), `check_budget`, `record_usage`, `GenerationInProgress`, `NewsSummaryPayload`, `map_guru_errors`, `GuruDep`, `_report_out`, `_recent`/`_instrument_for_symbol` (Task 2).
- Produces: `GuruService.generate_news_summary(db, user, instrument, headlines) -> GuruReport`; routes `POST /api/news/{symbol}/summary`, `GET /api/news/{symbol}/summary`.

- [ ] **Step 1: Write the failing tests**

```python
# add to backend/tests/test_news_summary.py
from app.services.guru.schemas import NewsSummaryPayload as _NSP


async def _hold_with_news(auth_client, db_session, make_instrument, symbol):
    from datetime import timedelta
    from app.models import NewsItem
    inst = await make_instrument(symbol)
    pid = (await auth_client.post("/api/portfolios",
           json={"name": "P", "kind": "real", "base_currency": "GBP"})).json()["id"]
    await auth_client.post(f"/api/portfolios/{pid}/positions", json={"symbol": symbol, "quantity": "1"})
    now = datetime.now(UTC).replace(tzinfo=None)
    db_session.add(NewsItem(instrument_id=inst.id, title=f"{symbol} up", source="Yahoo",
                            url=f"http://x/{symbol}", published_at=now, fetched_at=now))
    await db_session.commit()
    return inst


async def test_generate_and_get_summary(guru_client, db_session, make_instrument):
    guru_client.fake_llm.structured_queue.append(_NSP(
        summary="Strong quarter.", sentiment="positive", key_points=["Beat"], disclaimer="Not advice."))
    await _hold_with_news(guru_client, db_session, make_instrument, "AAPL")
    r = await guru_client.post("/api/news/AAPL/summary")
    assert r.status_code == 201
    assert r.json()["payload"]["sentiment"] == "positive"
    got = await guru_client.get("/api/news/AAPL/summary")
    assert got.status_code == 200 and got.json()["payload"]["summary"] == "Strong quarter."
    # the run used the SCAN model (cheap)
    from app.core.config import settings
    assert guru_client.fake_llm.calls[-1]["model"] == guru_client.guru_service.scan_model


async def test_summary_422_when_no_headlines(guru_client, db_session, make_instrument):
    inst = await make_instrument("MSFT")
    pid = (await guru_client.post("/api/portfolios",
           json={"name": "P", "kind": "real", "base_currency": "GBP"})).json()["id"]
    await guru_client.post(f"/api/portfolios/{pid}/positions", json={"symbol": "MSFT", "quantity": "1"})
    r = await guru_client.post("/api/news/MSFT/summary")
    assert r.status_code == 422


async def test_summary_budget_exhausted_429(guru_client, db_session, make_instrument, monkeypatch):
    await _hold_with_news(guru_client, db_session, make_instrument, "AAPL")
    async def over(db, user_id, *, now=None):
        from app.services.guru.budget import BudgetExhausted
        raise BudgetExhausted()
    monkeypatch.setattr("app.services.guru.service.check_budget", over)
    r = await guru_client.post("/api/news/AAPL/summary")
    assert r.status_code == 429 and r.json()["detail"] == "budget_exhausted"


async def test_summary_404_when_not_held(guru_client):
    r = await guru_client.post("/api/news/TSLA/summary")
    assert r.status_code == 404
```

- [ ] **Step 2: Run to verify failure**

Run: `cd backend && .venv/bin/pytest tests/test_news_summary.py -q`
Expected: FAIL (route + service method missing).

- [ ] **Step 3: Add `generate_news_summary`**

In `backend/app/services/guru/service.py` (add the news instruction near `_ORSO_INSTRUCTION`, and the method alongside `generate_orso`):

```python
_NEWS_INSTRUCTION = (
    "Summarize the recent news for this stock for a retail investor. Return a 2-3 "
    "sentence plain-English summary, a single overall sentiment "
    "(positive/negative/neutral/watch), the key points as short bullets, and a one-line "
    "disclaimer that this is general information, not advice. Base it ONLY on the "
    "headlines provided."
)
```

```python
    async def generate_news_summary(self, db: AsyncSession, user: User,
                                    instrument, headlines: list) -> GuruReport:
        provider = self._require_provider()
        lock = self._lock("news")
        if lock.locked():
            raise GenerationInProgress("news")
        async with lock:
            await check_budget(db, user.id)
            payload_in = [
                {"title": h.title, "source": h.source,
                 "published_at": (h.published_at or h.fetched_at).isoformat()}
                for h in headlines
            ]
            messages = [{"role": "user", "content":
                         f"{_NEWS_INSTRUCTION}\n\nStock: {instrument.symbol} ({instrument.name})\n\n"
                         + json.dumps(payload_in)}]
            payload, usage = await provider.generate_structured(
                system=PERSONA_V1, messages=messages, schema=NewsSummaryPayload,
                model=self.scan_model, max_tokens=1024)
            report = GuruReport(user_id=user.id, kind="news", portfolio_id=None,
                                instrument_id=instrument.id, payload=payload.model_dump(),
                                model=self.scan_model, created_at=_now())
            db.add(report)
            await db.flush()
            await usage_mod.record_usage(db, user_id=user.id, mode="news",
                                         model=self.scan_model, usage=usage,
                                         report_id=report.id, price=self.scan_price)
            await db.commit()
            return report
```

Add `NewsSummaryPayload` to the `from app.services.guru.schemas import ...` line in `service.py`.

- [ ] **Step 4: Add the summary endpoints**

In `backend/app/api/news.py` (import the guru helpers at top: `from app.api.guru import GuruDep, ReportOut, _report_out, map_guru_errors`):

```python
@router.post("/{symbol}/summary", response_model=ReportOut, status_code=201)
async def create_summary(symbol: str, db: SessionDep, user: CurrentUser, guru: GuruDep,
                         news: NewsServiceDep):
    inst = await _instrument_for_symbol(db, user.id, symbol)
    await news.refresh(db, [inst])
    await db.commit()
    headlines = dedupe(await _recent(db, inst.id))[:_PER_STOCK_FULL]
    if not headlines:
        raise HTTPException(status_code=422, detail="no_headlines")
    with map_guru_errors():
        report = await guru.generate_news_summary(db, user, inst, headlines)
    return _report_out(report)


@router.get("/{symbol}/summary", response_model=ReportOut)
async def latest_summary(symbol: str, db: SessionDep, user: CurrentUser):
    inst = await _instrument_for_symbol(db, user.id, symbol)
    r = (await db.execute(
        select(GuruReport).where(
            GuruReport.user_id == user.id, GuruReport.kind == "news",
            GuruReport.instrument_id == inst.id)
        .order_by(GuruReport.created_at.desc(), GuruReport.id.desc()).limit(1)
    )).scalar_one_or_none()
    if r is None:
        raise HTTPException(status_code=404, detail="no_summary")
    return _report_out(r)
```

- [ ] **Step 5: Run tests**

Run: `cd backend && .venv/bin/pytest tests/test_news_summary.py -q` → PASS. Then `.venv/bin/ruff check . && .venv/bin/pytest -q` → green (existing guru tests unaffected — `mode="news"` + new lock key are additive).

- [ ] **Step 6: Commit**

```bash
git add backend/app/services/guru/service.py backend/app/api/news.py backend/tests/test_news_summary.py
git commit -m "feat(news): on-demand per-stock Guru summary (scan model, budget-gated, saved+regenerable)"
```

---

## Task 4: Figma gate (USER GATE)

**Files:** none (Figma design artifacts).

- [ ] **Step 1: Produce Figma frames** for: (a) the dashboard **News panel** — per-holding cards ranked most-active first, each with de-duped headlines (source · relative time · external-link icon), a panel **Refresh** button + "fetched X ago", and a **Summarize / Regenerate** button revealing the Guru summary with a color-coded **sentiment tag** (positive=gain / negative=loss / watch=flag / neutral=muted) + key points; (b) the **per-position** news list + the same summary block. Match the existing dashboard/detail styling (file key `0gU58wfjttdZS0NXQeEtuD`).
- [ ] **Step 2: Present to the user (inline PNGs) and get explicit approval before Task 5.** Incorporate feedback and re-present until approved.

---

## Task 5: Frontend — News panel + per-position list + summarize (push seam)

**Files:**
- Modify: `frontend/src/lib/api.ts`, `frontend/src/lib/types.ts`, `frontend/src/pages/DashboardPage.tsx`, `frontend/src/pages/PortfolioDetailPage.tsx`
- Create: `frontend/src/components/NewsPanel.tsx`
- Test: `frontend/src/components/NewsPanel.test.tsx` (+ detail additions)

**Interfaces:**
- Consumes: `GET /api/news`, `GET /api/news/{symbol}`, `POST /api/news/refresh`, `GET/POST /api/news/{symbol}/summary`.

- [ ] **Step 1: Types + API client** — add `NewsGroup`/`NewsItem`/`NewsResponse`/`StockNews` types (mirror the backend models) and a `NewsSummary` type (the `ReportOut.payload` shape: `summary`, `sentiment`, `key_points`, `disclaimer`); add `getNews()`, `getStockNews(symbol)`, `refreshNews()`, `getNewsSummary(symbol)`, `generateNewsSummary(symbol)` in `lib/api.ts` following the existing fetch/`isBudgetExhausted` pattern.
- [ ] **Step 2: NewsPanel (TDD w/ vitest-axe)** — write a failing `NewsPanel.test.tsx` that (mocking fetch) renders two ranked groups, asserts the deduped headlines render with source + link, clicks **Summarize** on a group → mocks the summary POST → asserts the summary + sentiment tag render, and that a 429 shows the budget-exhausted message. Then implement `NewsPanel.tsx`: ranked cards, headline rows (source · relative time · `target="_blank" rel="noreferrer"` link), Refresh button (calls `refreshNews` then invalidates the `["news"]` query), Summarize/Regenerate button (calls `generateNewsSummary`, shows the returned payload; if `summary_available`, lazy-load via `getNewsSummary`). Sentiment tag color: positive=`text-gain`, negative=`text-loss`, watch=`text-flag`, neutral=`text-muted`. axe assertion on the populated panel.
- [ ] **Step 3: Mount on dashboard** — add `<NewsPanel />` to `DashboardPage.tsx` (below/next to `AttentionPanel`).
- [ ] **Step 4: Per-position news** — in `PortfolioDetailPage.tsx`, add a news list for each position's symbol (`getStockNews`) + the same Summarize/summary block. Keep it lazy/collapsible so it doesn't bloat the page.
- [ ] **Step 5: Verify** — `cd frontend && npm run check` (tsc + lint + vitest incl. axe + build) → green.
- [ ] **Step 6: Commit + push (push seam — reaches prod)**

```bash
git add frontend/src
git commit -m "feat(news): dashboard News panel + per-position list + on-demand summary (frontend)"
git push origin main
```

Confirm CI green (`gh run view <id> --json conclusion,jobs`, matched by head SHA); Railway deploys the backend on green CI (migration 0011 runs), Vercel the frontend.

---

## Task 6: Docs + live smoke + final Opus review

- [ ] **Step 1: Live smoke** on prod — `GET /api/news` returns grouped headlines for held stocks (401 unauth check for the new routes); a Summarize call generates + persists a summary (spends a small amount of budget); `GET` returns the stored summary; Refresh works; a non-held symbol → 404. Confirm migration 0011 ran (`railway logs … | grep 0011`), health 200.
- [ ] **Step 2: Docs** — AGENTS.md (head → 0011; the news read/summary surface), `docs/PROGRESS.md` (new section), README (news paragraph).
- [ ] **Step 3: Final whole-branch review on Opus** — base = the pre-Task-1 tip. Focus: degrade-never-500 across news reads + summary; cross-user scoping (only held instruments); budget-gating; dedupe/rank correctness; summary encrypted + schema-validated. Fix wave → re-review to merge-clean; push fixes; refresh docs if anything changed.
- [ ] **Step 4: Commit doc/fix changes + push.**

---

## Self-Review (completed by the plan author)

**1. Spec coverage:** `GuruReport.instrument_id` + `kind="news"` + `NewsSummaryPayload` → Task 1. Dedupe/rank/TTL-refresh + `GET /api/news`, `GET /{symbol}`, `POST /refresh` → Task 2. `generate_news_summary` (scan model, budget-gated, saved) + `POST`/`GET /{symbol}/summary` → Task 3. Figma gate → Task 4. Dashboard panel + per-position + summarize → Task 5. Docs+smoke+Opus → Task 6. Every spec §1–§5 requirement maps to a task. Headlines-render-even-if-summary-fails holds (reads and summary are separate endpoints).

**2. Placeholder scan:** no `TBD`/vague directives; degrade behaviour is spelled out with status codes; the dedupe/rank/TTL logic is concrete code.

**3. Type consistency:** `dedupe`/`rank_groups`/`norm_title` (Task 2) match the calls in `get_news`/`get_stock_news`/`create_summary`. `generate_news_summary(db, user, instrument, headlines)` (Task 3) matches the `create_summary` call and the test's model-attr assertion. `NewsSummaryPayload` fields (Task 1) match the schema seeded in the Task-3 test and the frontend `NewsSummary` type (Task 5). `get_news_service` dep name matches the test override in Task 2/3. `_report_out`/`ReportOut` reused unchanged (payload carries the summary).

**Fixture notes for executors:** `guru_client` (conftest) exposes `.fake_llm` (seed `structured_queue` with a `NewsSummaryPayload`) and `.guru_service` (assert `.scan_model`). `make_instrument(symbol)` creates an `Instrument`. The news read tests override `get_news_service` with `NewsService(_NoFetchNews())` AND seed `NewsItem` rows with a fresh `fetched_at` so `refresh()` is a no-op skip (no network). `POST /api/news/{symbol}/summary` needs the `guru_client` fixture (fake provider) — the `create_summary` route depends on `GuruDep` (the fake service) and `NewsServiceDep` (override it in the summary tests too, as `_override_news` does).
