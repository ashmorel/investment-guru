import asyncio
from datetime import UTC, datetime
from datetime import date as _date
from decimal import Decimal

from app.services.market_data.base import Bar, InstrumentInfo, Quote, infer_market


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


def parse_earnings_date(info: dict) -> _date | None:
    ts = info.get("earningsTimestamp")
    if ts is not None:
        return datetime.fromtimestamp(int(ts), tz=UTC).date()
    iso = info.get("earnings_date")
    if iso:
        return _date.fromisoformat(iso)
    return None


def parse_history(rows: list[dict]) -> list[Bar]:
    bars: list[Bar] = []
    for r in rows:
        close = r.get("close")
        if close is None:
            continue
        d = r["date"]
        bars.append(Bar(
            date=_date.fromisoformat(d) if isinstance(d, str) else d,
            open=Decimal(str(r["open"])), high=Decimal(str(r["high"])),
            low=Decimal(str(r["low"])), close=Decimal(str(close)),
            volume=None if r.get("volume") is None else int(r["volume"]),
        ))
    bars.sort(key=lambda b: b.date)
    return bars


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

    def _fetch_history(self, symbol: str, days: int) -> list[dict]:
        import yfinance as yf

        period = "2y" if days > 365 else "1y"
        df = yf.Ticker(symbol).history(period=period)
        rows = []
        for idx, row in df.iterrows():
            rows.append({
                "date": idx.date(),
                "open": row["Open"], "high": row["High"], "low": row["Low"],
                "close": row["Close"], "volume": row.get("Volume"),
            })
        return rows

    async def get_history(self, symbol: str, days: int = 400) -> list[Bar]:
        rows = await asyncio.to_thread(self._fetch_history, symbol, days)
        return parse_history(rows)

    async def get_earnings_date(self, symbol: str) -> _date | None:
        info = await asyncio.to_thread(self._fetch_info, symbol)
        return parse_earnings_date(info)
