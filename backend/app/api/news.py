from datetime import UTC, datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select

from app.api.deps import CurrentUser, SessionDep
from app.api.guru import GuruDep, ReportOut, _report_out, map_guru_errors
from app.models import GuruReport, Instrument, NewsItem, Portfolio, Position
from app.services.market_data.news import NewsService, YahooRssProvider
from app.services.market_data.news_read import dedupe, rank_groups

router = APIRouter(prefix="/api/news", tags=["news"])

_PER_STOCK_DASH = 8      # headlines per stock on the dashboard panel
_PER_STOCK_FULL = 30     # headlines on the per-stock page
_WINDOW = timedelta(days=14)

_news_service: NewsService | None = None


def get_news_service() -> NewsService:
    global _news_service
    if _news_service is None:
        _news_service = NewsService(YahooRssProvider())
    return _news_service


NewsServiceDep = Annotated[NewsService, Depends(get_news_service)]


async def user_instruments(db, user_id: int) -> list[Instrument]:
    return list((await db.execute(
        select(Instrument).distinct()
        .join(Position, Position.instrument_id == Instrument.id)
        .join(Portfolio, Portfolio.id == Position.portfolio_id)
        .where(Portfolio.user_id == user_id)
    )).scalars().all())


async def _instrument_for_symbol(db, user_id: int, symbol: str) -> Instrument:
    inst = (await db.execute(
        select(Instrument).distinct()
        .join(Position, Position.instrument_id == Instrument.id)
        .join(Portfolio, Portfolio.id == Position.portfolio_id)
        .where(Portfolio.user_id == user_id, Instrument.symbol == symbol.upper())
    )).scalar_one_or_none()
    if inst is None:
        raise HTTPException(status_code=404, detail="not_held")
    return inst


async def _recent(db, instrument_id: int) -> list[NewsItem]:
    cutoff = datetime.now(UTC).replace(tzinfo=None) - _WINDOW
    from sqlalchemy import func
    return list((await db.execute(
        select(NewsItem).where(
            NewsItem.instrument_id == instrument_id,
            func.coalesce(NewsItem.published_at, NewsItem.fetched_at) >= cutoff,
        )
    )).scalars().all())


def _item_out(n: NewsItem) -> dict:
    return {"title": n.title, "source": n.source, "url": n.url,
            "published_at": (n.published_at or n.fetched_at).isoformat()}


class NewsItemOut(BaseModel):
    title: str
    source: str
    url: str
    published_at: str


class NewsGroup(BaseModel):
    symbol: str
    name: str
    latest_published_at: str | None
    items: list[NewsItemOut]
    summary_available: bool


class NewsResponse(BaseModel):
    groups: list[NewsGroup]
    unavailable: list[str]
    as_of: str


@router.get("", response_model=NewsResponse)
async def get_news(db: SessionDep, user: CurrentUser, news: NewsServiceDep):
    insts = await user_instruments(db, user.id)
    refreshed = await news.refresh(db, insts)     # TTL-gated; failure-isolated
    await db.commit()
    unavailable = [i.symbol for i in insts if i.id not in refreshed]

    summarized = {iid for (iid,) in (await db.execute(
        select(GuruReport.instrument_id).where(
            GuruReport.user_id == user.id, GuruReport.kind == "news",
            GuruReport.instrument_id.isnot(None))
    )).all()}

    groups: list[dict] = []
    for inst in insts:
        items = dedupe(await _recent(db, inst.id))[:_PER_STOCK_DASH]
        if not items:
            continue
        groups.append({
            "symbol": inst.symbol, "name": inst.name,
            "latest_published_at": _item_out(items[0])["published_at"],
            "items": [_item_out(n) for n in items],
            "summary_available": inst.id in summarized,
        })
    groups = rank_groups(groups)
    return NewsResponse(groups=groups, unavailable=unavailable,
                        as_of=datetime.now(UTC).isoformat())


class StockNews(BaseModel):
    symbol: str
    name: str
    items: list[NewsItemOut]
    as_of: str


@router.get("/{symbol}", response_model=StockNews)
async def get_stock_news(symbol: str, db: SessionDep, user: CurrentUser, news: NewsServiceDep):
    inst = await _instrument_for_symbol(db, user.id, symbol)
    await news.refresh(db, [inst])
    await db.commit()
    items = dedupe(await _recent(db, inst.id))[:_PER_STOCK_FULL]
    return StockNews(symbol=inst.symbol, name=inst.name,
                     items=[NewsItemOut(**_item_out(n)) for n in items],
                     as_of=datetime.now(UTC).isoformat())


class RefreshOut(BaseModel):
    refreshed: list[str]
    unavailable: list[str]


@router.post("/{symbol}/summary", response_model=ReportOut, status_code=201)
async def create_summary(symbol: str, db: SessionDep, user: CurrentUser, guru: GuruDep,
                         news: NewsServiceDep):
    inst = await _instrument_for_symbol(db, user.id, symbol)
    await news.refresh(db, [inst])
    await db.commit()
    headlines = dedupe(await _recent(db, inst.id))[:_PER_STOCK_FULL]
    if not headlines:
        raise HTTPException(status_code=422, detail="no_headlines")
    with map_guru_errors():
        report = await guru.generate_news_summary(db, user, inst, headlines)
    return _report_out(report)


@router.get("/{symbol}/summary", response_model=ReportOut)
async def latest_summary(symbol: str, db: SessionDep, user: CurrentUser):
    inst = await _instrument_for_symbol(db, user.id, symbol)
    r = (await db.execute(
        select(GuruReport).where(
            GuruReport.user_id == user.id, GuruReport.kind == "news",
            GuruReport.instrument_id == inst.id)
        .order_by(GuruReport.created_at.desc(), GuruReport.id.desc()).limit(1)
    )).scalar_one_or_none()
    if r is None:
        raise HTTPException(status_code=404, detail="no_summary")
    return _report_out(r)


@router.post("/refresh", response_model=RefreshOut)
async def refresh_news(db: SessionDep, user: CurrentUser, news: NewsServiceDep):
    insts = await user_instruments(db, user.id)
    refreshed = await news.refresh(db, insts)
    await db.commit()
    return RefreshOut(
        refreshed=[i.symbol for i in insts if i.id in refreshed],
        unavailable=[i.symbol for i in insts if i.id not in refreshed])
