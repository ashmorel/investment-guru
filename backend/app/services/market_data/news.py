import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from time import mktime
from typing import Protocol

import feedparser
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Instrument, NewsItem

NEWS_TTL = timedelta(hours=6)


@dataclass(frozen=True)
class NewsDTO:
    title: str
    source: str
    url: str
    published_at: datetime | None


def parse_rss(data: bytes, source: str) -> list[NewsDTO]:
    feed = feedparser.parse(data)
    items: list[NewsDTO] = []
    for entry in feed.entries:
        title = getattr(entry, "title", None)
        link = getattr(entry, "link", None)
        if not title or not link:
            continue
        published = None
        if getattr(entry, "published_parsed", None) is not None:
            ts = mktime(entry.published_parsed)
            published = datetime.fromtimestamp(ts, tz=UTC).replace(tzinfo=None)
        items.append(
            NewsDTO(
                title=title[:500],
                source=source,
                url=link[:1000],
                published_at=published,
            )
        )
    return items


class NewsProvider(Protocol):
    async def get_news(self, symbol: str) -> list[NewsDTO]: ...


class YahooRssProvider:
    def _fetch(self, symbol: str) -> bytes:
        import urllib.request

        url = f"https://feeds.finance.yahoo.com/rss/2.0/headline?s={symbol}&region=US&lang=en-US"
        with urllib.request.urlopen(url, timeout=10) as resp:
            return resp.read()

    async def get_news(self, symbol: str) -> list[NewsDTO]:
        data = await asyncio.to_thread(self._fetch, symbol)
        return parse_rss(data, source="Yahoo")


class NewsService:
    def __init__(self, provider: NewsProvider):
        self.provider = provider

    async def refresh(self, db: AsyncSession, instruments: list[Instrument]) -> set[int]:
        now = datetime.now(UTC).replace(tzinfo=None)
        refreshed: set[int] = set()
        for inst in instruments:
            newest = (
                await db.execute(
                    select(NewsItem.fetched_at).where(NewsItem.instrument_id == inst.id)
                    .order_by(NewsItem.fetched_at.desc()).limit(1)
                )
            ).scalar_one_or_none()
            if newest is not None and now - newest < NEWS_TTL:
                refreshed.add(inst.id)
                continue
            try:
                dtos = await self.provider.get_news(inst.symbol)
            except Exception:
                continue
            existing = {
                u for (u,) in (
                    await db.execute(select(NewsItem.url).where(NewsItem.instrument_id == inst.id))
                ).all()
            }
            for dto in dtos:
                if dto.url in existing:
                    continue
                db.add(NewsItem(
                    instrument_id=inst.id, title=dto.title, source=dto.source, url=dto.url,
                    published_at=dto.published_at, fetched_at=now,
                ))
                existing.add(dto.url)
            refreshed.add(inst.id)
        await db.flush()
        return refreshed


async def recent_news(db: AsyncSession, instrument_id: int, within: timedelta) -> list[NewsItem]:
    cutoff = datetime.now(UTC).replace(tzinfo=None) - within
    rows = (
        await db.execute(
            select(NewsItem).where(
                NewsItem.instrument_id == instrument_id,
                NewsItem.fetched_at >= cutoff,
            ).order_by(NewsItem.published_at.desc().nullslast())
        )
    ).scalars().all()
    return list(rows)
