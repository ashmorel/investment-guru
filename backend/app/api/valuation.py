from datetime import UTC, datetime

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import select

from app.api.deps import CurrentUser, SessionDep
from app.api.portfolios import get_owned_portfolio
from app.models import Portfolio
from app.services.market_data.quotes import QuoteService, get_quote_service
from app.services.valuation import FxService, value_portfolio

router = APIRouter(prefix="/api", tags=["valuation"])


def get_services() -> tuple[QuoteService, FxService]:
    qs = get_quote_service()
    return qs, FxService(qs.provider)


class DashboardPortfolio(BaseModel):
    id: int
    name: str
    kind: str
    base_currency: str
    total_value: str | None
    day_change: str | None
    total_pnl_pct: str | None


class DashboardOut(BaseModel):
    portfolios: list[DashboardPortfolio]
    as_of: datetime


def _s(v) -> str | None:
    return None if v is None else str(v)


@router.get("/portfolios/{portfolio_id}/valuation")
async def portfolio_valuation(
    portfolio_id: int,
    db: SessionDep,
    user: CurrentUser,
    services: tuple[QuoteService, FxService] = Depends(get_services),
):
    pf = await get_owned_portfolio(db, user, portfolio_id)
    quote_service, fx = services
    summary = await value_portfolio(db, pf, quote_service, fx)
    return {
        "portfolio_id": summary.portfolio_id,
        "base_currency": summary.base_currency,
        "total_value": _s(summary.total_value),
        "total_cost": _s(summary.total_cost),
        "total_pnl": _s(summary.total_pnl),
        "total_pnl_pct": _s(summary.total_pnl_pct),
        "day_change": _s(summary.day_change),
        "currency_exposure": {k: str(v) for k, v in summary.currency_exposure.items()},
        "priced_positions": summary.priced_positions,
        "unpriced_positions": summary.unpriced_positions,
        "positions": [
            {
                "position_id": p.position_id, "symbol": p.symbol, "name": p.name,
                "market": p.market, "quantity": _s(p.quantity), "avg_cost": _s(p.avg_cost),
                "native_currency": p.native_currency, "price": _s(p.price),
                "market_value_base": _s(p.market_value_base),
                "cost_basis_base": _s(p.cost_basis_base),
                "unrealized_pnl_base": _s(p.unrealized_pnl_base),
                "unrealized_pnl_pct": _s(p.unrealized_pnl_pct),
                "day_change_base": _s(p.day_change_base),
                "quote_as_of": p.quote_as_of.isoformat() if p.quote_as_of else None,
            }
            for p in summary.positions
        ],
    }


@router.get("/dashboard", response_model=DashboardOut)
async def dashboard(
    db: SessionDep,
    user: CurrentUser,
    services: tuple[QuoteService, FxService] = Depends(get_services),
):
    quote_service, fx = services
    result = await db.execute(
        select(Portfolio).where(Portfolio.user_id == user.id).order_by(Portfolio.id)
    )
    out: list[DashboardPortfolio] = []
    for pf in result.scalars().all():
        summary = await value_portfolio(db, pf, quote_service, fx)
        out.append(
            DashboardPortfolio(
                id=pf.id, name=pf.name, kind=pf.kind, base_currency=pf.base_currency,
                total_value=_s(summary.total_value), day_change=_s(summary.day_change),
                total_pnl_pct=_s(summary.total_pnl_pct),
            )
        )
    return DashboardOut(portfolios=out, as_of=datetime.now(UTC))
