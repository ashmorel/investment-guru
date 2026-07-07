from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import ROUND_HALF_UP, Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import FxRate, Portfolio
from app.services.market_data.base import MarketDataProvider
from app.services.market_data.quotes import QuoteService

TWO_DP = Decimal("0.01")


def normalise(amount: Decimal, currency: str) -> tuple[Decimal, str]:
    """Convert minor-unit listings to major units. GBp (LSE pence) -> GBP."""
    if currency == "GBp":
        return amount / Decimal("100"), "GBP"
    return amount, currency


class FxService:
    def __init__(self, provider: MarketDataProvider):
        self.provider = provider

    async def get_rate(self, db: AsyncSession, base: str, quote: str) -> Decimal:
        if base == quote:
            return Decimal("1")
        pair = f"{base}{quote}"
        today = date.today()
        row = (
            await db.execute(
                select(FxRate).where(FxRate.pair == pair, FxRate.date == today)
            )
        ).scalar_one_or_none()
        if row is not None:
            return row.rate
        try:
            rate = await self.provider.get_fx_rate(base, quote)
        except Exception:
            fallback = (
                await db.execute(
                    select(FxRate).where(FxRate.pair == pair).order_by(FxRate.date.desc()).limit(1)
                )
            ).scalar_one_or_none()
            if fallback is None:
                raise LookupError(f"No FX rate available for {pair}") from None
            return fallback.rate
        db.add(FxRate(pair=pair, date=today, rate=rate))
        await db.commit()
        return rate


@dataclass
class PositionValuation:
    position_id: int
    symbol: str
    name: str
    market: str
    quantity: Decimal | None
    avg_cost: Decimal | None
    native_currency: str
    price: Decimal | None
    market_value_base: Decimal | None
    cost_basis_base: Decimal | None
    unrealized_pnl_base: Decimal | None
    unrealized_pnl_pct: Decimal | None
    day_change_base: Decimal | None
    quote_as_of: datetime | None
    currency_mismatch: bool = False


@dataclass
class PortfolioSummary:
    portfolio_id: int
    base_currency: str
    total_value: Decimal | None
    total_cost: Decimal | None
    total_pnl: Decimal | None
    total_pnl_pct: Decimal | None
    day_change: Decimal | None
    currency_exposure: dict[str, Decimal] = field(default_factory=dict)
    positions: list[PositionValuation] = field(default_factory=list)
    priced_positions: int = 0
    unpriced_positions: int = 0
    costed_positions: int = 0
    day_change_partial: bool = False


def _round(v: Decimal) -> Decimal:
    return v.quantize(TWO_DP, rounding=ROUND_HALF_UP)


async def value_portfolio(
    db: AsyncSession, portfolio: Portfolio, quote_service: QuoteService, fx: FxService
) -> PortfolioSummary:
    symbols = [p.instrument.symbol for p in portfolio.positions]
    quotes = await quote_service.get_quotes(db, symbols) if symbols else {}

    summary = PortfolioSummary(
        portfolio_id=portfolio.id, base_currency=portfolio.base_currency,
        total_value=None, total_cost=None, total_pnl=None,
        total_pnl_pct=None, day_change=None,
    )
    total_value = total_cost = day_change = Decimal("0")
    any_priced = False
    any_cost = False
    any_day_change_missing = False

    for pos in portfolio.positions:
        inst = pos.instrument
        quote = quotes.get(inst.symbol)
        pv = PositionValuation(
            position_id=pos.id, symbol=inst.symbol, name=inst.name, market=inst.market,
            quantity=pos.quantity, avg_cost=pos.avg_cost, native_currency=inst.currency,
            price=quote.price if quote else None,
            market_value_base=None, cost_basis_base=None, unrealized_pnl_base=None,
            unrealized_pnl_pct=None, day_change_base=None,
            quote_as_of=quote.as_of if quote else None,
        )
        if quote is not None and pos.quantity is not None:
            price_major, price_ccy = normalise(quote.price, quote.currency)
            rate = await fx.get_rate(db, price_ccy, portfolio.base_currency)
            value = _round(pos.quantity * price_major * rate)
            pv.market_value_base = value
            total_value += value
            any_priced = True

            exposure_key = price_ccy
            summary.currency_exposure[exposure_key] = (
                summary.currency_exposure.get(exposure_key, Decimal("0")) + value
            )

            if pos.avg_cost is not None:
                cost_major, cost_ccy = normalise(pos.avg_cost, inst.currency)
                if quote.currency != inst.currency:
                    # source-agreement guard: the live quote and the instrument's
                    # own listing currency disagree (e.g. quote says "GBp" but the
                    # instrument says "GBP") — normalising both hides that
                    # disagreement, so treat the cost basis as UNKNOWN rather than
                    # silently mixing units.
                    pv.currency_mismatch = True
                else:
                    cost_rate = await fx.get_rate(db, cost_ccy, portfolio.base_currency)
                    cost = _round(pos.quantity * cost_major * cost_rate)
                    pv.cost_basis_base = cost
                    pv.unrealized_pnl_base = value - cost
                    if cost != 0:
                        pv.unrealized_pnl_pct = _round((value - cost) / cost * 100)
                    total_cost += cost
                    any_cost = True
                    summary.costed_positions += 1

            if quote.previous_close is not None:
                prev_major, _ = normalise(quote.previous_close, quote.currency)
                pv.day_change_base = _round(pos.quantity * (price_major - prev_major) * rate)
                day_change += pv.day_change_base
            else:
                any_day_change_missing = True

            summary.priced_positions += 1
        elif pos.quantity is not None:
            summary.unpriced_positions += 1
        summary.positions.append(pv)

    if any_priced:
        summary.total_value = _round(total_value)
        summary.total_cost = _round(total_cost) if any_cost else None
        if summary.total_cost is not None:
            summary.total_pnl = summary.total_value - summary.total_cost
            if summary.total_cost != 0:
                summary.total_pnl_pct = _round(summary.total_pnl / summary.total_cost * 100)
        summary.day_change = _round(day_change)
        summary.day_change_partial = any_day_change_missing
    return summary
