from datetime import UTC, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Instrument, PriceBar
from app.services.market_data.base import MarketDataProvider

HISTORY_TTL = timedelta(hours=20)
TWO_DP = Decimal("0.01")


def _round(v: Decimal) -> Decimal:
    return v.quantize(TWO_DP, rounding=ROUND_HALF_UP)


def period_return(bars: list[PriceBar], trading_days: int) -> Decimal | None:
    if len(bars) <= trading_days:
        return None
    prev = bars[-1 - trading_days].close
    if prev == 0:
        return None
    return _round((bars[-1].close - prev) / prev * 100)


def fifty_two_week_range(bars: list[PriceBar]) -> tuple[Decimal, Decimal] | None:
    window = bars[-252:]
    if not window:
        return None
    lows = min(b.low for b in window)
    highs = max(b.high for b in window)
    return lows, highs


def avg_volume(bars: list[PriceBar], trading_days: int) -> Decimal | None:
    window = [b.volume for b in bars[-trading_days:] if b.volume is not None]
    if not window:
        return None
    return _round(Decimal(sum(window)) / Decimal(len(window)))


class HistoryService:
    def __init__(self, provider: MarketDataProvider):
        self.provider = provider

    async def refresh(self, db: AsyncSession, instruments: list[Instrument]) -> set[int]:
        now = datetime.now(UTC).replace(tzinfo=None)
        refreshed: set[int] = set()
        for inst in instruments:
            newest = (
                await db.execute(
                    select(PriceBar.date).where(PriceBar.instrument_id == inst.id)
                    .order_by(PriceBar.date.desc()).limit(1)
                )
            ).scalar_one_or_none()
            existing_dates = set()
            if newest is not None:
                # skip network if newest bar is within TTL of "today"
                if now.date() - newest < HISTORY_TTL:
                    refreshed.add(inst.id)
                    continue
                existing_dates = {
                    d for (d,) in (
                        await db.execute(
                            select(PriceBar.date).where(PriceBar.instrument_id == inst.id)
                        )
                    ).all()
                }
            try:
                bars = await self.provider.get_history(inst.symbol)
            except Exception:
                continue
            for bar in bars:
                if bar.date in existing_dates:
                    continue
                db.add(PriceBar(
                    instrument_id=inst.id, date=bar.date, open=bar.open, high=bar.high,
                    low=bar.low, close=bar.close, volume=bar.volume,
                ))
            refreshed.add(inst.id)
        await db.flush()
        return refreshed
