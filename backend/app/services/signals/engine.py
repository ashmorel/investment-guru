from dataclasses import dataclass
from datetime import UTC, date, datetime

from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import PriceBar, QuoteCache, Signal
from app.services.market_data.fundamentals import FundamentalsService, get_earnings_dates
from app.services.market_data.history import HistoryService
from app.services.market_data.news import NewsService, recent_news
from app.services.market_data.quotes import QuoteService
from app.services.signals import config
from app.services.signals.rules import ALL_RULES
from app.services.signals.types import SignalContext
from app.services.valuation import FxService, value_portfolio

# Sentinel "very old" timestamp used to expire the quote cache so a signals run pulls
# live quotes (the day-move signal must reflect the current market, not a ≤15-min cached tick).
_STALE = datetime(1970, 1, 1)


@dataclass
class AnalyzeResult:
    signals: list[Signal]
    as_of: datetime
    unavailable_inputs: list[str]


class SignalEngine:
    def __init__(self, quotes: QuoteService, fx: FxService, history: HistoryService,
                 fundamentals: FundamentalsService, news: NewsService, provider):
        self.quotes = quotes
        self.fx = fx
        self.history = history
        self.fundamentals = fundamentals
        self.news = news
        self.provider = provider

    async def analyze(self, db: AsyncSession, portfolio) -> AnalyzeResult:
        now = datetime.now(UTC).replace(tzinfo=None)
        instruments = [p.instrument for p in portfolio.positions]
        symbols = [i.symbol for i in instruments]
        ids = [i.id for i in instruments]
        unavailable: list[str] = []

        # Expire (don't delete) the quote cache for these symbols so this run pulls live
        # quotes; the stale row is retained so QuoteService can still fall back to it if the
        # provider is down. value_portfolio then reuses the freshly repopulated cache.
        if symbols:
            await db.execute(
                update(QuoteCache).where(QuoteCache.symbol.in_(symbols)).values(fetched_at=_STALE)
            )

        # quotes + valuation. value_portfolio never raises on provider failure (Phase 1
        # guarantee), so it is NOT wrapped; QuoteService.get_quotes is likewise degrade-safe.
        quotes = await self.quotes.get_quotes(db, symbols) if instruments else {}
        summary = await value_portfolio(db, portfolio, self.quotes, self.fx)

        # history (failure-isolated). The service swallows per-instrument provider errors and
        # omits the instrument from its "refreshed" set, so an uncovered id == this feed failed.
        try:
            refreshed = await self.history.refresh(db, instruments)
            if any(i not in refreshed for i in ids):
                unavailable.append("history")
        except Exception:
            unavailable.append("history")
        bars: dict[int, list[PriceBar]] = {}
        for inst in instruments:
            rows = (await db.execute(
                select(PriceBar).where(PriceBar.instrument_id == inst.id)
                .order_by(PriceBar.date.asc())
            )).scalars().all()
            # Defensive filter: already-persisted bars can carry a NaN OHLC value
            # (e.g. written before the yahoo.py parse_history guard existed). Drop
            # them here so no signal rule ever compares against a non-finite
            # Decimal, regardless of how the bad row got into the DB. Ordering is
            # preserved since we only remove elements, never reorder.
            bars[inst.id] = [
                b for b in rows
                if b.open.is_finite() and b.high.is_finite()
                and b.low.is_finite() and b.close.is_finite()
            ]

        # earnings (failure-isolated). FundamentalsService.refresh returns None and swallows
        # provider errors, so we detect failure by a missing fundamentals record: an instrument
        # absent from the read-back has no cached row and no fresh fetch succeeded.
        try:
            await self.fundamentals.refresh(db, instruments)
        except Exception:
            unavailable.append("earnings")
        earnings = await get_earnings_dates(db, ids) if instruments else {}
        if ids and any(i not in earnings for i in ids) and "earnings" not in unavailable:
            unavailable.append("earnings")

        # news (failure-isolated). Same "refreshed" set contract as history.
        try:
            refreshed_news = await self.news.refresh(db, instruments)
            if any(i not in refreshed_news for i in ids):
                unavailable.append("news")
        except Exception:
            unavailable.append("news")
        news_map: dict[int, list] = {}
        for inst in instruments:
            news_map[inst.id] = await recent_news(db, inst.id, config.NEWS_WINDOW)

        ctx = SignalContext(
            portfolio=portfolio, summary=summary, quotes=quotes, bars=bars,
            earnings=earnings, news=news_map, instruments=instruments, today=date.today(),
        )
        drafts = []
        for rule in ALL_RULES:
            drafts.extend(rule(ctx))

        # replace snapshot transactionally: delete the portfolio's existing signals, then
        # insert the fresh set, all stamped with one computed_at.
        await db.execute(delete(Signal).where(Signal.portfolio_id == portfolio.id))
        rows = [
            Signal(
                portfolio_id=portfolio.id, instrument_id=d.instrument_id, kind=d.kind,
                severity=d.severity, title=d.title, detail=d.detail, data=d.data,
                computed_at=now,
            )
            for d in drafts
        ]
        db.add_all(rows)
        await db.flush()
        return AnalyzeResult(signals=rows, as_of=now, unavailable_inputs=unavailable)


_engine: "SignalEngine | None" = None


def get_engine() -> "SignalEngine":
    global _engine
    if _engine is None:
        from app.services.market_data.news import YahooRssProvider
        from app.services.market_data.quotes import get_quote_service

        qs = get_quote_service()
        provider = qs.provider
        _engine = SignalEngine(
            quotes=qs, fx=FxService(provider), history=HistoryService(provider),
            fundamentals=FundamentalsService(provider), news=NewsService(YahooRssProvider()),
            provider=provider,
        )
    return _engine
