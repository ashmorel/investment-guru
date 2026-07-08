from dataclasses import dataclass, field
from datetime import date

from app.services.market_data.base import Quote


@dataclass
class SignalDraft:
    kind: str
    severity: str
    title: str
    detail: str
    data: dict[str, str]
    instrument_id: int | None = None


@dataclass
class SignalContext:
    portfolio: object            # app.models.Portfolio (avoid circular import at type level)
    summary: object              # app.services.valuation.PortfolioSummary | None
    quotes: dict[str, Quote]
    bars: dict[int, list]        # instrument_id -> list[PriceBar]
    earnings: dict[int, date | None]
    news: dict[int, list]        # instrument_id -> list[NewsItem]
    instruments: list = field(default_factory=list)  # list[Instrument] held
    today: date = None
