import json
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

_CATALOG_PATH = Path(__file__).parents[2] / "data" / "recommendation_catalog.json"
_MARKETS = {"US", "UK", "HK"}
_INSTRUMENT_TYPES = {"stock", "etf"}


@dataclass(frozen=True)
class CatalogEntry:
    symbol: str
    name: str
    market: Literal["US", "UK", "HK"]
    currency: str
    instrument_type: Literal["stock", "etf"]
    sector: str | None
    themes: tuple[str, ...]


def parse_catalog(raw: object) -> tuple[CatalogEntry, ...]:
    if not isinstance(raw, list):
        raise ValueError("catalogue must be a list")

    entries: list[CatalogEntry] = []
    symbols: set[str] = set()
    required = {"symbol", "name", "market", "currency", "instrument_type"}
    for item in raw:
        if not isinstance(item, dict) or not required <= item.keys():
            raise ValueError("catalogue entry is missing required fields")
        if not all(isinstance(item[field], str) and item[field].strip() for field in required):
            raise ValueError("catalogue required fields must be non-empty strings")

        symbol = item["symbol"].strip().upper()
        market = item["market"].strip().upper()
        instrument_type = item["instrument_type"].strip().lower()
        if symbol in symbols:
            raise ValueError(f"duplicate catalogue symbol: {symbol}")
        if market not in _MARKETS:
            raise ValueError(f"unsupported catalogue market: {market}")
        if instrument_type not in _INSTRUMENT_TYPES:
            raise ValueError(f"unsupported instrument type: {instrument_type}")

        sector = item.get("sector")
        themes = item.get("themes", [])
        if sector is not None and (not isinstance(sector, str) or not sector.strip()):
            raise ValueError("sector must be a non-empty string or null")
        if not isinstance(themes, list) or not all(
            isinstance(theme, str) and theme.strip() for theme in themes
        ):
            raise ValueError("themes must be a list of non-empty strings")

        symbols.add(symbol)
        entries.append(
            CatalogEntry(
                symbol=symbol,
                name=item["name"].strip(),
                market=market,
                currency=item["currency"].strip(),
                instrument_type=instrument_type,
                sector=sector.strip() if sector else None,
                themes=tuple(theme.strip() for theme in themes),
            )
        )
    return tuple(entries)


def load_catalog() -> tuple[CatalogEntry, ...]:
    raw = json.loads(_CATALOG_PATH.read_text())
    return parse_catalog(raw)
