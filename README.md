# Investment Guru

Personal portfolio management with an AI adviser (US/UK/HK markets) and HK ORSO fund tracking.
Spec: `docs/superpowers/specs/2026-07-07-investment-guru-design.md`.

## Status
Phase 1 (portfolio core) complete — portfolios/watchlists, Yahoo CSV import, live multi-currency valuation, dashboard.

Phase 2a (signals engine) complete — deterministic analysis (earnings, price/volume moves, 52-week, concentration, FX exposure, news) with a stored snapshot and live dashboard attention flags.

Phase 2b (the Guru) complete — investor profile, provider-agnostic LLM layer (Anthropic first; advice model Opus 4.8, scan model Haiku 4.5, both config-swappable), on-demand portfolio reviews with per-position verdicts, scheduled daily digest with startup catch-up, dashboard "Guru's take" panel, per-position takes, SSE-streamed chat, and per-call usage/cost logging. Signals stay deterministic code; the LLM only judges what the data layer hands it. Without an API key everything else keeps working and Guru surfaces show a "not configured" state. Next: Phase 4 (ORSO). Progress detail: `docs/PROGRESS.md`.

## Local setup
```bash
docker compose up -d db
cd backend && python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env   # then edit values; add ANTHROPIC_API_KEY=... to enable the Guru
alembic upgrade head && python -m app.seed
uvicorn app.main:app --reload --factory  # (app.main:create_app)
# frontend (from repo root):
cd frontend && npm install && npm run dev
```
