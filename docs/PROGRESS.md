# Progress

_Last updated: 2026-07-07 (Task 15 — Phase 1 complete)._

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
Profile, the Guru (Phase 2), signals + price history charts (Phase 3), digest, ORSO (Phase 4). `price_bars` table exists but is unpopulated until Phase 3.

### Post-review hardening (2026-07-07)
Four fix-commits from the final whole-branch review: valuation integrity flags (costed_positions/day_change_partial/currency_mismatch guard + zero-cost-basis fix); position uniqueness (migration 0003 + 409), symbol normalisation at all API boundaries, and request-owned transaction boundaries; test conftest default null provider + CI now exercises the full alembic upgrade/downgrade chain; import-wizard error rendering + docs/repo cleanup.

## How to run locally
```bash
docker compose up -d db                      # Postgres on :5433
cd backend && source .venv/bin/activate
alembic upgrade head && python -m app.seed   # seeds you@example.com / change-me
uvicorn app.main:app --reload --port 8000
# in another shell:
cd frontend && npm install && npm run dev    # Vite dev server, proxies /api
```
Checks: `cd backend && pytest -v && ruff check .` · `cd frontend && npm run check`.
