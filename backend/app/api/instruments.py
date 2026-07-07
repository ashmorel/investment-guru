from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select

from app.api.deps import CurrentUser, SessionDep
from app.models import Instrument
from app.services.market_data.base import MarketDataProvider
from app.services.market_data.quotes import get_quote_service

router = APIRouter(prefix="/api/instruments", tags=["instruments"])


def get_provider() -> MarketDataProvider:
    return get_quote_service().provider


class InstrumentOut(BaseModel):
    symbol: str
    name: str
    exchange: str
    market: str
    currency: str
    sector: str | None
    industry: str | None
    known: bool


@router.get("/lookup", response_model=InstrumentOut)
async def lookup(
    symbol: str,
    db: SessionDep,
    user: CurrentUser,
    provider: Annotated[MarketDataProvider, Depends(get_provider)],
) -> InstrumentOut:
    existing = (
        await db.execute(select(Instrument).where(Instrument.symbol == symbol))
    ).scalar_one_or_none()
    if existing is not None:
        return InstrumentOut(
            symbol=existing.symbol, name=existing.name, exchange=existing.exchange,
            market=existing.market, currency=existing.currency,
            sector=existing.sector, industry=existing.industry, known=True,
        )
    info = await provider.lookup(symbol)
    if info is None:
        raise HTTPException(status_code=404, detail=f"Symbol {symbol} not found")
    inst = Instrument(
        symbol=info.symbol, name=info.name, exchange=info.exchange, market=info.market,
        currency=info.currency, sector=info.sector, industry=info.industry,
    )
    db.add(inst)
    await db.commit()
    return InstrumentOut(**info.__dict__, known=False)
