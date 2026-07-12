from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import func, select
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
