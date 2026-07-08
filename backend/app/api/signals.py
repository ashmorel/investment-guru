from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy import select

from app.api.deps import CurrentUser, SessionDep
from app.api.portfolios import get_owned_portfolio
from app.models import Instrument, Portfolio, Signal
from app.services.signals.engine import SignalEngine, get_engine

router = APIRouter(prefix="/api", tags=["signals"])

_SEV_ORDER = {"high": 0, "watch": 1, "info": 2}


def get_analyzer() -> SignalEngine:
    return get_engine()


def _sig_out(sig: Signal, symbol: str | None) -> dict:
    return {
        "id": sig.id, "instrument_id": sig.instrument_id, "symbol": symbol,
        "kind": sig.kind, "severity": sig.severity, "title": sig.title,
        "detail": sig.detail, "data": sig.data,
        "computed_at": sig.computed_at.isoformat(),
    }


async def _symbol_map(db, instrument_ids: set[int]) -> dict[int, str]:
    if not instrument_ids:
        return {}
    rows = (await db.execute(
        select(Instrument.id, Instrument.symbol).where(Instrument.id.in_(instrument_ids))
    )).all()
    return {i: s for (i, s) in rows}


@router.post("/portfolios/{portfolio_id}/analyze")
async def analyze(
    portfolio_id: int, db: SessionDep, user: CurrentUser,
    analyzer: Annotated[SignalEngine, Depends(get_analyzer)],
):
    pf = await get_owned_portfolio(db, user, portfolio_id)
    result = await analyzer.analyze(db, pf)
    await db.commit()
    ids = {s.instrument_id for s in result.signals if s.instrument_id is not None}
    symbols = await _symbol_map(db, ids)
    return {
        "signals": [_sig_out(s, symbols.get(s.instrument_id)) for s in result.signals],
        "as_of": result.as_of.isoformat(),
        "unavailable_inputs": result.unavailable_inputs,
    }


@router.get("/portfolios/{portfolio_id}/signals")
async def read_signals(portfolio_id: int, db: SessionDep, user: CurrentUser):
    pf = await get_owned_portfolio(db, user, portfolio_id)
    rows = (await db.execute(
        select(Signal).where(Signal.portfolio_id == pf.id)
    )).scalars().all()
    rows = sorted(rows, key=lambda s: (_SEV_ORDER.get(s.severity, 9),))
    ids = {s.instrument_id for s in rows if s.instrument_id is not None}
    symbols = await _symbol_map(db, ids)
    computed_at = rows[0].computed_at.isoformat() if rows else None
    return {
        "signals": [_sig_out(s, symbols.get(s.instrument_id)) for s in rows],
        "computed_at": computed_at,
    }


@router.get("/dashboard/attention")
async def attention(db: SessionDep, user: CurrentUser):
    rows = (await db.execute(
        select(Signal, Portfolio.name)
        .join(Portfolio, Signal.portfolio_id == Portfolio.id)
        .where(Portfolio.user_id == user.id)
    )).all()
    rows = sorted(
        rows, key=lambda r: (_SEV_ORDER.get(r[0].severity, 9), _neg_ts(r[0].computed_at))
    )
    ids = {s.instrument_id for (s, _) in rows if s.instrument_id is not None}
    symbols = await _symbol_map(db, ids)
    out = []
    for sig, pf_name in rows:
        item = _sig_out(sig, symbols.get(sig.instrument_id))
        item["portfolio_id"] = sig.portfolio_id
        item["portfolio_name"] = pf_name
        out.append(item)
    return {"signals": out}


def _neg_ts(dt: datetime) -> float:
    return -dt.timestamp()
