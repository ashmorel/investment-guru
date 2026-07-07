from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import QuoteCache
from app.services.market_data.base import MarketDataProvider, Quote
from app.services.market_data.yahoo import YahooProvider

QUOTE_TTL = timedelta(minutes=15)


def _cache_to_quote(row: QuoteCache) -> Quote:
    return Quote(
        symbol=row.symbol, price=row.price, currency=row.currency,
        previous_close=row.previous_close,
        as_of=row.fetched_at.replace(tzinfo=UTC),
    )


class QuoteService:
    def __init__(self, provider: MarketDataProvider):
        self.provider = provider

    async def get_quotes(self, db: AsyncSession, symbols: list[str]) -> dict[str, Quote]:
        now = datetime.now(UTC).replace(tzinfo=None)
        rows = (
            await db.execute(select(QuoteCache).where(QuoteCache.symbol.in_(symbols)))
        ).scalars().all()
        cached = {r.symbol: r for r in rows}

        fresh = {s: _cache_to_quote(r) for s, r in cached.items() if now - r.fetched_at < QUOTE_TTL}
        missing = [s for s in symbols if s not in fresh]
        if not missing:
            return fresh

        try:
            fetched = await self.provider.get_quotes(missing)
        except Exception:
            fetched = {}

        for symbol, quote in fetched.items():
            row = cached.get(symbol)
            if row is None:
                row = QuoteCache(symbol=symbol, price=quote.price, currency=quote.currency,
                                 previous_close=quote.previous_close, fetched_at=now)
                db.add(row)
            else:
                row.price = quote.price
                row.currency = quote.currency
                row.previous_close = quote.previous_close
                row.fetched_at = now
        await db.flush()

        result = fresh | fetched
        for symbol in missing:  # stale-cache fallback for anything the provider missed
            if symbol not in result and symbol in cached:
                result[symbol] = _cache_to_quote(cached[symbol])
        return result


_service: QuoteService | None = None


def get_quote_service() -> QuoteService:
    global _service
    if _service is None:
        _service = QuoteService(YahooProvider())
    return _service
