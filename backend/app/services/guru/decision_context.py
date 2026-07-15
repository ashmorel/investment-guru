import json
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import select

from app.models import (
    HoldingGroup,
    Instrument,
    InvestorProfile,
    NewsItem,
    Portfolio,
    Position,
    Signal,
)
from app.services.groups.exposure import compute_group_exposure
from app.services.guru.context import MAX_CONTEXT_CHARS, _profile_dict
from app.services.market_data.news_read import dedupe
from app.services.recommendations.candidates import CandidateSeed, assemble_candidates
from app.services.recommendations.scoring import ScoredCandidate, score_candidates
from app.services.valuation import value_portfolio

_SEV_ORDER = {"high": 0, "watch": 1, "info": 2}
_MAX_HEADLINES = 20
_MAX_CANDIDATES = 12


class DecisionContextTooLarge(RuntimeError):
    """The required held-symbol skeleton cannot fit the context ceiling."""


def _string(value: Decimal | None) -> str | None:
    return None if value is None else str(value)


async def _profile(db, user):
    return (
        await db.execute(
            select(InvestorProfile).where(InvestorProfile.user_id == user.id)
        )
    ).scalar_one_or_none()


async def _portfolios(db, user):
    return (
        await db.execute(
            select(Portfolio).where(
                Portfolio.user_id == user.id, Portfolio.kind == "real"
            )
        )
    ).scalars().all()


async def _aggregate_holdings(db, portfolios, quote_service, fx):
    aggregate: dict[int, dict[str, Any]] = {}
    valuation_available = True
    for portfolio in portfolios:
        try:
            summary = await value_portfolio(db, portfolio, quote_service, fx)
            valued = {row.position_id: row for row in summary.positions}
            rate = await fx.get_rate(db, portfolio.base_currency, "GBP")
        except Exception:
            valued = {}
            rate = None
            valuation_available = False
        for position in portfolio.positions:
            row = valued.get(position.id)
            available = (
                row is not None and row.market_value_base is not None and rate is not None
            )
            valuation_available = valuation_available and available
            item = aggregate.setdefault(
                position.instrument_id,
                {
                    "symbol": position.instrument.symbol,
                    "name": position.instrument.name,
                    "market": position.instrument.market,
                    "currency": position.instrument.currency,
                    "sector": position.instrument.sector,
                    "quantity_decimal": Decimal("0"),
                    "value_decimal": Decimal("0"),
                    "has_value": False,
                    "source_portfolio_ids": [],
                    "availability": {"valuation": True},
                },
            )
            if position.quantity is not None:
                item["quantity_decimal"] += position.quantity
            item["source_portfolio_ids"].append(portfolio.id)
            if available:
                item["value_decimal"] += row.market_value_base * rate
                item["has_value"] = True
            else:
                item["availability"]["valuation"] = False
    holdings = []
    for item in aggregate.values():
        item["quantity"] = _string(item.pop("quantity_decimal"))
        value = item.pop("value_decimal")
        item["market_value"] = (
            _string(value.quantize(Decimal("0.01")))
            if item.pop("has_value")
            else None
        )
        item["source_portfolio_ids"].sort()
        holdings.append(item)
    holdings.sort(key=lambda row: row["symbol"])
    return holdings, valuation_available


async def _signals(db, user):
    rows = (
        await db.execute(
            select(Signal, Portfolio.name, Instrument.symbol)
            .join(Portfolio, Portfolio.id == Signal.portfolio_id)
            .outerjoin(Instrument, Instrument.id == Signal.instrument_id)
            .where(Portfolio.user_id == user.id, Portfolio.kind == "real")
        )
    ).all()
    rows.sort(key=lambda row: (_SEV_ORDER.get(row[0].severity, 9), -row[0].computed_at.timestamp()))
    return [
        {
            "evidence_ref": f"signal:{signal.id}",
            "portfolio": portfolio_name,
            "symbol": symbol,
            "kind": signal.kind,
            "severity": signal.severity,
            "title": signal.title,
            "detail": signal.detail,
        }
        for signal, portfolio_name, symbol in rows
    ]


async def _news(db, instrument_ids: set[int]):
    if not instrument_ids:
        return []
    rows = (
        await db.execute(
            select(NewsItem, Instrument.symbol)
            .join(Instrument, Instrument.id == NewsItem.instrument_id)
            .where(NewsItem.instrument_id.in_(instrument_ids))
        )
    ).all()
    symbols = {item.id: symbol for item, symbol in rows}
    items = dedupe([item for item, _ in rows])[:_MAX_HEADLINES]
    return [
        {
            "evidence_ref": f"news:{item.id}",
            "symbol": symbols[item.id],
            "headline": item.title,
            "source": item.source,
            "url": item.url,
            "published_at": item.published_at.isoformat() if item.published_at else None,
        }
        for item in items
    ]


async def _candidate_context(db, user, profile, quote_service, holdings):
    seeds = await assemble_candidates(db, user, profile)
    quotes: dict[str, Any] = {}
    input_availability = {
        "quotes": True,
        "history": True,
        "fundamentals": True,
        "news": True,
        "signals": True,
        "diversification": True,
    }
    try:
        quotes = await quote_service.get_quotes(db, [seed.symbol for seed in seeds])
    except Exception:
        input_availability["quotes"] = False
    if seeds and not quotes:
        # QuoteService deliberately degrades provider exceptions to an empty result.
        input_availability["quotes"] = False

    relevant = (
        await db.execute(
            select(Instrument)
            .join(Position, Position.instrument_id == Instrument.id)
            .join(Portfolio, Portfolio.id == Position.portfolio_id)
            .where(Portfolio.user_id == user.id)
        )
    ).scalars().all()
    by_symbol = {instrument.symbol.upper(): instrument for instrument in relevant}
    held_labels = {
        str(value).casefold()
        for holding in holdings
        for value in (holding.get("sector"),)
        if value
    }
    held_labels.update(
        name.casefold()
        for name in (
            await db.execute(select(HoldingGroup.name).where(HoldingGroup.user_id == user.id))
        ).scalars().all()
    )

    async def quote_reader(seed: CandidateSeed):
        return quotes.get(seed.symbol)

    async def history_reader(seed: CandidateSeed):
        try:
            return await quote_service.provider.get_history(seed.symbol)
        except Exception:
            input_availability["history"] = False
            return []

    async def fundamentals_reader(seed: CandidateSeed):
        try:
            earnings = await quote_service.provider.get_earnings_date(seed.symbol)
        except Exception:
            input_availability["fundamentals"] = False
            return None
        return {"next_earnings_date": earnings} if earnings is not None else None

    async def news_reader(seed: CandidateSeed):
        instrument = by_symbol.get(seed.symbol.upper())
        if instrument is None:
            return []
        try:
            return (
                await db.execute(select(NewsItem).where(NewsItem.instrument_id == instrument.id))
            ).scalars().all()
        except Exception:
            input_availability["news"] = False
            return []

    async def signal_reader(seed: CandidateSeed):
        instrument = by_symbol.get(seed.symbol.upper())
        if instrument is None:
            return None
        try:
            row = (
                await db.execute(
                    select(Signal)
                    .join(Portfolio, Portfolio.id == Signal.portfolio_id)
                    .where(
                        Portfolio.user_id == user.id,
                        Signal.instrument_id == instrument.id,
                        Signal.kind.in_(("price_move_day", "price_move_week")),
                    )
                    .order_by(Signal.computed_at.desc())
                    .limit(1)
                )
            ).scalar_one_or_none()
        except Exception:
            input_availability["signals"] = False
            return None
        if row is None:
            return None
        change = row.data.get("change_pct") if isinstance(row.data, dict) else None
        try:
            return "positive" if Decimal(str(change)) > 0 else "negative"
        except Exception:
            return "neutral"

    def diversification_reader(seed: CandidateSeed):
        labels = ({seed.sector.casefold()} if seed.sector else set()) | {
            theme.casefold() for theme in seed.themes
        }
        if not labels:
            return "low"
        overlap = labels & held_labels
        return "low" if overlap == labels else ("medium" if overlap else "high")

    scored = await score_candidates(
        seeds,
        quote_reader=quote_reader,
        history_reader=history_reader,
        fundamentals_reader=fundamentals_reader,
        news_reader=news_reader,
        signal_reader=signal_reader,
        diversification_reader=diversification_reader,
        limit=_MAX_CANDIDATES,
    )
    return scored, input_availability


def _candidate_dict(candidate: ScoredCandidate):
    factor_refs = [
        f"candidate:{candidate.seed.symbol}:{factor}"
        for factor, value in candidate.factors.items()
        if value is not None
    ]
    return {
        "symbol": candidate.seed.symbol,
        "name": candidate.seed.name,
        "market": candidate.seed.market,
        "instrument_type": candidate.seed.instrument_type,
        "sector": candidate.seed.sector,
        "themes": list(candidate.seed.themes),
        "sources": list(candidate.seed.sources),
        "score": str(candidate.score),
        "factors": candidate.factors,
        "availability": candidate.availability,
        "evidence_refs": factor_refs,
    }


def _evidence(signals, news, candidates):
    out = [
        {
            "ref": row["evidence_ref"],
            "kind": "signal",
            **{k: v for k, v in row.items() if k != "evidence_ref"},
        }
        for row in signals
    ]
    out.extend(
        {
            "ref": row["evidence_ref"],
            "kind": "news",
            **{k: v for k, v in row.items() if k != "evidence_ref"},
        }
        for row in news
    )
    for candidate in candidates:
        for factor, value in candidate["factors"].items():
            if value is not None:
                out.append(
                    {
                        "ref": f"candidate:{candidate['symbol']}:{factor}",
                        "kind": "candidate",
                        "symbol": candidate["symbol"],
                        "factor": factor,
                        "value": value,
                    }
                )
    return out


def _truncate(context):
    while len(json.dumps(context)) > MAX_CONTEXT_CHARS:
        if context["material_news"]:
            context["material_news"].pop()
        elif context["candidates"]:
            context["candidates"].pop()
        elif context["signals"]:
            context["signals"].pop()
        else:
            break
        kept = {
            row["evidence_ref"] for key in ("signals", "material_news") for row in context[key]
        } | {ref for row in context["candidates"] for ref in row["evidence_refs"]}
        context["evidence"] = [row for row in context["evidence"] if row["ref"] in kept]
        context["availability"]["context_truncated"] = True
    if len(json.dumps(context)) > MAX_CONTEXT_CHARS:
        context["profile"]["free_text"] = ""
        context["profile"]["sector_interests"] = []
        context["portfolio_context"] = {}
        context["availability"]["context_truncated"] = True
    if len(json.dumps(context)) > MAX_CONTEXT_CHARS:
        context["holdings"] = [
            {
                "symbol": row["symbol"],
                "source_portfolio_ids": row["source_portfolio_ids"],
                "availability": row["availability"],
            }
            for row in context["holdings"]
        ]
    if len(json.dumps(context)) > MAX_CONTEXT_CHARS:
        raise DecisionContextTooLarge(
            "Held-symbol decision context exceeds MAX_CONTEXT_CHARS"
        )
    return context


async def build_decision_context(db, user, quote_service, fx) -> dict[str, Any]:
    profile = await _profile(db, user)
    portfolios = await _portfolios(db, user)
    holdings, valuation_ok = await _aggregate_holdings(db, portfolios, quote_service, fx)
    instrument_ids = {
        position.instrument_id
        for portfolio in portfolios
        for position in portfolio.positions
    }

    unavailable = []
    try:
        signals = await _signals(db, user)
        signals_ok = True
    except Exception:
        signals, signals_ok = [], False
    try:
        news = await _news(db, instrument_ids)
        news_ok = True
    except Exception:
        news, news_ok = [], False
    try:
        portfolio_context = await compute_group_exposure(db, user, quote_service, fx)
        exposure_ok = True
    except Exception:
        portfolio_context, exposure_ok = {"groups": [], "total_base": None, "unpriced": []}, False
    try:
        scored, candidate_inputs = await _candidate_context(
            db, user, profile, quote_service, holdings
        )
        candidates = [_candidate_dict(item) for item in scored]
        candidates_ok = all(candidate_inputs.values())
    except Exception:
        candidates, candidates_ok = [], False
        candidate_inputs = {
            "quotes": False,
            "history": False,
            "fundamentals": False,
            "news": False,
            "signals": False,
            "diversification": False,
        }
    availability = {
        "valuation": valuation_ok,
        "signals": signals_ok,
        "news": news_ok,
        "group_exposure": exposure_ok,
        "candidates": candidates_ok,
        "candidate_inputs": candidate_inputs,
        "context_truncated": False,
    }
    unavailable.extend(
        key
        for key, ok in availability.items()
        if key != "context_truncated" and not ok
    )
    availability["unavailable_inputs"] = unavailable
    context = {
        "profile": _profile_dict(profile),
        "holdings": holdings,
        "signals": signals,
        "material_news": news,
        "portfolio_context": portfolio_context,
        "candidates": candidates,
        "evidence": _evidence(signals, news, candidates),
        "availability": availability,
        "data_as_of": datetime.now(UTC).isoformat(),
    }
    return _truncate(context)
