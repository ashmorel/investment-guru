from fastapi import APIRouter, HTTPException
from sqlalchemy import select

from app.api.deps import CurrentUser, SessionDep
from app.models import Portfolio
from app.schemas.portfolio import PortfolioCreate, PortfolioOut, PortfolioUpdate

router = APIRouter(prefix="/api/portfolios", tags=["portfolios"])


def _out(pf: Portfolio) -> PortfolioOut:
    return PortfolioOut(
        id=pf.id, name=pf.name, kind=pf.kind,
        base_currency=pf.base_currency, position_count=len(pf.positions),
    )


async def get_owned_portfolio(db: SessionDep, user: CurrentUser, portfolio_id: int) -> Portfolio:
    pf = await db.get(Portfolio, portfolio_id)
    if pf is None or pf.user_id != user.id:
        raise HTTPException(status_code=404, detail="Portfolio not found")
    return pf


@router.get("", response_model=list[PortfolioOut])
async def list_portfolios(db: SessionDep, user: CurrentUser) -> list[PortfolioOut]:
    result = await db.execute(
        select(Portfolio).where(Portfolio.user_id == user.id).order_by(Portfolio.id)
    )
    return [_out(p) for p in result.scalars().all()]


@router.post("", response_model=PortfolioOut, status_code=201)
async def create_portfolio(
    body: PortfolioCreate, db: SessionDep, user: CurrentUser
) -> PortfolioOut:
    pf = Portfolio(user_id=user.id, **body.model_dump())
    db.add(pf)
    await db.commit()
    await db.refresh(pf)
    return _out(pf)


@router.patch("/{portfolio_id}", response_model=PortfolioOut)
async def update_portfolio(
    portfolio_id: int, body: PortfolioUpdate, db: SessionDep, user: CurrentUser
) -> PortfolioOut:
    pf = await get_owned_portfolio(db, user, portfolio_id)
    for field, value in body.model_dump(exclude_none=True).items():
        setattr(pf, field, value)
    await db.commit()
    await db.refresh(pf)
    return _out(pf)


@router.delete("/{portfolio_id}", status_code=204)
async def delete_portfolio(portfolio_id: int, db: SessionDep, user: CurrentUser) -> None:
    pf = await get_owned_portfolio(db, user, portfolio_id)
    await db.delete(pf)
    await db.commit()
