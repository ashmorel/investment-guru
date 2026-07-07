# Investment Guru — Phase 2a: Signals Engine — Design Spec

**Date:** 2026-07-07
**Status:** Approved (brainstorm complete; implementation plan next)
**Repo:** `investment-guru` (Phase 1 complete, HEAD `d8e1dac`; Postgres 16 on port 5433)
**Supersedes/extends:** the parent design `2026-07-07-investment-guru-design.md` §4.1 (signals), §4.4 (attention flags), §7 (testing)

## 1. Context & scope decision

Phase 2 ("the Guru") is large enough to decompose. It is split into two independent design→plan→build cycles:

- **Phase 2a (this spec) — the signals engine.** A deterministic subsystem, no LLM, testable without an Anthropic API key. It produces the stored, timestamped "facts" the Guru will later reason over, and it lights up the dashboard's "Needs your attention" panel (a Phase 1 placeholder) on its own.
- **Phase 2b (later) — the Guru.** Investor profile, config-driven LLM layer (Opus 4.8 advice model + Haiku scan model), portfolio-review reports, chat, daily digest (+ local APScheduler), dashboard "Guru's take", per-position take. Safety posture: strong persona guardrails + a persistent "educational — not regulated financial advice" note (no separate content-moderation pass). Gets its own spec.

**Scoping decisions settled during brainstorm (2026-07-07):**

| Question | Decision |
|---|---|
| Signals engine timing | Pulled forward from Phase 3 into Phase 2 so the Guru is fully informed |
| Daily digest | On-demand + local APScheduler (Phase 2b); always-on delivery waits for Phase 5 cloud |
| Safety posture | Guardrails + disclaimer (Phase 2b); no moderation pass |
| Decomposition | 2a (signals) → 2b (Guru), each its own cycle |
| 2a stack depth | Backend signals engine **+** live dashboard attention flags + "Run analysis" refresh + per-position badges. No new Figma gate (reuses the approved Phase 1 design language). |
| News/data budget | Free sources only (yfinance + RSS), behind provider abstractions (inherited from Phase 1) |

**Out of 2a scope (deliberate lines):** any LLM call; the investor profile (2b) — so 2a signals use fixed/config thresholds, never profile-scaled; FX *drift* (rate-moved, needs ≥2 days `fx_rates` history) — 2a computes static FX *exposure* only; sector-peer earnings correlation (a Guru interpretation, 2b); news sentiment scoring (2b). Per-position deterministic badges are in 2a; the richer per-position "Guru take" is 2b.

## 2. Architecture

Chosen approach (over compute-on-read and a monolithic analyze function): **a registry of small, pure signal rules + a stored, timestamped snapshot.** Each rule is an isolated pure function `(positions + market facts) → zero-or-more Signal records`, independently unit-testable with recorded fixtures. A `SignalEngine` runs all rules for a portfolio and writes a fresh snapshot to the `signals` table stamped with one run time. This matches the parent spec's "signals are code, never hallucinated" principle and gives 2b's Guru stored, timestamped facts to reason over; each rule fails in isolation.

### 2.1 Data model (migration `0004`, chained on head `0003`)

- **`signals`** — id; portfolio_id (FK → portfolios, indexed); instrument_id (FK → instruments, **nullable** — null for portfolio-level signals like concentration/fx_exposure); kind (str); severity (`"info"|"watch"|"high"`); title (short str, e.g. "NVDA reports in 3 days"); detail (one-line str); data (JSONB — raw numbers behind the signal, e.g. `{"pct": -6.1, "close": 188.22}`); computed_at (datetime). Money/quantity values inside `data` are stored as JSON strings/numbers; any dedicated numeric columns added later use `Numeric` (project rule). Each `analyze` run **replaces** the portfolio's signal set transactionally (delete-all-for-portfolio then insert) so the table always holds the latest consistent snapshot.
- **`instrument_fundamentals`** — instrument_id (PK, FK → instruments); next_earnings_date (Date, nullable); fetched_at (datetime). TTL'd daily (like quotes).
- **`news_items`** — id; instrument_id (FK → instruments, **nullable** for market-level); title (str); source (str); url (str); published_at (datetime, nullable); fetched_at (datetime). Unique constraint on (instrument_id, url) to dedupe across refreshes.
- **`price_bars`** — already exists (Phase 1, migration `0002`) but is unpopulated. 2a backfills it; no schema change.

All new user-reachable data is scoped through the portfolio's `user_id` (ownership rule). Public repo → fixtures are synthetic only.

### 2.2 Module structure

- `backend/app/models/signals.py` — `Signal`; `backend/app/models/market.py` (existing) gains `InstrumentFundamentals`, `NewsItem` (or a new `news.py` — implementer's call at plan time, kept consistent with existing model file conventions).
- `backend/app/services/market_data/news.py` — `NewsProvider` Protocol + `YahooRssProvider` (per-ticker + market RSS). Pure `parse_rss(data: bytes) -> list[NewsItemDTO]` isolated for fixture testing (no network). Normalised fields: title, source, url, published_at, tickers.
- `backend/app/services/market_data/history.py` — `HistoryService`: `refresh(db, symbols)` backfills/updates `price_bars` from `YahooProvider.history` (cached, daily TTL); pure derived helpers `period_return(bars, days)`, `fifty_two_week_range(bars)`, `avg_volume(bars, days)`.
- Earnings-date fetch added to the market-data provider layer with a pure parser + fixture test; cached in `instrument_fundamentals`.
- `backend/app/services/signals/config.py` — threshold constants (below).
- `backend/app/services/signals/rules.py` — one pure function per signal kind (§3). Signature shape: `def rule_x(ctx: SignalContext) -> list[Signal]`, where `SignalContext` bundles the portfolio, positions/instruments, quotes, FX, price-bar helpers, fundamentals, and news for the held symbols.
- `backend/app/services/signals/engine.py` — `SignalEngine.analyze(db, portfolio) -> AnalyzeResult` (signals + `as_of` + list of unavailable inputs); orchestrates input refresh (failure-isolated), rule execution, transactional snapshot replace.
- `backend/app/api/signals.py` — analyze + per-portfolio read; dashboard aggregation extends `backend/app/api/valuation.py`'s dashboard area.
- Frontend: dashboard attention panel + "Run analysis" action + per-position badges (§5).

## 3. Signal kinds, thresholds & severity

Defaults live in `services/signals/config.py` (tunable; 2b may later scale them by risk profile). No profile dependency in 2a. Every signal stores raw numbers in `data`.

| Kind | Fires when | Severity |
|---|---|---|
| `earnings_upcoming` | Held instrument reports within **7 days** | ≤2 days → high; else watch |
| `price_move_day` | \|day change %\| ≥ **5%** | ≥10% → high; else watch |
| `price_move_week` | \|5-trading-day return %\| ≥ **10%** | ≥20% → high; else watch |
| `fifty_two_week` | New 52-week high/low, or **within 2%** of it | new high/low → high; near → watch |
| `unusual_volume` | Today's volume ≥ **2×** 30-day average | ≥3× → high; else watch |
| `concentration` | Single position ≥ **20%** of portfolio value, **or** a sector ≥ **40%** | ≥30% / ≥55% → high; else watch |
| `fx_exposure` | A single non-base currency ≥ **30%** of portfolio value | ≥50% → high; else watch |
| `news_recent` | Held ticker has ≥1 fresh headline in the last **48h** — **one signal per instrument**, headlines listed in `data` | always info (no sentiment — 2b) |

- `concentration` and `fx_exposure` are portfolio-level (instrument_id null); the rest are per-instrument. `concentration` computes both single-name and sector proportions from valued positions (reusing Phase 1 valuation currency-normalised values).
- Rules that need history (`price_move_week`, `fifty_two_week`, `unusual_volume`) simply don't fire when history is unavailable/insufficient — no error.
- GBp/pence and multi-currency handled by reusing Phase 1's `normalise()` + valuation values, so proportions and moves are consistent with the rest of the app.

## 4. Analyze flow, API & degradation

**`POST /api/portfolios/{id}/analyze`** (auth + ownership-scoped; 404 for another user's portfolio):
1. Gather inputs for the portfolio's held symbols, **each failure-isolated**: history refresh (HistoryService), next-earnings dates (fundamentals cache), recent news (NewsProvider). Quotes/FX come from the existing Phase 1 `QuoteService`/`FxService`. A failed input is logged and skipped; its dependent rules don't fire.
2. Run every registered rule over positions + gathered facts.
3. Replace the portfolio's signal snapshot transactionally; stamp `computed_at`.
4. Return `{signals: [...], as_of, unavailable_inputs: ["news", ...]}`.

**Reads (no recompute):**
- `GET /api/portfolios/{id}/signals` → stored snapshot for one portfolio.
- `GET /api/dashboard/attention` → signals across **all** the user's portfolios, severity-ranked (high → watch → info, then most-recent), each carrying portfolio + symbol. Backs the dashboard "Needs your attention" panel.

**Cost/rate:** yfinance history = one call per symbol per refresh, cached in `price_bars` with daily TTL (same-day re-analyze is near network-free). News RSS = one fetch per held ticker, short TTL cache. No LLM, no API key.

**Degradation (parent spec §7):** providers never crash an endpoint. Worst case (all upstream down), `analyze` still returns signals computable from cached quotes/valuation (`concentration`, `fx_exposure`, `price_move_day`) and reports the rest in `unavailable_inputs`. Endpoints never 500 on provider failure.

## 5. Frontend

Reuses the approved Phase 1 design language and tokens — **no new Figma gate**.

- **Dashboard "Needs your attention" panel** binds to `GET /api/dashboard/attention`: real signals, severity-ranked; each row = severity dot (high=loss/red, watch=flag/amber, info=muted) + `portfolio · symbol` + one-line title + relative timestamp. Empty state: "No flags right now — run analysis to refresh."
- **"Run analysis" action** — button on the dashboard (analyze all portfolios) and on the portfolio detail page (analyze that one). Calls `analyze`, then invalidates the signals + dashboard React Query keys; shows an "as of" stamp and any `unavailable_inputs` notice.
- **Per-position signal badges** on the portfolio table — small deterministic chips derived from that instrument's signals (e.g. "earnings 3d", "−6% today", "52w high"). No LLM. The richer per-position "Guru take" is 2b.

## 6. Testing & error handling

Per Phase 1 conventions (TDD; ruff + pytest with `loop_scope="session"` + shared fixtures; real Postgres in CI; frontend tsc + oxlint + vitest):

- Each rule function unit-tested with crafted `SignalContext` inputs (fires / doesn't fire / severity boundaries).
- `parse_rss`, the earnings parser, and any history parser get **recorded-fixture suites** with synthetic data so upstream format drift fails in CI, not at runtime.
- `analyze` endpoint tested with **fake providers** (network never hit in tests — same override pattern as Phase 1's `_NullProvider`/`get_provider`); degradation tested (provider raises → partial snapshot + `unavailable_inputs`, no 500); cross-user ownership 404 tested from the start.
- Frontend: vitest for the attention panel (severity ranking, empty state) and per-position badges.
- New Alembic migration `0004` chained on `0003`; the CI migration-chain step (added in Phase 1 hardening) exercises upgrade→downgrade→upgrade.
- Money/quantity stay `Decimal`/`Numeric`; no floats in stored signal numerics.

## 7. Build shape

Subagent-driven, task-by-task with review between tasks (same harness as Phase 1). Rough task arc (final granularity set by the implementation plan):

1. Models + migration `0004` (`Signal`, `InstrumentFundamentals`, `NewsItem`)
2. `HistoryService` (price_bars backfill + derived helpers) + fixtures
3. `NewsProvider` + `parse_rss` + fixtures
4. Earnings-date fetch + fundamentals cache + fixtures
5. `SignalContext` + signal rules (grouped) — pure, unit-tested
6. `SignalEngine.analyze` (input orchestration, failure isolation, snapshot replace)
7. Analyze + per-portfolio read API + dashboard `attention` aggregation (+ ownership/degradation tests)
8. Frontend: dashboard attention panel + "Run analysis" refresh + per-position badges
9. End-to-end smoke (real yfinance/RSS, offline-degradation check) + docs (README/PROGRESS) + CI green

Each task TDD'd, reviewed, committed; push + CI-green gate as in Phase 1.
