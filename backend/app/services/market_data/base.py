from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Protocol


@dataclass(frozen=True)
class Quote:
    symbol: str
    price: Decimal
    currency: str
    previous_close: Decimal | None
    as_of: datetime


@dataclass(frozen=True)
class InstrumentInfo:
    symbol: str
    name: str
    exchange: str
    market: str
    currency: str
    sector: str | None
    industry: str | None


def infer_market(symbol: str) -> str:
    if symbol.upper().endswith(".L"):
        return "UK"
    if symbol.upper().endswith(".HK"):
        return "HK"
    return "US"


class MarketDataProvider(Protocol):
    async def get_quotes(self, symbols: list[str]) -> dict[str, Quote]: ...
    async def get_fx_rate(self, base: str, quote: str) -> Decimal: ...
    async def lookup(self, symbol: str) -> InstrumentInfo | None: ...
