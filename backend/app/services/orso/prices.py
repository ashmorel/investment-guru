import json
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal, InvalidOperation

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import OrsoFund, OrsoFundPrice

# HSBC "Wayfoong Multi-funding System" ORSO fund-price endpoint, as used by the
# public unit-price widget at
# https://www.hsbc.com.hk/orso/tool/wayfoong-multi-funding-system/unit-prices/
# (discovered by inspecting that page's embedded widget config + bundled JS).
#
# The client_id/client_secret pair the widget sends as request headers is the
# widget's own front-end config, already shipped in that page's HTML/JS to
# every visitor's browser — it is not a privileged credential, just a header
# the reverse-proxy requires on requests. Even so, it is NOT hardcoded in this
# file because this is a public repo: the values are read from Settings
# (`orso_hsbc_client_id` / `orso_hsbc_client_secret`, env vars
# ORSO_HSBC_CLIENT_ID / ORSO_HSBC_CLIENT_SECRET — see app/core/config.py) and
# passed into HsbcFundCentreProvider by its caller.
#
# To find the current values yourself: open the unit-prices widget page above
# in a browser, open devtools > Network, reload the page, and inspect the
# request headers of the XHR to .../pensions-pws-fund-prices — client_id and
# client_secret are sent as plain request headers on that call.
_HSBC_BASE_URL = "https://rbwm-api.hsbc.com.hk"
_HSBC_PRICES_PATH = (
    "/wpb-gpbw-mmw-hk-hbap-pa-wpp-market-data-prod-proxy/v0/v1/pensions-pws-fund-prices"
)
_HSBC_SCHEME_IDENTIFIER = "WMFS"
_HSBC_PRODUCT = "ORSO"
_HSBC_TIMEOUT = 10.0
_HSBC_DATE_FORMAT = "%d/%m/%Y"

# A fund is considered fresh (and skipped on refresh) if it already has a
# price row fetched within this window, even when that row's as_of predates
# today (e.g. weekends/holidays where the upstream source has no new price).
_REFRESH_TTL = timedelta(hours=12)


@dataclass(frozen=True)
class PriceDTO:
    price: Decimal
    as_of: date


class OrsoPriceProvider(ABC):
    @abstractmethod
    async def get_prices(self, codes: list[str]) -> dict[str, PriceDTO]: ...


def parse_fund_prices(raw: str) -> dict[str, PriceDTO]:
    """Parse an HSBC pensions-pws-fund-prices JSON response into PriceDTOs keyed
    by fund identifier. Entries with a non-finite, zero, or negative bid price
    (or a missing code/date) are dropped here so a bad upstream value never
    reaches OrsoFundPrice.price (mirrors the finite guards in
    app.services.market_data.yahoo). Returns an empty dict (rather than
    raising) if the JSON top level isn't the expected object shape."""
    payload = json.loads(raw)
    if not isinstance(payload, dict):
        return {}
    result: dict[str, PriceDTO] = {}
    for block in payload.get("data", []):
        for entry in block.get("fundPriceList", []):
            code = entry.get("fundIdentifier")
            price_date = entry.get("priceDate")
            bid = entry.get("bidAmount")
            if not code or not price_date:
                continue
            try:
                price = Decimal(str(bid))
            except (InvalidOperation, TypeError):
                continue
            if not price.is_finite() or price <= 0:
                continue
            try:
                as_of = datetime.strptime(price_date, _HSBC_DATE_FORMAT).date()
            except (ValueError, TypeError):
                continue
            result[code] = PriceDTO(price=price, as_of=as_of)
    return result


class HsbcFundCentreProvider(OrsoPriceProvider):
    """Fetches the full HSBC WMFS ORSO fund-price list and filters to the
    requested codes. The upstream endpoint returns all funds in the scheme in
    one call regardless of which codes are asked for, so there is no
    per-code request to make."""

    def __init__(self, client_id: str, client_secret: str):
        self.client_id = client_id
        self.client_secret = client_secret

    async def get_prices(self, codes: list[str]) -> dict[str, PriceDTO]:
        params = {
            "endDate": date.today().isoformat(),
            "schemeIdentifier": _HSBC_SCHEME_IDENTIFIER,
            "product": _HSBC_PRODUCT,
        }
        headers = {
            "client_id": self.client_id,
            "client_secret": self.client_secret,
            "Accept": "application/json",
        }
        async with httpx.AsyncClient(timeout=_HSBC_TIMEOUT) as client:
            resp = await client.get(
                _HSBC_BASE_URL + _HSBC_PRICES_PATH, params=params, headers=headers
            )
            resp.raise_for_status()
            all_prices = parse_fund_prices(resp.text)
        wanted = set(codes)
        return {code: dto for code, dto in all_prices.items() if code in wanted}


@dataclass
class FakeOrsoPriceProvider(OrsoPriceProvider):
    prices: dict[str, PriceDTO] = field(default_factory=dict)
    fail: bool = False
    calls: list[list[str]] = field(default_factory=list)

    async def get_prices(self, codes: list[str]) -> dict[str, PriceDTO]:
        self.calls.append(list(codes))
        if self.fail:
            raise RuntimeError("FakeOrsoPriceProvider: simulated provider failure")
        return {c: self.prices[c] for c in codes if c in self.prices}


class OrsoPriceService:
    def __init__(self, provider: OrsoPriceProvider | None):
        self.provider = provider

    async def refresh(self, db: AsyncSession, funds: list[OrsoFund]) -> set[int]:
        """Fetch and persist a current price for every fund not already
        fresh. A fund is fresh if it has a row for today, or if its most
        recent row was fetched within `_REFRESH_TTL` (so weekends/holidays
        with no new upstream price don't re-hit the network every call).
        Never raises: a provider failure (or no provider) just leaves the
        already-fresh subset as the result and prior rows untouched. Existing
        rows with source "manual" are never overwritten."""
        if self.provider is None:
            return set()

        today = date.today()
        now = datetime.now(UTC).replace(tzinfo=None)
        fresh: set[int] = set()
        stale: list[OrsoFund] = []
        for fund in funds:
            today_row = (
                await db.execute(
                    select(OrsoFundPrice).where(
                        OrsoFundPrice.fund_id == fund.id, OrsoFundPrice.as_of == today
                    )
                )
            ).scalar_one_or_none()
            if today_row is not None:
                fresh.add(fund.id)
                continue

            most_recent = (
                await db.execute(
                    select(OrsoFundPrice)
                    .where(OrsoFundPrice.fund_id == fund.id)
                    .order_by(OrsoFundPrice.fetched_at.desc())
                    .limit(1)
                )
            ).scalar_one_or_none()
            if most_recent is not None and (now - most_recent.fetched_at) < _REFRESH_TTL:
                fresh.add(fund.id)
            else:
                stale.append(fund)

        if not stale:
            return fresh

        try:
            prices = await self.provider.get_prices([f.code for f in stale])
        except Exception:
            return fresh

        for fund in stale:
            dto = prices.get(fund.code)
            if dto is None:
                continue
            existing = (
                await db.execute(
                    select(OrsoFundPrice).where(
                        OrsoFundPrice.fund_id == fund.id, OrsoFundPrice.as_of == dto.as_of
                    )
                )
            ).scalar_one_or_none()
            if existing is None:
                db.add(OrsoFundPrice(
                    fund_id=fund.id, price=dto.price, as_of=dto.as_of,
                    source="hsbc", fetched_at=now,
                ))
            elif existing.source == "manual":
                # never overwrite a manually-entered price
                pass
            else:
                existing.price = dto.price
                existing.source = "hsbc"
                existing.fetched_at = now
            fresh.add(fund.id)

        await db.flush()
        return fresh

    async def upsert_manual_price(
        self, db: AsyncSession, fund: OrsoFund, price: Decimal, as_of: date
    ) -> OrsoFundPrice:
        now = datetime.now(UTC).replace(tzinfo=None)
        row = (
            await db.execute(
                select(OrsoFundPrice).where(
                    OrsoFundPrice.fund_id == fund.id, OrsoFundPrice.as_of == as_of
                )
            )
        ).scalar_one_or_none()
        if row is None:
            row = OrsoFundPrice(
                fund_id=fund.id, price=price, as_of=as_of, source="manual", fetched_at=now
            )
            db.add(row)
        else:
            row.price = price
            row.source = "manual"
            row.fetched_at = now
        await db.flush()
        return row

    async def latest_prices(
        self, db: AsyncSession, fund_ids: list[int]
    ) -> dict[int, OrsoFundPrice]:
        if not fund_ids:
            return {}
        rows = (
            await db.execute(
                select(OrsoFundPrice)
                .where(OrsoFundPrice.fund_id.in_(fund_ids))
                .order_by(OrsoFundPrice.fund_id, OrsoFundPrice.as_of.desc())
            )
        ).scalars().all()
        result: dict[int, OrsoFundPrice] = {}
        for row in rows:
            result.setdefault(row.fund_id, row)
        return result
