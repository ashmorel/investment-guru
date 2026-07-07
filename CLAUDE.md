# Investment Guru

Personal investment app: portfolios/watchlists, Yahoo CSV import, market signals, AI adviser (the Guru), HK ORSO tracking. Spec + plans live in `docs/superpowers/`.

## Golden rules
- **Public repo: NEVER commit real holdings data** — synthetic fixtures only. Never read/modify `.env`.
- Money/quantity = `Numeric`, never float. Every user-data table has `user_id`.
- DB change = hand-written chained Alembic migration (check `alembic heads` first).
- Async tests: `pytestmark = pytest.mark.asyncio(loop_scope="session")` + shared fixtures from `conftest.py`.
- Providers (yfinance etc.) are fixture-mocked in tests; endpoints degrade on provider failure, never 500.
- TDD: failing test → minimal code → commit. Verify with `ruff check . && pytest` (backend), `npm run check` (frontend).
- Local Postgres: `docker compose up -d db` (port 5433; DBs `guru` + `guru_test`).
