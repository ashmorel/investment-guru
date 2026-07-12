# Investment Guru — Agent Guide

> Resume-anywhere handoff doc for any agent/session (Claude Code, Codex, Cursor, …).
> **Keep this file current: refresh it at the end of any change pushed to production.**

## Status (2026-07-12)

**ALL FIVE MASTER-SPEC PHASES + ENHANCEMENT PROJECTS 1 (multi-user+encryption+admin), 2 (multi-provider LLM + admin config), 3 (dashboard/stock-news UX), 4 (user-defined sector/theme grouping), AND "ORSO data-entry + advice" COMPLETE AND LIVE IN PRODUCTION.**

- **Live app:** https://investment-guru-rose.vercel.app (Vercel frontend → `/api/*` rewrites → Railway backend)
- **Enhancement Project 4 — user-defined sector/theme grouping (2026-07-12, migration 0012):** users split holdings into their own named groups ("Big Tech", "Space") and see live exposure + a forward-building trend. Three additive tables (`app/models/groups.py`): `HoldingGroup(user_id, name, color, sort_order)`, `GroupAssignment` (**one group per instrument** — unique `(user_id, instrument_id)`, `ON DELETE CASCADE` from the group), `GroupSnapshot(user_id, group_id nullable, as_of, value_base **EncryptedDecimal**)` (unique `(user_id, group_id, as_of)` **NULLS NOT DISTINCT** for the Ungrouped bucket). Router `app/api/groups.py` (all user-scoped): CRUD (`GET/POST/PATCH/DELETE /api/groups`, 409 dup name), `PUT /assign` (422 not-held / null clears), `POST /seed-from-sectors` (idempotent, non-destructive, auto-sector→group; null→"Unclassified"), `GET /holdings` (each held instrument + its current group_id/name — powers the preselect), `GET /exposure?portfolio_id=` and `GET /trend?range=30d|90d|1y`. **Exposure aggregates every real portfolio into a single GBP base** (`app/services/groups/exposure.py` — per-portfolio `fx.get_rate(...,"GBP")`, FX/quote failure degrades that holding to `unpriced`, **never 500**; the opportunistic today-snapshot write is best-effort/rollback-safe under the unique-constraint race). Daily `run_group_snapshot_job` (scheduler, `digest_hour:30`, per-user failure-isolated) + startup `snapshot_catch_up` (always runs the idempotent job) build the forward-only trend (no backfill). Frontend `SectorsPage.tsx` + inline-SVG `TrendChart.tsx` (**no chart lib**) at `/sectors` (nav after Portfolios); group colour is `group_id`-keyed so a seeded (colourless) group renders the same swatch across manage/exposure/trend. Live: 0011→0012 clean, endpoints 401 unauth.
- **Enhancement Project 3 — dashboard/stock-news UX (2026-07-12, migration 0011):** a readable news surface from the already-collected `NewsItem` data. New `app/api/news.py`: `GET /api/news` (per-holding groups, headlines de-duped by normalized title via `app/services/market_data/news_read.py`, ranked most-active-first, TTL-refreshed through `NewsService.refresh` — feed failures degrade to cache per-instrument, never 500, `summary_available` flag), `GET /api/news/{symbol}` (fuller list, 404 if not held), `POST /api/news/refresh`. On-demand per-stock **Guru summary**: `GuruService.generate_news_summary` runs on the **scan model** (cheap), budget-gated (429), persists a `GuruReport(kind="news", instrument_id=…)` (encrypted payload `{summary, sentiment, key_points, disclaimer}`), saved + regenerable — `POST`/`GET /api/news/{symbol}/summary` (422 no-headlines). All scoped to instruments the user holds (Position→Portfolio join). Frontend `NewsPanel.tsx` on the dashboard + per-position list. Headlines always render even if a summary fails (separate endpoints).
- Phase 1 portfolio core · 2a signals engine · 2b the Guru (LLM) · 4 ORSO pension · 5 cloud — all shipped, smoke-verified in prod, final whole-branch reviews merge-clean.
- **Enhancement Project 2 — multi-provider LLM + admin config (2026-07-12, migration 0010):** the Guru can run on **Anthropic / OpenAI / Google Gemini**, chosen at runtime from the admin panel. Single-row `llm_config` (`app/models/guru.py`: provider, advice_model, scan_model, `api_key` **EncryptedText**, optional per-role $/1M prices); `load_active_config(db)` (`app/services/guru/config.py`) — a saved row is authoritative, else env fallback (`ANTHROPIC_API_KEY`/`guru_*_model`). Three `LLMProvider` adapters in `app/services/guru/llm/` (Anthropic canonical; `openai.py` + `google.py` translate from Anthropic-shape incl. per-message role → Gemini `Content`, assistant→"model") behind `factory.build_provider`. `get_guru_service(db)` is now **async, config-driven, and rebuildable** — `invalidate_guru_service()` clears the cache on save so the next request uses the new provider/model/key (no redeploy; single replica). Role→model: advice paths use `advice_model`, digest uses `scan_model`; cost via `estimate_cost(model, usage, price=)` (config price → built-in OpenAI/Gemini table → None+logged). Admin API `app/api/admin.py`: `GET/PUT /api/admin/llm-config` (**key never returned** — `key_set` only; omitted key preserved) + `POST /llm-config/test` (minimal live call, error `detail` **scrubbed** of the key). Admin panel in `frontend/src/pages/AdminPage.tsx`. **The `ANTHROPIC_API_KEY` env var is now only the pre-panel fallback — the active provider/key live in the encrypted `llm_config` row.**
- **ORSO data-entry + advice (2026-07-12, migration 0009):** per-fund native `currency` + user-set `orso_display_currency`/`orso_contribution_currency`; multi-currency `build_overview` (each fund → display currency via FxService, per-fund FX-failure → `flags.fx_unavailable`, never 500; legacy `total_hkd`/`value_hkd`/`total_base` kept additive) + projection-in-display-currency + `PUT /orso/display-currency`. Ingest: CSV (`POST /orso/ingest/csv`), statement screenshot via the Guru vision path (`POST /orso/ingest/screenshot`, budget-gated 429/degrade-502, image not persisted), and manual — all produce a read-only `AllocationDraft` (`app/services/orso/ingest.py`) the user reviews, committed by ONE transactional switch-logged `POST /orso/allocation/apply` (`app/services/orso/allocation.py`: create funds + derived `manual` prices `value÷units` + full-replace, all-or-nothing). `GET /orso/funds/search` (own menu, code+normalized-name). Guru ORSO advice enriched with goal-gap (projection shortfall + contribution headroom) → `OrsoAdvicePayload.contribution_suggestion`. Frontend ingest wizard at `/orso/import`. **Domain fact:** the user is on the HSBC **Local Staff DC Scheme**, NOT WMFS — the live WMFS price feed won't cover their funds, so statement-derived/manual prices are primary (feed stays best-effort).
- **Enhancement Project 1 (2026-07-09):** open registration + per-user isolation, encryption at rest (Fernet, `DATA_ENCRYPTION_KEY`), email-allowlist admin role + `/admin` shell, per-user daily LLM budget (429 `budget_exhausted`), opt-in daily digest. Live-smoke verified (encryption proven: prod ciphertext undecryptable with the committed dev key).
- **Enhancement programme (5 projects):** 1 multi-user+encryption ✅ · 2 multi-provider LLM + admin config ✅ · 3 dashboard/news UX ✅ · 4 user-defined sector/theme grouping ✅ · 5 sector-rotation advice. Specs land in `docs/superpowers/specs/` (project-4 `2026-07-12-sector-grouping-design.md`). Specs land in `docs/superpowers/specs/` as each is designed (project-1 `2026-07-09-multiuser-encryption-design.md`, project-2 `2026-07-12-multiprovider-llm-admin-design.md`, project-3 `2026-07-12-dashboard-news-ux-design.md`).
- Full history: `docs/PROGRESS.md`. Specs/plans: `docs/superpowers/{specs,plans}/`. Ops: `docs/deployment.md`.
- Outstanding user step: import real Yahoo CSV; run the first ORSO ingest against a real (redacted-for-repo) HSBC Local Staff DC statement to seed the fund menu/catalogue in prod. A redacted sample is also wanted to add a real-fixture extraction test for `tests/test_orso_vision.py` (currently a synthetic 1×1 PNG). To use OpenAI/Gemini instead of Anthropic, enter the provider + model ids + API key in `/admin` (takes effect immediately).
- Prod fund menu (2026-07-12): the 14 legacy WMFS starter funds are archived; the user's real 18-fund **HSBC LSRBS DC scheme** menu was seeded via `python -m app.seed_orso_lsrbs` (HKD/USD/EUR; risk ratings inferred — to verify against per-fund factsheets).
- Accepted maintenance minors (do NOT re-litigate; fix only if asked): login throttle is signalling-grade (bcrypt+strong password is the real control); an active lockout can be evicted under extreme email spray; Railway origin is directly reachable (all routes auth-gated); `/api/imports/commit` has no body-size cap. Encryption scope hides amounts/analysis/chat/notes but NOT which tickers a user holds (structural instrument FK stays plaintext — a deliberate, approved choice).
- **Enhancement Project 1 post-review fix-forward (2026-07-11):** `positions.notes` now encrypted at rest (migration **0008**, `EncryptedText`); the crypto layer refuses the committed dev key as an at-rest fallback in production (closes the migration-runs-before-boot trap); `DATA_ENCRYPTION_KEY` supports a `new,old` comma-separated list for staged key rotation (encrypt with first, decrypt with any — runbook in `docs/deployment.md`); scheduler `run_daily_job` uses a fresh DB session per user (matches `catch_up`).

## What this is

Personal (single-user) portfolio app: portfolios/watchlists, Yahoo CSV import, live multi-currency
valuation (US/UK/HK), deterministic signals engine, an LLM adviser ("the Guru": reviews, daily
digest + dashboard take, streaming chat), and an HK ORSO pension environment (HSBC WMFS scheme,
fund menu + allocation + 2/5/8% projections + switching advice).

**Core design ethos: facts are code, judgment is LLM.** Signals/valuations/projections are pure,
tested Python; the LLM only receives assembled context and returns schema-validated structured
output (`client.messages.parse`); chat is the only free-text path.

## Stack & layout

- `backend/` — FastAPI + SQLAlchemy 2 async + Alembic (head **0012**) + Postgres; APScheduler in-process
  (single replica = the scheduler). `app/services/{market_data,signals,guru,orso}/` behind provider
  abstractions; `app/api/*` routers. LLM: `app/services/guru/llm/` (Anthropic/OpenAI/Google adapters +
  `factory.build_provider`; FakeLLMProvider for tests). Provider + advice/scan models + key come from the
  `llm_config` row (admin panel) via `load_active_config`, else env fallback; `get_guru_service(db)` is
  config-driven + rebuilt on save (`invalidate_guru_service`).
- `frontend/` — React 18 + Vite + Tailwind v4 + TanStack Query. NO env vars — all API calls are
  relative `/api/...` (dev: vite proxy; prod: Vercel rewrites). SSE chat via `src/lib/sse.ts`.
- CI: `.github/workflows/ci.yml` (backend + frontend jobs). Railway deploys **only on green CI**
  (check-suite gating); Vercel auto-builds on push (root directory `frontend/`).

## Golden rules

- **Public repo — never commit real holdings data or secrets.** Synthetic fixtures only. Never read/modify `.env`.
  (History was rewritten once on 2026-07-09 to scrub leaked HSBC gateway values — pre-rewrite shas are invalid.
  After ANY secret-in-commit incident: `git log -S"<value>" --all` must be clean BEFORE any push.)
- Money/quantity = `Numeric`/`Decimal`, never float. Every user-data table has `user_id`.
- DB change = hand-written chained Alembic migration (`alembic heads` first).
- Async tests: `pytestmark = pytest.mark.asyncio(loop_scope="session")` (module-level only when ALL
  tests are async — `filterwarnings=["error"]` breaks it on sync tests otherwise) + conftest fixtures
  (`client`, `auth_client`, `guru_client`, `orso_client`, `db_session`, `make_instrument`, `fake_llm`).
- Providers are fixture-mocked in tests; endpoints degrade on provider failure, **never 500**.
  LLM errors map to 503 `llm_unconfigured` / 409 `generation_in_progress` / 502 `llm_error` (nothing persisted on failure).
- Frontend tests mock `globalThis.fetch` via `vi.spyOn`; vitest-axe on new UI.
- Anthropic API: `messages[0].role` must be `"user"` — chat history windows trim leading assistant turns.
- Commit to `main`. TDD: failing test → minimal code → commit.

## Verify (run before pushing)

```bash
cd backend && .venv/bin/ruff check . && .venv/bin/pytest -q   # 222+ tests; needs docker compose up -d db (Postgres :5433)
cd frontend && npm run check                                  # tsc + lint + vitest (64+) + build
```

## Workflow conventions

Feature work follows the superpowers pipeline: brainstorm → spec (`docs/superpowers/specs/`) →
plan (`docs/superpowers/plans/`) → subagent-driven TDD tasks with per-task review → live smoke →
final whole-branch review. Task ledger (gitignored scratch): `.superpowers/sdd/progress.md`.
Figma-first for non-trivial UI (file key `0gU58wfjttdZS0NXQeEtuD`, frames 01–08 approved).
**After every push that reaches production: update this file + `docs/PROGRESS.md`.**

## Production (details in docs/deployment.md)

- **Railway** project `investment-guru` (id `15ae32a0-e0dd-4f80-a974-e5858f04aedf`, workspace "Lee Ashmore's Projects"):
  `backend` service (id `144aaaf0…`, repo-connected, root `backend/`) + Postgres. Domain
  `backend-production-c90f.up.railway.app`.
  **Gotchas (already configured, needed again only on rebuild):** Railpack ignores Dockerfiles — service var
  `RAILWAY_DOCKERFILE_PATH=Dockerfile`; pin `PORT=8000` to match the domain target or it 502s; seed runs locally
  against the Postgres service's `DATABASE_PUBLIC_URL` (see runbook); `railway service restart --yes`.
- **Vercel** project `investment-guru` (team `investikid`, `prj_GszbP5YcYTsvXDdWD9v4RMh0r5jP`), root `frontend/`,
  git-connected. Rewrites in `frontend/vercel.json`. SSE verified streaming through the rewrite (unbuffered).
- Secrets live ONLY in the Railway dashboard (`ANTHROPIC_API_KEY`, `INITIAL_USER_EMAIL/PASSWORD`, `SECRET_KEY`,
  optional `ORSO_HSBC_CLIENT_ID/SECRET` — browser-public gateway values from the HSBC fund-centre page devtools).
- Scheduler: daily digest→take 07:00 Europe/London + idempotent startup catch-up. Prod DB holds the real user only.
- **WARNING:** the remote Railway MCP connector is authed to a DIFFERENT account — do not use it for this repo.
  Use the `railway` CLI with explicit `--project/--environment/--service` ids ALWAYS (a careless linked-context
  `railway add` once landed a service in the wrong project). An orphan empty `investment-guru` project
  (id `3e6ea2cb…`) may exist on the MCP's account.

## Likely next work (none committed)

Maintenance minors above · custom domain · automated DB backups · Vercel↔Railway shared-secret header ·
live HSBC price fetch needs the two `ORSO_HSBC_*` values set · new features = new brainstorm→spec→plan cycle.
