from fastapi import APIRouter, HTTPException
from sqlalchemy import select

from app.api.deps import CurrentUser, SessionDep
from app.api.portfolios import get_owned_portfolio
from app.models import Instrument, Portfolio, Position
from app.schemas.position import PositionCreate, PositionOut, PositionUpdate

router = APIRouter(prefix="/api", tags=["positions"])


def _out(pos: Position) -> PositionOut:
    return PositionOut(
        id=pos.id,
        symbol=pos.instrument.symbol,
        name=pos.instrument.name,
        market=pos.instrument.market,
        currency=pos.instrument.currency,
        quantity=pos.quantity,
        avg_cost=pos.avg_cost,
        notes=pos.notes,
    )


async def _get_owned_position(
    db: SessionDep, user: CurrentUser, position_id: int
) -> Position:
    pos = await db.get(Position, position_id)
    if pos is None:
        raise HTTPException(status_code=404, detail="Position not found")
    pf = await db.get(Portfolio, pos.portfolio_id)
    if pf is None or pf.user_id != user.id:
        raise HTTPException(status_code=404, detail="Position not found")
    return pos


@router.get("/portfolios/{portfolio_id}/positions", response_model=list[PositionOut])
async def list_positions(portfolio_id: int, db: SessionDep, user: CurrentUser):
    pf = await get_owned_portfolio(db, user, portfolio_id)
    return [_out(p) for p in pf.positions]


@router.post(
    "/portfolios/{portfolio_id}/positions", response_model=PositionOut, status_code=201
)
async def create_position(
    portfolio_id: int, body: PositionCreate, db: SessionDep, user: CurrentUser
):
    pf = await get_owned_portfolio(db, user, portfolio_id)
    inst = (
        await db.execute(select(Instrument).where(Instrument.symbol == body.symbol))
    ).scalar_one_or_none()
    if inst is None:
        raise HTTPException(status_code=422, detail=f"Unknown symbol {body.symbol}")
    pos = Position(
        portfolio_id=pf.id,
        instrument_id=inst.id,
        quantity=body.quantity,
        avg_cost=body.avg_cost,
        notes=body.notes,
    )
    db.add(pos)
    await db.commit()
    await db.refresh(pos)
    return _out(pos)


@router.patch("/positions/{position_id}", response_model=PositionOut)
async def update_position(
    position_id: int, body: PositionUpdate, db: SessionDep, user: CurrentUser
):
    pos = await _get_owned_position(db, user, position_id)
    for field, value in body.model_dump(exclude_unset=True).items():
        setattr(pos, field, value)
    await db.commit()
    await db.refresh(pos)
    return _out(pos)


@router.delete("/positions/{position_id}", status_code=204)
async def delete_position(position_id: int, db: SessionDep, user: CurrentUser) -> None:
    pos = await _get_owned_position(db, user, position_id)
    await db.delete(pos)
    await db.commit()
