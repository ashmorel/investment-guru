import json
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Instrument, InvestorProfile, Portfolio, Signal, User
from app.services.market_data.quotes import QuoteService
from app.services.valuation import FxService, PortfolioSummary, PositionValuation, value_portfolio

MAX_CONTEXT_CHARS = 60_000

# Same severity ordering idiom as app/api/signals.py.
_SEV_ORDER = {"high": 0, "watch": 1, "info": 2}


def _dec(value: Decimal | None) -> str | None:
    """Render a Decimal as str for JSON safety; pass through None untouched."""
    return str(value) if value is not None else None


def _profile_dict(profile: InvestorProfile | None) -> dict[str, Any]:
    if profile is None:
        return {
            "risk_appetite": "balanced",
            "horizon": "medium",
            "sector_interests": [],
            "free_text": "",
        }
    return {
        "risk_appetite": profile.risk_appetite,
        "horizon": profile.horizon,
        "sector_interests": profile.sector_interests,
        "free_text": profile.free_text,
    }


async def _symbol_map(db: AsyncSession, instrument_ids: set[int]) -> dict[int, str]:
    if not instrument_ids:
        return {}
    rows = (
        await db.execute(
            select(Instrument.id, Instrument.symbol).where(Instrument.id.in_(instrument_ids))
        )
    ).all()
    return {i: s for (i, s) in rows}


def _position_dict(pv: PositionValuation) -> dict[str, Any]:
    return {
        "symbol": pv.symbol,
        "name": pv.name,
        "market": pv.market,
        "quantity": _dec(pv.quantity),
        "market_value": _dec(pv.market_value_base),
        "unrealized_pnl_pct": _dec(pv.unrealized_pnl_pct),
        "day_change": _dec(pv.day_change_base),
        "currency": pv.native_currency,
        "currency_mismatch": pv.currency_mismatch,
        "watchlist_entry": pv.quantity is None,
    }


def _portfolio_dict(pf: Portfolio, summary: PortfolioSummary) -> dict[str, Any]:
    return {
        "name": pf.name,
        "base_currency": summary.base_currency,
        "total_value": _dec(summary.total_value),
        "total_pnl": _dec(summary.total_pnl),
        "day_change": _dec(summary.day_change),
        "integrity": {
            "costed_positions": summary.costed_positions,
            "priced_positions": summary.priced_positions,
            "unpriced_positions": summary.unpriced_positions,
            "day_change_partial": summary.day_change_partial,
        },
        "positions": [_position_dict(pv) for pv in summary.positions],
    }


async def _portfolio_signals(db: AsyncSession, pf: Portfolio) -> list[dict[str, Any]]:
    rows = (
        await db.execute(select(Signal).where(Signal.portfolio_id == pf.id))
    ).scalars().all()
    rows = sorted(rows, key=lambda s: (_SEV_ORDER.get(s.severity, 9), -s.computed_at.timestamp()))
    ids = {s.instrument_id for s in rows if s.instrument_id is not None}
    symbols = await _symbol_map(db, ids)
    return [
        {
            "portfolio": pf.name,
            "symbol": symbols.get(s.instrument_id),
            "kind": s.kind,
            "severity": s.severity,
            "title": s.title,
            "detail": s.detail,
        }
        for s in rows
    ]


def _truncate(ctx: dict) -> dict:
    while len(json.dumps(ctx)) > MAX_CONTEXT_CHARS:
        all_pos = [(pf, p) for pf in ctx["portfolios"] for p in pf["positions"]]
        if not all_pos:
            break
        pf, smallest = min(
            all_pos, key=lambda t: Decimal(t[1]["market_value"] or "0")
        )
        pf["positions"].remove(smallest)
        ctx["context_truncated"] = True
    return ctx


async def build_context(
    db: AsyncSession,
    user: User,
    *,
    quote_service: QuoteService,
    fx: FxService,
    portfolios: list[Portfolio],
    profile: InvestorProfile | None,
) -> dict:
    ctx: dict[str, Any] = {
        "profile": _profile_dict(profile),
        "portfolios": [],
        "signals": [],
        "as_of": datetime.now(UTC).isoformat(),
        "context_truncated": False,
    }
    for pf in portfolios:
        summary = await value_portfolio(db, pf, quote_service, fx)
        ctx["portfolios"].append(_portfolio_dict(pf, summary))
        ctx["signals"].extend(await _portfolio_signals(db, pf))

    return _truncate(ctx)
