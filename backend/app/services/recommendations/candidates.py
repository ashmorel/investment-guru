from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.guru import InvestorProfile
from app.models.instrument import Instrument
from app.models.portfolio import Portfolio, Position
from app.models.user import User
from app.services.recommendations.catalog import CatalogEntry, load_catalog


@dataclass(frozen=True)
class CandidateSeed:
    symbol: str
    name: str
    market: str
    currency: str
    instrument_type: str
    sector: str | None
    themes: tuple[str, ...]
    sources: tuple[str, ...]


def _matches_profile(entry: CatalogEntry, interests: set[str]) -> bool:
    labels = {entry.sector.casefold()} if entry.sector else set()
    labels.update(theme.casefold() for theme in entry.themes)
    return bool(labels & interests)


async def assemble_candidates(
    db: AsyncSession, user: User, profile: InvestorProfile | None
) -> list[CandidateSeed]:
    rows = (
        await db.execute(
            select(Portfolio.kind, Instrument)
            .join(Position, Position.portfolio_id == Portfolio.id)
            .join(Instrument, Instrument.id == Position.instrument_id)
            .where(Portfolio.user_id == user.id)
        )
    ).all()

    held_symbols = {
        instrument.symbol.upper() for kind, instrument in rows if kind == "real"
    }
    watchlist = {
        instrument.symbol.upper(): instrument
        for kind, instrument in rows
        if kind == "watchlist"
    }
    interests = {
        interest.strip().casefold()
        for interest in (profile.sector_interests if profile else [])
        if interest.strip()
    }

    candidates: dict[str, tuple[CatalogEntry | Instrument, set[str]]] = {}
    for entry in load_catalog():
        sources = {"catalog"}
        if _matches_profile(entry, interests):
            sources.add("profile_interest")
        candidates[entry.symbol] = (entry, sources)

    for symbol, instrument in watchlist.items():
        if symbol in candidates:
            candidates[symbol][1].add("watchlist")
        else:
            candidates[symbol] = (instrument, {"watchlist"})

    seeds: list[CandidateSeed] = []
    for symbol, (item, sources) in candidates.items():
        if symbol in held_symbols:
            continue
        seeds.append(
            CandidateSeed(
                symbol=symbol,
                name=item.name,
                market=item.market,
                currency=item.currency,
                instrument_type=(
                    item.instrument_type if isinstance(item, CatalogEntry) else "stock"
                ),
                sector=item.sector,
                themes=item.themes if isinstance(item, CatalogEntry) else (),
                sources=tuple(sorted(sources)),
            )
        )
    return sorted(seeds, key=lambda seed: seed.symbol)
