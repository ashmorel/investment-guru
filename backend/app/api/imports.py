from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, UploadFile
from sqlalchemy import select

from app.api.deps import CurrentUser, SessionDep
from app.api.instruments import get_provider
from app.api.portfolios import get_owned_portfolio
from app.models import Instrument, Portfolio, Position
from app.schemas.imports import ImportCommitIn, ImportCommitOut
from app.services.csv_import import CsvFormatError, parse_yahoo_csv
from app.services.market_data.base import MarketDataProvider

router = APIRouter(prefix="/api/imports", tags=["imports"])


async def _resolve_instrument(db, provider, symbol: str) -> Instrument | None:
    inst = (
        await db.execute(select(Instrument).where(Instrument.symbol == symbol))
    ).scalar_one_or_none()
    if inst is not None:
        return inst
    info = await provider.lookup(symbol)
    if info is None:
        return None
    inst = Instrument(
        symbol=info.symbol, name=info.name, exchange=info.exchange, market=info.market,
        currency=info.currency, sector=info.sector, industry=info.industry,
    )
    db.add(inst)
    await db.flush()
    return inst


@router.post("/preview")
async def preview(
    file: UploadFile,
    db: SessionDep,
    user: CurrentUser,
    provider: Annotated[MarketDataProvider, Depends(get_provider)],
):
    data = await file.read()
    try:
        parsed = parse_yahoo_csv(data)
    except CsvFormatError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    rows = []
    for row in parsed:
        inst = await _resolve_instrument(db, provider, row.symbol)
        rows.append({
            "symbol": row.symbol,
            "quantity": None if row.quantity is None else str(row.quantity),
            "purchase_price": None if row.purchase_price is None else str(row.purchase_price),
            "comment": row.comment,
            "known": inst is not None,
        })
    await db.commit()
    return {"rows": rows, "errors": []}


@router.post("/commit", response_model=ImportCommitOut)
async def commit(
    body: ImportCommitIn,
    db: SessionDep,
    user: CurrentUser,
    provider: Annotated[MarketDataProvider, Depends(get_provider)],
):
    if body.portfolio_id is not None:
        pf = await get_owned_portfolio(db, user, body.portfolio_id)
    elif body.new_portfolio is not None:
        pf = Portfolio(user_id=user.id, **body.new_portfolio.model_dump())
        db.add(pf)
        await db.flush()
    else:
        raise HTTPException(status_code=422, detail="portfolio_id or new_portfolio required")

    # resolve all instruments first — all-or-nothing
    instruments: dict[str, Instrument] = {}
    for row in body.rows:
        inst = await _resolve_instrument(db, provider, row.symbol)
        if inst is None:
            await db.rollback()
            raise HTTPException(status_code=422, detail=f"Unknown symbol {row.symbol}")
        instruments[row.symbol] = inst

    existing = {
        p.instrument.symbol: p
        for p in (
            await db.execute(select(Position).where(Position.portfolio_id == pf.id))
        ).scalars().all()
    }

    created = updated = skipped = 0
    for row in body.rows:
        current = existing.get(row.symbol)
        if current is None:
            db.add(Position(
                portfolio_id=pf.id, instrument_id=instruments[row.symbol].id,
                quantity=row.quantity, avg_cost=row.avg_cost,
            ))
            created += 1
        elif body.merge == "skip":
            skipped += 1
        elif body.merge == "update":
            current.quantity = row.quantity
            current.avg_cost = row.avg_cost
            updated += 1
        else:  # replace
            await db.delete(current)
            await db.flush()
            db.add(Position(
                portfolio_id=pf.id, instrument_id=instruments[row.symbol].id,
                quantity=row.quantity, avg_cost=row.avg_cost,
            ))
            updated += 1

    await db.commit()
    return ImportCommitOut(created=created, updated=updated, skipped=skipped, portfolio_id=pf.id)
