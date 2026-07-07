import asyncio
from datetime import UTC, datetime
from decimal import Decimal

from app.services.market_data.base import InstrumentInfo, Quote, infer_market


def parse_quote(symbol: str, info: dict) -> Quote | None:
    price = info.get("regularMarketPrice")
    currency = info.get("currency")
    if price is None or currency is None:
        return None
    prev = info.get("regularMarketPreviousClose")
    return Quote(
        symbol=symbol,
        price=Decimal(str(price)),
        currency=currency,
        previous_close=None if prev is None else Decimal(str(prev)),
        as_of=datetime.now(UTC),
    )


def parse_instrument_info(symbol: str, info: dict) -> InstrumentInfo | None:
    name = info.get("longName") or info.get("shortName")
    currency = info.get("currency")
    if name is None or currency is None:
        return None
    return InstrumentInfo(
        symbol=symbol,
        name=name,
        exchange=info.get("exchange", ""),
        market=infer_market(symbol),
        currency=currency,
        sector=info.get("sector"),
        industry=info.get("industry"),
    )


class YahooProvider:
    """yfinance-backed provider. yfinance is sync — calls run in a thread."""

    def _fetch_info(self, symbol: str) -> dict:
        import yfinance as yf

        return yf.Ticker(symbol).info or {}

    async def get_quotes(self, symbols: list[str]) -> dict[str, Quote]:
        results: dict[str, Quote] = {}
        infos = await asyncio.gather(
            *(asyncio.to_thread(self._fetch_info, s) for s in symbols),
            return_exceptions=True,
        )
        for symbol, info in zip(symbols, infos, strict=True):
            if isinstance(info, BaseException):
                continue
            quote = parse_quote(symbol, info)
            if quote is not None:
                results[symbol] = quote
        return results

    async def get_fx_rate(self, base: str, quote: str) -> Decimal:
        if base == quote:
            return Decimal("1")
        info = await asyncio.to_thread(self._fetch_info, f"{base}{quote}=X")
        price = info.get("regularMarketPrice")
        if price is None:
            raise LookupError(f"No FX rate for {base}{quote}")
        return Decimal(str(price))

    async def lookup(self, symbol: str) -> InstrumentInfo | None:
        try:
            info = await asyncio.to_thread(self._fetch_info, symbol)
        except Exception:
            return None
        return parse_instrument_info(symbol, info)
