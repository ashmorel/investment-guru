# Investment Guru

Personal portfolio management with an AI adviser (US/UK/HK markets) and HK ORSO fund tracking.
Spec: `docs/superpowers/specs/2026-07-07-investment-guru-design.md`.

## Status
Phase 1 (portfolio core) complete — portfolios/watchlists, Yahoo CSV import, live multi-currency valuation, dashboard.

Phase 2a (signals engine) complete — deterministic analysis (earnings, price/volume moves, 52-week, concentration, FX exposure, news) with a stored snapshot and live dashboard attention flags.

Phase 2b (the Guru) complete — investor profile, provider-agnostic LLM layer (Anthropic first; advice model Opus 4.8, scan model Haiku 4.5, both config-swappable), on-demand portfolio reviews with per-position verdicts, scheduled daily digest with startup catch-up, dashboard "Guru's take" panel, per-position takes, SSE-streamed chat, and per-call usage/cost logging. Signals stay deterministic code; the LLM only judges what the data layer hands it. Without an API key everything else keeps working and Guru surfaces show a "not configured" state.

Phase 4 (ORSO) complete — HK pension environment for the HSBC ORSO (WMFS) scheme: real fund menu (seeded from the scheme's own price feed), manual allocation snapshot with an automatic switch log, HKD fund pricing (live HSBC fund-centre fetcher when `ORSO_HSBC_CLIENT_ID`/`SECRET` are set, manual entry always available), retirement goals with deterministic 2/5/8% projections, and a Guru switching-advice mode restricted to your own fund menu.

Phase 5 (cloud) complete — **live at the Vercel production URL**: Railway runs the backend (Docker, migrations on deploy, always-on daily digest) + Postgres; Vercel serves the frontend and proxies `/api/*` to Railway (single origin, first-party cookies, SSE verified streaming through the rewrite). Deploys are CI-gated on both platforms — merge to green `main` and it ships. Production hardening: secure/lax session cookies, fail-hard on default secrets, login throttling, upload caps, security headers. Operational detail: `docs/deployment.md`. **All five phases of the master spec are complete.** Progress detail: `docs/PROGRESS.md`.

Enhancement 1 (multi-user + encryption + admin) complete — open self-service registration with strict per-user isolation; financially-sensitive data (holdings quantities/costs, ORSO allocations, Guru report payloads, chat, profile free-text, position notes) is **encrypted at rest** with a server-held Fernet key (`DATA_ENCRYPTION_KEY`, distinct from the committed dev key, refused in prod); an email-allowlisted admin role (`ADMIN_EMAILS`) + admin-area shell; a per-user daily LLM budget (429 `budget_exhausted`); and an opt-in daily digest (the scheduler iterates opted-in users). Next: enhancement 2 (multi-provider LLM + admin config panel). See `docs/superpowers/specs/2026-07-09-multiuser-encryption-design.md`.

## Local setup
```bash
docker compose up -d db
cd backend && python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env   # then edit values; add ANTHROPIC_API_KEY=... to enable the Guru
# note: (re)start the backend after adding the key — it is read at startup
alembic upgrade head && python -m app.seed
uvicorn app.main:app --reload
# frontend (from repo root):
cd frontend && npm install && npm run dev
```
