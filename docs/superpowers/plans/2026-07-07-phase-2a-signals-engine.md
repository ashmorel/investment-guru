# Investment Guru Phase 2a — Signals Engine Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A deterministic signals engine that analyzes a portfolio against market facts (earnings proximity, price/volume moves, 52-week breaches, concentration, FX exposure, recent news), stores a timestamped snapshot, and lights up the dashboard "Needs your attention" panel — with no LLM.

**Architecture:** A registry of pure signal-rule functions each returning `SignalDraft`s from a `SignalContext` (portfolio + market facts). A `SignalEngine.analyze` gathers inputs (failure-isolated), runs the rules, and replaces the portfolio's snapshot in a `signals` table transactionally. New market-data inputs (price-bar history, next-earnings dates, RSS news) sit behind provider abstractions with pure, fixture-tested parsers. The frontend binds the existing dashboard placeholder to a new `attention` endpoint and adds a "Run analysis" action + per-position badges.

**Tech Stack:** Python 3.13, FastAPI, SQLAlchemy 2 async, Alembic, yfinance, feedparser, pytest; React 19 + Vite + TS + Tailwind v4 + React Query; Postgres 16 on port 5433.

## Global Constraints

- Spec: `docs/superpowers/specs/2026-07-07-phase-2a-signals-engine-design.md` — all tasks bound by it.
- Builds on Phase 1 at HEAD `d8e1dac`; Alembic head is `0003` (new migration is `0004`).
- **Public repo: no real holdings data** — all fixtures/seeds synthetic. Never read or modify any `.env`.
- **No LLM, no Anthropic API key** anywhere in 2a. Entirely deterministic.
- Money/quantity/prices are `Decimal` in Python and `Numeric` in DB — never float. Volumes are `int`/`BigInteger`.
- Signal rules are **pure** — they take a `SignalContext` and return `list[SignalDraft]`; they never touch the DB, network, or clock (the engine injects `today`).
- Every `analyze` run **replaces** the portfolio's signal snapshot transactionally (delete-then-insert) and stamps one `computed_at`.
- Providers/feeds **never crash an endpoint** — a failed input is skipped and reported in `unavailable_inputs`; endpoints never 500 on provider failure.
- yfinance/RSS/network is **never** called in tests — providers are fixture/fake-tested (same pattern as Phase 1's `_NullProvider` + dependency overrides).
- Async tests: `pytestmark = pytest.mark.asyncio(loop_scope="session")` + the shared `client`/`auth_client`/`db_session`/`make_instrument` fixtures.
- All portfolio-scoped routes require auth and 404 on another user's portfolio (reuse `get_owned_portfolio`).
- Backend gate: `cd backend && source .venv/bin/activate && pytest -v && ruff check .` (zero warnings). Frontend gate: `cd frontend && npm run test && npm run check`.
- Commit messages end with: `Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>`. Do not push until Task 10.
- Postgres runs via `docker compose up -d db` (port 5433; DBs `guru` + `guru_test`).

### Phase 1 interfaces this plan consumes (exact)

- `app.models.Portfolio` — `.positions` (list, selectin), `.base_currency`, `.user_id`, `.id`, `.name`, `.kind`.
- `app.models.Position` — `.instrument` (selectin), `.quantity: Decimal | None`, `.avg_cost: Decimal | None`, `.id`, `.portfolio_id`, `.instrument_id`.
- `app.models.Instrument` — `.id`, `.symbol`, `.name`, `.market`, `.sector: str | None`, `.currency`.
- `app.models.PriceBar` — `.instrument_id`, `.date`, `.open/.high/.low/.close: Decimal`, `.volume: int | None`; unique `(instrument_id, date)`.
- `app.services.market_data.base.Quote` — frozen dataclass `symbol, price: Decimal, currency, previous_close: Decimal | None, as_of: datetime`.
- `app.services.market_data.base.MarketDataProvider` — Protocol with `get_quotes`, `get_fx_rate`, `lookup` (this plan extends it).
- `app.services.market_data.quotes.QuoteService(provider).get_quotes(db, symbols) -> dict[str, Quote]`.
- `app.services.valuation.FxService(provider).get_rate(db, base, quote) -> Decimal`.
- `app.services.valuation.value_portfolio(db, portfolio, quote_service, fx) -> PortfolioSummary` — `.total_value: Decimal | None`, `.currency_exposure: dict[str, Decimal]`, `.positions: list[PositionValuation]` (each `.symbol`, `.market_value_base: Decimal | None`, `.day_change_base: Decimal | None`, `.native_currency`, `.position_id`).
- `app.services.valuation.normalise(amount, currency) -> tuple[Decimal, str]` (GBp→GBP ÷100).
- `app.api.portfolios.get_owned_portfolio(db, user, portfolio_id) -> Portfolio`.
- `app.api.deps.CurrentUser`, `app.api.deps.SessionDep`.
- Test fixtures (`backend/tests/conftest.py`): `client`, `auth_client`, `db_session`, `make_instrument(symbol, **overrides) -> Instrument`; `_NullProvider` (class); default dependency overrides for `get_session`, `get_services`, `get_provider`.
- Frontend: `apiFetch<T>(path, init?)` (`src/lib/api.ts`), types in `src/lib/types.ts`, `<Money>` (`src/components/Money.tsx`), `DashboardPage.tsx`, `PortfolioDetailPage.tsx`; Tailwind tokens `bg-bg bg-surface border-border text-text text-muted text-gain text-loss text-flag` + `.tabular-nums`.

---

### Task 1: Domain models + migration `0004`

**Files:**
- Create: `backend/app/models/signals.py`
- Modify: `backend/app/models/market.py` (add `InstrumentFundamentals`, `NewsItem`)
- Modify: `backend/app/models/__init__.py`
- Create: `backend/alembic/versions/0004_signals.py`
- Test: `backend/tests/test_signals_models.py`

**Interfaces:**
- Produces:
  - `Signal`: id; portfolio_id (FK portfolios, index); instrument_id (FK instruments, nullable); kind (str32); severity (str8); title (str200); detail (str500); data (JSONB dict); computed_at (datetime)
  - `InstrumentFundamentals`: instrument_id (PK, FK instruments); next_earnings_date (Date, nullable); fetched_at (datetime)
  - `NewsItem`: id; instrument_id (FK instruments, nullable); title (str500); source (str100); url (str1000); published_at (datetime, nullable); fetched_at (datetime); unique `(instrument_id, url)`

- [ ] **Step 1: Write the failing test**

`backend/tests/test_signals_models.py`:
```python
from datetime import UTC, date, datetime

import pytest

from app.models import Instrument, NewsItem, Portfolio, Signal, User
from app.models import InstrumentFundamentals

pytestmark = pytest.mark.asyncio(loop_scope="session")


async def test_signal_persists_with_json_data(db_session):
    user = User(email="s@test.dev", password_hash="x")
    inst = Instrument(symbol="AAPL", name="Apple", exchange="NMS", market="US", currency="USD")
    db_session.add_all([user, inst])
    await db_session.flush()
    pf = Portfolio(user_id=user.id, name="P", kind="real", base_currency="GBP")
    db_session.add(pf)
    await db_session.flush()
    sig = Signal(
        portfolio_id=pf.id, instrument_id=inst.id, kind="price_move_day",
        severity="watch", title="AAPL -6.1% today", detail="Down 6.1% on the day",
        data={"pct": "-6.1", "close": "188.22"},
        computed_at=datetime.now(UTC).replace(tzinfo=None),
    )
    db_session.add(sig)
    await db_session.commit()
    loaded = await db_session.get(Signal, sig.id)
    assert loaded.data["pct"] == "-6.1"
    assert loaded.instrument_id == inst.id


async def test_portfolio_level_signal_allows_null_instrument(db_session):
    user = User(email="s2@test.dev", password_hash="x")
    db_session.add(user)
    await db_session.flush()
    pf = Portfolio(user_id=user.id, name="P", kind="real", base_currency="GBP")
    db_session.add(pf)
    await db_session.flush()
    db_session.add(Signal(
        portfolio_id=pf.id, instrument_id=None, kind="concentration",
        severity="high", title="AAPL is 32% of portfolio", detail="Single-name concentration",
        data={"pct": "32.0"}, computed_at=datetime.now(UTC).replace(tzinfo=None),
    ))
    await db_session.commit()


async def test_fundamentals_and_news(db_session):
    inst = Instrument(symbol="NVDA", name="Nvidia", exchange="NMS", market="US", currency="USD")
    db_session.add(inst)
    await db_session.flush()
    db_session.add(InstrumentFundamentals(
        instrument_id=inst.id, next_earnings_date=date(2026, 8, 20),
        fetched_at=datetime.now(UTC).replace(tzinfo=None),
    ))
    db_session.add(NewsItem(
        instrument_id=inst.id, title="Nvidia announces X", source="Yahoo",
        url="https://example.com/n1", published_at=datetime.now(UTC).replace(tzinfo=None),
        fetched_at=datetime.now(UTC).replace(tzinfo=None),
    ))
    await db_session.commit()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && source .venv/bin/activate && docker compose -f ../docker-compose.yml up -d db && pytest tests/test_signals_models.py -v`
Expected: FAIL — ImportError (`Signal`, `NewsItem`, `InstrumentFundamentals` not defined)

- [ ] **Step 3: Implement the models**

`backend/app/models/signals.py`:
```python
from datetime import datetime
from typing import Any

from sqlalchemy import ForeignKey, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base


class Signal(Base):
    __tablename__ = "signals"

    id: Mapped[int] = mapped_column(primary_key=True)
    portfolio_id: Mapped[int] = mapped_column(ForeignKey("portfolios.id"), index=True)
    instrument_id: Mapped[int | None] = mapped_column(ForeignKey("instruments.id"))
    kind: Mapped[str] = mapped_column(String(32))
    severity: Mapped[str] = mapped_column(String(8))  # info | watch | high
    title: Mapped[str] = mapped_column(String(200))
    detail: Mapped[str] = mapped_column(String(500))
    data: Mapped[dict[str, Any]] = mapped_column(JSONB)
    computed_at: Mapped[datetime] = mapped_column()
```

Append to `backend/app/models/market.py`:
```python
class InstrumentFundamentals(Base):
    __tablename__ = "instrument_fundamentals"

    instrument_id: Mapped[int] = mapped_column(ForeignKey("instruments.id"), primary_key=True)
    next_earnings_date: Mapped[date | None] = mapped_column(Date)
    fetched_at: Mapped[datetime] = mapped_column()


class NewsItem(Base):
    __tablename__ = "news_items"
    __table_args__ = (UniqueConstraint("instrument_id", "url"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    instrument_id: Mapped[int | None] = mapped_column(ForeignKey("instruments.id"), index=True)
    title: Mapped[str] = mapped_column(String(500))
    source: Mapped[str] = mapped_column(String(100))
    url: Mapped[str] = mapped_column(String(1000))
    published_at: Mapped[datetime | None] = mapped_column()
    fetched_at: Mapped[datetime] = mapped_column()
```
(`market.py` already imports `date, datetime`, `ForeignKey, Date, String, UniqueConstraint`, `Mapped, mapped_column` — reuse; no new imports needed.)

`backend/app/models/__init__.py` — add to imports and `__all__`:
```python
from app.models.market import FxRate, InstrumentFundamentals, NewsItem, PriceBar, QuoteCache
from app.models.signals import Signal
```
Add `"InstrumentFundamentals"`, `"NewsItem"`, `"Signal"` to `__all__` (keep the list alphabetically consistent with the existing style).

- [ ] **Step 4: Write migration `0004`**

`backend/alembic/versions/0004_signals.py` — `revision = "0004"`, `down_revision = "0003"`. Verify chain first: `alembic heads` → must show `0003`. Create three tables matching the models exactly:
```python
"""signals, instrument_fundamentals, news_items

Revision ID: 0004
Revises: 0003
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "signals",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("portfolio_id", sa.Integer(), sa.ForeignKey("portfolios.id"), nullable=False),
        sa.Column("instrument_id", sa.Integer(), sa.ForeignKey("instruments.id"), nullable=True),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("severity", sa.String(8), nullable=False),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("detail", sa.String(500), nullable=False),
        sa.Column("data", postgresql.JSONB(), nullable=False),
        sa.Column("computed_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_signals_portfolio_id", "signals", ["portfolio_id"])
    op.create_table(
        "instrument_fundamentals",
        sa.Column("instrument_id", sa.Integer(), sa.ForeignKey("instruments.id"), primary_key=True),
        sa.Column("next_earnings_date", sa.Date(), nullable=True),
        sa.Column("fetched_at", sa.DateTime(), nullable=False),
    )
    op.create_table(
        "news_items",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("instrument_id", sa.Integer(), sa.ForeignKey("instruments.id"), nullable=True),
        sa.Column("title", sa.String(500), nullable=False),
        sa.Column("source", sa.String(100), nullable=False),
        sa.Column("url", sa.String(1000), nullable=False),
        sa.Column("published_at", sa.DateTime(), nullable=True),
        sa.Column("fetched_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("instrument_id", "url"),
    )
    op.create_index("ix_news_items_instrument_id", "news_items", ["instrument_id"])


def downgrade() -> None:
    op.drop_table("news_items")
    op.drop_table("instrument_fundamentals")
    op.drop_table("signals")
```
Run: `alembic upgrade head` → `Running upgrade 0003 -> 0004`. Then verify with `docker exec investment-guru-db-1 psql -U guru -d guru -c '\d signals'`.

- [ ] **Step 5: Run tests + lint, commit**

Run: `cd backend && pytest -v && ruff check .`
Expected: all PASS

```bash
git add -A
git commit -m "feat: signal, fundamentals, news models + migration 0004

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 2: Price-history provider + `HistoryService`

**Files:**
- Modify: `backend/app/services/market_data/base.py` (extend Protocol + `Bar` dataclass)
- Modify: `backend/app/services/market_data/yahoo.py` (implement `get_history`; pure `parse_history`)
- Create: `backend/app/services/market_data/history.py`
- Create: `backend/tests/fixtures/yahoo_history_aapl.json`
- Test: `backend/tests/test_history.py`

**Interfaces:**
- Produces:
  - `base.Bar` frozen dataclass: `date: date, open: Decimal, high: Decimal, low: Decimal, close: Decimal, volume: int | None`
  - `MarketDataProvider.get_history(self, symbol, days: int = 400) -> list[Bar]` added to the Protocol
  - `yahoo.parse_history(rows: list[dict]) -> list[Bar]` (pure; rows are `{date, open, high, low, close, volume}`; ascending by date; skips rows with null close)
  - `history.HistoryService(provider).refresh(db, instruments: list[Instrument]) -> set[int]` — upserts `PriceBar`s, returns instrument_ids successfully refreshed; cached with `HISTORY_TTL = timedelta(hours=20)` (skips instruments whose newest bar is fresher than TTL)
  - `history.period_return(bars: list[PriceBar], trading_days: int) -> Decimal | None`
  - `history.fifty_two_week_range(bars: list[PriceBar]) -> tuple[Decimal, Decimal] | None` (low, high over last 252 bars)
  - `history.avg_volume(bars: list[PriceBar], trading_days: int) -> Decimal | None`

- [ ] **Step 1: Create the fixture (synthetic)**

`backend/tests/fixtures/yahoo_history_aapl.json` — 6 ascending daily rows (enough for the parser test):
```json
[
  {"date": "2026-06-30", "open": 150.0, "high": 152.0, "low": 149.0, "close": 151.0, "volume": 40000000},
  {"date": "2026-07-01", "open": 151.0, "high": 155.0, "low": 150.5, "close": 154.0, "volume": 55000000},
  {"date": "2026-07-02", "open": 154.0, "high": 156.0, "low": 152.0, "close": 153.0, "volume": 38000000},
  {"date": "2026-07-03", "open": 153.0, "high": 158.0, "low": 152.5, "close": 157.5, "volume": 61000000},
  {"date": "2026-07-06", "open": 157.5, "high": 160.0, "low": 156.0, "close": 159.0, "volume": 47000000},
  {"date": "2026-07-07", "open": 159.0, "high": 162.0, "low": 158.0, "close": 161.0, "volume": 52000000}
]
```

- [ ] **Step 2: Write the failing parser + helper tests**

`backend/tests/test_history.py`:
```python
import json
from datetime import date
from decimal import Decimal
from pathlib import Path

from app.services.market_data.history import avg_volume, fifty_two_week_range, period_return
from app.services.market_data.yahoo import parse_history

FIXTURES = Path(__file__).parent / "fixtures"


def _bars():
    rows = json.loads((FIXTURES / "yahoo_history_aapl.json").read_text())
    return parse_history(rows)


def test_parse_history_ascending_decimal():
    bars = _bars()
    assert len(bars) == 6
    assert bars[0].date == date(2026, 6, 30)
    assert bars[-1].close == Decimal("161.0")
    assert bars[-1].volume == 52000000


def test_parse_history_skips_null_close():
    bars = parse_history([{"date": "2026-07-07", "open": 1, "high": 1, "low": 1,
                           "close": None, "volume": 1}])
    assert bars == []


def test_period_return():
    bars = _bars()
    # from close 151.0 (index -6) to 161.0 (last) over 5 trading days
    r = period_return(bars, 5)
    assert r == Decimal("6.62")  # (161-151)/151*100 rounded 2dp


def test_fifty_two_week_range():
    bars = _bars()
    low, high = fifty_two_week_range(bars)
    assert (low, high) == (Decimal("149.0"), Decimal("162.0"))


def test_avg_volume():
    bars = _bars()
    # avg of last 3 volumes: (38000000+61000000+47000000... ) use 3 days = last 3
    assert avg_volume(bars, 3) == Decimal("53333333.33")  # (38m? -> last 3 are idx -3.. )
```
Note: the `avg_volume`/`period_return` expected values must match your rounding — compute them exactly against the fixture when implementing and set the literals accordingly (2dp, ROUND_HALF_UP). Last-3 volumes are 61000000, 47000000, 52000000 → mean 53333333.33; the last-5 close return is (161−154)/154? — recompute: 5 trading days back from index 5 is index 0 (151.0) only if counting 5 gaps. Define `period_return(bars, n)` as `(bars[-1].close - bars[-1-n].close)/bars[-1-n].close*100`; with n=5 that's bars[0]=151 → 6.62. Set literals to the exact computed values.

- [ ] **Step 3: Run to verify fail, implement**

Run: `cd backend && pytest tests/test_history.py -v` → FAIL (module missing)

Add to `backend/app/services/market_data/base.py` — change its top import from `from datetime import datetime` to `from datetime import date, datetime`, then add:
```python
@dataclass(frozen=True)
class Bar:
    date: date
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: int | None
```
Add to the `MarketDataProvider` Protocol:
```python
    async def get_history(self, symbol: str, days: int = 400) -> list["Bar"]: ...
```

Add to `backend/app/services/market_data/yahoo.py`:
```python
from datetime import date as _date

from app.services.market_data.base import Bar


def parse_history(rows: list[dict]) -> list[Bar]:
    bars: list[Bar] = []
    for r in rows:
        close = r.get("close")
        if close is None:
            continue
        d = r["date"]
        bars.append(Bar(
            date=_date.fromisoformat(d) if isinstance(d, str) else d,
            open=Decimal(str(r["open"])), high=Decimal(str(r["high"])),
            low=Decimal(str(r["low"])), close=Decimal(str(close)),
            volume=None if r.get("volume") is None else int(r["volume"]),
        ))
    bars.sort(key=lambda b: b.date)
    return bars
```
Add a `get_history` method to `YahooProvider` that runs yfinance in a thread and passes rows to `parse_history`:
```python
    def _fetch_history(self, symbol: str, days: int) -> list[dict]:
        import yfinance as yf

        period = "2y" if days > 365 else "1y"
        df = yf.Ticker(symbol).history(period=period)
        rows = []
        for idx, row in df.iterrows():
            rows.append({
                "date": idx.date(),
                "open": row["Open"], "high": row["High"], "low": row["Low"],
                "close": row["Close"], "volume": row.get("Volume"),
            })
        return rows

    async def get_history(self, symbol: str, days: int = 400) -> list[Bar]:
        rows = await asyncio.to_thread(self._fetch_history, symbol, days)
        return parse_history(rows)
```

`backend/app/services/market_data/history.py`:
```python
from datetime import UTC, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Instrument, PriceBar
from app.services.market_data.base import MarketDataProvider

HISTORY_TTL = timedelta(hours=20)
TWO_DP = Decimal("0.01")


def _round(v: Decimal) -> Decimal:
    return v.quantize(TWO_DP, rounding=ROUND_HALF_UP)


def period_return(bars: list[PriceBar], trading_days: int) -> Decimal | None:
    if len(bars) <= trading_days:
        return None
    prev = bars[-1 - trading_days].close
    if prev == 0:
        return None
    return _round((bars[-1].close - prev) / prev * 100)


def fifty_two_week_range(bars: list[PriceBar]) -> tuple[Decimal, Decimal] | None:
    window = bars[-252:]
    if not window:
        return None
    lows = min(b.low for b in window)
    highs = max(b.high for b in window)
    return lows, highs


def avg_volume(bars: list[PriceBar], trading_days: int) -> Decimal | None:
    window = [b.volume for b in bars[-trading_days:] if b.volume is not None]
    if not window:
        return None
    return _round(Decimal(sum(window)) / Decimal(len(window)))


class HistoryService:
    def __init__(self, provider: MarketDataProvider):
        self.provider = provider

    async def refresh(self, db: AsyncSession, instruments: list[Instrument]) -> set[int]:
        now = datetime.now(UTC).replace(tzinfo=None)
        refreshed: set[int] = set()
        for inst in instruments:
            newest = (
                await db.execute(
                    select(PriceBar.date).where(PriceBar.instrument_id == inst.id)
                    .order_by(PriceBar.date.desc()).limit(1)
                )
            ).scalar_one_or_none()
            existing_dates = set()
            if newest is not None:
                # skip network if newest bar is within TTL of "today"
                if now.date() - newest < HISTORY_TTL:
                    refreshed.add(inst.id)
                    continue
                existing_dates = {
                    d for (d,) in (
                        await db.execute(
                            select(PriceBar.date).where(PriceBar.instrument_id == inst.id)
                        )
                    ).all()
                }
            try:
                bars = await self.provider.get_history(inst.symbol)
            except Exception:
                continue
            for bar in bars:
                if bar.date in existing_dates:
                    continue
                db.add(PriceBar(
                    instrument_id=inst.id, date=bar.date, open=bar.open, high=bar.high,
                    low=bar.low, close=bar.close, volume=bar.volume,
                ))
            refreshed.add(inst.id)
        await db.flush()
        return refreshed
```
Note on `HISTORY_TTL` comparison: `now.date() - newest` is a `timedelta` in days; compare against `HISTORY_TTL` (also timedelta). Fine.

- [ ] **Step 4: Run tests + lint, commit**

Run: `cd backend && pytest tests/test_history.py -v && ruff check .` — set the fixture-derived literals to the exact computed values first so the test passes. Then full `pytest -v`.

```bash
git add -A
git commit -m "feat: price-history provider + HistoryService (backfill + derived helpers)

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 3: News provider (RSS) + cache

**Files:**
- Modify: `backend/pyproject.toml` (add `feedparser>=6.0`)
- Create: `backend/app/services/market_data/news.py`
- Create: `backend/tests/fixtures/yahoo_news_aapl.xml`
- Test: `backend/tests/test_news.py`

**Interfaces:**
- Produces:
  - `news.NewsDTO` frozen dataclass: `title: str, source: str, url: str, published_at: datetime | None`
  - `news.parse_rss(data: bytes, source: str) -> list[NewsDTO]` (pure; via feedparser)
  - `news.NewsProvider` Protocol: `async get_news(self, symbol: str) -> list[NewsDTO]`
  - `news.YahooRssProvider` implementing it (per-ticker Yahoo RSS URL); pure parse isolated
  - `news.NewsService(provider).refresh(db, instruments) -> set[int]` — upserts `NewsItem`s (dedupe on `(instrument_id, url)`), TTL `NEWS_TTL = timedelta(hours=6)`, returns refreshed instrument_ids
  - `news.recent_news(db, instrument_id, within: timedelta) -> list[NewsItem]`

- [ ] **Step 1: Add dep + fixture**

Add `"feedparser>=6.0"` to `backend/pyproject.toml` `dependencies`, then `pip install -e ".[dev]"`.

`backend/tests/fixtures/yahoo_news_aapl.xml` (synthetic RSS 2.0):
```xml
<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel>
  <title>AAPL News</title>
  <item>
    <title>Apple unveils new product line</title>
    <link>https://example.com/aapl-1</link>
    <pubDate>Mon, 06 Jul 2026 14:30:00 GMT</pubDate>
  </item>
  <item>
    <title>Analysts weigh in on Apple quarter</title>
    <link>https://example.com/aapl-2</link>
    <pubDate>Sun, 05 Jul 2026 09:00:00 GMT</pubDate>
  </item>
</channel></rss>
```

- [ ] **Step 2: Write the failing parser test**

`backend/tests/test_news.py`:
```python
from pathlib import Path

from app.services.market_data.news import parse_rss

FIXTURES = Path(__file__).parent / "fixtures"


def test_parse_rss_extracts_items():
    data = (FIXTURES / "yahoo_news_aapl.xml").read_bytes()
    items = parse_rss(data, source="Yahoo")
    assert len(items) == 2
    assert items[0].title == "Apple unveils new product line"
    assert items[0].url == "https://example.com/aapl-1"
    assert items[0].source == "Yahoo"
    assert items[0].published_at is not None


def test_parse_rss_empty_on_garbage():
    assert parse_rss(b"not xml at all", source="Yahoo") == []
```

- [ ] **Step 3: Run to verify fail, implement**

Run: `cd backend && pytest tests/test_news.py -v` → FAIL

`backend/app/services/market_data/news.py`:
```python
import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from time import mktime
from typing import Protocol

import feedparser
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Instrument, NewsItem

NEWS_TTL = timedelta(hours=6)


@dataclass(frozen=True)
class NewsDTO:
    title: str
    source: str
    url: str
    published_at: datetime | None


def parse_rss(data: bytes, source: str) -> list[NewsDTO]:
    feed = feedparser.parse(data)
    items: list[NewsDTO] = []
    for entry in feed.entries:
        title = getattr(entry, "title", None)
        link = getattr(entry, "link", None)
        if not title or not link:
            continue
        published = None
        if getattr(entry, "published_parsed", None) is not None:
            published = datetime.fromtimestamp(mktime(entry.published_parsed), tz=UTC).replace(tzinfo=None)
        items.append(NewsDTO(title=title[:500], source=source, url=link[:1000], published_at=published))
    return items


class NewsProvider(Protocol):
    async def get_news(self, symbol: str) -> list[NewsDTO]: ...


class YahooRssProvider:
    def _fetch(self, symbol: str) -> bytes:
        import urllib.request

        url = f"https://feeds.finance.yahoo.com/rss/2.0/headline?s={symbol}&region=US&lang=en-US"
        with urllib.request.urlopen(url, timeout=10) as resp:
            return resp.read()

    async def get_news(self, symbol: str) -> list[NewsDTO]:
        data = await asyncio.to_thread(self._fetch, symbol)
        return parse_rss(data, source="Yahoo")


class NewsService:
    def __init__(self, provider: NewsProvider):
        self.provider = provider

    async def refresh(self, db: AsyncSession, instruments: list[Instrument]) -> set[int]:
        now = datetime.now(UTC).replace(tzinfo=None)
        refreshed: set[int] = set()
        for inst in instruments:
            newest = (
                await db.execute(
                    select(NewsItem.fetched_at).where(NewsItem.instrument_id == inst.id)
                    .order_by(NewsItem.fetched_at.desc()).limit(1)
                )
            ).scalar_one_or_none()
            if newest is not None and now - newest < NEWS_TTL:
                refreshed.add(inst.id)
                continue
            try:
                dtos = await self.provider.get_news(inst.symbol)
            except Exception:
                continue
            existing = {
                u for (u,) in (
                    await db.execute(select(NewsItem.url).where(NewsItem.instrument_id == inst.id))
                ).all()
            }
            for dto in dtos:
                if dto.url in existing:
                    continue
                db.add(NewsItem(
                    instrument_id=inst.id, title=dto.title, source=dto.source, url=dto.url,
                    published_at=dto.published_at, fetched_at=now,
                ))
                existing.add(dto.url)
            refreshed.add(inst.id)
        await db.flush()
        return refreshed


async def recent_news(db: AsyncSession, instrument_id: int, within: timedelta) -> list[NewsItem]:
    cutoff = datetime.now(UTC).replace(tzinfo=None) - within
    rows = (
        await db.execute(
            select(NewsItem).where(
                NewsItem.instrument_id == instrument_id,
                NewsItem.fetched_at >= cutoff,
            ).order_by(NewsItem.published_at.desc().nullslast())
        )
    ).scalars().all()
    return list(rows)
```

- [ ] **Step 4: Run tests + lint, commit**

Run: `cd backend && pytest -v && ruff check .`

```bash
git add -A
git commit -m "feat: RSS news provider + NewsService cache (feedparser, dedupe, TTL)

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 4: Earnings-date fetch + fundamentals cache

**Files:**
- Modify: `backend/app/services/market_data/base.py` (Protocol `get_earnings_date`)
- Modify: `backend/app/services/market_data/yahoo.py` (`parse_earnings_date`, `get_earnings_date`)
- Create: `backend/app/services/market_data/fundamentals.py`
- Test: `backend/tests/test_fundamentals.py`

**Interfaces:**
- Produces:
  - `MarketDataProvider.get_earnings_date(self, symbol) -> date | None` (Protocol)
  - `yahoo.parse_earnings_date(info: dict) -> date | None` (pure; reads `info["earningsTimestamp"]` epoch or `info["earnings_date"]` iso)
  - `fundamentals.FundamentalsService(provider).refresh(db, instruments) -> None` — upserts `InstrumentFundamentals`, TTL `FUNDAMENTALS_TTL = timedelta(hours=20)`
  - `fundamentals.get_earnings_dates(db, instrument_ids) -> dict[int, date | None]`

- [ ] **Step 1: Write the failing test**

`backend/tests/test_fundamentals.py`:
```python
from datetime import UTC, date, datetime

import pytest

from app.models import Instrument, InstrumentFundamentals
from app.services.market_data.fundamentals import FundamentalsService, get_earnings_dates
from app.services.market_data.yahoo import parse_earnings_date

pytestmark = pytest.mark.asyncio(loop_scope="session")


def test_parse_earnings_from_timestamp():
    # 2026-08-20 00:00 UTC epoch
    ts = int(datetime(2026, 8, 20, tzinfo=UTC).timestamp())
    assert parse_earnings_date({"earningsTimestamp": ts}) == date(2026, 8, 20)


def test_parse_earnings_missing_returns_none():
    assert parse_earnings_date({}) is None


class FakeProvider:
    async def get_earnings_date(self, symbol):
        return date(2026, 8, 20) if symbol == "NVDA" else None

    async def get_quotes(self, symbols):
        return {}

    async def get_fx_rate(self, base, quote):
        raise NotImplementedError

    async def lookup(self, symbol):
        return None

    async def get_history(self, symbol, days=400):
        return []


async def test_fundamentals_refresh_and_read(db_session, make_instrument):
    inst = await make_instrument("NVDA")
    svc = FundamentalsService(FakeProvider())
    await svc.refresh(db_session, [inst])
    await db_session.commit()
    mapping = await get_earnings_dates(db_session, [inst.id])
    assert mapping[inst.id] == date(2026, 8, 20)
```

- [ ] **Step 2: Run to verify fail, implement**

Run: `cd backend && pytest tests/test_fundamentals.py -v` → FAIL

Add to `backend/app/services/market_data/base.py` Protocol:
```python
    async def get_earnings_date(self, symbol: str) -> "date | None": ...
```
(import `date` at top of `base.py` if not present: `from datetime import date, datetime`.)

Add to `backend/app/services/market_data/yahoo.py`:
```python
def parse_earnings_date(info: dict) -> _date | None:
    ts = info.get("earningsTimestamp")
    if ts is not None:
        from datetime import UTC, datetime
        return datetime.fromtimestamp(int(ts), tz=UTC).date()
    iso = info.get("earnings_date")
    if iso:
        return _date.fromisoformat(iso)
    return None


# on YahooProvider:
    async def get_earnings_date(self, symbol: str) -> _date | None:
        info = await asyncio.to_thread(self._fetch_info, symbol)
        return parse_earnings_date(info)
```
(`_fetch_info` already exists on `YahooProvider` from Phase 1.)

`backend/app/services/market_data/fundamentals.py`:
```python
from datetime import UTC, date, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Instrument, InstrumentFundamentals
from app.services.market_data.base import MarketDataProvider

FUNDAMENTALS_TTL = timedelta(hours=20)


class FundamentalsService:
    def __init__(self, provider: MarketDataProvider):
        self.provider = provider

    async def refresh(self, db: AsyncSession, instruments: list[Instrument]) -> None:
        now = datetime.now(UTC).replace(tzinfo=None)
        for inst in instruments:
            row = await db.get(InstrumentFundamentals, inst.id)
            if row is not None and now - row.fetched_at < FUNDAMENTALS_TTL:
                continue
            try:
                ed = await self.provider.get_earnings_date(inst.symbol)
            except Exception:
                continue
            if row is None:
                db.add(InstrumentFundamentals(
                    instrument_id=inst.id, next_earnings_date=ed, fetched_at=now,
                ))
            else:
                row.next_earnings_date = ed
                row.fetched_at = now
        await db.flush()


async def get_earnings_dates(db: AsyncSession, instrument_ids: list[int]) -> dict[int, date | None]:
    rows = (
        await db.execute(
            select(InstrumentFundamentals).where(
                InstrumentFundamentals.instrument_id.in_(instrument_ids)
            )
        )
    ).scalars().all()
    return {r.instrument_id: r.next_earnings_date for r in rows}
```

- [ ] **Step 3: Run tests + lint, commit**

Run: `cd backend && pytest -v && ruff check .`

```bash
git add -A
git commit -m "feat: earnings-date fetch + fundamentals cache

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 5: Signal config, `SignalDraft`, `SignalContext` + per-instrument rules

**Files:**
- Create: `backend/app/services/signals/__init__.py`
- Create: `backend/app/services/signals/config.py`
- Create: `backend/app/services/signals/types.py`
- Create: `backend/app/services/signals/rules.py`
- Test: `backend/tests/test_signal_rules.py`

**Interfaces:**
- Produces:
  - `config` constants: `EARNINGS_DAYS=7, EARNINGS_HIGH_DAYS=2, DAY_MOVE_PCT=Decimal("5"), DAY_MOVE_HIGH_PCT=Decimal("10"), WEEK_MOVE_PCT=Decimal("10"), WEEK_MOVE_HIGH_PCT=Decimal("20"), FIFTY_TWO_NEAR_PCT=Decimal("2"), VOLUME_MULT=Decimal("2"), VOLUME_HIGH_MULT=Decimal("3"), CONC_NAME_PCT=Decimal("20"), CONC_NAME_HIGH_PCT=Decimal("30"), CONC_SECTOR_PCT=Decimal("40"), CONC_SECTOR_HIGH_PCT=Decimal("55"), FX_PCT=Decimal("30"), FX_HIGH_PCT=Decimal("50"), NEWS_WINDOW=timedelta(hours=48)`
  - `types.SignalDraft` dataclass: `kind: str, severity: str, title: str, detail: str, data: dict[str, str], instrument_id: int | None = None`
  - `types.SignalContext` dataclass (below)
  - `rules.PER_INSTRUMENT_RULES: list[Callable[[SignalContext], list[SignalDraft]]]` — 6 rules: `earnings_upcoming, price_move_day, price_move_week, fifty_two_week, unusual_volume, news_recent`

- [ ] **Step 1: Write the failing tests**

`backend/tests/test_signal_rules.py`:
```python
from datetime import UTC, date, datetime
from decimal import Decimal

from app.services.market_data.base import Quote
from app.services.signals.rules import (
    earnings_upcoming, fifty_two_week, price_move_day, price_move_week,
    unusual_volume,
)
from app.services.signals.types import SignalContext


class _Inst:
    def __init__(self, id, symbol, sector="Tech", currency="USD"):
        self.id, self.symbol, self.sector, self.currency = id, symbol, sector, currency
        self.name = symbol


class _Bar:
    def __init__(self, d, close, volume, high=None, low=None):
        self.date, self.close, self.volume = d, Decimal(str(close)), volume
        self.high = Decimal(str(high if high is not None else close))
        self.low = Decimal(str(low if low is not None else close))


def _ctx(**kw):
    base = dict(
        portfolio=None, summary=None, quotes={}, bars={}, earnings={}, news={},
        instruments=[], today=date(2026, 7, 7),
    )
    base.update(kw)
    return SignalContext(**base)


def _q(price, prev):
    return Quote(symbol="X", price=Decimal(str(price)), currency="USD",
                 previous_close=Decimal(str(prev)), as_of=datetime.now(UTC))


def test_earnings_within_window_fires_high():
    inst = _Inst(1, "NVDA")
    ctx = _ctx(instruments=[inst], earnings={1: date(2026, 7, 8)})
    out = earnings_upcoming(ctx)
    assert len(out) == 1 and out[0].severity == "high" and out[0].instrument_id == 1


def test_earnings_far_out_no_fire():
    inst = _Inst(1, "NVDA")
    ctx = _ctx(instruments=[inst], earnings={1: date(2026, 9, 1)})
    assert earnings_upcoming(ctx) == []


def test_price_move_day_watch_and_high():
    inst = _Inst(1, "AAPL")
    ctx = _ctx(instruments=[inst], quotes={"AAPL": _q(94, 100)})  # -6%
    out = price_move_day(ctx)
    assert out[0].severity == "watch"
    ctx2 = _ctx(instruments=[inst], quotes={"AAPL": _q(88, 100)})  # -12%
    assert price_move_day(ctx2)[0].severity == "high"


def test_price_move_week_needs_bars():
    inst = _Inst(1, "AAPL")
    # no history for this instrument → week rule can't fire
    ctx = _ctx(instruments=[inst], bars={1: []})
    assert price_move_week(ctx) == []


def test_unusual_volume_fires():
    inst = _Inst(1, "AAPL")
    bars = [_Bar(date(2026, 7, i + 1), 100, 10_000_000) for i in range(30)]
    bars.append(_Bar(date(2026, 7, 31), 100, 40_000_000))  # 4x avg
    ctx = _ctx(instruments=[inst], bars={1: bars})
    out = unusual_volume(ctx)
    assert out and out[0].severity == "high"
```

- [ ] **Step 2: Run to verify fail, implement config + types + rules**

Run: `cd backend && pytest tests/test_signal_rules.py -v` → FAIL

`backend/app/services/signals/__init__.py`: empty.

`backend/app/services/signals/config.py`:
```python
from datetime import timedelta
from decimal import Decimal

EARNINGS_DAYS = 7
EARNINGS_HIGH_DAYS = 2
DAY_MOVE_PCT = Decimal("5")
DAY_MOVE_HIGH_PCT = Decimal("10")
WEEK_MOVE_PCT = Decimal("10")
WEEK_MOVE_HIGH_PCT = Decimal("20")
FIFTY_TWO_NEAR_PCT = Decimal("2")
VOLUME_MULT = Decimal("2")
VOLUME_HIGH_MULT = Decimal("3")
CONC_NAME_PCT = Decimal("20")
CONC_NAME_HIGH_PCT = Decimal("30")
CONC_SECTOR_PCT = Decimal("40")
CONC_SECTOR_HIGH_PCT = Decimal("55")
FX_PCT = Decimal("30")
FX_HIGH_PCT = Decimal("50")
NEWS_WINDOW = timedelta(hours=48)
```

`backend/app/services/signals/types.py`:
```python
from dataclasses import dataclass, field
from datetime import date

from app.services.market_data.base import Quote


@dataclass
class SignalDraft:
    kind: str
    severity: str
    title: str
    detail: str
    data: dict[str, str]
    instrument_id: int | None = None


@dataclass
class SignalContext:
    portfolio: object            # app.models.Portfolio (avoid circular import at type level)
    summary: object              # app.services.valuation.PortfolioSummary | None
    quotes: dict[str, Quote]
    bars: dict[int, list]        # instrument_id -> list[PriceBar]
    earnings: dict[int, date | None]
    news: dict[int, list]        # instrument_id -> list[NewsItem]
    instruments: list = field(default_factory=list)  # list[Instrument] held
    today: date = None
```

`backend/app/services/signals/rules.py`:
```python
from decimal import ROUND_HALF_UP, Decimal

from app.services.market_data.history import avg_volume, fifty_two_week_range, period_return
from app.services.signals import config
from app.services.signals.types import SignalContext, SignalDraft

TWO_DP = Decimal("0.01")


def _round(v: Decimal) -> Decimal:
    return v.quantize(TWO_DP, rounding=ROUND_HALF_UP)


def earnings_upcoming(ctx: SignalContext) -> list[SignalDraft]:
    out = []
    for inst in ctx.instruments:
        ed = ctx.earnings.get(inst.id)
        if ed is None:
            continue
        days = (ed - ctx.today).days
        if days < 0 or days > config.EARNINGS_DAYS:
            continue
        sev = "high" if days <= config.EARNINGS_HIGH_DAYS else "watch"
        when = "today" if days == 0 else f"in {days} day{'s' if days != 1 else ''}"
        out.append(SignalDraft(
            kind="earnings_upcoming", severity=sev, instrument_id=inst.id,
            title=f"{inst.symbol} reports {when}",
            detail=f"Earnings on {ed.isoformat()}",
            data={"date": ed.isoformat(), "days_until": str(days)},
        ))
    return out


def price_move_day(ctx: SignalContext) -> list[SignalDraft]:
    out = []
    for inst in ctx.instruments:
        q = ctx.quotes.get(inst.symbol)
        if q is None or q.previous_close is None or q.previous_close == 0:
            continue
        pct = _round((q.price - q.previous_close) / q.previous_close * 100)
        if abs(pct) < config.DAY_MOVE_PCT:
            continue
        sev = "high" if abs(pct) >= config.DAY_MOVE_HIGH_PCT else "watch"
        arrow = "up" if pct > 0 else "down"
        out.append(SignalDraft(
            kind="price_move_day", severity=sev, instrument_id=inst.id,
            title=f"{inst.symbol} {arrow} {abs(pct)}% today",
            detail=f"Day move {pct}% (last {q.price})",
            data={"pct": str(pct), "close": str(q.price)},
        ))
    return out


def price_move_week(ctx: SignalContext) -> list[SignalDraft]:
    out = []
    for inst in ctx.instruments:
        bars = ctx.bars.get(inst.id) or []
        r = period_return(bars, 5)
        if r is None or abs(r) < config.WEEK_MOVE_PCT:
            continue
        sev = "high" if abs(r) >= config.WEEK_MOVE_HIGH_PCT else "watch"
        arrow = "up" if r > 0 else "down"
        out.append(SignalDraft(
            kind="price_move_week", severity=sev, instrument_id=inst.id,
            title=f"{inst.symbol} {arrow} {abs(r)}% this week",
            detail=f"5-day return {r}%",
            data={"pct": str(r)},
        ))
    return out


def fifty_two_week(ctx: SignalContext) -> list[SignalDraft]:
    out = []
    for inst in ctx.instruments:
        bars = ctx.bars.get(inst.id) or []
        rng = fifty_two_week_range(bars)
        q = ctx.quotes.get(inst.symbol)
        if rng is None or q is None:
            continue
        low, high = rng
        price = q.price
        near_high = high > 0 and (high - price) / high * 100 <= config.FIFTY_TWO_NEAR_PCT
        near_low = low > 0 and (price - low) / low * 100 <= config.FIFTY_TWO_NEAR_PCT
        if price >= high or (near_high and price > 0):
            sev = "high" if price >= high else "watch"
            out.append(SignalDraft(
                kind="fifty_two_week", severity=sev, instrument_id=inst.id,
                title=f"{inst.symbol} near 52-week high",
                detail=f"Price {price} vs 52w high {high}",
                data={"price": str(price), "high": str(high), "low": str(low)},
            ))
        elif price <= low or near_low:
            sev = "high" if price <= low else "watch"
            out.append(SignalDraft(
                kind="fifty_two_week", severity=sev, instrument_id=inst.id,
                title=f"{inst.symbol} near 52-week low",
                detail=f"Price {price} vs 52w low {low}",
                data={"price": str(price), "high": str(high), "low": str(low)},
            ))
    return out


def unusual_volume(ctx: SignalContext) -> list[SignalDraft]:
    out = []
    for inst in ctx.instruments:
        bars = ctx.bars.get(inst.id) or []
        if len(bars) < 2 or bars[-1].volume is None:
            continue
        avg = avg_volume(bars[:-1], 30)
        if avg is None or avg == 0:
            continue
        mult = _round(Decimal(bars[-1].volume) / avg)
        if mult < config.VOLUME_MULT:
            continue
        sev = "high" if mult >= config.VOLUME_HIGH_MULT else "watch"
        out.append(SignalDraft(
            kind="unusual_volume", severity=sev, instrument_id=inst.id,
            title=f"{inst.symbol} volume {mult}x average",
            detail=f"Today {bars[-1].volume:,} vs avg {int(avg):,}",
            data={"mult": str(mult), "volume": str(bars[-1].volume)},
        ))
    return out


def news_recent(ctx: SignalContext) -> list[SignalDraft]:
    out = []
    for inst in ctx.instruments:
        items = ctx.news.get(inst.id) or []
        if not items:
            continue
        top = items[0]
        out.append(SignalDraft(
            kind="news_recent", severity="info", instrument_id=inst.id,
            title=f"{inst.symbol}: {top.title}",
            detail=f"{len(items)} recent headline{'s' if len(items) != 1 else ''}",
            data={"count": str(len(items)), "url": top.url},
        ))
    return out


PER_INSTRUMENT_RULES = [
    earnings_upcoming, price_move_day, price_move_week,
    fifty_two_week, unusual_volume, news_recent,
]
```

- [ ] **Step 3: Run tests + lint, commit**

Run: `cd backend && pytest -v && ruff check .`

```bash
git add -A
git commit -m "feat: signal config, SignalDraft/SignalContext, per-instrument rules

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 6: Portfolio-level rules (concentration, fx_exposure)

**Files:**
- Modify: `backend/app/services/signals/rules.py` (add `concentration`, `fx_exposure`, `PORTFOLIO_RULES`, `ALL_RULES`)
- Test: `backend/tests/test_signal_rules_portfolio.py`

**Interfaces:**
- Consumes: `SignalContext.summary` (a `PortfolioSummary` with `.total_value`, `.currency_exposure`, `.positions` each `.symbol`/`.market_value_base`), `SignalContext.portfolio` (`.base_currency`), `SignalContext.instruments` (for symbol→sector).
- Produces: `rules.concentration(ctx)`, `rules.fx_exposure(ctx)`, `rules.PORTFOLIO_RULES`, `rules.ALL_RULES = PER_INSTRUMENT_RULES + PORTFOLIO_RULES`.

- [ ] **Step 1: Write the failing tests**

`backend/tests/test_signal_rules_portfolio.py`:
```python
from dataclasses import dataclass
from decimal import Decimal

from app.services.signals.rules import concentration, fx_exposure
from app.services.signals.types import SignalContext


@dataclass
class _PV:
    symbol: str
    market_value_base: Decimal | None


@dataclass
class _Summary:
    total_value: Decimal | None
    currency_exposure: dict
    positions: list


class _Inst:
    def __init__(self, id, symbol, sector, currency="USD"):
        self.id, self.symbol, self.sector, self.currency = id, symbol, sector, currency
        self.name = symbol


class _PF:
    base_currency = "GBP"


def _ctx(summary, instruments):
    return SignalContext(
        portfolio=_PF(), summary=summary, quotes={}, bars={}, earnings={}, news={},
        instruments=instruments, today=None,
    )


def test_single_name_concentration_high():
    summary = _Summary(
        total_value=Decimal("1000"),
        currency_exposure={"GBP": Decimal("1000")},
        positions=[_PV("AAPL", Decimal("320")), _PV("MSFT", Decimal("680"))],
    )
    insts = [_Inst(1, "AAPL", "Tech"), _Inst(2, "MSFT", "Tech")]
    out = concentration(_ctx(summary, insts))
    # AAPL 32% -> high single-name; sector Tech 100% -> high sector
    kinds = [(s.kind, s.severity) for s in out]
    assert ("concentration", "high") in kinds


def test_fx_exposure_fires_on_non_base():
    summary = _Summary(
        total_value=Decimal("1000"),
        currency_exposure={"GBP": Decimal("400"), "USD": Decimal("600")},
        positions=[],
    )
    out = fx_exposure(_ctx(summary, []))
    assert out and out[0].severity == "high" and "USD" in out[0].title


def test_no_summary_no_fire():
    assert concentration(_ctx(None, [])) == []
    assert fx_exposure(_ctx(None, [])) == []
```

- [ ] **Step 2: Run to verify fail, implement**

Run: `cd backend && pytest tests/test_signal_rules_portfolio.py -v` → FAIL

Append to `backend/app/services/signals/rules.py`:
```python
def concentration(ctx: SignalContext) -> list[SignalDraft]:
    s = ctx.summary
    if s is None or not s.total_value or s.total_value == 0:
        return []
    total = s.total_value
    sector_by_symbol = {i.symbol: (i.sector or "Unclassified") for i in ctx.instruments}
    out = []
    # single-name
    for pv in s.positions:
        if pv.market_value_base is None:
            continue
        pct = _round(pv.market_value_base / total * 100)
        if pct < config.CONC_NAME_PCT:
            continue
        sev = "high" if pct >= config.CONC_NAME_HIGH_PCT else "watch"
        out.append(SignalDraft(
            kind="concentration", severity=sev, instrument_id=None,
            title=f"{pv.symbol} is {pct}% of portfolio",
            detail=f"Single-name concentration {pct}%",
            data={"symbol": pv.symbol, "pct": str(pct), "scope": "name"},
        ))
    # sector
    sector_totals: dict[str, Decimal] = {}
    for pv in s.positions:
        if pv.market_value_base is None:
            continue
        sec = sector_by_symbol.get(pv.symbol, "Unclassified")
        sector_totals[sec] = sector_totals.get(sec, Decimal("0")) + pv.market_value_base
    for sec, val in sector_totals.items():
        pct = _round(val / total * 100)
        if pct < config.CONC_SECTOR_PCT:
            continue
        sev = "high" if pct >= config.CONC_SECTOR_HIGH_PCT else "watch"
        out.append(SignalDraft(
            kind="concentration", severity=sev, instrument_id=None,
            title=f"{sec} sector is {pct}% of portfolio",
            detail=f"Sector concentration {pct}%",
            data={"sector": sec, "pct": str(pct), "scope": "sector"},
        ))
    return out


def fx_exposure(ctx: SignalContext) -> list[SignalDraft]:
    s = ctx.summary
    if s is None or not s.total_value or s.total_value == 0:
        return []
    base = ctx.portfolio.base_currency
    out = []
    for ccy, val in s.currency_exposure.items():
        if ccy == base:
            continue
        pct = _round(val / s.total_value * 100)
        if pct < config.FX_PCT:
            continue
        sev = "high" if pct >= config.FX_HIGH_PCT else "watch"
        out.append(SignalDraft(
            kind="fx_exposure", severity=sev, instrument_id=None,
            title=f"{pct}% exposure to {ccy}",
            detail=f"Non-base ({base}) currency exposure to {ccy}",
            data={"currency": ccy, "pct": str(pct)},
        ))
    return out


PORTFOLIO_RULES = [concentration, fx_exposure]
ALL_RULES = PER_INSTRUMENT_RULES + PORTFOLIO_RULES
```

- [ ] **Step 3: Run tests + lint, commit**

Run: `cd backend && pytest -v && ruff check .`

```bash
git add -A
git commit -m "feat: portfolio-level signal rules (concentration, fx exposure)

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 7: `SignalEngine.analyze` (orchestration + snapshot replace)

**Files:**
- Create: `backend/app/services/signals/engine.py`
- Test: `backend/tests/test_signal_engine.py`

**Interfaces:**
- Consumes: all services (`QuoteService`, `FxService`, `HistoryService`, `FundamentalsService`, `NewsService`, `value_portfolio`), `ALL_RULES`, `Signal` model, `recent_news`, `get_earnings_dates`.
- Produces:
  - `engine.AnalyzeResult` dataclass: `signals: list[Signal], as_of: datetime, unavailable_inputs: list[str]`
  - `engine.SignalEngine(quotes, fx, history, fundamentals, news, provider)` with `async analyze(db, portfolio) -> AnalyzeResult`. Steps: refresh inputs (each in its own try/except that appends the input name to `unavailable_inputs` on failure), build `SignalContext`, run `ALL_RULES`, convert drafts → `Signal` rows, **delete existing signals for the portfolio then insert** the new set, `flush`, return.
  - `engine.get_engine() -> SignalEngine` singleton (real providers) used by the API; tests build their own with fakes.

- [ ] **Step 1: Write the failing test**

`backend/tests/test_signal_engine.py`:
```python
from datetime import UTC, date, datetime
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.models import Portfolio, Position, Signal, User
from app.services.market_data.base import Quote
from app.services.market_data.fundamentals import FundamentalsService
from app.services.market_data.history import HistoryService
from app.services.market_data.news import NewsService
from app.services.market_data.quotes import QuoteService
from app.services.signals.engine import SignalEngine
from app.services.valuation import FxService

pytestmark = pytest.mark.asyncio(loop_scope="session")


class FakeMarket:
    def __init__(self, quotes=None, earnings=None):
        self._quotes = quotes or {}
        self._earnings = earnings or {}

    async def get_quotes(self, symbols):
        return {s: self._quotes[s] for s in symbols if s in self._quotes}

    async def get_fx_rate(self, base, quote):
        return Decimal("1")

    async def lookup(self, symbol):
        return None

    async def get_history(self, symbol, days=400):
        return []

    async def get_earnings_date(self, symbol):
        return self._earnings.get(symbol)


class FakeNews:
    async def get_news(self, symbol):
        return []


def _q(sym, price, prev):
    return Quote(symbol=sym, price=Decimal(str(price)), currency="USD",
                 previous_close=Decimal(str(prev)), as_of=datetime.now(UTC))


async def _make_pf(db, make_instrument):
    user = User(email="e@test.dev", password_hash="x")
    db.add(user)
    await db.flush()
    inst = await make_instrument("AAPL")
    pf = Portfolio(user_id=user.id, name="P", kind="real", base_currency="USD")
    db.add(pf)
    await db.flush()
    db.add(Position(portfolio_id=pf.id, instrument_id=inst.id,
                    quantity=Decimal("10"), avg_cost=Decimal("100")))
    await db.commit()
    await db.refresh(pf, ["positions"])
    return pf, inst


def _engine(market, news=None):
    news = news or FakeNews()
    qs = QuoteService(market)
    return SignalEngine(
        quotes=qs, fx=FxService(market), history=HistoryService(market),
        fundamentals=FundamentalsService(market), news=NewsService(news), provider=market,
    )


async def test_analyze_produces_and_replaces_snapshot(db_session, make_instrument):
    pf, inst = await _make_pf(db_session, make_instrument)
    market = FakeMarket(quotes={"AAPL": _q("AAPL", 88, 100)},  # -12% day move
                        earnings={"AAPL": date.today()})
    result = await _engine(market).analyze(db_session, pf)
    await db_session.commit()
    kinds = {s.kind for s in result.signals}
    assert "price_move_day" in kinds
    assert "earnings_upcoming" in kinds
    stored = (await db_session.execute(
        select(Signal).where(Signal.portfolio_id == pf.id)
    )).scalars().all()
    assert len(stored) == len(result.signals) > 0

    # re-analyze with calm quote → snapshot replaced (old price_move_day gone)
    calm = FakeMarket(quotes={"AAPL": _q("AAPL", 100, 100)}, earnings={})
    result2 = await _engine(calm).analyze(db_session, pf)
    await db_session.commit()
    assert "price_move_day" not in {s.kind for s in result2.signals}
    stored2 = (await db_session.execute(
        select(Signal).where(Signal.portfolio_id == pf.id)
    )).scalars().all()
    assert len(stored2) == len(result2.signals)


async def test_provider_failure_is_isolated(db_session, make_instrument):
    pf, inst = await _make_pf(db_session, make_instrument)

    class Boom(FakeMarket):
        async def get_history(self, symbol, days=400):
            raise RuntimeError("down")

        async def get_earnings_date(self, symbol):
            raise RuntimeError("down")

    market = Boom(quotes={"AAPL": _q("AAPL", 88, 100)})
    result = await _engine(market).analyze(db_session, pf)
    await db_session.commit()
    # day-move (from quote) still computed; history/earnings reported unavailable
    assert "price_move_day" in {s.kind for s in result.signals}
    assert "history" in result.unavailable_inputs
    assert "earnings" in result.unavailable_inputs
```

- [ ] **Step 2: Run to verify fail, implement**

Run: `cd backend && pytest tests/test_signal_engine.py -v` → FAIL

`backend/app/services/signals/engine.py`:
```python
from dataclasses import dataclass
from datetime import UTC, date, datetime

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import PriceBar, Signal
from app.services.market_data.fundamentals import FundamentalsService, get_earnings_dates
from app.services.market_data.history import HistoryService
from app.services.market_data.news import NewsService, recent_news
from app.services.market_data.quotes import QuoteService
from app.services.signals import config
from app.services.signals.rules import ALL_RULES
from app.services.signals.types import SignalContext
from app.services.valuation import FxService, value_portfolio


@dataclass
class AnalyzeResult:
    signals: list[Signal]
    as_of: datetime
    unavailable_inputs: list[str]


class SignalEngine:
    def __init__(self, quotes: QuoteService, fx: FxService, history: HistoryService,
                 fundamentals: FundamentalsService, news: NewsService, provider):
        self.quotes = quotes
        self.fx = fx
        self.history = history
        self.fundamentals = fundamentals
        self.news = news
        self.provider = provider

    async def analyze(self, db: AsyncSession, portfolio) -> AnalyzeResult:
        now = datetime.now(UTC).replace(tzinfo=None)
        instruments = [p.instrument for p in portfolio.positions]
        unavailable: list[str] = []

        # quotes + valuation (from cached quotes; degrade to whatever QuoteService returns)
        quotes = await self.quotes.get_quotes(db, [i.symbol for i in instruments]) if instruments else {}
        summary = await value_portfolio(db, portfolio, self.quotes, self.fx)

        # history (failure-isolated)
        try:
            await self.history.refresh(db, instruments)
        except Exception:
            unavailable.append("history")
        bars: dict[int, list[PriceBar]] = {}
        for inst in instruments:
            rows = (await db.execute(
                select(PriceBar).where(PriceBar.instrument_id == inst.id)
                .order_by(PriceBar.date.asc())
            )).scalars().all()
            bars[inst.id] = list(rows)

        # earnings (failure-isolated)
        try:
            await self.fundamentals.refresh(db, instruments)
        except Exception:
            unavailable.append("earnings")
        earnings = await get_earnings_dates(db, [i.id for i in instruments]) if instruments else {}

        # news (failure-isolated)
        try:
            await self.news.refresh(db, instruments)
        except Exception:
            unavailable.append("news")
        news_map: dict[int, list] = {}
        for inst in instruments:
            news_map[inst.id] = await recent_news(db, inst.id, config.NEWS_WINDOW)

        ctx = SignalContext(
            portfolio=portfolio, summary=summary, quotes=quotes, bars=bars,
            earnings=earnings, news=news_map, instruments=instruments, today=date.today(),
        )
        drafts = []
        for rule in ALL_RULES:
            drafts.extend(rule(ctx))

        # replace snapshot transactionally
        await db.execute(delete(Signal).where(Signal.portfolio_id == portfolio.id))
        rows = [
            Signal(
                portfolio_id=portfolio.id, instrument_id=d.instrument_id, kind=d.kind,
                severity=d.severity, title=d.title, detail=d.detail, data=d.data,
                computed_at=now,
            )
            for d in drafts
        ]
        db.add_all(rows)
        await db.flush()
        return AnalyzeResult(signals=rows, as_of=now, unavailable_inputs=unavailable)


_engine: "SignalEngine | None" = None


def get_engine() -> "SignalEngine":
    global _engine
    if _engine is None:
        from app.services.market_data.news import YahooRssProvider
        from app.services.market_data.quotes import get_quote_service

        qs = get_quote_service()
        provider = qs.provider
        _engine = SignalEngine(
            quotes=qs, fx=FxService(provider), history=HistoryService(provider),
            fundamentals=FundamentalsService(provider), news=NewsService(YahooRssProvider()),
            provider=provider,
        )
    return _engine
```
Note: `value_portfolio` uses cached quotes via `QuoteService` and never raises on provider failure (Phase 1 guarantee), so it isn't wrapped. Quotes fetch is likewise degrade-safe inside `QuoteService`.

- [ ] **Step 3: Run tests + lint, commit**

Run: `cd backend && pytest -v && ruff check .`

```bash
git add -A
git commit -m "feat: SignalEngine.analyze — input orchestration, failure isolation, snapshot replace

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 8: Signals API — analyze, read, dashboard attention

**Files:**
- Create: `backend/app/api/signals.py`
- Modify: `backend/app/main.py` (include router)
- Modify: `backend/tests/conftest.py` (add `_NullNewsProvider`, extend `_NullProvider` with `get_history`/`get_earnings_date`, default-override `get_analyzer`)
- Test: `backend/tests/test_signals_api.py`

**Interfaces:**
- Consumes: `SignalEngine`, `get_owned_portfolio`, `Signal`, `Portfolio`, `CurrentUser`, `SessionDep`.
- Produces:
  - dependency `signals.get_analyzer() -> SignalEngine` (overridable in tests)
  - `POST /api/portfolios/{id}/analyze` → `{signals: [SignalOut], as_of, unavailable_inputs: [str]}`; 404 on other users' portfolios
  - `GET /api/portfolios/{id}/signals` → `{signals: [SignalOut], computed_at | null}`
  - `GET /api/dashboard/attention` → `{signals: [AttentionSignalOut]}` — all the user's portfolios, severity-ranked (high→watch→info, then computed_at desc)
  - `SignalOut = {id, instrument_id, symbol | null, kind, severity, title, detail, data, computed_at}`; `AttentionSignalOut` adds `portfolio_id, portfolio_name`.

- [ ] **Step 1: Extend conftest with signals-safe defaults**

Append to `backend/tests/conftest.py` (near the existing `_NullProvider`):
```python
class _NullNewsProvider:
    async def get_news(self, symbol):
        return []


def _null_analyzer():
    from app.services.market_data.fundamentals import FundamentalsService
    from app.services.market_data.history import HistoryService
    from app.services.market_data.news import NewsService
    from app.services.market_data.quotes import QuoteService
    from app.services.signals.engine import SignalEngine
    from app.services.valuation import FxService

    provider = _NullProvider()
    qs = QuoteService(provider)
    return SignalEngine(
        quotes=qs, fx=FxService(provider), history=HistoryService(provider),
        fundamentals=FundamentalsService(provider), news=NewsService(_NullNewsProvider()),
        provider=provider,
    )
```
Extend the existing `_NullProvider` class to satisfy the widened Protocol:
```python
    async def get_history(self, symbol, days=400):
        return []

    async def get_earnings_date(self, symbol):
        return None
```
In the `client` fixture, add after the other overrides:
```python
    from app.api.signals import get_analyzer
    app.dependency_overrides[get_analyzer] = _null_analyzer
```

- [ ] **Step 2: Write the failing tests**

`backend/tests/test_signals_api.py`:
```python
from datetime import UTC, date, datetime
from decimal import Decimal

import pytest

from app.api.signals import get_analyzer
from app.models import Portfolio, Position, User
from app.services.market_data.base import Quote

pytestmark = pytest.mark.asyncio(loop_scope="session")


class FakeMarket:
    def __init__(self, quotes):
        self._q = quotes

    async def get_quotes(self, symbols):
        return {s: self._q[s] for s in symbols if s in self._q}

    async def get_fx_rate(self, base, quote):
        return Decimal("1")

    async def lookup(self, symbol):
        return None

    async def get_history(self, symbol, days=400):
        return []

    async def get_earnings_date(self, symbol):
        return date.today()


class FakeNews:
    async def get_news(self, symbol):
        return []


def _analyzer_with(market):
    from app.services.market_data.fundamentals import FundamentalsService
    from app.services.market_data.history import HistoryService
    from app.services.market_data.news import NewsService
    from app.services.market_data.quotes import QuoteService
    from app.services.signals.engine import SignalEngine
    from app.services.valuation import FxService

    qs = QuoteService(market)
    return SignalEngine(qs, FxService(market), HistoryService(market),
                        FundamentalsService(market), NewsService(FakeNews()), market)


async def _seed_portfolio(auth_client, db_session, make_instrument):
    await make_instrument("AAPL")
    pid = (await auth_client.post(
        "/api/portfolios", json={"name": "P", "kind": "real", "base_currency": "USD"}
    )).json()["id"]
    await auth_client.post(
        f"/api/portfolios/{pid}/positions",
        json={"symbol": "AAPL", "quantity": "10", "avg_cost": "100"},
    )
    return pid


async def test_analyze_then_read_and_attention(auth_client, db_session, make_instrument):
    pid = await _seed_portfolio(auth_client, db_session, make_instrument)
    market = FakeMarket({"AAPL": Quote("AAPL", Decimal("88"), "USD", Decimal("100"),
                                       datetime.now(UTC))})
    auth_client.app.dependency_overrides[get_analyzer] = lambda: _analyzer_with(market)

    resp = await auth_client.post(f"/api/portfolios/{pid}/analyze")
    assert resp.status_code == 200
    kinds = {s["kind"] for s in resp.json()["signals"]}
    assert "price_move_day" in kinds and "earnings_upcoming" in kinds

    read = await auth_client.get(f"/api/portfolios/{pid}/signals")
    assert read.status_code == 200
    assert len(read.json()["signals"]) == len(resp.json()["signals"])

    att = await auth_client.get("/api/dashboard/attention")
    assert att.status_code == 200
    sev = [s["severity"] for s in att.json()["signals"]]
    # high sorts before watch/info
    assert sev == sorted(sev, key=lambda x: {"high": 0, "watch": 1, "info": 2}[x])
    assert att.json()["signals"][0]["portfolio_name"] == "P"


async def test_analyze_requires_auth(client):
    assert (await client.post("/api/portfolios/1/analyze")).status_code == 401
    assert (await client.get("/api/dashboard/attention")).status_code == 401


async def test_other_users_portfolio_analyze_is_404(auth_client, client, db_session, make_instrument):
    pid = await _seed_portfolio(auth_client, db_session, make_instrument)
    from app.core.security import hash_password
    other = User(email="other@test.dev", password_hash=hash_password("pw123456"))
    db_session.add(other)
    await db_session.commit()
    await client.post("/api/auth/login", json={"email": "other@test.dev", "password": "pw123456"})
    assert (await client.post(f"/api/portfolios/{pid}/analyze")).status_code == 404
    assert (await client.get(f"/api/portfolios/{pid}/signals")).status_code == 404
```

- [ ] **Step 3: Run to verify fail, implement router**

Run: `cd backend && pytest tests/test_signals_api.py -v` → FAIL

`backend/app/api/signals.py`:
```python
from datetime import datetime

from fastapi import APIRouter, Depends
from sqlalchemy import select

from app.api.deps import CurrentUser, SessionDep
from app.api.portfolios import get_owned_portfolio
from app.models import Instrument, Portfolio, Signal
from app.services.signals.engine import SignalEngine, get_engine

router = APIRouter(prefix="/api", tags=["signals"])

_SEV_ORDER = {"high": 0, "watch": 1, "info": 2}


def get_analyzer() -> SignalEngine:
    return get_engine()


def _sig_out(sig: Signal, symbol: str | None) -> dict:
    return {
        "id": sig.id, "instrument_id": sig.instrument_id, "symbol": symbol,
        "kind": sig.kind, "severity": sig.severity, "title": sig.title,
        "detail": sig.detail, "data": sig.data,
        "computed_at": sig.computed_at.isoformat(),
    }


async def _symbol_map(db, instrument_ids: set[int]) -> dict[int, str]:
    if not instrument_ids:
        return {}
    rows = (await db.execute(
        select(Instrument.id, Instrument.symbol).where(Instrument.id.in_(instrument_ids))
    )).all()
    return {i: s for (i, s) in rows}


@router.post("/portfolios/{portfolio_id}/analyze")
async def analyze(
    portfolio_id: int, db: SessionDep, user: CurrentUser,
    analyzer: SignalEngine = Depends(get_analyzer),
):
    pf = await get_owned_portfolio(db, user, portfolio_id)
    result = await analyzer.analyze(db, pf)
    await db.commit()
    ids = {s.instrument_id for s in result.signals if s.instrument_id is not None}
    symbols = await _symbol_map(db, ids)
    return {
        "signals": [_sig_out(s, symbols.get(s.instrument_id)) for s in result.signals],
        "as_of": result.as_of.isoformat(),
        "unavailable_inputs": result.unavailable_inputs,
    }


@router.get("/portfolios/{portfolio_id}/signals")
async def read_signals(portfolio_id: int, db: SessionDep, user: CurrentUser):
    pf = await get_owned_portfolio(db, user, portfolio_id)
    rows = (await db.execute(
        select(Signal).where(Signal.portfolio_id == pf.id)
    )).scalars().all()
    rows = sorted(rows, key=lambda s: (_SEV_ORDER.get(s.severity, 9),))
    ids = {s.instrument_id for s in rows if s.instrument_id is not None}
    symbols = await _symbol_map(db, ids)
    computed_at = rows[0].computed_at.isoformat() if rows else None
    return {
        "signals": [_sig_out(s, symbols.get(s.instrument_id)) for s in rows],
        "computed_at": computed_at,
    }


@router.get("/dashboard/attention")
async def attention(db: SessionDep, user: CurrentUser):
    rows = (await db.execute(
        select(Signal, Portfolio.name)
        .join(Portfolio, Signal.portfolio_id == Portfolio.id)
        .where(Portfolio.user_id == user.id)
    )).all()
    rows = sorted(
        rows, key=lambda r: (_SEV_ORDER.get(r[0].severity, 9), _neg_ts(r[0].computed_at))
    )
    ids = {s.instrument_id for (s, _) in rows if s.instrument_id is not None}
    symbols = await _symbol_map(db, ids)
    out = []
    for sig, pf_name in rows:
        item = _sig_out(sig, symbols.get(sig.instrument_id))
        item["portfolio_id"] = sig.portfolio_id
        item["portfolio_name"] = pf_name
        out.append(item)
    return {"signals": out}


def _neg_ts(dt: datetime) -> float:
    return -dt.timestamp()
```
In `backend/app/main.py`: `from app.api.signals import router as signals_router` and `app.include_router(signals_router)`.

- [ ] **Step 4: Run all tests + lint, commit**

Run: `cd backend && pytest -v && ruff check .`
Expected: all PASS

```bash
git add -A
git commit -m "feat: signals API — analyze, read, dashboard attention (auth + ownership + severity rank)

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 9: Frontend — attention panel, Run-analysis, per-position badges

**Files:**
- Modify: `frontend/src/lib/types.ts` (signal types)
- Create: `frontend/src/components/AttentionPanel.tsx`
- Create: `frontend/src/components/SignalBadges.tsx`
- Modify: `frontend/src/pages/DashboardPage.tsx` (mount AttentionPanel + Run-analysis)
- Modify: `frontend/src/pages/PortfolioDetailPage.tsx` (Run-analysis + badges)
- Test: `frontend/src/components/AttentionPanel.test.tsx`

**Interfaces:**
- Consumes: `apiFetch`, backend `GET /api/dashboard/attention`, `POST /api/portfolios/{id}/analyze`, `GET /api/portfolios/{id}/signals`.
- Produces: `types.ts` `Signal`, `AttentionSignal`, `AttentionResponse`, `SignalsResponse`, `AnalyzeResponse`; `<AttentionPanel />` (fetches attention, renders severity-ranked rows, empty state); `<SignalBadges signals={...} />` chips.

- [ ] **Step 1: Add types**

Append to `frontend/src/lib/types.ts`:
```ts
export interface Signal {
  id: number;
  instrument_id: number | null;
  symbol: string | null;
  kind: string;
  severity: "info" | "watch" | "high";
  title: string;
  detail: string;
  data: Record<string, string>;
  computed_at: string;
}

export interface AttentionSignal extends Signal {
  portfolio_id: number;
  portfolio_name: string;
}

export interface AttentionResponse {
  signals: AttentionSignal[];
}

export interface SignalsResponse {
  signals: Signal[];
  computed_at: string | null;
}

export interface AnalyzeResponse {
  signals: Signal[];
  as_of: string;
  unavailable_inputs: string[];
}
```

- [ ] **Step 2: Write the failing test**

`frontend/src/components/AttentionPanel.test.tsx`:
```tsx
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import AttentionPanel from "./AttentionPanel";

function renderPanel() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <AttentionPanel />
    </QueryClientProvider>,
  );
}

describe("AttentionPanel", () => {
  it("renders severity-ranked signals", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(
        JSON.stringify({
          signals: [
            { id: 1, instrument_id: 5, symbol: "NVDA", kind: "earnings_upcoming",
              severity: "high", title: "NVDA reports in 2 days", detail: "Earnings on 2026-07-09",
              data: {}, computed_at: "2026-07-07T09:00:00Z",
              portfolio_id: 1, portfolio_name: "Core Growth" },
          ],
        }),
        { status: 200, headers: { "Content-Type": "application/json" } },
      ),
    );
    renderPanel();
    expect(await screen.findByText("NVDA reports in 2 days")).toBeInTheDocument();
    expect(screen.getByText(/Core Growth/)).toBeInTheDocument();
  });

  it("shows empty state when no signals", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(JSON.stringify({ signals: [] }), {
        status: 200, headers: { "Content-Type": "application/json" },
      }),
    );
    renderPanel();
    expect(await screen.findByText(/No flags right now/i)).toBeInTheDocument();
  });
});
```

- [ ] **Step 3: Run to verify fail, implement components**

Run: `cd frontend && npm run test` → FAIL

`frontend/src/components/AttentionPanel.tsx`:
```tsx
import { useQuery } from "@tanstack/react-query";
import { apiFetch } from "../lib/api";
import type { AttentionResponse, AttentionSignal } from "../lib/types";

const DOT: Record<AttentionSignal["severity"], string> = {
  high: "bg-loss",
  watch: "bg-flag",
  info: "bg-muted",
};

export default function AttentionPanel() {
  const q = useQuery({
    queryKey: ["attention"],
    queryFn: () => apiFetch<AttentionResponse>("/api/dashboard/attention"),
  });

  if (q.isPending) return <p className="text-muted">Loading signals…</p>;
  if (q.isError) return <p className="text-loss">Failed to load signals.</p>;
  const signals = q.data.signals;

  return (
    <section className="rounded-xl border border-border bg-surface p-5">
      <h2 className="mb-3 font-medium text-text">Needs your attention</h2>
      {signals.length === 0 ? (
        <p className="text-sm text-muted">No flags right now — run analysis to refresh.</p>
      ) : (
        <ul className="space-y-2">
          {signals.map((s) => (
            <li key={s.id} className="flex items-start gap-3 text-sm">
              <span className={`mt-1 h-2 w-2 shrink-0 rounded-full ${DOT[s.severity]}`} />
              <div>
                <p className="text-text">{s.title}</p>
                <p className="text-xs text-muted">
                  {s.portfolio_name}
                  {s.symbol ? ` · ${s.symbol}` : ""} · {new Date(s.computed_at).toLocaleString()}
                </p>
              </div>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
```

`frontend/src/components/SignalBadges.tsx`:
```tsx
import type { Signal } from "../lib/types";

const TONE: Record<Signal["severity"], string> = {
  high: "bg-[#FEECEC] text-loss",
  watch: "bg-[#FFFBEB] text-flag",
  info: "bg-bg text-muted",
};

function label(s: Signal): string {
  switch (s.kind) {
    case "earnings_upcoming":
      return `earnings ${s.data.days_until ?? ""}d`;
    case "price_move_day":
      return `${s.data.pct ?? ""}% today`;
    case "price_move_week":
      return `${s.data.pct ?? ""}% wk`;
    case "fifty_two_week":
      return s.title.includes("high") ? "52w high" : "52w low";
    case "unusual_volume":
      return `${s.data.mult ?? ""}x vol`;
    case "news_recent":
      return "news";
    default:
      return s.kind;
  }
}

export default function SignalBadges({ signals }: { signals: Signal[] }) {
  if (signals.length === 0) return null;
  return (
    <span className="flex flex-wrap gap-1">
      {signals.map((s) => (
        <span
          key={s.id}
          title={s.title}
          className={`rounded px-1.5 py-0.5 text-[10px] font-medium ${TONE[s.severity]}`}
        >
          {label(s)}
        </span>
      ))}
    </span>
  );
}
```

Wire into `DashboardPage.tsx`: import `AttentionPanel`, replace the existing placeholder attention markup with `<AttentionPanel />`, and add a "Run analysis" button that POSTs analyze for each portfolio then invalidates `["attention"]` + `["dashboard"]`. Minimal mutation:
```tsx
// in DashboardPage, using useMutation + useQueryClient already imported in the app
const runAnalysis = useMutation({
  mutationFn: async () => {
    const dash = await apiFetch<DashboardData>("/api/dashboard");
    await Promise.all(
      dash.portfolios.map((p) =>
        apiFetch(`/api/portfolios/${p.id}/analyze`, { method: "POST" }),
      ),
    );
  },
  onSuccess: () => {
    qc.invalidateQueries({ queryKey: ["attention"] });
    qc.invalidateQueries({ queryKey: ["dashboard"] });
  },
});
```
Render a button: `<button onClick={() => runAnalysis.mutate()} disabled={runAnalysis.isPending} className="rounded-md bg-accent px-4 py-2 text-sm text-white disabled:opacity-50">{runAnalysis.isPending ? "Analyzing…" : "Run analysis"}</button>`.

Wire into `PortfolioDetailPage.tsx`: a per-portfolio "Run analysis" button (POST `/api/portfolios/{id}/analyze`, invalidate `["signals", id]` + `["valuation", id]` + `["attention"]`), a `useQuery(["signals", id], () => apiFetch<SignalsResponse>(\`/api/portfolios/${id}/signals\`))`, and render `<SignalBadges signals={signalsForThatPosition} />` in each position row by grouping the signals response by `instrument_id` (match `position.instrument_id` — note the valuation payload exposes `symbol`; map via the portfolio's positions, or add instrument_id to the signals list which it already carries). Keep the badge cell compact.

- [ ] **Step 4: Run tests + full check, commit**

Run: `cd frontend && npm run test && npm run check`
Expected: PASS; tsc + oxlint + vitest + build clean

```bash
git add -A
git commit -m "feat: dashboard attention panel + Run-analysis + per-position signal badges

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 10: End-to-end smoke, docs, CI green

**Files:**
- Modify: `README.md` (Status), `docs/PROGRESS.md` (Phase 2a summary)
- No new code beyond fixes the smoke reveals.

- [ ] **Step 1: Backend + frontend gates**

Run: `cd backend && source .venv/bin/activate && pytest -v && ruff check .` (all green, no warnings) then `cd ../frontend && npm run test && npm run check` (green).

- [ ] **Step 2: End-to-end curl smoke (real yfinance + RSS, network on)**

Start backend on the real providers (no `.env` needed — seeded defaults):
```bash
cd backend && source .venv/bin/activate && alembic upgrade head && python -m app.seed \
  && nohup uvicorn app.main:app --port 8000 > /tmp/ig-uvicorn.log 2>&1 &
```
Then, with a cookie jar: login → create a GBP portfolio → add `AAPL`, `NVDA`, `HSBA.L`, `0700.HK` → `POST /api/portfolios/{id}/analyze` and confirm the response has real signals (expect at least `concentration`/`fx_exposure` from valuation, plus `earnings_upcoming`/`price_move_*`/`unusual_volume`/`news_recent` as live data warrants) and an `as_of`; sanity-check that a signal's `data` numbers are plausible. Then `GET /api/dashboard/attention` shows them severity-ranked. Paste key JSON excerpts into the report. If yfinance/RSS is unavailable, confirm the endpoint still returns (degraded) with populated `unavailable_inputs` — do not fake success.

- [ ] **Step 3: Offline-degradation check**

Temporarily block network (or point the provider at a bad host) and confirm `analyze` returns 200 with `unavailable_inputs` listing the down feeds and still emits the valuation-derived signals (`concentration`, `fx_exposure`, `price_move_day` if quotes cached). Restore.

- [ ] **Step 4: Docs + push + CI**

Add to `README.md` `## Status`: "Phase 2a (signals engine) complete — deterministic analysis (earnings, price/volume moves, 52-week, concentration, FX exposure, news) with a stored snapshot and live dashboard attention flags. Next: Phase 2b, the Guru (LLM)." Add a `docs/PROGRESS.md` Phase 2a section (endpoints, signal kinds, how to run analysis).

Run the full gates once more, then:
```bash
git add -A
git commit -m "feat: Phase 2a smoke verified + docs; signals engine complete

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
git push
gh run view <id> --repo ashmorel/investment-guru --json status,conclusion  # poll until success (never `gh run watch | tail`)
```
Both backend + frontend CI jobs (incl. the alembic migration-chain step) must be green.

---

## Self-Review Notes

- **Spec coverage:** signals table + caches + migration (Task 1); price history backfill + derived helpers (Task 2); RSS news provider + cache (Task 3); earnings dates + fundamentals cache (Task 4); all 8 signal kinds with the spec's thresholds + severity (Tasks 5–6); analyze pipeline with failure isolation + transactional snapshot replace (Task 7); analyze/read/attention API with auth + ownership + degradation (Task 8); dashboard attention panel + Run-analysis + per-position badges reusing Phase 1 tokens, no Figma gate (Task 9); e2e smoke incl. offline-degradation + docs + CI (Task 10). Deliberately out of 2a per spec: no LLM, no profile, no FX drift, no sector-peer earnings, no news sentiment.
- **Type consistency:** `SignalDraft`/`SignalContext`/`SignalEngine`/`AnalyzeResult` names and fields consistent across Tasks 5–8; `Bar`/`parse_history`/history helpers consistent across Tasks 2/5/7; provider Protocol widened once (Task 2 `get_history`, Task 4 `get_earnings_date`) and `_NullProvider` updated in Task 8 to match; `SignalOut` JSON shape matches the frontend `Signal` type (Task 9); severity ordering `{high:0,watch:1,info:2}` identical in backend (Task 8) and frontend (implicitly, since backend pre-sorts).
- **Known follow-ups (2b or later, intentionally not in 2a):** profile-scaled thresholds; FX drift; sector-peer earnings correlation; news sentiment; the daily scheduler (2b digest). Threshold literals in `test_history.py` (period_return/avg_volume) must be set to values computed exactly against the fixture during Task 2.
