# Dashboard / Stock-News UX Restructure — Design Spec

_Date: 2026-07-12 · Enhancement Project 3 of Investment Guru._

## Goal

Turn the buried, one-line stock-news signal into a readable news experience:
per-holding **headlines** (organized, de-duplicated, linked) on a dashboard
**News panel** and each position's detail page, plus an **on-demand Guru
summary** per stock (saved, regenerable, budget-gated) with a sentiment tag.

## Context & constraints (what exists today)

- **`NewsItem`** (`app/models/market.py`): `instrument_id`, `title(500)`,
  `source(100)`, `url(1000)`, `published_at`, `fetched_at`; unique
  `(instrument_id, url)`.
- **`NewsService`** (`app/services/market_data/news.py`): `YahooRssProvider`
  fetches Yahoo Finance RSS per symbol; `refresh(db, instruments)` upserts
  `NewsItem` with a 6h `NEWS_TTL`; `recent_news(db, instrument_id, within)`
  returns recent items. **Not exposed via any API today.**
- News currently surfaces **only** as the `news_recent` signal
  (`app/services/signals/rules.py`) — a single line "SYMBOL: <top headline> · N
  recent headlines" mixed into the dashboard "Needs your attention"
  (`AttentionPanel.tsx`). No link, no list, no grouping, no summary.
- **`GuruReport`** (`app/models/guru.py`): `user_id`, `kind` (review|digest|
  take|orso), `portfolio_id` nullable, `payload` (`EncryptedJSON`), `model`,
  `created_at`. The Guru layer (`app/services/guru/`) generates schema-validated
  structured output through the admin-selected provider (Project 2), governed by
  the per-user **daily budget** (`check_budget` → 429 `budget_exhausted`), with
  errors mapped `LLMNotConfigured`→503 / `GenerationInProgress`→409 /
  `LLMError`→502 (via `map_guru_errors`); endpoints **degrade, never 500**.
- Frontend: React 18 + Vite + Tailwind + TanStack Query; dashboard at `/`
  (portfolio cards + `AttentionPanel`); per-position `PortfolioDetailPage`.
  Alembic head **0010**.

**Golden rules that apply:** money = `Decimal`; every user-data table has
`user_id` and every route 404s on another user's data; providers fixture-mocked
in tests, endpoints degrade-never-500; LLM output schema-validated + encrypted
at rest + budget-gated; DB change = one hand-written chained migration;
Figma-first for non-trivial UI.

## Decisions (resolved during brainstorming)

1. **Depth = both:** organized raw headlines by default (free, instant) + an
   **on-demand** per-stock Guru summary (budget-gated).
2. **Placement:** a dashboard **News panel** (grouped by holding) **and** a
   fuller news list on each position's detail page.
3. **Summaries are saved + regenerable** (persisted per stock, "generated X
   ago", Regenerate button) — stored as a `GuruReport`.
4. **Freshness:** news reads **auto-refresh** a holding's RSS if its cache is
   older than the 6h TTL, plus a manual **Refresh** button.
5. **Storage = extend `GuruReport`** (Approach A): a nullable `instrument_id` FK
   + `kind="news"`, reusing the existing encryption/serializer/budget plumbing.

---

## Section 1 — Data model (migration 0011)

- **`NewsItem` reused unchanged.**
- **`GuruReport.instrument_id`** — new nullable `ForeignKey("instruments.id")`
  (indexed). `kind` gains the value `"news"` (still `String(8)`). The news
  summary `payload` (existing `EncryptedJSON` column) holds
  `{ "summary": str, "sentiment": "positive"|"negative"|"neutral"|"watch",
  "key_points": [str], "disclaimer": str }`.
- Migration **0011** (chained on 0010): `add_column guru_reports.instrument_id`
  (nullable FK + index). Additive, reversible.

New pydantic schema `NewsSummaryPayload` in `app/services/guru/schemas.py`:
`summary: str`, `sentiment: Literal["positive","negative","neutral","watch"]`,
`key_points: list[str]`, `disclaimer: str`.

## Section 2 — News read API

New router `app/api/news.py` (prefix `/api/news`), all under auth, scoped to the
user's own instruments (instruments referenced by the user's positions across
real + watchlist portfolios).

**Helpers** (`app/services/market_data/news.py` or a new `news_read.py`):
- `dedupe(items) -> list[NewsItem]` — drop near-duplicates by **normalized
  title** (lowercased, collapsed whitespace, stripped punctuation); keep the
  earliest-published of a duplicate set; return newest-first.
- `rank_holdings(groups)` — order instruments by recent-headline **count desc**,
  then newest `published_at` desc.
- `ensure_fresh(db, instruments, news_service)` — for each instrument whose
  newest `fetched_at` is older than `NEWS_TTL` (6h), call `news_service.refresh`
  for just those; per-instrument failure is non-fatal (keep stale cache, collect
  the symbol into `unavailable`). Never raises.

**Endpoints:**
- **`GET /api/news`** → `{ groups: [ NewsGroup ], unavailable: [str], as_of }`
  where `NewsGroup = { symbol, name, latest_published_at, items: [NewsItemOut],
  summary_available: bool }`. Runs `ensure_fresh` first (TTL-gated), then dedupes
  + caps each stock's items (top 8), ranks holdings. `NewsItemOut =
  {title, source, url, published_at}`. `summary_available` = a `kind="news"`
  `GuruReport` exists for that instrument.
- **`GET /api/news/{symbol}`** → the fuller per-stock list `{ symbol, name,
  items: [NewsItemOut], as_of }` (cap ~30); **404** if the user doesn't
  hold/watch `symbol`. Also TTL-refreshes that one instrument.
- **`POST /api/news/refresh`** → forces `news_service.refresh` for all the user's
  instruments; returns `{ refreshed: [str], unavailable: [str] }`. Never 500 on
  feed failure.

## Section 3 — On-demand per-stock summary

In `GuruService` (`app/services/guru/service.py`), add `generate_news_summary`
mirroring the existing generate paths: `check_budget` → assemble the stock's
recent headlines (titles + sources + dates) into the prompt → `generate_structured`
with `NewsSummaryPayload` on the **scan model** (`self.scan_model` +
`self.scan_price` — cheap summarization, not deep advice) → persist
`GuruReport(user_id, kind="news", instrument_id, payload, model, created_at)` →
`record_usage(mode="news", ...)`. Concurrency-guarded like the others
(`GenerationInProgress` per kind).

**Endpoints** (in `app/api/news.py`, under `map_guru_errors`):
- **`POST /api/news/{symbol}/summary`** — generate or **regenerate**; **422** if
  the stock has no headlines to summarize; **404** if not held. Budget-gated
  (→429), degrade-never-500 (LLM failures → 502/503; headlines unaffected).
- **`GET /api/news/{symbol}/summary`** — the latest stored `kind="news"` report
  for that instrument as `ReportOut` (with `created_at`); **404** if none yet.

## Section 4 — Frontend

- **Dashboard "News" panel** (`frontend/src/components/NewsPanel.tsx`, added to
  `DashboardPage`, separate from `AttentionPanel`): per-holding cards ranked
  most-active first; each shows symbol + name, its de-duped headlines
  (source · relative time · external-link icon opening `url` in a new tab), a
  panel-level **Refresh** button + "fetched X ago", and graceful stale /
  `unavailable` states. Each card has a **Summarize** button (or **Regenerate**
  when `summary_available`) that reveals the Guru summary with a color-coded
  **sentiment tag** (positive=gain / negative=loss / watch=flag / neutral=muted)
  + key points; budget-exhausted (429) surfaces the existing
  `isBudgetExhausted()` message.
- **Per-position detail** (`PortfolioDetailPage`): a fuller news list for that
  stock (`GET /api/news/{symbol}`) + the same summarize/summary block.
- The existing `news_recent` **signal stays** on the attention panel (a
  one-line "there's news" nudge; distinct from the reading surface).
- New API client fns in `lib/api.ts` (`getNews`, `getStockNews`,
  `refreshNews`, `getNewsSummary`, `generateNewsSummary`) + `lib/types.ts`.
  `vitest-axe` on the new panel + detail additions.

## Section 5 — Error handling, testing, rollout

**Error handling**
- News reads degrade to cache on any RSS/feed failure (per-instrument
  `unavailable`), never 500. Headlines always render even when summaries fail.
- Summary generation: `check_budget` → 429; `LLMNotConfigured`→503;
  `LLMError`→502; `GenerationInProgress`→409; nothing persisted on failure.
- Summary payload schema-validated + encrypted at rest (`EncryptedJSON`).
- Cross-user: a user can only read news / summarize instruments they hold.

**Testing**
- Unit: `dedupe` (normalized-title collision), `rank_holdings`, `ensure_fresh`
  (TTL-gated refresh + per-instrument failure isolation).
- API: `GET /api/news` grouping/ranking/`summary_available`; `GET /api/news/{symbol}`
  404 for un-held; `POST /refresh` degrade; summary generate/regenerate/latest;
  budget 429; no-headlines 422; cross-user 404. `FakeLLMProvider` for summaries.
- Frontend: `vitest-axe` on `NewsPanel` + detail; fetch mocked via `vi.spyOn`;
  Summarize→summary render; Regenerate; budget-exhausted state.

**Figma gate (standing rule):** the dashboard News panel (headline cards +
summary block + sentiment tag) and the per-position news list get a Figma pass
for user approval **before** the frontend build.

**Build order (rough):**
1. Migration 0011 (`GuruReport.instrument_id` + `NewsSummaryPayload` schema).
2. News read helpers (`dedupe`/`rank_holdings`/`ensure_fresh`) + `GET /api/news`,
   `GET /api/news/{symbol}`, `POST /api/news/refresh`.
3. `generate_news_summary` + `POST`/`GET /api/news/{symbol}/summary`.
4. Figma gate (USER GATE).
5. Frontend News panel + per-position list + summarize (push seam).
6. Docs + live smoke + final Opus review.

## Out of scope

- Cross-holding "what matters today" digest (per-stock summaries only).
- Sentiment/impact scoring beyond the single LLM tag (no numeric model).
- News sources beyond the existing Yahoo Finance RSS.
- Real-time push / websockets (TTL-gated refresh + manual button only).
- Full-article fetching or storage (headlines + links only).
