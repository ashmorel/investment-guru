# Investment Guru

Personal portfolio management with an AI adviser (US/UK/HK markets) and HK ORSO fund tracking.
Spec: `docs/superpowers/specs/2026-07-07-investment-guru-design.md`.

## Local setup
```bash
docker compose up -d db
cd backend && python3.12 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env   # then edit values
alembic upgrade head && python -m app.seed
uvicorn app.main:app --reload --factory  # (app.main:create_app)
# frontend (from repo root, once Task 12 lands):
cd frontend && npm install && npm run dev
```
