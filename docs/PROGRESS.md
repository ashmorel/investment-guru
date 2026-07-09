# Progress

_Last updated: 2026-07-08 (Phase 2a — signals engine complete)._

## Phase 1 — portfolio core: COMPLETE

### Backend (FastAPI + Postgres)
- **Auth-lite**: `POST /api/auth/login` (session cookie), `POST /api/auth/logout`, `GET /api/auth/me`; single seeded user (`python -m app.seed`).
- **Portfolios**: `GET/POST /api/portfolios`, `PATCH/DELETE /api/portfolios/{id}` — real + watchlist kinds, 3-letter base currency.
- **Positions**: `GET/POST /api/portfolios/{id}/positions`, `PATCH/DELETE /api/positions/{id}`.
- **Instruments**: `GET /api/instruments/lookup?symbol=` — live Yahoo Finance lookup (US/UK/HK markets inferred).
- **Valuation**: `GET /api/portfolios/{id}/valuation` — live quotes with cache, FX conversion to base currency, GBp pence→GBP normalisation, per-position and total P&L, day change, currency exposure; degrades to null prices (never crashes) when a quote is unavailable.
- **CSV import**: `POST /api/imports/preview` (multipart Yahoo export parse) and `POST /api/imports/commit` (into existing or new portfolio; merge strategies update/skip/replace; all-or-nothing).
- **Dashboard**: `GET /api/dashboard` — per-portfolio total value, day change, P&L % and an `as_of` stamp.

### Frontend (React + Vite + Tailwind tokens + TanStack Query)
- Login page; authenticated shell with sidebar nav (Guru/ORSO slots reserved for later phases).
- **Dashboard** (`/`) — portfolio cards (name, kind, total value, day change, P&L %), as-of stamp, empty state linking to create/import, and a "Guru's take" placeholder panel (Phase 2 slot).
- **Portfolios** (`/portfolios`) — list + create; **detail** (`/portfolios/:id`) — positions with live valuation.
- **Import wizard** (`/import`) — upload → preview → assign to new/existing portfolio → commit.

### Not yet (later phases, per spec §8)
Profile, the Guru (Phase 2b, LLM), price history charts, digest scheduler, ORSO (Phase 4). Signals shipped in Phase 2a (below).

### Post-review hardening (2026-07-07)
Four fix-commits from the final whole-branch review: valuation integrity flags (costed_positions/day_change_partial/currency_mismatch guard + zero-cost-basis fix); position uniqueness (migration 0003 + 409), symbol normalisation at all API boundaries, and request-owned transaction boundaries; test conftest default null provider + CI now exercises the full alembic upgrade/downgrade chain; import-wizard error rendering + docs/repo cleanup.

## Phase 2a — signals engine: COMPLETE

A deterministic, no-LLM analysis pass that scores a portfolio against market facts, stores a timestamped snapshot, and lights up the dashboard "Needs your attention" panel. Every rule is a pure function of a `SignalContext`; provider/feed failures are isolated (an endpoint never 500s on a down feed) and reported in `unavailable_inputs`.

### Endpoints
- **`POST /api/portfolios/{id}/analyze`** — force-refreshes quotes, gathers market inputs (failure-isolated), runs the rules, and transactionally **replaces** the portfolio's signal snapshot (one `computed_at`). Returns `{ signals, as_of, unavailable_inputs }`.
- **`GET /api/portfolios/{id}/signals`** — reads the stored snapshot (`{ signals, computed_at }`).
- **`GET /api/dashboard/attention`** — all of the user's signals across portfolios, severity-ranked (`high` → `watch` → `info`), each tagged with its `portfolio_id`/`portfolio_name`.

All three require auth and 404 on another user's portfolio.

### Signal kinds (8)
Per-instrument: `earnings_upcoming`, `price_move_day`, `price_move_week`, `fifty_two_week`, `unusual_volume`, `news_recent`. Portfolio-level: `concentration` (single-name + sector) and `fx_exposure` (non-base currency). Thresholds/severity live in `app/services/signals/config.py`.

### Market-data inputs (behind provider abstractions, fixture-tested parsers)
Daily price-bar history (backfill + derived helpers: period return, 52-week range, average volume — all non-finite/NaN bars dropped at ingest and at the engine loader), next-earnings dates + fundamentals cache, and an RSS news provider + cache. yfinance/RSS is never called in tests.

### Frontend
Dashboard **"Needs your attention"** panel (`<AttentionPanel />`, severity-ranked with an empty state) + a **"Run analysis"** action; per-position **signal badges** (`<SignalBadges />`) on the portfolio detail page, matched to positions by symbol. Reuses Phase 1 Tailwind tokens.

### How to run an analysis
```bash
# with the backend running and logged in (cookie jar $J):
curl -s -b $J -X POST http://localhost:8000/api/portfolios/{id}/analyze   # writes the snapshot
curl -s -b $J http://localhost:8000/api/dashboard/attention               # severity-ranked flags
```
Or click **Run analysis** on the dashboard / a portfolio page.

### Verified end-to-end (2026-07-08)
Live smoke against real yfinance + RSS: login → GBP portfolio → AAPL/HSBA.L/0700.HK → `analyze` returned `price_move_week`, `concentration` (name + sector), and `fx_exposure` signals with plausible figures; the attention endpoint returned them severity-ranked. The RSS news feed was down during the run and degraded correctly (HTTP 200, `unavailable_inputs: ["news"]`, other signals still emitted). Backend 91 tests + ruff clean; frontend 11 tests + `npm run check` clean.

## Phase 2b — the Guru: COMPLETE

The judgment layer on top of the Phase 2a signals engine. A provider-agnostic LLM layer (`app/services/guru/llm/`, Anthropic first) receives *profile + valuations + stored signals* assembled by a shared `ContextBuilder` and returns schema-validated structured output; chat is the only free-text path. Signals stay deterministic code — the LLM never fetches data itself. No API key → Guru endpoints return `503 llm_unconfigured`, the UI shows a "not configured" banner, and everything else keeps working.

### Models & config (`app/core/config.py`)
`guru_advice_model` (default `claude-opus-4-8`: reviews, Guru's take, chat) and `guru_scan_model` (default `claude-haiku-4-5`: daily digest); `anthropic_api_key`; `guru_digest_hour` + `guru_timezone` for the scheduler. Model swaps are config edits.

### Endpoints (all `/api/guru/*`, auth + ownership)
- **Profile** — `GET/PUT /profile` (risk appetite, horizon, sector interests, free text; upsert).
- **Reviews** — `POST /reviews {portfolio_id}` (per-position verdict hold/increase/reduce/exit + conviction + rationale, portfolio observations, watch-next; a post-parse check forces coverage of every position with one corrective retry), `GET /reviews[?portfolio_id=]`, `GET /reviews/{id}`. Versioned history = rows.
- **Digest** — `GET /digest/latest`, `POST /digest` (scan model: earnings this week, movers, news flags, summary).
- **Guru's take** — `GET /take/latest`, `POST /take` (advice model; sees the latest digest; commentary, risks, rebalance ideas).
- **Chat** — `GET/POST /chat/threads`, `GET /chat/threads/{id}`, `POST /chat/threads/{id}/messages` → **SSE stream** (`delta`/`done`/`error` frames); user message persists immediately, assistant message only on stream completion. "Discuss" links seed threads from take ideas.
- **Usage** — `GET /usage/summary` (per-mode calls/tokens/estimated cost + 30-day total; every LLM call writes an `llm_usage` row).

Errors map to `503 llm_unconfigured` / `409 generation_in_progress` (per-kind in-flight lock) / `502 llm_error` (nothing persisted on failure).

### Scheduler (APScheduler, FastAPI lifespan)
Daily job at `guru_digest_hour` in `guru_timezone`: digest → take. **Startup catch-up**: on boot, generates whatever is missing for "today" (digest+take, or just a missing take after a partial failure); never raises — no key or provider failure logs and moves on. Phase 5 (always-on cloud) inherits this unchanged.

### Frontend
- **Guru page** — Guru's-take card, daily-digest card, portfolio reviews (run-review per portfolio, history, per-position `VerdictChip`s + observations), chat panel (thread pills, optimistic bubbles, token-by-token streaming via `src/lib/sse.ts`, thread-switch-safe).
- **Dashboard** — `GuruTakePanel` fills the reserved slot (refresh, staleness label, discuss links, unconfigured banner).
- **Portfolio detail** — per-position take column derived from the latest review (no extra LLM call) with an ask-in-chat link.
- **Settings** — investor profile form (segmented risk control, horizon, sector chips, free text) + usage/cost readout.
Screens match the approved Figma mocks (file `0gU58wfjttdZS0NXQeEtuD`, frames 05–07).

### Verified end-to-end (2026-07-09)
Live smoke with a real Anthropic key: boot ran the startup catch-up and generated a real Haiku digest + Opus 4.8 take (grounded in actual valuations — flagged the 79% AAPL concentration and watchlist-only holdings correctly); portfolio review covered all 3 positions with verdicts; chat streamed 15 SSE delta frames and persisted both turns; a backend restart correctly did **not** regenerate (catch-up idempotent per day); `usage/summary` showed all four modes totalling ≈$0.11. Browser pass over the Vite dev server confirmed the Guru page, dashboard take panel and chat render with the live data. Backend 145 tests + ruff clean; frontend 50 tests + `npm run check` clean.

## How to run locally
```bash
docker compose up -d db                      # Postgres on :5433
cd backend && source .venv/bin/activate
alembic upgrade head && python -m app.seed   # seeds you@example.com / change-me
uvicorn app.main:app --reload --port 8000    # ANTHROPIC_API_KEY in backend env file enables the Guru
# in another shell:
cd frontend && npm install && npm run dev    # Vite dev server, proxies /api
```
Checks: `cd backend && pytest -v && ruff check .` · `cd frontend && npm run check`.
