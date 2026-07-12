import logging
from collections import defaultdict
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from app.api.deps import CurrentUser, SessionDep
from app.api.guru import GuruDep, ReportOut, _report_out, map_guru_errors
from app.api.valuation import get_services
from app.models import (
    GroupAssignment,
    GroupSnapshot,
    GuruReport,
    HoldingGroup,
    Instrument,
    Portfolio,
    Position,
)
from app.services.groups.exposure import compute_group_exposure, write_snapshot

_RANGE_DAYS = {"30d": 30, "90d": 90, "1y": 365}

logger = logging.getLogger(__name__)

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


class HoldingOut(BaseModel):
    symbol: str
    name: str
    group_id: int | None
    group_name: str | None


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


@router.get("/holdings", response_model=list[HoldingOut])
async def list_holdings(db: SessionDep, user: CurrentUser):
    """The user's real held instruments with their current group (if any).
    Ungrouped holdings report group_id/group_name = None. User-scoped."""
    insts = await user_held_instruments(db, user.id)
    groups = {g.id: g for g in (await db.execute(
        select(HoldingGroup).where(HoldingGroup.user_id == user.id))).scalars().all()}
    inst_to_group = {iid: gid for gid, iid in (await db.execute(
        select(GroupAssignment.group_id, GroupAssignment.instrument_id)
        .where(GroupAssignment.user_id == user.id))).all()}
    out = []
    for inst in sorted(insts, key=lambda i: i.symbol):
        gid = inst_to_group.get(inst.id)
        group = groups.get(gid) if gid is not None else None
        out.append(HoldingOut(symbol=inst.symbol, name=inst.name,
                              group_id=group.id if group else None,
                              group_name=group.name if group else None))
    return out


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
    if portfolio_id is None:
        # Opportunistic snapshot: only for the whole-account view. A
        # portfolio-scoped view is a partial breakdown and would corrupt
        # today's trend point if written as if it were the full picture.
        # Best-effort only (the daily job / a concurrent request already
        # covers today), so a failure here — e.g. two concurrent first-of-day
        # requests racing the (user_id, group_id, as_of) unique constraint —
        # must never fail the exposure request. Roll back and still return 200.
        try:
            await write_snapshot(db, user, result, datetime.now(UTC).date())
            await db.commit()  # persists snapshot rows + FxRate rows cached by fx.get_rate
        except Exception:
            logger.exception("opportunistic group snapshot write failed")
            await db.rollback()
    else:
        await db.commit()  # persist any FxRate rows cached by fx.get_rate during valuation
    result["as_of"] = datetime.now(UTC).isoformat()
    return result


@router.get("/trend")
async def trend(db: SessionDep, user: CurrentUser, range: str = "30d"):
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
    by_date: dict = defaultdict(lambda: Decimal("0"))
    for r in rows:
        by_date[r.as_of] += r.value_base
    series: dict = defaultdict(lambda: {"points": []})
    for r in rows:
        name = groups[r.group_id].name if r.group_id in groups else "Ungrouped"
        color = groups[r.group_id].color if r.group_id in groups else ""
        total = by_date[r.as_of]
        pct = ((r.value_base / total * 100).quantize(Decimal("0.01"))
               if total > 0 else Decimal("0.00"))
        s = series[(r.group_id, name, color)]
        s["points"].append({"as_of": r.as_of.isoformat(),
                            "value_base": str(r.value_base.quantize(Decimal("0.01"))),
                            "pct": str(pct)})
    out = [{"group_id": k[0], "name": k[1], "color": k[2], "points": v["points"]}
           for k, v in series.items()]
    return {"series": out, "as_of": datetime.now(UTC).isoformat()}


# --- sector-rotation advice (Guru) ------------------------------------------

@router.post("/rotation", response_model=ReportOut, status_code=201)
async def create_rotation(db: SessionDep, user: CurrentUser, guru: GuruDep):
    with map_guru_errors():
        report = await guru.generate_rotation(db, user)
    return _report_out(report)


@router.get("/rotation", response_model=ReportOut | None)
async def read_rotation(db: SessionDep, user: CurrentUser):
    r = (await db.execute(
        select(GuruReport).where(GuruReport.user_id == user.id, GuruReport.kind == "rotation")
        .order_by(GuruReport.created_at.desc(), GuruReport.id.desc()).limit(1)
    )).scalar_one_or_none()
    return _report_out(r) if r is not None else None
