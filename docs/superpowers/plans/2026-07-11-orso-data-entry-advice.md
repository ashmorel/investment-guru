# ORSO Data-Entry + Advice Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let the user ingest and maintain their real HSBC ORSO holdings by CSV, statement-screenshot (vision), or manual entry — with per-fund currency, a searchable fund menu, and Guru commentary oriented around closing the gap to their retirement goal.

**Architecture:** All ingest paths (CSV/screenshot/manual) converge on one read-only parse → `AllocationDraft` → user review → single transactional `apply` that reuses the existing switch-logged allocation-replace. Per-fund native currency is added to `OrsoFund`; the overview converts each fund to a user-set display currency via the existing `FxService`. Statement-derived prices (`value ÷ units`) are the primary pricing mechanism (the live HSBC feed is WMFS-only and won't cover the Local Staff DC Scheme). Vision reuses the existing `LLMProvider.generate_structured` seam with an Anthropic image block — no LLM-layer change.

**Tech Stack:** FastAPI + SQLAlchemy 2 async + Alembic (head **0008** → new **0009**) + Postgres; Anthropic via the existing Guru LLM layer; React 18 + Vite + Tailwind v4 + TanStack Query.

## Global Constraints

- Public repo — **never commit real holdings data or secrets**; synthetic/redacted fixtures only. Never read/modify `.env`.
- Money/quantity = `Decimal`, never float. Every user-data table has `user_id`; every ORSO route 404s on another user's data.
- DB change = one hand-written chained Alembic migration; run `alembic heads` first (must be `0008`). New head is `0009`.
- Providers are fixture-mocked in tests; endpoints **degrade, never 500** on provider/FX/parse failure.
- LLM calls go through the per-user daily budget (`check_budget` → 429 `budget_exhausted`) and record usage (`record_usage`).
- Encrypted columns stay encrypted (`units`, `contribution_pct`, switch-log state). `currency` codes are **non-sensitive plaintext**.
- Async tests: `pytestmark = pytest.mark.asyncio(loop_scope="session")` + conftest fixtures (`client`, `auth_client`, `orso_client`, `guru_client`, `db_session`, `fake_llm`, `make_instrument`). Postgres on :5433 via `docker compose up -d db`.
- **Backend payload changes must stay additive** through Task 9 (frontend catches up last): keep existing overview keys (`total_hkd`, `total_base`, per-fund `value_hkd`) populated so the live app keeps working between pushes; add new display-currency keys alongside.
- Backend verify: `cd backend && .venv/bin/ruff check . && .venv/bin/pytest -q`. Frontend verify: `cd frontend && npm run check`.
- Commit to `main`; end commit messages with `Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>`.

---

## File Structure

**Backend**
- `backend/alembic/versions/0009_orso_currency.py` — CREATE: migration (adds `orso_funds.currency`, `investor_profiles.orso_display_currency`, `investor_profiles.orso_contribution_currency`).
- `backend/app/models/orso.py` — MODIFY: `OrsoFund.currency` + validator.
- `backend/app/models/guru.py` — MODIFY: `InvestorProfile.orso_display_currency`, `.orso_contribution_currency`.
- `backend/app/api/orso.py` — MODIFY: currency in fund schemas; multi-currency `build_overview`; `PUT /display-currency`; `GET /funds/search`; ingest + apply endpoints.
- `backend/app/services/orso/ingest.py` — CREATE: `AllocationDraft`/`DraftRow` models, CSV parser, fund matching, implied-price derivation.
- `backend/app/services/orso/vision.py` — CREATE: statement-image extraction → `AllocationDraft` via `generate_structured`.
- `backend/app/services/orso/allocation.py` — CREATE: shared transactional allocation-apply (used by `PUT /allocation` and `POST /allocation/apply`).
- `backend/app/services/orso/context.py` — MODIFY: goal-gap enrichment (projection gap, contribution headroom, per-fund risk).
- `backend/app/services/guru/schemas.py` — MODIFY: `OrsoAdvicePayload.contribution_suggestion`; CREATE `OrsoStatementExtraction`.
- `backend/app/services/guru/service.py` — MODIFY: `_ORSO_INSTRUCTION` goal-gap directive.
- Tests: `backend/tests/test_orso_currency.py`, `test_orso_overview_multicurrency.py`, `test_orso_ingest_csv.py`, `test_orso_apply.py`, `test_orso_vision.py`, `test_orso_fund_search.py`, `test_orso_advice_goalgap.py`.

**Frontend**
- `frontend/src/lib/api.ts`, `frontend/src/lib/types.ts` — MODIFY: ingest/apply/search/display-currency clients + types.
- `frontend/src/pages/OrsoPage.tsx` — MODIFY: display-currency switcher; consume display-currency fields.
- `frontend/src/pages/orso/IngestWizard.tsx` (+ `DraftReview.tsx`, `FundSearch.tsx`) — CREATE: upload → review/edit → confirm.
- Tests: co-located `*.test.tsx` with vitest-axe.

---

## Task 1: Migration 0009 + per-fund & profile currency

**Files:**
- Create: `backend/alembic/versions/0009_orso_currency.py`
- Modify: `backend/app/models/orso.py`, `backend/app/models/guru.py`, `backend/app/api/orso.py` (fund schemas only)
- Test: `backend/tests/test_orso_currency.py`

**Interfaces:**
- Produces: `OrsoFund.currency: str` (3-letter upper, default `"HKD"`); `InvestorProfile.orso_display_currency: str` (default `"GBP"`), `.orso_contribution_currency: str` (default `"HKD"`); `FundCreate`/`FundUpdate`/`FundOut` carry `currency: str`.

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_orso_currency.py
import pytest

pytestmark = pytest.mark.asyncio(loop_scope="session")


async def test_create_fund_defaults_currency_hkd(orso_client):
    r = await orso_client.post("/api/orso/funds", json={
        "code": "GEQ", "name": "Global Equity", "asset_class": "equity", "risk_rating": 5,
    })
    assert r.status_code == 201
    assert r.json()["currency"] == "HKD"


async def test_create_fund_accepts_and_uppercases_currency(orso_client):
    r = await orso_client.post("/api/orso/funds", json={
        "code": "USB", "name": "US Bond", "asset_class": "bond", "risk_rating": 3,
        "currency": "usd",
    })
    assert r.status_code == 201
    assert r.json()["currency"] == "USD"


async def test_patch_fund_currency(orso_client):
    fid = (await orso_client.post("/api/orso/funds", json={
        "code": "MMF", "name": "Money Market", "asset_class": "cash", "risk_rating": 1,
    })).json()["id"]
    r = await orso_client.patch(f"/api/orso/funds/{fid}", json={"currency": "gbp"})
    assert r.status_code == 200 and r.json()["currency"] == "GBP"


async def test_invalid_currency_rejected(orso_client):
    r = await orso_client.post("/api/orso/funds", json={
        "code": "BAD", "name": "Bad", "asset_class": "equity", "risk_rating": 4,
        "currency": "US",
    })
    assert r.status_code == 422
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd backend && .venv/bin/pytest tests/test_orso_currency.py -q`
Expected: FAIL (currency not accepted / KeyError on `currency`).

- [ ] **Step 3: Add the model columns + validator**

In `backend/app/models/orso.py`, add to `OrsoFund` (after `archived`):

```python
    currency: Mapped[str] = mapped_column(String(3), default="HKD", server_default="HKD")

    @validates("currency")
    def _upper_currency(self, key: str, value: str) -> str:
        return value.upper()
```

(`String`, `validates` are already imported in this file.)

In `backend/app/models/guru.py`, add to `InvestorProfile` (after `digest_enabled`):

```python
    orso_display_currency: Mapped[str] = mapped_column(
        String(3), default="GBP", server_default="GBP")
    orso_contribution_currency: Mapped[str] = mapped_column(
        String(3), default="HKD", server_default="HKD")
```

- [ ] **Step 4: Write the migration**

```python
# backend/alembic/versions/0009_orso_currency.py
"""per-fund native currency + ORSO display/contribution currency

Additive, forward-only. orso_funds.currency (native pricing currency, default
HKD); investor_profiles.orso_display_currency (overview display, default GBP)
and orso_contribution_currency (default HKD). All non-sensitive plaintext.

Revision ID: 0009
Revises: 0008
"""
import sqlalchemy as sa

from alembic import op

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("orso_funds", sa.Column(
        "currency", sa.String(3), nullable=False, server_default="HKD"))
    op.add_column("investor_profiles", sa.Column(
        "orso_display_currency", sa.String(3), nullable=False, server_default="GBP"))
    op.add_column("investor_profiles", sa.Column(
        "orso_contribution_currency", sa.String(3), nullable=False, server_default="HKD"))


def downgrade() -> None:
    op.drop_column("investor_profiles", "orso_contribution_currency")
    op.drop_column("investor_profiles", "orso_display_currency")
    op.drop_column("orso_funds", "currency")
```

- [ ] **Step 5: Add `currency` to the fund API schemas**

In `backend/app/api/orso.py`:

```python
# FundOut: add field
    currency: str

# FundCreate: add field (validated 3-letter, stored upper by the model validator)
    currency: str = Field(default="HKD", min_length=3, max_length=3)

# FundUpdate: add field
    currency: str | None = Field(default=None, min_length=3, max_length=3)

# _fund_out(): add currency=f.currency
```

Update `_fund_out`:

```python
def _fund_out(f: OrsoFund) -> FundOut:
    return FundOut(id=f.id, code=f.code, name=f.name, asset_class=f.asset_class,
                   risk_rating=f.risk_rating, archived=f.archived, currency=f.currency)
```

`create_fund` already does `OrsoFund(user_id=user.id, **{**body.model_dump(), "code": code})`, so `currency` flows through and the model validator uppercases it.

- [ ] **Step 6: Run the tests to verify they pass**

Run: `cd backend && .venv/bin/pytest tests/test_orso_currency.py -q`
Expected: PASS (4 tests).

- [ ] **Step 7: Verify the migration chain + full suite**

Run: `cd backend && .venv/bin/alembic heads` → expect single head `0009`.
Run: `cd backend && .venv/bin/ruff check . && .venv/bin/pytest -q` → all green (existing fund tests still pass; `FundOut` now includes `currency`).

- [ ] **Step 8: Commit**

```bash
git add backend/alembic/versions/0009_orso_currency.py backend/app/models/orso.py backend/app/models/guru.py backend/app/api/orso.py backend/tests/test_orso_currency.py
git commit -m "feat(orso): per-fund native currency + profile display/contribution currency (0009)"
```

---

## Task 2: Multi-currency overview + projection-in-display-currency + display switcher

**Files:**
- Modify: `backend/app/api/orso.py` (`build_overview`, add `PUT /display-currency`)
- Test: `backend/tests/test_orso_overview_multicurrency.py`

**Interfaces:**
- Consumes: `OrsoFund.currency`, `InvestorProfile.orso_display_currency`, `.orso_contribution_currency` (Task 1); `FxService.get_rate(db, base, quote) -> Decimal`; `project(pot, monthly_contribution, years, target_pot)`.
- Produces: `build_overview` returns additional keys — per-fund `value_native`, `value_display`, `currency`; top-level `total_display`, `display_currency`; `flags.fx_unavailable: list[str]`. Existing keys (`value_hkd`, `total_hkd`, `total_base`) stay populated (now currency-correct: each fund converted to HKD/GBP). New route `PUT /api/orso/display-currency`.

**Design notes for the implementer:**
- Today `build_overview` computes `value_hkd = units * price` assuming price is HKD. Now `price` is in the fund's `currency`. Compute `value_native = units * price` (in `f.currency`), then convert to **HKD** (for the legacy `value_hkd`/`total_hkd` keys) and to the **display currency** (`value_display`) via `FxService`. Same-currency conversions must short-circuit to rate `1` (don't call FX for HKD→HKD).
- FX failures are per-fund and non-fatal: on failure set that target value to `None` and append the fund code to `flags["fx_unavailable"]`. Never raise.
- Projection runs in the display currency: convert `total` (sum of `value_display`) and the monthly contribution (from `orso_contribution_currency` → display currency) before calling `project`.

- [ ] **Step 1: Write the failing tests**

```python
# backend/tests/test_orso_overview_multicurrency.py
from decimal import Decimal

import pytest

pytestmark = pytest.mark.asyncio(loop_scope="session")


async def _fund_with_price(orso_client, db_session, code, currency, units, price):
    from datetime import date
    fid = (await orso_client.post("/api/orso/funds", json={
        "code": code, "name": code, "asset_class": "equity", "risk_rating": 4,
        "currency": currency,
    })).json()["id"]
    await orso_client.put("/api/orso/allocation", json={"allocations": [
        {"fund_id": fid, "units": str(units), "contribution_pct": "100"}]})
    await orso_client.put("/api/orso/prices/manual", json={
        "fund_id": fid, "price": str(price), "as_of": date.today().isoformat()})
    return fid


async def test_overview_converts_each_fund_to_display_currency(orso_client, db_session, monkeypatch):
    # Stub FX: HKD->GBP = 0.1, USD->GBP = 0.8, identity otherwise.
    from app.services import valuation

    async def fake_rate(self, db, base, quote):
        table = {("HKD", "GBP"): Decimal("0.1"), ("USD", "GBP"): Decimal("0.8"),
                 ("HKD", "HKD"): Decimal("1"), ("USD", "HKD"): Decimal("8")}
        if base == quote:
            return Decimal("1")
        return table[(base, quote)]
    monkeypatch.setattr(valuation.FxService, "get_rate", fake_rate)

    await _fund_with_price(orso_client, db_session, "HKEQ", "HKD", 100, 10)   # 1000 HKD
    r = await orso_client.get("/api/orso/overview")
    body = r.json()
    assert body["display_currency"] == "GBP"
    row = next(f for f in body["funds"] if f["code"] == "HKEQ")
    assert row["value_native"] == "1000.00"
    assert row["currency"] == "HKD"
    assert row["value_display"] == "100.00"          # 1000 * 0.1
    assert body["total_display"] == "100.00"


async def test_overview_fx_failure_degrades_not_500(orso_client, db_session, monkeypatch):
    from app.services import valuation

    async def boom(self, db, base, quote):
        if base == quote:
            return Decimal("1")
        raise RuntimeError("fx down")
    monkeypatch.setattr(valuation.FxService, "get_rate", boom)

    await _fund_with_price(orso_client, db_session, "USEQ", "USD", 10, 50)   # 500 USD
    r = await orso_client.get("/api/orso/overview")
    assert r.status_code == 200
    body = r.json()
    row = next(f for f in body["funds"] if f["code"] == "USEQ")
    assert row["value_native"] == "500.00"
    assert row["value_display"] is None
    assert "USEQ" in body["flags"]["fx_unavailable"]


async def test_put_display_currency_persists_and_recomputes(orso_client, db_session, monkeypatch):
    from app.services import valuation

    async def fake_rate(self, db, base, quote):
        if base == quote:
            return Decimal("1")
        return {("HKD", "USD"): Decimal("0.128")}[(base, quote)]
    monkeypatch.setattr(valuation.FxService, "get_rate", fake_rate)

    await _fund_with_price(orso_client, db_session, "HKEQ2", "HKD", 100, 10)  # 1000 HKD
    r = await orso_client.put("/api/orso/display-currency", json={"currency": "usd"})
    assert r.status_code == 200 and r.json()["currency"] == "USD"
    body = (await orso_client.get("/api/orso/overview")).json()
    assert body["display_currency"] == "USD"
    row = next(f for f in body["funds"] if f["code"] == "HKEQ2")
    assert row["value_display"] == "128.00"          # 1000 * 0.128
```

- [ ] **Step 2: Run to verify failure**

Run: `cd backend && .venv/bin/pytest tests/test_orso_overview_multicurrency.py -q`
Expected: FAIL (`value_native`/`value_display`/`display_currency` absent; no `/display-currency` route).

- [ ] **Step 3: Refactor `build_overview` for multi-currency**

In `backend/app/api/orso.py`, replace the per-fund valuation loop and totals. Add a small helper above `build_overview`:

```python
async def _convert(fx, db, amount: Decimal | None, src: str, dst: str,
                   failed: list[str], code: str) -> Decimal | None:
    """Convert amount src->dst; None on FX failure (records `code` in `failed`)."""
    if amount is None:
        return None
    if src == dst:
        return amount.quantize(Decimal("0.01"))
    try:
        rate = await fx.get_rate(db, src, dst)
    except Exception:
        if code not in failed:
            failed.append(code)
        return None
    return (amount * rate).quantize(Decimal("0.01"))
```

Inside `build_overview`, read the display currency from the profile and thread the new fields. Key changes:

```python
    profile = await get_profile_row(db, user)
    display_ccy = (profile.orso_display_currency if profile
                   and profile.orso_display_currency else _BASE_CURRENCY)
    contrib_ccy = (profile.orso_contribution_currency if profile
                   and profile.orso_contribution_currency else "HKD")

    fx_unavailable: list[str] = []
    total_hkd = Decimal("0")
    total_display = Decimal("0")
    # ... existing stale/unpriced/contribution_sum setup ...

    for f in funds:
        # ... existing skip-archived-zero-units logic ...
        price_row = latest.get(f.id)
        if price_row is None:
            value_native = value_hkd = value_display = None
            price = price_as_of = price_source = None
            unpriced.append(f.code)
        else:
            price = price_row.price
            price_as_of = price_row.as_of.isoformat()
            price_source = price_row.source
            value_native = (units * price).quantize(Decimal("0.01"))
            value_hkd = await _convert(fx_service, db, value_native, f.currency, "HKD",
                                       fx_unavailable, f.code)
            value_display = await _convert(fx_service, db, value_native, f.currency,
                                           display_ccy, fx_unavailable, f.code)
            if value_hkd is not None:
                total_hkd += value_hkd
            if value_display is not None:
                total_display += value_display
            if (today - price_row.as_of).days > _STALE_AFTER_DAYS:
                stale.append(f.code)
        # ... existing contribution_sum accumulation ...
        fund_rows.append({
            # ... existing keys ...
            "currency": f.currency,
            "value_native": (None if value_native is None else str(value_native)),
            "value_hkd": (None if value_hkd is None else str(value_hkd)),
            "value_display": (None if value_display is None else str(value_display)),
            # ... price/price_as_of/price_source unchanged ...
        })
```

Replace the `total_base` block and projection block:

```python
    # legacy total_base (HKD -> GBP) kept for the not-yet-migrated frontend
    total_base = None
    gbp = await _convert(fx_service, db, total_hkd, "HKD", _BASE_CURRENCY, [], "__total__")
    if gbp is not None:
        total_base = {"currency": _BASE_CURRENCY, "value": str(gbp)}

    # projection runs in the display currency
    projection = None
    if not goals_incomplete:
        current_year = datetime.now(UTC).year
        years = goal_values["retirement_target_age"] - (
            current_year - goal_values["birth_year"])
        monthly_display = await _convert(
            fx_service, db, goal_values["orso_monthly_contribution"],
            contrib_ccy, display_ccy, [], "__contrib__")
        if monthly_display is not None:
            scenarios = project(total_display, monthly_display, years,
                                goal_values["retirement_target_pot"])
            projection = [{"rate": str(s.rate), "projected_pot": str(s.projected_pot),
                           "on_track": s.on_track,
                           "gap": (None if s.gap is None else str(s.gap))}
                          for s in scenarios]
```

Add to the returned dict:

```python
        "total_display": str(total_display),
        "display_currency": display_ccy,
        # flags dict gains:
        "fx_unavailable": fx_unavailable,
```

(Keep `total_hkd`, `total_base` in the return for backward-compat.)

- [ ] **Step 4: Add the display-currency route**

Append to `backend/app/api/orso.py`:

```python
class DisplayCurrencyIn(BaseModel):
    currency: str = Field(min_length=3, max_length=3)


class DisplayCurrencyOut(BaseModel):
    currency: str


@router.put("/display-currency", response_model=DisplayCurrencyOut)
async def set_display_currency(body: DisplayCurrencyIn, db: SessionDep, user: CurrentUser):
    ccy = body.currency.upper()
    stmt = pg_insert(InvestorProfile).values(
        user_id=user.id, orso_display_currency=ccy)
    stmt = stmt.on_conflict_do_update(
        index_elements=["user_id"], set_={"orso_display_currency": ccy})
    await db.execute(stmt)
    await db.commit()
    return DisplayCurrencyOut(currency=ccy)
```

(`pg_insert`, `InvestorProfile`, `BaseModel`, `Field` are already imported in this module.)

- [ ] **Step 5: Run the tests to verify they pass**

Run: `cd backend && .venv/bin/pytest tests/test_orso_overview_multicurrency.py -q`
Expected: PASS (3 tests).

- [ ] **Step 6: Run the full suite (existing overview/advice tests use HKD funds → default currency keeps them green)**

Run: `cd backend && .venv/bin/ruff check . && .venv/bin/pytest -q`
Expected: PASS. If a pre-existing overview test asserts an exact `total_base`, confirm it still holds (HKD→GBP path unchanged for HKD funds).

- [ ] **Step 7: Commit**

```bash
git add backend/app/api/orso.py backend/tests/test_orso_overview_multicurrency.py
git commit -m "feat(orso): multi-currency overview + projection-in-display-currency + PUT /display-currency"
```

---

## Task 3: AllocationDraft schema + CSV ingest (parse only)

**Files:**
- Create: `backend/app/services/orso/ingest.py`
- Modify: `backend/app/api/orso.py` (`POST /ingest/csv`)
- Test: `backend/tests/test_orso_ingest_csv.py`

**Interfaces:**
- Produces: `DraftRow`, `AllocationDraft` (pydantic); `parse_csv(text: str) -> list[dict]`; `build_draft(db, user_id, parsed_rows, source) -> AllocationDraft`; route `POST /api/orso/ingest/csv` (multipart `file`) → `AllocationDraft`.
- Consumes: `OrsoFund` (matching by code/name).

- [ ] **Step 1: Write the failing tests**

```python
# backend/tests/test_orso_ingest_csv.py
import pytest

pytestmark = pytest.mark.asyncio(loop_scope="session")

CSV_OK = (
    "fund_code,fund_name,units,value,currency,contribution_pct\n"
    "HKEQ,HK Equity,100,1000,HKD,60\n"
    "USBD,US Bond,50,2500,USD,40\n"
)


async def _csv(orso_client, text, filename="alloc.csv"):
    return await orso_client.post(
        "/api/orso/ingest/csv",
        files={"file": (filename, text.encode(), "text/csv")})


async def test_csv_matches_existing_fund_by_code(orso_client):
    fid = (await orso_client.post("/api/orso/funds", json={
        "code": "HKEQ", "name": "HK Equity", "asset_class": "equity",
        "risk_rating": 5, "currency": "HKD"})).json()["id"]
    r = await _csv(orso_client, CSV_OK)
    assert r.status_code == 200
    body = r.json()
    hkeq = next(row for row in body["rows"] if row["parsed_code"] == "HKEQ")
    assert hkeq["matched_fund_id"] == fid
    assert hkeq["implied_price"] == "10.0000"        # 1000 / 100
    assert hkeq["contribution_pct"] == "60"
    usbd = next(row for row in body["rows"] if row["parsed_code"] == "USBD")
    assert usbd["matched_fund_id"] is None
    assert usbd["proposed_fund"]["currency"] == "USD"
    assert usbd["implied_price"] == "50.0000"        # 2500 / 50


async def test_csv_flags_pct_sum_off(orso_client):
    text = ("fund_code,units,value,contribution_pct\n"
            "AAA,10,100,50\nBBB,10,100,40\n")
    body = (await _csv(orso_client, text)).json()
    assert any("pct_sum" in w for w in body["warnings"])


async def test_csv_malformed_row_becomes_flagged_not_500(orso_client):
    text = ("fund_code,units,value,contribution_pct\n"
            "AAA,notanumber,100,50\n")
    r = await _csv(orso_client, text)
    assert r.status_code == 200
    row = r.json()["rows"][0]
    assert "unparseable_units" in row["flags"]


async def test_csv_missing_required_header_422(orso_client):
    text = "units,value\n10,100\n"
    r = await _csv(orso_client, text)
    assert r.status_code == 422
```

- [ ] **Step 2: Run to verify failure**

Run: `cd backend && .venv/bin/pytest tests/test_orso_ingest_csv.py -q`
Expected: FAIL (route missing).

- [ ] **Step 3: Implement the ingest module**

```python
# backend/app/services/orso/ingest.py
"""ORSO ingest: parse a CSV (or vision extraction) into an AllocationDraft the
user reviews before committing via POST /allocation/apply. Read-only — building
a draft never writes."""
import csv
import io
from decimal import Decimal, InvalidOperation

from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import OrsoFund

_REQUIRED_HEADERS = {"fund_code", "units", "contribution_pct"}
_UNITS_Q = Decimal("0.0001")
_PRICE_Q = Decimal("0.0001")


class ProposedFund(BaseModel):
    code: str
    name: str
    currency: str
    asset_class: str = "unknown"
    risk_rating: int = 4


class DraftRow(BaseModel):
    parsed_code: str
    parsed_name: str | None
    matched_fund_id: int | None
    proposed_fund: ProposedFund | None
    units: str | None
    value: str | None
    currency: str
    contribution_pct: str | None
    implied_price: str | None
    flags: list[str]


class AllocationDraft(BaseModel):
    rows: list[DraftRow]
    warnings: list[str]
    source: str


class CsvHeaderError(Exception):
    """Required CSV headers missing."""


def parse_csv(text: str) -> list[dict]:
    reader = csv.DictReader(io.StringIO(text))
    headers = {(h or "").strip().lower() for h in (reader.fieldnames or [])}
    if not _REQUIRED_HEADERS.issubset(headers):
        raise CsvHeaderError(sorted(_REQUIRED_HEADERS - headers))
    out: list[dict] = []
    for raw in reader:
        out.append({(k or "").strip().lower(): (v or "").strip() for k, v in raw.items()})
    return out


def _dec(val: str | None) -> Decimal | None:
    if not val:
        return None
    try:
        return Decimal(val)
    except (InvalidOperation, TypeError):
        return None


async def build_draft(
    db: AsyncSession, user_id: int, parsed_rows: list[dict], source: str
) -> AllocationDraft:
    funds = (await db.execute(
        select(OrsoFund).where(OrsoFund.user_id == user_id)
    )).scalars().all()
    by_code = {f.code.upper(): f for f in funds}
    by_name = {f.name.strip().lower(): f for f in funds}

    rows: list[DraftRow] = []
    pct_sum = Decimal("0")
    for r in parsed_rows:
        code = (r.get("fund_code") or "").upper()
        name = r.get("fund_name") or None
        units = _dec(r.get("units"))
        value = _dec(r.get("value"))
        pct = _dec(r.get("contribution_pct"))
        currency = (r.get("currency") or "").upper()
        flags: list[str] = []

        match = by_code.get(code) or (by_name.get(name.strip().lower()) if name else None)
        if r.get("units") and units is None:
            flags.append("unparseable_units")
        if r.get("contribution_pct") and pct is None:
            flags.append("unparseable_pct")
        if match is None:
            flags.append("unmatched")

        eff_currency = currency or (match.currency if match else "HKD")
        implied = None
        if units and value and units != 0:
            implied = (value / units).quantize(_PRICE_Q)

        proposed = None
        if match is None:
            proposed = ProposedFund(
                code=code, name=name or code, currency=eff_currency)

        if pct is not None:
            pct_sum += pct

        rows.append(DraftRow(
            parsed_code=code, parsed_name=name,
            matched_fund_id=(match.id if match else None),
            proposed_fund=proposed,
            units=(None if units is None else str(units)),
            value=(None if value is None else str(value)),
            currency=eff_currency,
            contribution_pct=(None if pct is None else str(pct)),
            implied_price=(None if implied is None else str(implied)),
            flags=flags,
        ))

    warnings: list[str] = []
    if rows and pct_sum != Decimal("100"):
        warnings.append(f"pct_sum={pct_sum} (not 100)")
    return AllocationDraft(rows=rows, warnings=warnings, source=source)
```

- [ ] **Step 4: Add the CSV ingest route**

In `backend/app/api/orso.py`, add imports and route:

```python
from fastapi import UploadFile
from app.core.hardening import MAX_UPLOAD_BYTES
from app.services.orso.ingest import (
    AllocationDraft, CsvHeaderError, build_draft, parse_csv,
)


@router.post("/ingest/csv", response_model=AllocationDraft)
async def ingest_csv(db: SessionDep, user: CurrentUser, file: UploadFile):
    data = await file.read()
    if len(data) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail="upload_too_large")
    try:
        parsed = parse_csv(data.decode("utf-8-sig"))
    except CsvHeaderError as exc:
        raise HTTPException(status_code=422, detail=f"missing_headers:{exc.args[0]}") from None
    except UnicodeDecodeError:
        raise HTTPException(status_code=422, detail="not_utf8_csv") from None
    return await build_draft(db, user.id, parsed, source="csv")
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `cd backend && .venv/bin/pytest tests/test_orso_ingest_csv.py -q`
Expected: PASS (4 tests).

- [ ] **Step 6: Commit**

```bash
git add backend/app/services/orso/ingest.py backend/app/api/orso.py backend/tests/test_orso_ingest_csv.py
git commit -m "feat(orso): AllocationDraft + CSV ingest (read-only parse + fund matching + implied price)"
```

---

## Task 4: Shared allocation-apply service + POST /allocation/apply

**Files:**
- Create: `backend/app/services/orso/allocation.py`
- Modify: `backend/app/api/orso.py` (refactor `replace_allocation` to use the service; add `POST /allocation/apply`)
- Test: `backend/tests/test_orso_apply.py`

**Interfaces:**
- Consumes: `OrsoAllocation`, `OrsoFund`, `OrsoSwitchLog`, `_canonical`, `_current_alloc_entries` (existing in `app/api/orso.py`); `OrsoPriceService.upsert_manual_price`.
- Produces: `apply_allocation(db, user, *, new_funds, items, note) -> dict` where `items` reference funds by `fund_id` or `new_fund_code`; transactional; returns `{"created_funds": [...], "switched": bool}`. Route `POST /api/orso/allocation/apply`.

**Design note:** the existing `replace_allocation` deletes+inserts allocations and writes the switch log, then commits. Extract the delete/insert/switch-log core into `_replace_core(db, user_id, code_units_pct, note)` in the new service so both endpoints share it; `apply_allocation` additionally creates confirmed new funds and writes derived manual prices **before** calling the core, all in one transaction (single commit at the end).

- [ ] **Step 1: Write the failing tests**

```python
# backend/tests/test_orso_apply.py
from datetime import date

import pytest
from sqlalchemy import func, select

from app.models import OrsoAllocation, OrsoFund, OrsoFundPrice, OrsoSwitchLog

pytestmark = pytest.mark.asyncio(loop_scope="session")


async def test_apply_creates_new_fund_price_and_allocation_with_switchlog(orso_client, db_session):
    body = {
        "new_funds": [{"code": "NEWEQ", "name": "New Equity", "currency": "HKD",
                       "asset_class": "equity", "risk_rating": 5}],
        "allocations": [{"new_fund_code": "NEWEQ", "units": "100",
                         "contribution_pct": "100",
                         "price": {"market_value": "1500", "as_of": date.today().isoformat()}}],
        "note": "from statement",
    }
    r = await orso_client.post("/api/orso/allocation/apply", json=body)
    assert r.status_code == 200
    assert r.json()["switched"] is True

    fund = (await db_session.execute(
        select(OrsoFund).where(OrsoFund.code == "NEWEQ"))).scalar_one()
    alloc = (await db_session.execute(
        select(OrsoAllocation).where(OrsoAllocation.fund_id == fund.id))).scalar_one()
    assert alloc.units == pytest.approx  # placeholder replaced below

    price = (await db_session.execute(
        select(OrsoFundPrice).where(OrsoFundPrice.fund_id == fund.id))).scalar_one()
    assert str(price.price) == "15.0000"          # 1500 / 100
    assert price.source == "manual"
    n_switch = (await db_session.execute(
        select(func.count()).select_from(OrsoSwitchLog).where(
            OrsoSwitchLog.user_id == fund.user_id))).scalar_one()
    assert n_switch == 1


async def test_apply_is_all_or_nothing_on_bad_row(orso_client, db_session):
    # a fund_id that doesn't belong to the user -> whole apply rejected, nothing created
    before = (await db_session.execute(select(func.count()).select_from(OrsoFund))).scalar_one()
    body = {
        "new_funds": [{"code": "GHOST", "name": "Ghost", "currency": "HKD",
                       "asset_class": "equity", "risk_rating": 4}],
        "allocations": [{"fund_id": 999999, "units": "1", "contribution_pct": "100"}],
        "note": None,
    }
    r = await orso_client.post("/api/orso/allocation/apply", json=body)
    assert r.status_code == 422
    after = (await db_session.execute(select(func.count()).select_from(OrsoFund))).scalar_one()
    assert after == before        # GHOST was NOT created (rolled back)


async def test_apply_rejects_other_users_fund(orso_client, client, db_session):
    from app.core.security import hash_password
    from app.models.user import User
    # user A creates a fund
    fid = (await orso_client.post("/api/orso/funds", json={
        "code": "AONLY", "name": "A only", "asset_class": "equity",
        "risk_rating": 4})).json()["id"]
    # user B logs in and tries to allocate to A's fund
    db_session.add(User(email="bapply@test.dev", password_hash=hash_password("pw123456")))
    await db_session.commit()
    await client.post("/api/auth/login", json={"email": "bapply@test.dev", "password": "pw123456"})
    r = await client.post("/api/orso/allocation/apply", json={
        "new_funds": [], "allocations": [{"fund_id": fid, "units": "1",
                                          "contribution_pct": "100"}], "note": None})
    assert r.status_code == 422
```

Fix the placeholder assertion before running: replace
`assert alloc.units == pytest.approx  # placeholder replaced below`
with:
```python
    from decimal import Decimal
    assert alloc.units == Decimal("100.0000")
```

- [ ] **Step 2: Run to verify failure**

Run: `cd backend && .venv/bin/pytest tests/test_orso_apply.py -q`
Expected: FAIL (route missing).

- [ ] **Step 3: Implement the shared apply service**

```python
# backend/app/services/orso/allocation.py
"""Shared, transactional ORSO allocation writes. Both PUT /allocation (form
path) and POST /allocation/apply (reviewed ingest draft) funnel through
_replace_core so every write is validated and switch-logged identically. The
service flushes but does not commit — the caller owns the transaction boundary."""
from datetime import UTC, date, datetime
from decimal import Decimal

from fastapi import HTTPException
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import OrsoAllocation, OrsoFund, OrsoSwitchLog

_UNITS_Q = Decimal("0.0001")
_PCT_Q = Decimal("0.01")


def _canonical(entries: list[tuple[str, Decimal, Decimal]]) -> list[dict]:
    items = [{"code": code, "units": str(Decimal(units).quantize(_UNITS_Q)),
              "contribution_pct": str(Decimal(pct).quantize(_PCT_Q))}
             for code, units, pct in entries]
    return sorted(items, key=lambda x: x["code"])


async def _current_entries(db: AsyncSession, user_id: int) -> list[tuple[str, Decimal, Decimal]]:
    rows = (await db.execute(
        select(OrsoFund.code, OrsoAllocation.units, OrsoAllocation.contribution_pct)
        .join(OrsoAllocation, OrsoAllocation.fund_id == OrsoFund.id)
        .where(OrsoAllocation.user_id == user_id)
    )).all()
    return [(c, u, p) for c, u, p in rows]


async def _replace_core(
    db: AsyncSession, user_id: int,
    items: list[tuple[int, str, Decimal, Decimal]],   # (fund_id, code, units, pct)
    note: str | None,
) -> bool:
    """Full-replace the allocation; write a switch-log entry iff it changed.
    Returns `switched`. Assumes fund ownership/validity already checked."""
    previous = _canonical(await _current_entries(db, user_id))
    await db.execute(delete(OrsoAllocation).where(OrsoAllocation.user_id == user_id))
    for fund_id, _code, units, pct in items:
        db.add(OrsoAllocation(user_id=user_id, fund_id=fund_id, units=units,
                              contribution_pct=pct))
    new_state = _canonical([(code, units, pct) for _fid, code, units, pct in items])
    switched = new_state != previous
    if switched:
        db.add(OrsoSwitchLog(
            user_id=user_id, changed_at=datetime.now(UTC).replace(tzinfo=None),
            old_state=previous, new_state=new_state, note=note))
    await db.flush()
    return switched


async def apply_allocation(
    db: AsyncSession, user, *, new_funds: list[dict], allocations: list[dict],
    note: str | None, price_service,
) -> dict:
    """Create confirmed new funds, write derived manual prices, and replace the
    allocation — one transaction (caller commits). Raises HTTPException(422) on
    any validation failure (nothing is committed)."""
    # 1. create new funds (code unique per user)
    existing_codes = {c for (c,) in (await db.execute(
        select(OrsoFund.code).where(OrsoFund.user_id == user.id))).all()}
    created: dict[str, OrsoFund] = {}
    for nf in new_funds:
        code = nf["code"].upper()
        if code in existing_codes or code in created:
            raise HTTPException(status_code=422, detail=f"duplicate_new_fund:{code}")
        fund = OrsoFund(user_id=user.id, code=code, name=nf["name"],
                        asset_class=nf.get("asset_class", "unknown"),
                        risk_rating=nf.get("risk_rating", 4),
                        currency=nf.get("currency", "HKD"))
        db.add(fund)
        created[code] = fund
    await db.flush()   # assigns ids to created funds

    # 2. resolve allocation rows to (fund_id, code, units, pct)
    owned = {f.id: f for f in (await db.execute(
        select(OrsoFund).where(OrsoFund.user_id == user.id))).scalars().all()}
    items: list[tuple[int, str, Decimal, Decimal]] = []
    seen: set[int] = set()
    for a in allocations:
        if a.get("new_fund_code"):
            fund = created.get(a["new_fund_code"].upper())
            if fund is None:
                raise HTTPException(status_code=422, detail="unknown_new_fund_code")
        else:
            fund = owned.get(a.get("fund_id"))
            if fund is None:
                raise HTTPException(status_code=422, detail="unknown_fund_id")
        if fund.id in seen:
            raise HTTPException(status_code=422, detail="duplicate_fund_id")
        seen.add(fund.id)
        units = Decimal(str(a["units"]))
        pct = Decimal(str(a["contribution_pct"]))
        if units < 0 or pct < 0 or pct > 100:
            raise HTTPException(status_code=422, detail="out_of_range")
        items.append((fund.id, fund.code, units, pct))

        # 3. derive + write manual price when market_value provided
        price = a.get("price")
        if price and price.get("market_value") and units != 0:
            mv = Decimal(str(price["market_value"]))
            as_of = date.fromisoformat(price["as_of"])
            await price_service.upsert_manual_price(db, fund, (mv / units), as_of)

    switched = await _replace_core(db, user.id, items, note)
    return {"created_funds": sorted(created), "switched": switched}
```

- [ ] **Step 4: Wire the apply route + refactor the form path**

In `backend/app/api/orso.py`, add:

```python
from app.services.orso.allocation import apply_allocation


class ApplyPriceIn(BaseModel):
    market_value: Decimal = Field(gt=0)
    as_of: date


class ApplyItem(BaseModel):
    fund_id: int | None = None
    new_fund_code: str | None = None
    units: Decimal = Field(ge=0)
    contribution_pct: Decimal = Field(ge=0, le=100)
    price: ApplyPriceIn | None = None


class ApplyNewFund(BaseModel):
    code: str = Field(min_length=1, max_length=16)
    name: str = Field(min_length=1, max_length=120)
    currency: str = Field(min_length=3, max_length=3)
    asset_class: str = Field(default="unknown", max_length=32)
    risk_rating: int = Field(default=4, ge=1, le=7)


class ApplyRequest(BaseModel):
    new_funds: list[ApplyNewFund] = []
    allocations: list[ApplyItem]
    note: str | None = Field(default=None, max_length=300)


class ApplyResult(BaseModel):
    created_funds: list[str]
    switched: bool


@router.post("/allocation/apply", response_model=ApplyResult)
async def apply_reviewed(body: ApplyRequest, db: SessionDep, user: CurrentUser,
                         prices: OrsoPriceDep):
    result = await apply_allocation(
        db, user,
        new_funds=[f.model_dump() for f in body.new_funds],
        allocations=[a.model_dump() for a in body.allocations],
        note=body.note, price_service=prices)
    await db.commit()
    return ApplyResult(**result)
```

Leave the existing `PUT /allocation` as-is (it already works and is tested); the shared `_replace_core` is available for a future refactor but is not required to change the passing form path now. (YAGNI: do not rewrite `replace_allocation` unless a reviewer requests it.)

- [ ] **Step 5: Run the tests to verify they pass**

Run: `cd backend && .venv/bin/pytest tests/test_orso_apply.py -q`
Expected: PASS (3 tests).

- [ ] **Step 6: Full suite + commit**

Run: `cd backend && .venv/bin/ruff check . && .venv/bin/pytest -q` → green.

```bash
git add backend/app/services/orso/allocation.py backend/app/api/orso.py backend/tests/test_orso_apply.py
git commit -m "feat(orso): transactional POST /allocation/apply (create funds + derived prices + switch-logged replace)"
```

---

## Task 5: Screenshot vision ingest

**Files:**
- Create: `backend/app/services/orso/vision.py`
- Modify: `backend/app/services/guru/schemas.py` (`OrsoStatementExtraction`), `backend/app/api/orso.py` (`POST /ingest/screenshot`)
- Test: `backend/tests/test_orso_vision.py`

**Interfaces:**
- Consumes: `LLMProvider.generate_structured`, `check_budget`, `record_usage`, `build_draft` (Task 3), `GuruDep`, `map_guru_errors`.
- Produces: `OrsoStatementExtraction` (pydantic); `extract_statement(provider, db, user_id, image_b64, media_type) -> AllocationDraft`; route `POST /api/orso/ingest/screenshot` (multipart `file`) → `AllocationDraft`.

- [ ] **Step 1: Add the extraction schema**

In `backend/app/services/guru/schemas.py`:

```python
class ExtractedFundRow(BaseModel):
    fund_code: str
    fund_name: str | None = None
    units: str | None = None            # decimal-as-string; None if not shown
    value: str | None = None
    currency: str | None = None
    contribution_pct: str | None = None


class OrsoStatementExtraction(BaseModel):
    rows: list[ExtractedFundRow]
```

- [ ] **Step 2: Write the failing tests**

```python
# backend/tests/test_orso_vision.py
import base64

import pytest

from app.services.guru.schemas import ExtractedFundRow, OrsoStatementExtraction

pytestmark = pytest.mark.asyncio(loop_scope="session")

PNG_1x1 = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==")


async def test_screenshot_returns_draft(orso_client, fake_llm):
    fake_llm.structured_queue.append(OrsoStatementExtraction(rows=[
        ExtractedFundRow(fund_code="HKEQ", fund_name="HK Equity", units="100",
                         value="1000", currency="HKD", contribution_pct="100")]))
    r = await orso_client.post("/api/orso/ingest/screenshot",
                               files={"file": ("stmt.png", PNG_1x1, "image/png")})
    assert r.status_code == 200
    body = r.json()
    assert body["source"] == "screenshot"
    assert body["rows"][0]["parsed_code"] == "HKEQ"
    assert body["rows"][0]["implied_price"] == "10.0000"
    # the image block was actually sent to the provider
    call = fake_llm.calls[-1]
    blocks = call["messages"][0]["content"]
    assert any(b.get("type") == "image" for b in blocks)


async def test_screenshot_budget_exhausted_429(orso_client, fake_llm, db_session, monkeypatch):
    async def over(db, user_id, *, now=None):
        from app.services.guru.budget import BudgetExhausted
        raise BudgetExhausted()
    monkeypatch.setattr("app.services.orso.vision.check_budget", over)
    r = await orso_client.post("/api/orso/ingest/screenshot",
                               files={"file": ("stmt.png", PNG_1x1, "image/png")})
    assert r.status_code == 429 and r.json()["detail"] == "budget_exhausted"


async def test_screenshot_llm_failure_502_not_500(orso_client, fake_llm):
    fake_llm.fail_structured = 1
    r = await orso_client.post("/api/orso/ingest/screenshot",
                               files={"file": ("stmt.png", PNG_1x1, "image/png")})
    assert r.status_code == 502 and r.json()["detail"] == "llm_error"


async def test_screenshot_rejects_non_image_415(orso_client):
    r = await orso_client.post("/api/orso/ingest/screenshot",
                               files={"file": ("x.txt", b"hello", "text/plain")})
    assert r.status_code == 415
```

- [ ] **Step 3: Run to verify failure**

Run: `cd backend && .venv/bin/pytest tests/test_orso_vision.py -q`
Expected: FAIL (route + module missing).

- [ ] **Step 4: Implement the vision service**

```python
# backend/app/services/orso/vision.py
"""Extract an ORSO allocation from a statement screenshot via the Guru LLM
layer's vision path. Reuses generate_structured with an Anthropic image block —
no LLM-layer change. Governed by the per-user daily budget; usage is recorded.
Output is always a reviewable AllocationDraft (never auto-committed)."""
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.services.guru.budget import check_budget
from app.services.guru.llm.base import LLMProvider
from app.services.guru.schemas import OrsoStatementExtraction
from app.services.guru import usage as usage_mod
from app.services.orso.ingest import AllocationDraft, build_draft

_INSTRUCTION = (
    "This image is an HSBC ORSO pension statement. Extract every fund row: the "
    "fund code, fund name, unit holdings, current market value, currency, and "
    "contribution percentage. Use null for any field not visible. Do not invent rows."
)


async def extract_statement(
    provider: LLMProvider, db: AsyncSession, user_id: int,
    image_b64: str, media_type: str,
) -> AllocationDraft:
    await check_budget(db, user_id)
    messages = [{"role": "user", "content": [
        {"type": "image", "source": {"type": "base64",
                                     "media_type": media_type, "data": image_b64}},
        {"type": "text", "text": _INSTRUCTION},
    ]}]
    payload, usage = await provider.generate_structured(
        system="You extract structured data from financial statement images.",
        messages=messages, schema=OrsoStatementExtraction,
        model=settings.guru_advice_model, max_tokens=2048)
    await usage_mod.record_usage(db, user_id=user_id, mode="orso_ingest",
                                 model=settings.guru_advice_model, usage=usage)
    await db.commit()
    parsed = [{"fund_code": r.fund_code, "fund_name": r.fund_name, "units": r.units,
               "value": r.value, "currency": r.currency,
               "contribution_pct": r.contribution_pct} for r in payload.rows]
    return await build_draft(db, user_id, parsed, source="screenshot")
```

- [ ] **Step 5: Add the route**

In `backend/app/api/orso.py`:

```python
import base64
from app.services.guru.llm.base import LLMProvider  # noqa (if not already imported)
from app.api.guru import get_guru  # for provider access
from app.services.orso.vision import extract_statement

_ALLOWED_IMAGE_TYPES = {"image/png", "image/jpeg", "image/webp", "image/gif"}


@router.post("/ingest/screenshot", response_model=AllocationDraft)
async def ingest_screenshot(db: SessionDep, user: CurrentUser, guru: GuruDep,
                            file: UploadFile):
    if file.content_type not in _ALLOWED_IMAGE_TYPES:
        raise HTTPException(status_code=415, detail="unsupported_image_type")
    data = await file.read()
    if len(data) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail="upload_too_large")
    provider = guru.provider
    if provider is None:
        raise HTTPException(status_code=503, detail="llm_unconfigured")
    b64 = base64.b64encode(data).decode()
    with map_guru_errors():
        return await extract_statement(provider, db, user.id, b64, file.content_type)
```

(`GuruDep`, `map_guru_errors` are already imported at the top of `app/api/orso.py`; confirm and add if missing.)

- [ ] **Step 6: Run the tests to verify they pass**

Run: `cd backend && .venv/bin/pytest tests/test_orso_vision.py -q`
Expected: PASS (4 tests).

- [ ] **Step 7: Ask the user for a redacted sample statement**

**USER TOUCHPOINT:** request a redacted screenshot of a real statement to (a) sanity-check the extraction schema against real layout and (b) enrich the fixture. If provided, add one assertion-backed test using a committed **redacted** fixture image (no real values). If not available, proceed with the synthetic fixture above and note it.

- [ ] **Step 8: Commit**

```bash
git add backend/app/services/orso/vision.py backend/app/services/guru/schemas.py backend/app/api/orso.py backend/tests/test_orso_vision.py
git commit -m "feat(orso): screenshot vision ingest via Guru LLM layer (budget-gated, degrade-not-500)"
```

---

## Task 6: Fund search

**Files:**
- Modify: `backend/app/api/orso.py` (`GET /funds/search`)
- Test: `backend/tests/test_orso_fund_search.py`

**Interfaces:**
- Produces: `GET /api/orso/funds/search?q=` → `list[FundOut]` (matches code OR name, case-insensitive substring; includes archived and zero-allocation funds; own funds only).

- [ ] **Step 1: Write the failing tests**

```python
# backend/tests/test_orso_fund_search.py
import pytest

pytestmark = pytest.mark.asyncio(loop_scope="session")


async def test_search_matches_code_and_name(orso_client):
    await orso_client.post("/api/orso/funds", json={
        "code": "GLEQ", "name": "Global Equity Fund", "asset_class": "equity",
        "risk_rating": 5})
    await orso_client.post("/api/orso/funds", json={
        "code": "HKBD", "name": "HK Bond", "asset_class": "bond", "risk_rating": 3})
    by_name = (await orso_client.get("/api/orso/funds/search?q=equity")).json()
    assert [f["code"] for f in by_name] == ["GLEQ"]
    by_code = (await orso_client.get("/api/orso/funds/search?q=hkb")).json()
    assert [f["code"] for f in by_code] == ["HKBD"]


async def test_search_excludes_other_users(orso_client, client, db_session):
    from app.core.security import hash_password
    from app.models.user import User
    await orso_client.post("/api/orso/funds", json={
        "code": "SECRET", "name": "Secret Fund", "asset_class": "equity",
        "risk_rating": 5})
    db_session.add(User(email="bsearch@test.dev", password_hash=hash_password("pw123456")))
    await db_session.commit()
    await client.post("/api/auth/login", json={"email": "bsearch@test.dev", "password": "pw123456"})
    res = (await client.get("/api/orso/funds/search?q=secret")).json()
    assert res == []


async def test_search_empty_query_returns_all_own_funds(orso_client):
    res = (await orso_client.get("/api/orso/funds/search?q=")).json()
    assert isinstance(res, list)
```

- [ ] **Step 2: Run to verify failure**

Run: `cd backend && .venv/bin/pytest tests/test_orso_fund_search.py -q`
Expected: FAIL (route missing / matches list_funds instead).

- [ ] **Step 3: Add the search route**

In `backend/app/api/orso.py` (place above `create_fund` so the static `/funds/search` path is matched before any `/funds` collision is irrelevant — both are distinct paths, but keep it near `list_funds`):

```python
@router.get("/funds/search", response_model=list[FundOut])
async def search_funds(db: SessionDep, user: CurrentUser, q: str = ""):
    stmt = select(OrsoFund).where(OrsoFund.user_id == user.id)
    term = q.strip().lower()
    if term:
        like = f"%{term}%"
        stmt = stmt.where(
            func.lower(OrsoFund.code).like(like) | func.lower(OrsoFund.name).like(like))
    rows = (await db.execute(stmt.order_by(OrsoFund.code))).scalars().all()
    return [_fund_out(f) for f in rows]
```

Add `from sqlalchemy import func` to the imports if not present.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd backend && .venv/bin/pytest tests/test_orso_fund_search.py -q`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add backend/app/api/orso.py backend/tests/test_orso_fund_search.py
git commit -m "feat(orso): GET /funds/search over own fund menu (code+name, includes archived)"
```

---

## Task 7: Goal-gap advice enrichment

**Files:**
- Modify: `backend/app/services/orso/context.py` (enrich context), `backend/app/services/guru/schemas.py` (`OrsoAdvicePayload.contribution_suggestion`), `backend/app/services/guru/service.py` (`_ORSO_INSTRUCTION`)
- Test: `backend/tests/test_orso_advice_goalgap.py`

**Interfaces:**
- Consumes: `build_overview` (now returns `projection`, `total_display`, `display_currency`); `get_profile_row`.
- Produces: `build_orso_context` adds `goal_gap` (projection gap per scenario, in display currency), `monthly_contribution`, `contribution_currency`, and per-fund `risk_rating`/`asset_class` (already in overview funds). `OrsoAdvicePayload` gains `contribution_suggestion: str`.

- [ ] **Step 1: Write the failing tests**

```python
# backend/tests/test_orso_advice_goalgap.py
import pytest

from app.services.guru.schemas import OrsoAdvicePayload

pytestmark = pytest.mark.asyncio(loop_scope="session")


def test_orso_advice_payload_has_contribution_suggestion():
    p = OrsoAdvicePayload(fund_verdicts=[], switch_plan=[], projection_comment="ok",
                          watch=[], disclaimer="not advice",
                          contribution_suggestion="Consider raising to HKD 40,000/mo.")
    assert p.contribution_suggestion.startswith("Consider")


async def test_context_includes_goal_gap(orso_client, db_session, monkeypatch):
    from decimal import Decimal

    from app.api.orso import get_orso_prices
    from app.services import valuation
    from app.services.orso.context import build_orso_context

    async def ident(self, db, base, quote):
        return Decimal("1")
    monkeypatch.setattr(valuation.FxService, "get_rate", ident)

    # goals so projection is populated
    await orso_client.put("/api/orso/goals", json={
        "birth_year": 1985, "retirement_target_age": 65,
        "retirement_target_pot": "5000000", "orso_monthly_contribution": "10000"})

    from app.models.user import User
    from sqlalchemy import select
    user = (await db_session.execute(select(User).where(
        User.email == "lee@test.dev"))).scalar_one()   # orso_client -> auth_client user
    fx = valuation.FxService(None)
    ctx = await build_orso_context(db_session, user, get_orso_prices(), fx)
    assert "goal_gap" in ctx
    assert "monthly_contribution" in ctx
```

(The `orso_client` fixture chains up to `auth_client`, whose user is `lee@test.dev` — used in the lookup above.)

- [ ] **Step 2: Run to verify failure**

Run: `cd backend && .venv/bin/pytest tests/test_orso_advice_goalgap.py -q`
Expected: FAIL (`contribution_suggestion` missing; `goal_gap` absent).

- [ ] **Step 3: Add the payload field**

In `backend/app/services/guru/schemas.py`, add to `OrsoAdvicePayload`:

```python
    contribution_suggestion: str
```

(Stored historical reports read via `_report_out` as a free `dict` — unaffected. Only new generations validate against the new field.)

- [ ] **Step 4: Enrich the context**

In `backend/app/services/orso/context.py`, extend `build_orso_context` after it assembles `ctx`:

```python
    proj = overview.get("projection")
    goals = ctx.get("goals")
    ctx["display_currency"] = overview.get("display_currency")
    ctx["total_display"] = overview.get("total_display")
    ctx["monthly_contribution"] = (
        None if goals is None else goals.get("orso_monthly_contribution"))
    ctx["contribution_currency"] = getattr(
        await _profile_currency(db, user), "orso_contribution_currency", "HKD")
    ctx["goal_gap"] = None if proj is None else [
        {"rate": s["rate"], "gap": s["gap"], "on_track": s["on_track"]} for s in proj]
```

Add the small helper near the top of the module:

```python
async def _profile_currency(db, user):
    from app.api.guru import get_profile_row
    return await get_profile_row(db, user)
```

- [ ] **Step 5: Update the ORSO instruction (goal-gap directive)**

In `backend/app/services/guru/service.py`, replace `_ORSO_INSTRUCTION`:

```python
_ORSO_INSTRUCTION = (
    "Advise on this ORSO pension. Only reference fund codes from the fund menu "
    "provided. Give a verdict for every fund currently holding units and a concrete "
    "switch plan. Then, using goal_gap (shortfall/surplus vs the target pot per growth "
    "scenario, in display_currency) and monthly_contribution, give a "
    "contribution_suggestion: a concrete, specific lever to close any gap — e.g. a "
    "revised monthly contribution figure and/or an allocation shift by asset class — "
    "framed as general guidance, not licensed financial advice. Comment on the "
    "projection in projection_comment."
)
```

- [ ] **Step 6: Run the tests to verify they pass**

Run: `cd backend && .venv/bin/pytest tests/test_orso_advice_goalgap.py -q`
Expected: PASS (2 tests).

- [ ] **Step 7: Update any existing ORSO advice test that constructs `OrsoAdvicePayload`**

Search: `grep -rn "OrsoAdvicePayload(" backend/tests` — add `contribution_suggestion="..."` to each constructor and to any `fake_llm.structured_queue` seed, or the schema validation will fail.

Run: `cd backend && .venv/bin/ruff check . && .venv/bin/pytest -q` → all green.

- [ ] **Step 8: Commit**

```bash
git add backend/app/services/orso/context.py backend/app/services/guru/schemas.py backend/app/services/guru/service.py backend/tests/test_orso_advice_goalgap.py
git commit -m "feat(orso): goal-gap advice — context gap/contribution + contribution_suggestion payload"
```

---

## Task 8: Figma gate (USER GATE)

**Files:** none (design artifacts in Figma).

- [ ] **Step 1: Produce Figma frames** for: (a) the ingest wizard — **Upload** (CSV or screenshot, drag-drop) → **Review/Edit draft** (per-row: matched fund vs proposed-new, units, value, %, currency, implied price, flags; draft-level warnings banner; pct-sum indicator) → **Confirm**; (b) fund search (typeahead adding a row); (c) the display-currency switcher on the overview.
- [ ] **Step 2: Follow the project's Figma-first flow** (file key `0gU58wfjttdZS0NXQeEtuD`; reuse the existing ORSO frame styles).
- [ ] **Step 3: Present frames to the user and get explicit approval before Task 9.** Incorporate feedback and re-present until approved.

---

## Task 9: Frontend — ingest wizard, fund search, currency switcher (push seam)

**Files:**
- Modify: `frontend/src/lib/api.ts`, `frontend/src/lib/types.ts`, `frontend/src/pages/OrsoPage.tsx`
- Create: `frontend/src/pages/orso/IngestWizard.tsx`, `frontend/src/pages/orso/DraftReview.tsx`, `frontend/src/pages/orso/FundSearch.tsx`
- Test: co-located `*.test.tsx` (vitest-axe)

**Interfaces:**
- Consumes backend routes: `POST /api/orso/ingest/csv`, `POST /api/orso/ingest/screenshot`, `POST /api/orso/allocation/apply`, `GET /api/orso/funds/search`, `PUT /api/orso/display-currency`; overview's new `display_currency` / `total_display` / per-fund `value_display` fields.

- [ ] **Step 1: Types** — add `AllocationDraft`, `DraftRow`, `ApplyRequest`, and extend the overview type with `display_currency`, `total_display`, per-fund `value_native`/`value_display`/`currency`, `flags.fx_unavailable`. (Mirror the backend pydantic shapes exactly.)
- [ ] **Step 2: API client** — add `ingestCsv(file)`, `ingestScreenshot(file)`, `applyAllocation(body)`, `searchFunds(q)`, `setDisplayCurrency(ccy)` in `lib/api.ts`, following the existing fetch+`isBudgetExhausted` patterns; surface 429 (budget), 413 (too large), 415 (bad image), 422 (validation) as typed errors.
- [ ] **Step 3: Ingest wizard (TDD with vitest-axe)** — write a failing test that renders `IngestWizard`, mocks `fetch` to return a two-row draft, asserts the review table renders both rows + the pct-sum warning, edits a row, and that Confirm calls `applyAllocation` with the reviewed payload. Then implement `IngestWizard` + `DraftReview`. Never auto-submit; Confirm is an explicit button.
- [ ] **Step 4: Fund search** — `FundSearch` typeahead calling `searchFunds`; selecting a result adds a draft row. Test with mocked fetch + axe.
- [ ] **Step 5: Currency switcher** — a select on `OrsoPage` calling `setDisplayCurrency`, then refetching the overview; render `total_display` in `display_currency` and per-fund `value_display`; show an "FX unavailable for: …" note when `flags.fx_unavailable` is non-empty. Keep reading gracefully if a field is null.
- [ ] **Step 6: Verify** — `cd frontend && npm run check` (tsc + lint + vitest incl. axe + build) → green.
- [ ] **Step 7: Commit + push (push seam — reaches prod)**

```bash
git add frontend/src
git commit -m "feat(orso): ingest wizard + fund search + display-currency switcher (frontend)"
git push origin main
```

Confirm CI green (`gh run view <id> --json conclusion,jobs`); Vercel auto-deploys the frontend, Railway the backend on green CI.

---

## Task 10: Docs + live smoke + final Opus review

**Files:**
- Modify: `AGENTS.md`, `docs/PROGRESS.md`, `README.md`, `docs/deployment.md` (if any env/behaviour notes needed).

- [ ] **Step 1: Live smoke** on the deployed app — CSV ingest → review → apply; screenshot ingest (real redacted statement) → review → apply; verify overview totals in the chosen display currency; switch display currency; regenerate ORSO advice and confirm it references `contribution_suggestion` + goal gap. Confirm no 500s; degrade paths behave (FX down / no key).
- [ ] **Step 2: Seed the real catalogue** — with the user, run the first real ingest to populate their Local Staff DC Scheme fund menu (the "seed now from a sample statement" step from the spec).
- [ ] **Step 3: Refresh handoff docs** — update AGENTS.md (head → `0009`; new ORSO ingest/currency/advice surface; note the WMFS-vs-Local-Staff feed caveat), `docs/PROGRESS.md` (new section), README ORSO paragraph.
- [ ] **Step 4: Final whole-branch review on Opus** — base = the pre-Task-1 tip. Security/correctness focus given the vision upload + FX + transactional apply surface. Fix wave → re-review to merge-clean; push fixes; re-run docs refresh if anything changed.
- [ ] **Step 5: Commit any doc/fix changes.**

```bash
git add AGENTS.md docs/PROGRESS.md README.md docs/deployment.md
git commit -m "docs(orso): data-entry + advice feature live; smoke verified"
git push origin main
```

---

## Self-Review (completed by the plan author)

**1. Spec coverage:**
- CSV upload → Task 3. Screenshot/vision → Task 5. Contribution amount + % → existing model, surfaced in ingest/apply (Tasks 3–4) + wizard (Task 9). Fund search → Task 6. Change fund base currency → Tasks 1–2 (+ switcher in 2/9). Goal-gap regenerable commentary → Task 7 (regenerate already exists). Unified review-before-commit pipeline → Tasks 3–5 (draft) + 4 (apply). Figma gate → Task 8. Per-fund + display currency → Tasks 1–2. Every spec §1–§7 requirement maps to a task.

**2. Placeholder scan:** one intentional `pytest.approx` placeholder in Task 4 Step 1 is explicitly replaced in the same step. No `TBD`/`implement later`/vague error-handling directives — degrade behaviour is spelled out with status codes.

**3. Type consistency:** `AllocationDraft`/`DraftRow`/`ProposedFund` defined in Task 3 and consumed unchanged in Tasks 4/5/9. `apply_allocation(new_funds, allocations, note, price_service)` defined in Task 4 matches the `POST /allocation/apply` body (`ApplyRequest`). `OrsoStatementExtraction` defined in Task 5 Step 1, used in the same task. `OrsoAdvicePayload.contribution_suggestion` added in Task 7 and every existing constructor updated (Task 7 Step 7). Overview additive keys (`value_display`/`total_display`/`display_currency`/`fx_unavailable`) defined in Task 2, consumed in Tasks 7 + 9.

**Fixtures confirmed:** `conftest.py` provides `orso_client` (→ `guru_client` → `auth_client`, user `lee@test.dev`, with `fake_llm` wired) + `db_session`, `client`, `make_instrument`. The `User.email == "lee@test.dev"` lookups in Tasks 4/7 use that fixture's user.
