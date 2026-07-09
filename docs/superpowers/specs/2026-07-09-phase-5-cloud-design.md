# Phase 5 — Cloud deployment — Design Spec

**Date:** 2026-07-09 · **Status:** Approved pending user spec review
**Parent spec:** `2026-07-07-investment-guru-design.md` §8 phase 5 ("Cloud — Railway + Vercel, always-on digest/alerting")
**Depends on:** Phases 1–4, all live on `main` (`82591f8` lineage).

## 1. Summary

Deploy the backend (FastAPI + Postgres + in-process APScheduler) to Railway and
the frontend (Vite/React) to Vercel, with the browser talking to a single
origin via Vercel rewrites. Pushes to `main` auto-deploy only on green CI. The
daily digest becomes always-on. The security items deferred from Phase 1 ship
behind a production flag. No feature code, no UI changes, no Figma pass.

**Decisions locked during brainstorm (2026-07-09):**
- Vercel rewrites proxy `/api/*` → Railway (first-party cookies, no CORS, no
  `VITE_*` env baking — the frontend already uses only relative paths).
- Auto-deploy on green CI (Railway check-suite gating; Vercel auto-build).
- Production DB starts clean: migrate + seed with real credentials; the user
  imports real data through the UI as final acceptance.

## 2. Railway backend

- **Build:** new `backend/Dockerfile` — `python:3.12-slim`, install
  `.[dev]`-free prod deps (`pip install .`), non-root user, `EXPOSE 8000`.
- **Start command:** `alembic upgrade head && uvicorn app.main:app --host 0.0.0.0
  --port $PORT --proxy-headers --forwarded-allow-ips '*'` (migrations on every
  deploy; proxy headers so scheme/IP detection works behind Railway's proxy).
- **Topology:** single replica — the in-process APScheduler is the sole
  scheduler instance; existing per-day idempotent catch-up absorbs deploy
  restarts. Healthcheck: existing `GET /api/health`.
- **Postgres:** Railway Postgres service; `DATABASE_URL` via service reference.
  Backend must accept `postgres://`-style URLs (normalise to
  `postgresql+asyncpg://` in config if needed).
- **Deploy gating:** Railway "wait for CI" — deploys `main` only when the
  GitHub check suite is green (both existing jobs).
- **Env vars (documented in the runbook):** `DATABASE_URL`, `SECRET_KEY`
  (generated, ≥32 chars), `ANTHROPIC_API_KEY`, `INITIAL_USER_EMAIL`,
  `INITIAL_USER_PASSWORD`, `ENV=production`, optional `ORSO_HSBC_CLIENT_ID`/
  `ORSO_HSBC_CLIENT_SECRET`, optional `GURU_DIGEST_HOUR`/`GURU_TIMEZONE`
  (defaults 7 / Europe/London).
- **Seed:** one-off `railway run python -m app.seed` during the deployment
  smoke (idempotent; refuses default creds in production per §4).

## 3. Vercel frontend

- Project root `frontend/`, framework Vite, auto-deploy on push to `main`.
- New `frontend/vercel.json`:
  `{"rewrites": [{"source": "/api/:path*", "destination": "https://<railway-backend-domain>/api/:path*"}]}`
  plus SPA fallback `{"source": "/((?!api/).*)", "destination": "/index.html"}`
  so client-side routes deep-link correctly.
- No frontend code changes; no `VITE_*` variables. SSE chat must be verified
  through the rewrite in the smoke (streaming responses pass through Vercel
  rewrites; confirm no buffering breaks token-by-token delivery — if the proxy
  buffers, fallback decision is documented in the runbook: point only the SSE
  endpoint at the Railway domain via an absolute URL constant, cookie still
  sent because SameSite=Lax + top-level GET is not involved — this fallback
  requires CORS for exactly one route and is a contingency, not the plan).

## 4. Production hardening (Phase-1 deferred items)

New `Settings.env: str = "dev"`; `is_production = env == "production"`. All
gated behaviour is off in dev/tests, except the security headers, which are
harmless and therefore always on.

| Item | Behaviour in production |
|---|---|
| Session cookie | `secure=True`, `samesite="lax"` (same-origin via proxy), `httponly` (already) |
| Secret key | Startup **fails hard** (raise at `create_app`) if `SECRET_KEY` is the dev default or shorter than 32 chars |
| Seed credentials | `app/seed.py` **refuses** to create/keep `change-me` or `you@example.com` defaults when `is_production` |
| Login throttling | In-memory per-email backoff: 5 consecutive failures → 60s lockout (HTTP 429 `too_many_attempts`); reset on success; single-process state (single replica) |
| Upload cap | CSV import request body capped at 2 MB → 413 `upload_too_large` |
| Security headers | Middleware adding `X-Content-Type-Options: nosniff`, `X-Frame-Options: DENY`, `Referrer-Policy: same-origin` (all responses; harmless in dev, enabled always) |

All hardening is unit-tested (production flag toggled via settings override in
tests; throttling tested with a fake clock or monkeypatched time).

## 5. CI/CD flow

`git push main` → GitHub Actions (existing backend + frontend jobs) →
green: Railway builds/deploys backend (Docker), Vercel builds/deploys frontend
→ red: nothing deploys. Rollback = redeploy the previous build from either
dashboard. CI itself is unchanged.

## 6. Ops & runbook (`docs/deployment.md`)

Env-var table (names, purpose, how to generate/obtain), first-deploy sequence,
seed step, rollback procedure, DB backup (`railway` CLI pg-dump one-liner +
recommendation to run before risky migrations), scheduler notes (single
replica; digest hour/timezone), key rotation, and the SSE-through-rewrite
verification result (§3).

## 7. Live smoke (acceptance)

1. Backend deployed on green CI; migrations applied; seed with real creds.
2. From the public Vercel URL: login (verify cookie has `Secure`/`SameSite=Lax`),
   wrong-password ×5 → 429, CSV >2 MB → 413.
3. Generate digest → take → review in prod; chat turn streams token-by-token
   through the rewrite; ORSO manual price + switching advice.
4. Scheduler: confirm catch-up/daily behaviour from logs or report timestamps.
5. User acceptance: import real Yahoo CSV + enter real ORSO allocation.
6. Update README/PROGRESS; final whole-branch review (Opus).

## 8. Error handling / failure posture

Deploy-time migration failure → deploy fails, previous build keeps serving.
Provider failures unchanged (degrade, never 500). Scheduler failures logged,
recovered by next catch-up. Throttling and caps return structured 429/413.
No new alerting beyond Railway/Vercel dashboards (YAGNI for single user).

## 9. Out of scope

Custom domain (layerable later); staging environment; Redis/multi-replica
scaling; automated DB backups; monitoring/alerting stack; CORS (except the
documented SSE contingency); mobile/native packaging.

## 10. Build order (for the implementation plan)

1. Hardening (flag, cookies, fail-hard, throttle, cap, headers — all TDD) →
2. Dockerfile + config URL-normalisation + local container boot check →
3. `vercel.json` + deployment runbook →
4. Provision Railway + Vercel (operator steps driven via CLIs/MCP, user holds
   the accounts) → 5. Live smoke §7 → docs + final whole-branch review (Opus).
