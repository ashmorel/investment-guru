# Investment Guru — Agent Guide

> Resume-anywhere handoff doc for any agent/session (Claude Code, Codex, Cursor, …).
> **Keep this file current: refresh it at the end of any change pushed to production.**

## Status (2026-07-09)

**ALL FIVE MASTER-SPEC PHASES COMPLETE AND LIVE IN PRODUCTION.**

- **Live app:** https://investment-guru-rose.vercel.app (Vercel frontend → `/api/*` rewrites → Railway backend)
- Phase 1 portfolio core · 2a signals engine · 2b the Guru (LLM) · 4 ORSO pension · 5 cloud — all shipped, smoke-verified in prod, final whole-branch reviews merge-clean.
- Full history: `docs/PROGRESS.md`. Specs/plans: `docs/superpowers/{specs,plans}/`. Ops: `docs/deployment.md`.
- Only outstanding user step: import real Yahoo CSV + enter real ORSO allocation in the live UI.
- Accepted maintenance minors (do NOT re-litigate; fix only if asked): login throttle is signalling-grade (bcrypt+strong password is the real control); an active lockout can be evicted under extreme email spray; Railway origin is directly reachable (all routes auth-gated); `/api/imports/commit` has no body-size cap.

## What this is

Personal (single-user) portfolio app: portfolios/watchlists, Yahoo CSV import, live multi-currency
valuation (US/UK/HK), deterministic signals engine, an LLM adviser ("the Guru": reviews, daily
digest + dashboard take, streaming chat), and an HK ORSO pension environment (HSBC WMFS scheme,
fund menu + allocation + 2/5/8% projections + switching advice).

**Core design ethos: facts are code, judgment is LLM.** Signals/valuations/projections are pure,
tested Python; the LLM only receives assembled context and returns schema-validated structured
output (`client.messages.parse`); chat is the only free-text path.

## Stack & layout

- `backend/` — FastAPI + SQLAlchemy 2 async + Alembic (head **0006**) + Postgres; APScheduler in-process
  (single replica = the scheduler). `app/services/{market_data,signals,guru,orso}/` behind provider
  abstractions; `app/api/*` routers. LLM: `app/services/guru/llm/` (AnthropicProvider; FakeLLMProvider for tests).
  Models config: advice `claude-opus-4-8`, scan `claude-haiku-4-5` (swap via env).
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
