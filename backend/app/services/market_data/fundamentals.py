from datetime import UTC, date, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Instrument, InstrumentFundamentals
from app.services.market_data.base import MarketDataProvider

FUNDAMENTALS_TTL = timedelta(hours=20)


class FundamentalsService:
    def __init__(self, provider: MarketDataProvider):
        self.provider = provider

    async def refresh(self, db: AsyncSession, instruments: list[Instrument]) -> None:
        now = datetime.now(UTC).replace(tzinfo=None)
        for inst in instruments:
            row = await db.get(InstrumentFundamentals, inst.id)
            if row is not None and now - row.fetched_at < FUNDAMENTALS_TTL:
                continue
            try:
                ed = await self.provider.get_earnings_date(inst.symbol)
            except Exception:
                continue
            if row is None:
                db.add(InstrumentFundamentals(
                    instrument_id=inst.id, next_earnings_date=ed, fetched_at=now,
                ))
            else:
                row.next_earnings_date = ed
                row.fetched_at = now
        await db.flush()


async def get_earnings_dates(db: AsyncSession, instrument_ids: list[int]) -> dict[int, date | None]:
    rows = (
        await db.execute(
            select(InstrumentFundamentals).where(
                InstrumentFundamentals.instrument_id.in_(instrument_ids)
            )
        )
    ).scalars().all()
    return {r.instrument_id: r.next_earnings_date for r in rows}
