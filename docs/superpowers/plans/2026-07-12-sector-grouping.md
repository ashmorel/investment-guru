# User-Defined Sector/Theme Grouping Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let the user split holdings into their own named groups (seeded from the auto-sector, freely editable), and see current exposure (value + % per group across all real portfolios, with a per-portfolio filter) plus a forward-building trend.

**Architecture:** Three new user-scoped tables (`HoldingGroup`, one-per-stock `GroupAssignment`, encrypted `GroupSnapshot`). A shared `compute_group_exposure` values the user's real portfolios and aggregates `market_value_base` by group (unassigned → an implicit Ungrouped bucket). A daily APScheduler job + startup catch-up + opportunistic write-on-view captures snapshots so the trend builds forward from launch. Frontend is a new Sectors page (manage + breakdown bars + inline-SVG trend).

**Tech Stack:** FastAPI + SQLAlchemy 2 async + Alembic (head **0011** → new **0012**) + Postgres 16; React 18 + Vite + Tailwind + TanStack Query (no chart library).

## Global Constraints

- Money = `Decimal`. Monetary amounts encrypted at rest (`GroupSnapshot.value_base` = `EncryptedDecimal`). Every new table has `user_id`; every route 404s on another user's data.
- DB change = ONE hand-written chained Alembic migration; single head `0012` on down_revision `0011`; additive + reversible.
- Exposure/snapshot degrade per-position on quote failure (a position with no price contributes 0 and its symbol goes in `unpriced`) — **never 500**.
- One group per stock: `GroupAssignment` unique `(user_id, instrument_id)`. Ungrouped = no assignment (rendered as a `group_id: null` bucket). "Held instruments" = instruments referenced by the user's **real** (`kind="real"`) portfolio positions (watchlists excluded).
- `seed-from-sectors` is idempotent + non-destructive (creates missing sector-named groups, assigns only currently-unassigned held instruments, never overrides an existing assignment).
- Trend is across-all-holdings only (the per-portfolio filter applies to the live breakdown only); forward-only from launch (no backfill).
- Snapshot write = **delete today's rows for the user, then insert** (idempotent; safe for the NULL Ungrouped bucket). Cheap, no LLM, per-user failure-isolated.
- Providers/valuation are mock/fixture-backed in tests. **Run pytest in the FOREGROUND only** (backgrounding poisons the local DB; if a run shows mass IntegrityErrors/hangs, `docker compose down db && docker compose up -d db` and re-run).
- Async tests: `pytestmark = pytest.mark.asyncio(loop_scope="session")` + conftest fixtures (`client`, `auth_client`, `db_session`, `make_instrument`). Postgres :5433 via `docker compose up -d db`.
- Backend verify: `cd backend && .venv/bin/ruff check . && .venv/bin/pytest -q`. Frontend: `cd frontend && npm run check`.
- Commit to `main`; co-author trailer `Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>`.

---

## File Structure

**Backend**
- `backend/alembic/versions/0012_holding_groups.py` — CREATE: 3 tables.
- `backend/app/models/groups.py` — CREATE: `HoldingGroup`, `GroupAssignment`, `GroupSnapshot`.
- `backend/app/models/__init__.py` — MODIFY: export the three.
- `backend/app/api/groups.py` — CREATE: CRUD + `seed-from-sectors` + `exposure` + `trend` routes.
- `backend/app/services/groups/exposure.py` — CREATE: `compute_group_exposure` (shared by exposure endpoint + snapshot job) + `write_snapshot`.
- `backend/app/services/groups/snapshot.py` — CREATE: `run_group_snapshot_job` + `snapshot_catch_up`.
- `backend/app/services/guru/scheduler.py` — MODIFY: add the snapshot job to `create_scheduler`.
- `backend/app/main.py` — MODIFY: register `groups_router`; run `snapshot_catch_up` on startup.
- Tests: `backend/tests/test_groups_crud.py`, `test_group_exposure.py`, `test_group_snapshot.py`.

**Frontend**
- `frontend/src/lib/api.ts`, `frontend/src/lib/types.ts` — MODIFY: group clients/types.
- `frontend/src/pages/SectorsPage.tsx` — CREATE: manage + breakdown + trend.
- `frontend/src/components/TrendChart.tsx` — CREATE: inline-SVG multi-line chart.
- `frontend/src/App.tsx` — MODIFY: `/sectors` route + nav.
- Tests: `SectorsPage.test.tsx`, `TrendChart.test.tsx`.

---

## Task 1: Migration 0012 + models

**Files:**
- Create: `backend/alembic/versions/0012_holding_groups.py`, `backend/app/models/groups.py`
- Modify: `backend/app/models/__init__.py`
- Test: `backend/tests/test_groups_crud.py` (Step-1 subset)

**Interfaces:**
- Produces: `HoldingGroup(id, user_id, name, color, sort_order)`; `GroupAssignment(id, user_id, instrument_id, group_id)` unique `(user_id, instrument_id)`; `GroupSnapshot(id, user_id, group_id: int|None, as_of, value_base: Decimal[encrypted])`.

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_groups_crud.py
from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import text

from app.models import GroupAssignment, GroupSnapshot, HoldingGroup

pytestmark = pytest.mark.asyncio(loop_scope="session")


async def test_group_models_persist_and_snapshot_encrypts(db_session, make_instrument):
    from app.core.security import hash_password
    from app.models.user import User
    u = User(email="grp1@test.dev", password_hash=hash_password("pw123456"))
    db_session.add(u)
    await db_session.commit()
    inst = await make_instrument("AAPL")

    g = HoldingGroup(user_id=u.id, name="Tech", color="#4F46E5", sort_order=0)
    db_session.add(g)
    await db_session.commit()
    db_session.add(GroupAssignment(user_id=u.id, instrument_id=inst.id, group_id=g.id))
    db_session.add(GroupSnapshot(user_id=u.id, group_id=g.id, as_of=date(2026, 7, 12),
                                 value_base=Decimal("1234.56")))
    # Ungrouped snapshot (group_id NULL)
    db_session.add(GroupSnapshot(user_id=u.id, group_id=None, as_of=date(2026, 7, 12),
                                 value_base=Decimal("50.00")))
    await db_session.commit()

    snap = (await db_session.execute(text(
        "SELECT value_base FROM group_snapshots WHERE user_id=:u AND group_id=:g"),
        {"u": u.id, "g": g.id})).scalar_one()
    assert snap.startswith("v1:") and "1234.56" not in snap   # encrypted at rest
    got = (await db_session.execute(text(
        "SELECT value_base FROM group_snapshots WHERE user_id=:u AND group_id IS NULL"),
        {"u": u.id})).scalar_one()
    assert got.startswith("v1:")
```

- [ ] **Step 2: Run to verify failure**

Run: `cd backend && .venv/bin/pytest tests/test_groups_crud.py -q`
Expected: FAIL (models missing).

- [ ] **Step 3: Add the models**

```python
# backend/app/models/groups.py
from datetime import date
from decimal import Decimal

from sqlalchemy import ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.core.crypto import EncryptedDecimal
from app.core.db import Base
from app.models.base import TimestampMixin


class HoldingGroup(TimestampMixin, Base):
    __tablename__ = "holding_groups"
    __table_args__ = (UniqueConstraint("user_id", "name"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    name: Mapped[str] = mapped_column(String(64))
    color: Mapped[str] = mapped_column(String(16), default="", server_default="")
    sort_order: Mapped[int] = mapped_column(default=0, server_default="0")


class GroupAssignment(Base):
    __tablename__ = "group_assignments"
    __table_args__ = (UniqueConstraint("user_id", "instrument_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    instrument_id: Mapped[int] = mapped_column(ForeignKey("instruments.id"))
    group_id: Mapped[int] = mapped_column(
        ForeignKey("holding_groups.id", ondelete="CASCADE"))


class GroupSnapshot(Base):
    __tablename__ = "group_snapshots"
    __table_args__ = (
        UniqueConstraint("user_id", "group_id", "as_of",
                         postgresql_nulls_not_distinct=True),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    group_id: Mapped[int | None] = mapped_column(
        ForeignKey("holding_groups.id", ondelete="CASCADE"), nullable=True)
    as_of: Mapped[date] = mapped_column()
    value_base: Mapped[Decimal] = mapped_column(EncryptedDecimal())
```

Export from `backend/app/models/__init__.py`: add `GroupAssignment, GroupSnapshot, HoldingGroup` to the imports + `__all__`.

- [ ] **Step 4: Write the migration**

```python
# backend/alembic/versions/0012_holding_groups.py
"""user-defined holding groups + assignments + snapshots

Additive, forward-only. HoldingGroup (per-user named groups), GroupAssignment
(one group per instrument, unique per user), GroupSnapshot (encrypted per-group
daily value; group_id NULL = the Ungrouped bucket).

Revision ID: 0012
Revises: 0011
"""
import sqlalchemy as sa

from alembic import op

revision = "0012"
down_revision = "0011"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "holding_groups",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("name", sa.String(64), nullable=False),
        sa.Column("color", sa.String(16), nullable=False, server_default=""),
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("user_id", "name"),
    )
    op.create_index("ix_holding_groups_user_id", "holding_groups", ["user_id"])
    op.create_table(
        "group_assignments",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("instrument_id", sa.Integer(), sa.ForeignKey("instruments.id"), nullable=False),
        sa.Column("group_id", sa.Integer(),
                  sa.ForeignKey("holding_groups.id", ondelete="CASCADE"), nullable=False),
        sa.UniqueConstraint("user_id", "instrument_id"),
    )
    op.create_index("ix_group_assignments_user_id", "group_assignments", ["user_id"])
    op.create_table(
        "group_snapshots",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("group_id", sa.Integer(),
                  sa.ForeignKey("holding_groups.id", ondelete="CASCADE"), nullable=True),
        sa.Column("as_of", sa.Date(), nullable=False),
        sa.Column("value_base", sa.Text(), nullable=False),
        sa.UniqueConstraint("user_id", "group_id", "as_of", postgresql_nulls_not_distinct=True),
    )
    op.create_index("ix_group_snapshots_user_id", "group_snapshots", ["user_id"])


def downgrade() -> None:
    op.drop_table("group_snapshots")
    op.drop_table("group_assignments")
    op.drop_table("holding_groups")
```

- [ ] **Step 5: Run tests + migration check**

Run: `cd backend && .venv/bin/pytest tests/test_groups_crud.py -q` → PASS.
Run: `.venv/bin/alembic heads` → single head `0012`.
Run: `.venv/bin/ruff check . && .venv/bin/pytest -q` → green.

- [ ] **Step 6: Commit**

```bash
git add backend/alembic/versions/0012_holding_groups.py backend/app/models/groups.py backend/app/models/__init__.py backend/tests/test_groups_crud.py
git commit -m "feat(groups): HoldingGroup + GroupAssignment + GroupSnapshot models (0012)"
```

---

## Task 2: Groups CRUD + seed-from-sectors

**Files:**
- Create: `backend/app/api/groups.py`
- Modify: `backend/app/main.py` (register router)
- Test: `backend/tests/test_groups_crud.py` (extend)

**Interfaces:**
- Consumes: `HoldingGroup`, `GroupAssignment`, `Instrument`, `Position`, `Portfolio`, `CurrentUser`, `SessionDep`.
- Produces: `user_held_instruments(db, user_id) -> list[Instrument]` (real portfolios only); routes `GET/POST /api/groups`, `PATCH/DELETE /api/groups/{id}`, `PUT /api/groups/assign`, `POST /api/groups/seed-from-sectors`.

- [ ] **Step 1: Write the failing tests**

```python
# add to backend/tests/test_groups_crud.py
async def _hold(auth_client, symbol, make_instrument, sector=None):
    await make_instrument(symbol, sector=sector)
    pid = (await auth_client.post("/api/portfolios",
           json={"name": "P", "kind": "real", "base_currency": "GBP"})).json()["id"]
    await auth_client.post(f"/api/portfolios/{pid}/positions", json={"symbol": symbol, "quantity": "1"})


async def test_group_crud_and_assign(auth_client, make_instrument):
    await _hold(auth_client, "AAPL", make_instrument)
    g = (await auth_client.post("/api/groups", json={"name": "Tech", "color": "#4F46E5"})).json()
    assert g["name"] == "Tech"
    # duplicate name -> 409
    assert (await auth_client.post("/api/groups", json={"name": "Tech"})).status_code == 409
    # assign a held symbol
    r = await auth_client.put("/api/groups/assign", json={"symbol": "AAPL", "group_id": g["id"]})
    assert r.status_code == 200
    lst = (await auth_client.get("/api/groups")).json()
    assert lst[0]["holding_count"] == 1
    # assign a symbol not held -> 422
    assert (await auth_client.put("/api/groups/assign",
            json={"symbol": "NVDA", "group_id": g["id"]})).status_code == 422
    # clear assignment (null) -> Ungrouped
    await auth_client.put("/api/groups/assign", json={"symbol": "AAPL", "group_id": None})
    assert (await auth_client.get("/api/groups")).json()[0]["holding_count"] == 0
    # delete group cascades
    assert (await auth_client.delete(f"/api/groups/{g['id']}")).status_code == 204


async def test_seed_from_sectors_idempotent_nondestructive(auth_client, make_instrument):
    await _hold(auth_client, "AAPL", make_instrument, sector="Technology")
    await _hold(auth_client, "XOM", make_instrument, sector="Energy")
    await _hold(auth_client, "ZZZ", make_instrument, sector=None)  # -> "Unclassified"
    r1 = (await auth_client.post("/api/groups/seed-from-sectors")).json()
    assert set(r1["created"]) == {"Technology", "Energy", "Unclassified"} and r1["assigned"] == 3
    # move AAPL into a hand-made "Space" group
    space = (await auth_client.post("/api/groups", json={"name": "Space"})).json()
    await auth_client.put("/api/groups/assign", json={"symbol": "AAPL", "group_id": space["id"]})
    # re-seed: creates nothing new, assigns nothing (all assigned), does NOT move AAPL back
    r2 = (await auth_client.post("/api/groups/seed-from-sectors")).json()
    assert r2["created"] == [] and r2["assigned"] == 0
    groups = {g["name"]: g for g in (await auth_client.get("/api/groups")).json()}
    assert groups["Space"]["holding_count"] == 1        # AAPL stayed in Space
    assert groups["Technology"]["holding_count"] == 0


async def test_groups_are_user_scoped(auth_client, client, db_session, make_instrument):
    g = (await auth_client.post("/api/groups", json={"name": "Mine"})).json()
    from app.core.security import hash_password
    from app.models.user import User
    db_session.add(User(email="bgrp@test.dev", password_hash=hash_password("pw123456")))
    await db_session.commit()
    await client.post("/api/auth/login", json={"email": "bgrp@test.dev", "password": "pw123456"})
    assert (await client.get("/api/groups")).json() == []
    assert (await client.patch(f"/api/groups/{g['id']}", json={"name": "x"})).status_code == 404
    assert (await client.delete(f"/api/groups/{g['id']}")).status_code == 404
```

- [ ] **Step 2: Run to verify failure**

Run: `cd backend && .venv/bin/pytest tests/test_groups_crud.py -q`
Expected: FAIL (routes missing).

- [ ] **Step 3: Implement the router**

```python
# backend/app/api/groups.py
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError

from app.api.deps import CurrentUser, SessionDep
from app.models import GroupAssignment, HoldingGroup, Instrument, Portfolio, Position

router = APIRouter(prefix="/api/groups", tags=["groups"])


async def user_held_instruments(db, user_id: int) -> list[Instrument]:
    return list((await db.execute(
        select(Instrument).distinct()
        .join(Position, Position.instrument_id == Instrument.id)
        .join(Portfolio, Portfolio.id == Position.portfolio_id)
        .where(Portfolio.user_id == user_id, Portfolio.kind == "real")
    )).scalars().all())


async def _owned_group(db, user_id: int, group_id: int) -> HoldingGroup:
    g = await db.get(HoldingGroup, group_id)
    if g is None or g.user_id != user_id:
        raise HTTPException(status_code=404, detail="group_not_found")
    return g


class GroupOut(BaseModel):
    id: int
    name: str
    color: str
    sort_order: int
    holding_count: int


class GroupIn(BaseModel):
    name: str = Field(min_length=1, max_length=64)
    color: str = Field(default="", max_length=16)


class GroupPatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=64)
    color: str | None = Field(default=None, max_length=16)
    sort_order: int | None = None


async def _counts(db, user_id: int) -> dict[int, int]:
    rows = (await db.execute(
        select(GroupAssignment.group_id, func.count()).where(
            GroupAssignment.user_id == user_id).group_by(GroupAssignment.group_id)
    )).all()
    return {gid: n for gid, n in rows}


@router.get("", response_model=list[GroupOut])
async def list_groups(db: SessionDep, user: CurrentUser):
    groups = (await db.execute(
        select(HoldingGroup).where(HoldingGroup.user_id == user.id)
        .order_by(HoldingGroup.sort_order, HoldingGroup.id)
    )).scalars().all()
    counts = await _counts(db, user.id)
    return [GroupOut(id=g.id, name=g.name, color=g.color, sort_order=g.sort_order,
                     holding_count=counts.get(g.id, 0)) for g in groups]


@router.post("", response_model=GroupOut, status_code=201)
async def create_group(body: GroupIn, db: SessionDep, user: CurrentUser):
    g = HoldingGroup(user_id=user.id, name=body.name, color=body.color)
    db.add(g)
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(status_code=409, detail="duplicate_name") from None
    await db.refresh(g)
    return GroupOut(id=g.id, name=g.name, color=g.color, sort_order=g.sort_order, holding_count=0)


@router.patch("/{group_id}", response_model=GroupOut)
async def update_group(group_id: int, body: GroupPatch, db: SessionDep, user: CurrentUser):
    g = await _owned_group(db, user.id, group_id)
    for field, value in body.model_dump(exclude_unset=True).items():
        setattr(g, field, value)
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(status_code=409, detail="duplicate_name") from None
    counts = await _counts(db, user.id)
    return GroupOut(id=g.id, name=g.name, color=g.color, sort_order=g.sort_order,
                    holding_count=counts.get(g.id, 0))


@router.delete("/{group_id}", status_code=204)
async def delete_group(group_id: int, db: SessionDep, user: CurrentUser):
    g = await _owned_group(db, user.id, group_id)
    await db.delete(g)          # assignments + snapshots cascade (ondelete=CASCADE)
    await db.commit()


class AssignIn(BaseModel):
    symbol: str
    group_id: int | None


@router.put("/assign", status_code=200)
async def assign(body: AssignIn, db: SessionDep, user: CurrentUser):
    held = {i.symbol: i for i in await user_held_instruments(db, user.id)}
    inst = held.get(body.symbol.upper())
    if inst is None:
        raise HTTPException(status_code=422, detail="not_held")
    existing = (await db.execute(
        select(GroupAssignment).where(
            GroupAssignment.user_id == user.id, GroupAssignment.instrument_id == inst.id)
    )).scalar_one_or_none()
    if body.group_id is None:
        if existing is not None:
            await db.delete(existing)
        await db.commit()
        return {"symbol": inst.symbol, "group_id": None}
    await _owned_group(db, user.id, body.group_id)
    if existing is None:
        db.add(GroupAssignment(user_id=user.id, instrument_id=inst.id, group_id=body.group_id))
    else:
        existing.group_id = body.group_id
    await db.commit()
    return {"symbol": inst.symbol, "group_id": body.group_id}


class SeedOut(BaseModel):
    created: list[str]
    assigned: int


@router.post("/seed-from-sectors", response_model=SeedOut)
async def seed_from_sectors(db: SessionDep, user: CurrentUser):
    insts = await user_held_instruments(db, user.id)
    existing_groups = {g.name: g for g in (await db.execute(
        select(HoldingGroup).where(HoldingGroup.user_id == user.id))).scalars().all()}
    assigned_ids = {iid for (iid,) in (await db.execute(
        select(GroupAssignment.instrument_id).where(GroupAssignment.user_id == user.id))).all()}

    created: list[str] = []
    assigned = 0
    for inst in insts:
        if inst.id in assigned_ids:
            continue
        sector = inst.sector or "Unclassified"
        group = existing_groups.get(sector)
        if group is None:
            group = HoldingGroup(user_id=user.id, name=sector)
            db.add(group)
            await db.flush()
            existing_groups[sector] = group
            created.append(sector)
        db.add(GroupAssignment(user_id=user.id, instrument_id=inst.id, group_id=group.id))
        assigned += 1
    await db.commit()
    return SeedOut(created=created, assigned=assigned)
```

Register in `backend/app/main.py`: `from app.api.groups import router as groups_router` (alphabetical) + `app.include_router(groups_router)`.

- [ ] **Step 4: Run tests**

Run: `cd backend && .venv/bin/pytest tests/test_groups_crud.py -q` → PASS. Then `.venv/bin/ruff check . && .venv/bin/pytest -q` → green.

- [ ] **Step 5: Commit**

```bash
git add backend/app/api/groups.py backend/app/main.py backend/tests/test_groups_crud.py
git commit -m "feat(groups): CRUD + assign + idempotent seed-from-sectors"
```

---

## Task 3: Live exposure API

**Files:**
- Create: `backend/app/services/groups/exposure.py`
- Modify: `backend/app/api/groups.py` (add `GET /exposure`)
- Test: `backend/tests/test_group_exposure.py`

**Interfaces:**
- Consumes: `value_portfolio(db, portfolio, quote_service, fx) -> PortfolioSummary` (positions have `symbol`, `market_value_base`, `day_change_base`); `get_services()` → `(QuoteService, FxService)`; `GroupAssignment`, `Instrument`, `Portfolio`.
- Produces: `compute_group_exposure(db, user, quote_service, fx, portfolio_id=None) -> dict` with `{"groups": [{group_id, name, color, value_base, day_change_base}], "total_base": Decimal, "unpriced": [str]}`; route `GET /api/groups/exposure`.

- [ ] **Step 1: Write the failing tests**

```python
# backend/tests/test_group_exposure.py
from decimal import Decimal

import pytest

pytestmark = pytest.mark.asyncio(loop_scope="session")


async def _hold(auth_client, symbol, qty, make_instrument):
    await make_instrument(symbol)
    pid = (await auth_client.post("/api/portfolios",
           json={"name": symbol, "kind": "real", "base_currency": "GBP"})).json()["id"]
    await auth_client.post(f"/api/portfolios/{pid}/positions",
                           json={"symbol": symbol, "quantity": str(qty)})
    return pid


def _stub_valuation(monkeypatch, prices: dict):
    """Stub value_portfolio so exposure is deterministic (no live quotes).
    prices: symbol -> market_value_base (or None for unpriced).
    compute_group_exposure only reads summary.positions[].{symbol,
    market_value_base, day_change_base}, so a SimpleNamespace suffices — no need
    to construct the real PositionValuation/PortfolioSummary dataclasses. Patch
    the name imported INTO the exposure module (that's what the code calls)."""
    import types as _types

    import app.services.groups.exposure as expo

    async def fake(db, portfolio, quote_service, fx):
        positions = [
            _types.SimpleNamespace(
                symbol=p.instrument.symbol,
                market_value_base=prices.get(p.instrument.symbol),
                day_change_base=(None if prices.get(p.instrument.symbol) is None
                                 else Decimal("1")),
            )
            for p in portfolio.positions
        ]
        return _types.SimpleNamespace(positions=positions)

    monkeypatch.setattr(expo, "value_portfolio", fake)


async def test_exposure_groups_ungrouped_and_pct(auth_client, make_instrument, monkeypatch):
    await _hold(auth_client, "AAPL", 1, make_instrument)
    await _hold(auth_client, "XOM", 1, make_instrument)
    _stub_valuation(monkeypatch, {"AAPL": Decimal("70"), "XOM": Decimal("30")})
    g = (await auth_client.post("/api/groups", json={"name": "Tech"})).json()
    await auth_client.put("/api/groups/assign", json={"symbol": "AAPL", "group_id": g["id"]})

    body = (await auth_client.get("/api/groups/exposure")).json()
    assert body["total_base"] == "100.00"
    by = {(x["group_id"] or "ungrouped"): x for x in body["groups"]}
    assert by[g["id"]]["value_base"] == "70.00" and by[g["id"]]["pct"] == "70.00"
    assert by["ungrouped"]["value_base"] == "30.00" and by["ungrouped"]["name"] == "Ungrouped"


async def test_exposure_unpriced_degrades(auth_client, make_instrument, monkeypatch):
    await _hold(auth_client, "AAPL", 1, make_instrument)
    _stub_valuation(monkeypatch, {"AAPL": None})
    body = (await auth_client.get("/api/groups/exposure")).json()
    assert body["total_base"] == "0.00" and "AAPL" in body["unpriced"]
```

(The stub returns a `SimpleNamespace` with only the three attributes the exposure code reads — deliberately avoiding the real `PositionValuation`/`PortfolioSummary` dataclasses, whose full required-field lists would make the stub brittle.)

- [ ] **Step 2: Run to verify failure**

Run: `cd backend && .venv/bin/pytest tests/test_group_exposure.py -q`
Expected: FAIL (route/service missing).

- [ ] **Step 3: Implement the exposure service**

```python
# backend/app/services/groups/exposure.py
from decimal import Decimal

from sqlalchemy import select

from app.models import GroupAssignment, HoldingGroup, Portfolio
from app.services.valuation import value_portfolio

_Q = Decimal("0.01")


async def compute_group_exposure(db, user, quote_service, fx, portfolio_id=None) -> dict:
    """Aggregate current market value by user group across the user's real
    portfolios (or a single owned portfolio_id). Unassigned holdings → the
    Ungrouped bucket (group_id=None, name='Ungrouped'). Degrades per-position:
    an unpriced position contributes 0 and its symbol goes in `unpriced`."""
    q = select(Portfolio).where(Portfolio.user_id == user.id, Portfolio.kind == "real")
    if portfolio_id is not None:
        q = q.where(Portfolio.id == portfolio_id)
    portfolios = (await db.execute(q)).scalars().all()

    groups = {g.id: g for g in (await db.execute(
        select(HoldingGroup).where(HoldingGroup.user_id == user.id))).scalars().all()}
    sym_to_group: dict[str, int] = {}
    rows = (await db.execute(
        select(GroupAssignment.group_id, GroupAssignment.instrument_id)
        .where(GroupAssignment.user_id == user.id))).all()
    # instrument_id -> symbol via the portfolios' positions (loaded below)
    inst_to_group = {iid: gid for gid, iid in rows}

    agg_val: dict[int | None, Decimal] = {}
    agg_day: dict[int | None, Decimal] = {}
    unpriced: list[str] = []
    total = Decimal("0")
    for pf in portfolios:
        summary = await value_portfolio(db, pf, quote_service, fx)
        pos_inst = {p.instrument.symbol: p.instrument_id for p in pf.positions}
        for pv in summary.positions:
            if pv.market_value_base is None:
                unpriced.append(pv.symbol)
                continue
            gid = inst_to_group.get(pos_inst.get(pv.symbol))
            agg_val[gid] = agg_val.get(gid, Decimal("0")) + pv.market_value_base
            if pv.day_change_base is not None:
                agg_day[gid] = agg_day.get(gid, Decimal("0")) + pv.day_change_base
            total += pv.market_value_base

    out_groups = []
    for gid, val in agg_val.items():
        name = groups[gid].name if gid in groups else "Ungrouped"
        color = groups[gid].color if gid in groups else ""
        pct = (val / total * 100).quantize(_Q) if total > 0 else Decimal("0.00")
        out_groups.append({
            "group_id": gid, "name": name, "color": color,
            "value_base": str(val.quantize(_Q)), "pct": str(pct),
            "day_change_base": str(agg_day.get(gid, Decimal("0")).quantize(_Q)),
        })
    out_groups.sort(key=lambda x: Decimal(x["value_base"]), reverse=True)
    return {"groups": out_groups, "total_base": str(total.quantize(_Q)),
            "unpriced": sorted(set(unpriced))}
```

- [ ] **Step 4: Add the route**

In `backend/app/api/groups.py`:

```python
from datetime import UTC, datetime
from typing import Annotated

from fastapi import Depends

from app.api.valuation import get_services
from app.services.groups.exposure import compute_group_exposure


@router.get("/exposure")
async def exposure(db: SessionDep, user: CurrentUser,
                   services: Annotated[tuple, Depends(get_services)],
                   portfolio_id: int | None = None):
    quotes, fx = services
    if portfolio_id is not None:
        pf = await db.get(Portfolio, portfolio_id)
        if pf is None or pf.user_id != user.id:
            raise HTTPException(status_code=404, detail="portfolio_not_found")
    result = await compute_group_exposure(db, user, quotes, fx, portfolio_id)
    result["as_of"] = datetime.now(UTC).isoformat()
    return result
```

- [ ] **Step 5: Run tests + commit**

Run: `cd backend && .venv/bin/pytest tests/test_group_exposure.py -q` → PASS. `.venv/bin/ruff check . && .venv/bin/pytest -q` → green.

```bash
git add backend/app/services/groups/exposure.py backend/app/api/groups.py backend/tests/test_group_exposure.py
git commit -m "feat(groups): live exposure API (aggregate valuation by group + Ungrouped + filter, degrade)"
```

---

## Task 4: Daily snapshot job + trend API

**Files:**
- Create: `backend/app/services/groups/snapshot.py`
- Modify: `backend/app/services/groups/exposure.py` (`write_snapshot`), `backend/app/api/groups.py` (`GET /trend` + opportunistic write in `/exposure`), `backend/app/services/guru/scheduler.py` (add job), `backend/app/main.py` (startup catch-up)
- Test: `backend/tests/test_group_snapshot.py`

**Interfaces:**
- Consumes: `compute_group_exposure` (Task 3), `GroupSnapshot`, the scheduler `create_scheduler`.
- Produces: `write_snapshot(db, user, exposure_result, as_of)` (delete-today-then-insert); `run_group_snapshot_job(session_factory=None)`; `snapshot_catch_up(session_factory=None)`; route `GET /api/groups/trend`.

- [ ] **Step 1: Write the failing tests**

```python
# backend/tests/test_group_snapshot.py
from datetime import UTC, date, datetime
from decimal import Decimal

import pytest
from sqlalchemy import func, select

from app.models import GroupSnapshot

pytestmark = pytest.mark.asyncio(loop_scope="session")


async def _hold(auth_client, symbol, make_instrument):
    await make_instrument(symbol)
    pid = (await auth_client.post("/api/portfolios",
           json={"name": symbol, "kind": "real", "base_currency": "GBP"})).json()["id"]
    await auth_client.post(f"/api/portfolios/{pid}/positions", json={"symbol": symbol, "quantity": "1"})


async def test_write_snapshot_is_idempotent_delete_then_insert(auth_client, db_session, make_instrument):
    from app.services.groups.exposure import write_snapshot
    from app.models.user import User
    user = (await db_session.execute(select(User).where(User.email == "lee@test.dev"))).scalar_one()
    await _hold(auth_client, "AAPL", make_instrument)
    result = {"groups": [{"group_id": None, "name": "Ungrouped", "value_base": "42.00"}],
              "total_base": "42.00", "unpriced": []}
    today = date(2026, 7, 12)
    await write_snapshot(db_session, user, result, today)
    await write_snapshot(db_session, user, result, today)   # re-run same day
    n = (await db_session.execute(select(func.count()).select_from(GroupSnapshot)
         .where(GroupSnapshot.user_id == user.id, GroupSnapshot.as_of == today))).scalar_one()
    assert n == 1                                            # not duplicated
    val = (await db_session.execute(select(GroupSnapshot.value_base)
           .where(GroupSnapshot.user_id == user.id))).scalar_one()
    assert val == Decimal("42.00")


async def test_trend_returns_series_with_pct(auth_client, db_session, make_instrument):
    from app.models.user import User
    user = (await db_session.execute(select(User).where(User.email == "lee@test.dev"))).scalar_one()
    g = (await auth_client.post("/api/groups", json={"name": "Tech"})).json()
    db_session.add(GroupSnapshot(user_id=user.id, group_id=g["id"], as_of=date(2026, 7, 11),
                                 value_base=Decimal("80")))
    db_session.add(GroupSnapshot(user_id=user.id, group_id=None, as_of=date(2026, 7, 11),
                                 value_base=Decimal("20")))
    await db_session.commit()
    body = (await auth_client.get("/api/groups/trend?range=90d")).json()
    series = {s["name"]: s for s in body["series"]}
    tech_pt = series["Tech"]["points"][0]
    assert tech_pt["value_base"] == "80.00" and tech_pt["pct"] == "80.00"   # 80/(80+20)
```

- [ ] **Step 2: Run to verify failure**

Run: `cd backend && .venv/bin/pytest tests/test_group_snapshot.py -q`
Expected: FAIL (write_snapshot/trend missing).

- [ ] **Step 3: Add `write_snapshot`**

Append to `backend/app/services/groups/exposure.py`:

```python
from datetime import date as _date

from sqlalchemy import delete as _delete

from app.models import GroupSnapshot


async def write_snapshot(db, user, exposure_result: dict, as_of: _date) -> None:
    """Idempotent daily snapshot: delete this user's rows for `as_of`, then
    insert one per group (incl. the null Ungrouped bucket). Safe for the NULL
    group_id (a plain unique upsert can't dedupe NULLs)."""
    await db.execute(_delete(GroupSnapshot).where(
        GroupSnapshot.user_id == user.id, GroupSnapshot.as_of == as_of))
    for grp in exposure_result["groups"]:
        db.add(GroupSnapshot(user_id=user.id, group_id=grp["group_id"], as_of=as_of,
                             value_base=Decimal(grp["value_base"])))
    await db.flush()
```

- [ ] **Step 4: Add the snapshot job**

```python
# backend/app/services/groups/snapshot.py
"""Daily per-group value snapshots (forward-only trend history). Cheap, no LLM;
per-user failure-isolated; idempotent (write_snapshot delete-then-inserts)."""
import logging
from datetime import UTC, datetime

from sqlalchemy import select

from app.api.valuation import get_services
from app.core.db import SessionLocal
from app.models import Portfolio, User
from app.services.groups.exposure import compute_group_exposure, write_snapshot

logger = logging.getLogger(__name__)


async def _users_with_real_holdings(db) -> list[int]:
    return list((await db.execute(
        select(User.id).distinct()
        .join(Portfolio, Portfolio.user_id == User.id)
        .where(Portfolio.kind == "real")
    )).scalars().all())


async def run_group_snapshot_job(session_factory=None) -> None:
    factory = session_factory or SessionLocal
    quotes, fx = get_services()
    async with factory() as db:
        user_ids = await _users_with_real_holdings(db)
    today = datetime.now(UTC).date()
    for uid in user_ids:
        try:
            async with factory() as db:
                user = await db.get(User, uid)
                if user is None:
                    continue
                result = await compute_group_exposure(db, user, quotes, fx)
                await write_snapshot(db, user, result, today)
                await db.commit()
        except Exception:
            logger.exception("group snapshot failed for user %s", uid)


async def snapshot_catch_up(session_factory=None) -> None:
    """On startup, write today's snapshot if the job hasn't run yet today."""
    from app.models import GroupSnapshot
    factory = session_factory or SessionLocal
    async with factory() as db:
        today = datetime.now(UTC).date()
        exists = (await db.execute(
            select(GroupSnapshot.id).where(GroupSnapshot.as_of == today).limit(1)
        )).scalar_one_or_none()
    if exists is None:
        await run_group_snapshot_job(session_factory)
```

- [ ] **Step 5: Wire the scheduler + startup + trend route + opportunistic write**

`backend/app/services/guru/scheduler.py` — in `create_scheduler`, add:

```python
    from app.services.groups.snapshot import run_group_snapshot_job
    sched.add_job(run_group_snapshot_job, CronTrigger(
        hour=settings.guru_digest_hour, minute=30, timezone=settings.guru_timezone))
```

`backend/app/main.py` lifespan — after the existing `catch_up()` task, add:

```python
    from app.services.groups.snapshot import snapshot_catch_up
    snap_task = asyncio.create_task(snapshot_catch_up())
    snap_task.add_done_callback(_log_catch_up_result)
```

`backend/app/api/groups.py` — opportunistic write in `/exposure` (so viewing seeds today's point) and the trend route:

```python
from datetime import UTC, datetime

from app.services.groups.exposure import write_snapshot
from app.models import GroupSnapshot, HoldingGroup

# inside exposure(): after computing result and BEFORE returning:
    await write_snapshot(db, user, result, datetime.now(UTC).date())
    await db.commit()
    result["as_of"] = datetime.now(UTC).isoformat()
    return result


_RANGE_DAYS = {"30d": 30, "90d": 90, "1y": 365}


@router.get("/trend")
async def trend(db: SessionDep, user: CurrentUser, range: str = "30d"):
    from datetime import timedelta
    days = _RANGE_DAYS.get(range, 30)
    cutoff = datetime.now(UTC).date() - timedelta(days=days)
    rows = (await db.execute(
        select(GroupSnapshot).where(
            GroupSnapshot.user_id == user.id, GroupSnapshot.as_of >= cutoff)
        .order_by(GroupSnapshot.as_of)
    )).scalars().all()
    groups = {g.id: g for g in (await db.execute(
        select(HoldingGroup).where(HoldingGroup.user_id == user.id))).scalars().all()}
    # per-date totals for pct
    from collections import defaultdict
    from decimal import Decimal
    by_date: dict = defaultdict(lambda: Decimal("0"))
    for r in rows:
        by_date[r.as_of] += r.value_base
    series: dict = defaultdict(lambda: {"points": []})
    for r in rows:
        name = groups[r.group_id].name if r.group_id in groups else "Ungrouped"
        color = groups[r.group_id].color if r.group_id in groups else ""
        total = by_date[r.as_of]
        pct = (r.value_base / total * 100).quantize(Decimal("0.01")) if total > 0 else Decimal("0.00")
        s = series[(r.group_id, name, color)]
        s["points"].append({"as_of": r.as_of.isoformat(),
                            "value_base": str(r.value_base.quantize(Decimal("0.01"))),
                            "pct": str(pct)})
    out = [{"group_id": k[0], "name": k[1], "color": k[2], "points": v["points"]}
           for k, v in series.items()]
    return {"series": out, "as_of": datetime.now(UTC).isoformat()}
```

Remove the now-duplicated `result["as_of"]`/`return` from the earlier exposure body (keep a single write+commit+return). Ensure the earlier `exposure()` edit and this one compose into one coherent handler (opportunistic snapshot write, then return).

- [ ] **Step 6: Run tests**

Run: `cd backend && .venv/bin/pytest tests/test_group_snapshot.py tests/test_group_exposure.py -q` → PASS. Then `.venv/bin/ruff check . && .venv/bin/pytest -q` → green (scheduler job registered but not triggered in tests; `snapshot_catch_up` runs at app startup — the conftest app may trigger it, so ensure it's failure-isolated and no-ops without holdings).

- [ ] **Step 7: Commit**

```bash
git add backend/app/services/groups/ backend/app/api/groups.py backend/app/services/guru/scheduler.py backend/app/main.py backend/tests/test_group_snapshot.py
git commit -m "feat(groups): daily snapshot job + catch-up + opportunistic write + GET /trend"
```

---

## Task 5: Figma gate (USER GATE)

**Files:** none (Figma design artifacts).

- [ ] **Step 1: Produce Figma frames** for the **Sectors** page: (a) group management — the groups list (create / rename / recolor / delete / reorder), a "Seed from sectors" button, and a holdings list with a per-holding group `<select>`; (b) the exposure **breakdown** — horizontal bars (value + %) per group in the group color with today's change + a portfolio filter + `total_base` + an unpriced note; (c) the **trend** — an inline-SVG multi-line chart with a range selector and a "history is building" empty state. Match existing dashboard/detail styling (file key `0gU58wfjttdZS0NXQeEtuD`).
- [ ] **Step 2: Present to the user (inline PNGs) and get explicit approval before Task 6.**

---

## Task 6: Frontend — Sectors page (push seam)

**Files:**
- Modify: `frontend/src/lib/api.ts`, `frontend/src/lib/types.ts`, `frontend/src/App.tsx`
- Create: `frontend/src/pages/SectorsPage.tsx`, `frontend/src/components/TrendChart.tsx`
- Test: `frontend/src/pages/SectorsPage.test.tsx`, `frontend/src/components/TrendChart.test.tsx`

**Interfaces:**
- Consumes: `GET/POST /api/groups`, `PATCH/DELETE /api/groups/{id}`, `PUT /api/groups/assign`, `POST /api/groups/seed-from-sectors`, `GET /api/groups/exposure`, `GET /api/groups/trend`.

- [ ] **Step 1: Types + API client** — add `HoldingGroup`, `GroupExposure`/`GroupExposureItem`, `GroupTrend`/`TrendSeries` types (mirror the backend shapes) + `getGroups`, `createGroup`, `updateGroup`, `deleteGroup`, `assignGroup`, `seedGroups`, `getGroupExposure`, `getGroupTrend` in `lib/api.ts`.
- [ ] **Step 2: TrendChart (TDD + axe)** — a pure presentational inline-SVG multi-line chart: props `series: {name, color, points:[{as_of, value_base|pct}]}[]`, a `metric: "value"|"pct"` toggle. Write a failing test that renders 2 series and asserts N `<polyline>`/`<path>` with the right point count + an accessible `<title>`/`role="img"` label; empty-series → "history is building" message. Then implement (compute min/max, map to a viewBox, draw a polyline per series in its color). No external lib.
- [ ] **Step 3: SectorsPage (TDD + axe)** — write a failing `SectorsPage.test.tsx` (mock fetch): renders groups + exposure bars (value/%), clicking **Seed from sectors** calls `seedGroups` + refetches, changing a holding's group `<select>` calls `assignGroup`, creating a group calls `createGroup`, and the TrendChart renders from `getGroupTrend`. axe on the populated page. Then implement `SectorsPage.tsx`: management section (groups CRUD + seed + per-holding assign), breakdown bars (color + value + % + day change + portfolio filter + unpriced note), and `<TrendChart>` with a range selector. Use TanStack Query; invalidate `["groups"]`/`["group-exposure"]` on mutations.
- [ ] **Step 4: Route + nav** — add `/sectors` (under `RequireAuth`) in `App.tsx` + a "Sectors" nav item.
- [ ] **Step 5: Verify** — `cd frontend && npm run check` → green.
- [ ] **Step 6: Commit + push (push seam — reaches prod)**

```bash
git add frontend/src
git commit -m "feat(groups): Sectors page — manage + exposure breakdown + inline-SVG trend (frontend)"
git push origin main
```

Confirm CI green (matched by head SHA); Railway deploys the backend (migration 0012 runs), Vercel the frontend.

---

## Task 7: Docs + live smoke + final Opus review

- [ ] **Step 1: Live smoke** on prod — `GET /api/groups` (401 unauth check on the new routes); seed-from-sectors creates groups from your holdings; assign moves a stock; exposure returns per-group value/% + Ungrouped; trend returns (may be near-empty until snapshots accrue). Confirm migration 0012 ran (`railway logs … | grep 0012`), health 200.
- [ ] **Step 2: Docs** — AGENTS.md (head → 0012; the groups surface + daily snapshot job), `docs/PROGRESS.md` (new section), README.
- [ ] **Step 3: Final whole-branch review on Opus** — base = the pre-Task-1 tip. Focus: cross-user scoping (all group routes + exposure + trend own-data only), degrade-never-500 (exposure/snapshot), snapshot idempotency (delete-then-insert, null bucket), seed idempotent+non-destructive, `value_base` encrypted, cascade-on-delete. Fix wave → re-review to merge-clean; push fixes; refresh docs if changed.
- [ ] **Step 4: Commit doc/fix changes + push.**

---

## Self-Review (completed by the plan author)

**1. Spec coverage:** 3 tables (`HoldingGroup`/`GroupAssignment` one-per-stock/`GroupSnapshot` encrypted) → Task 1. Groups CRUD + idempotent non-destructive seed → Task 2. Live exposure (aggregate valuation by group + Ungrouped + portfolio filter + degrade) → Task 3. Daily snapshot job + catch-up + opportunistic write + trend → Task 4. Figma gate → Task 5. Sectors page (manage + bars + inline-SVG trend) → Task 6. Docs+smoke+Opus → Task 7. Every spec §1–§6 requirement maps to a task.

**2. Placeholder scan:** no `TBD`/vague directives; degrade/encryption/idempotency behaviours are concrete code. The Task-3 test flags a "confirm PositionValue/PortfolioSummary constructor field names" check — that's a real integration caution (the dataclass kwargs must match `app/services/valuation.py`), not a placeholder; full stub code is given.

**3. Type consistency:** `compute_group_exposure(db, user, quote_service, fx, portfolio_id=None) -> dict{groups,total_base,unpriced}` (Task 3) is consumed by the exposure route and `run_group_snapshot_job`/`write_snapshot` (Task 4). `write_snapshot(db, user, exposure_result, as_of)` reads `exposure_result["groups"][].{group_id,value_base}` — matches Task 3's output keys. `GroupSnapshot.value_base` `EncryptedDecimal` (Task 1) is written as `Decimal` (Task 4) and read/aggregated in `/trend`. `GroupOut`/exposure/trend shapes (Tasks 2-4) match the frontend types (Task 6). `user_held_instruments` (Task 2) reused conceptually; the exposure service does its own portfolio load (real-only) consistently.

**Executor notes:** confirm `PositionValue`/`PortfolioSummary` dataclass field names in `app/services/valuation.py` before finalizing the Task-3 stub (adjust kwargs). `snapshot_catch_up` runs at app startup (conftest builds the app) — it MUST be failure-isolated and a no-op when there are no holdings, so it can't break the test app boot. The exposure route's opportunistic `write_snapshot` + the trend route both live in `groups.py`; compose the two `exposure()` edits (Task 3 route + Task 4 opportunistic write) into ONE handler that writes today's snapshot then returns.
