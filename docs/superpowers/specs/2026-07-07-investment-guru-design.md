# Investment Guru — Design Spec

**Date:** 2026-07-07
**Status:** Approved (brainstorm complete; implementation plan next)
**Repo:** `investment-guru` (standalone; deliberately separate from InvestiKid)

## 1. Purpose

A personal investment-management application for actively managing stock positions across the US, UK and HK markets. It maintains real portfolios and watchlists, imports Yahoo Finance CSV exports, surfaces market events and indicators that warrant attention, and provides advice (hold / increase / reduce / exit, with rationale) from an AI adviser — "the Guru" — calibrated to the user's stated risk appetite, horizon and sector interests. A separate environment tracks HK ORSO pension funds (HSBC/Hang Seng scheme) and advises on fund-switching strategy against retirement goals.

## 2. Scoping decisions (settled during brainstorm)

| Question | Decision |
|---|---|
| Audience | Built for one user now, with multi-user foundations (users table, `user_id` FKs, per-user isolation) so productising later is additive, not a rewrite |
| Deployment | Local-first now (Mac, Docker Postgres); designed for a later Railway (backend) + Vercel (frontend) cloud move |
| Data budget | Free sources only to start (yfinance, RSS news) behind provider abstractions so paid feeds can slot in later |
| LLM | Provider-agnostic layer; Anthropic/Claude as the default first implementation |
| Monitoring | On-demand analysis + a scheduled daily digest (runs while the app is up locally; always-on comes with cloud) |
| Investor profile | Structured form + free-text nuance |
| Guru UX | Structured reports AND a chat panel |
| ORSO provider | HSBC / Hang Seng; automated price fetch attempted, manual entry as permanent fallback |
| Build order | Portfolio core → Guru → Monitoring → ORSO → Cloud |
| Architecture | FastAPI + Postgres + React (Approach A; chosen over Next.js full-stack and SQLite-lightweight) |

## 3. Architecture

Monorepo:

```
investment-guru/
  backend/    FastAPI (async), SQLAlchemy 2 async, Alembic, Postgres 16 (docker-compose locally)
  frontend/   React + Vite + TypeScript + Tailwind + shadcn/ui, React Query
  docs/       specs, plans (docs/superpowers/), project docs
```

- **Auth (phase 1):** single account, email + password, session cookie. No signup/verification flow yet; all tables carry `user_id` from day one.
- **Scheduling:** APScheduler inside the FastAPI process (daily digest). Replaced by platform cron on cloud move.
- **Privacy:** repo is public (user's choice, 2026-07-07) — therefore **no real holdings data is ever committed**: test fixtures, seed data and screenshots use synthetic portfolios only; real data lives only in the local DB. Never read or commit `.env`; `.env.example` documents variables.

### 3.1 Data model (core)

- `users` — id, email, password_hash, created_at
- `portfolios` — user_id, name, kind (`real` | `watchlist`), base_currency
- `positions` — portfolio_id, instrument_id, quantity (nullable), avg_cost (nullable), currency, notes; watchlist entries are positions without quantity/cost
- `instruments` — symbol, name, exchange, market (`US`|`UK`|`HK`), sector, industry, native currency (cached metadata)
- `price_bars` — instrument_id, date, OHLCV daily closes (cache for charts, P&L, signals)
- `fx_rates` — pair, date, rate (GBP/USD/HKD crosses)
- `investor_profile` — user_id, risk_tolerance (scale), horizon, objective (income/growth/balanced), sector_interests, max_position_pct, free_text
- `signals` — instrument_id/portfolio_id, kind, value, computed_at (deterministic engine output)
- `analysis_reports` — user_id, portfolio_id, kind (`review` | `digest` | `orso`), content (markdown), model_used, portfolio_snapshot (JSON), created_at — versioned, comparable over time
- `chat_threads` / `chat_messages` — Guru conversations with context references
- ORSO cluster (phase 4): `orso_funds` (available menu: code, name, asset class, risk rating), `orso_allocations` (units per fund, contribution split), `orso_fund_prices`, retirement goals on profile

**Deferred deliberately:** buy/sell transaction ledger with realised P&L. Phase 1 is snapshot positions with average cost (matches the Yahoo CSV). A ledger can be layered on later without schema breakage.

### 3.2 Market data & news layer

Two provider interfaces, free implementations first:

- `MarketDataProvider` → `YahooProvider` (yfinance): quotes, daily history, fundamentals snapshot, earnings dates, FX. All reads flow through a Postgres cache with TTLs (quotes ~15 min in market hours; fundamentals daily). App works on stale cache with an "as of …" stamp; provider failure degrades, never crashes.
- `NewsProvider` → Yahoo per-ticker RSS + market-level RSS. Normalised output: title, source, timestamp, url, tickers. Finnhub/Marketaux free tiers can be added behind the same interface.

yfinance is unofficial and periodically breaks: mitigations are the abstraction, the cache, and recorded-fixture tests (§7) so upstream drift fails in CI, not at runtime.

### 3.3 CSV import (Yahoo Finance portfolio export)

Three-step wizard:
1. **Upload & parse** — pandas; recognises Yahoo export columns (Symbol, Current Price, Purchase Price, Quantity, …); a manual column-mapping step handles header drift.
2. **Preview & assign** — parsed rows in a table; choose target portfolio (existing or new; real or watchlist); rows with quantity/cost become holdings, rows without become watchlist entries; symbols validated against the market data provider before commit.
3. **Merge rules** — per import, choose how clashes with existing symbols resolve: update quantity/avg-cost, skip, or replace.

Manual position CRUD with symbol search/autocomplete exists alongside import.

## 4. The Guru

### 4.1 Signals are code; narrative is LLM

A deterministic Python signals engine computes facts: earnings within N days (stock + sector peers held), price moves beyond day/week thresholds, 52-week high/low breaches, unusual volume, portfolio concentration, FX drift on non-base holdings, matched news headlines. Signals are stored, unit-testable and never hallucinated. The LLM receives *signals + portfolio + investor profile* and produces the judgment layer. LLM down → signal flags still work. Signal wrong → testable bug, not a prompt mystery.

### 4.2 LLM layer

`LLMProvider` interface with structured-output support; `AnthropicProvider` first. Config-driven lineup: **scan model** (cheap; digest) and **advice model** (premium; reviews and chat). Provider/models swap via config.

### 4.3 Persona

One versioned system prompt: world-class investment adviser, deep US/UK/HK expertise; measured, evidence-first; states conviction levels; always explains *why*; relates every recommendation to the stated risk profile and flags ideas outside it. Every output carries a brief "not regulated financial advice" note.

### 4.4 Output modes

- **Portfolio review (on-demand):** per-position verdict (hold/increase/reduce/exit + conviction + rationale), portfolio-level observations (concentration, currency exposure, profile fit), "what to watch next". Stored versioned.
- **Daily digest (scheduled):** morning scan → earnings this week, overnight movers, flagged news, one-line Guru commentary per flag. Cheap model, short output.
- **Chat:** threads with profile + selected portfolio snapshot + latest signals injected as context.

## 5. ORSO environment (Phase 4 — shape fixed now, detailed design later)

Distinct nav area, never mixed with trading portfolios. Holds: the available **fund menu** (HSBC/Hang Seng ORSO funds: code, name, asset class, risk rating), **current allocation** (units per fund + contribution split), and **retirement goals** (target age, target pot, contribution rate). Pricing via an HSBC HK fund-centre price fetcher behind the provider abstraction, with manual price entry as the permanent fallback. Guru gains an ORSO advice mode: switching strategy among *the user's available funds only*, against goals and horizon, same rationale-and-conviction format. Full ORSO design gets its own brainstorm at that phase.

## 6. UI

Modern, clean, data-dense-but-calm. Left nav: Dashboard / Portfolios / Guru / ORSO / Settings.
- **Dashboard:** total value, day change, currency exposure, attention flags.
- **Portfolios:** table with inline editing; position detail drawer (chart, fundamentals, news, position-specific Guru take).
- **Guru:** report reader (reviews + digests, versioned history) and chat panel.
- **Settings:** investor profile (structured + free text), data/LLM provider config.

Per the user's Figma-first rule: design tokens + key screens (Dashboard, Portfolio, Guru report, Chat) are mocked in Figma for approval before frontend build, as the first step of the frontend work.

## 7. Testing & error handling

- TDD throughout (failing test → minimal code → commit). Backend: ruff + pytest (async, session loop-scope, real Postgres in CI). Frontend: tsc + eslint + vitest (+ RTL). GitHub Actions CI on push.
- Providers mocked in unit tests; **recorded-fixture suites** for the yfinance and HSBC parsers so upstream format drift shows up as failing fixtures in CI.
- Provider failures degrade to stale cache + UI banner. LLM failures leave deterministic signals/flags intact. CSV import validates before commit; nothing partial is written.

## 8. Phasing

1. **Foundations + portfolio core** — repo, CI, auth-lite, models, portfolio/position CRUD, CSV import wizard, live quotes + FX + P&L, dashboard
2. **The Guru** — investor profile, LLM layer, portfolio review reports, chat
3. **Monitoring** — signals engine, daily digest, attention flags
4. **ORSO environment** — fund menu, allocations, HSBC pricing, switching advice
5. **Cloud** — Railway + Vercel, always-on digest/alerting

Each phase is independently usable; each gets its own implementation plan.
