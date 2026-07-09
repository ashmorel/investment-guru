"""ORSO price-service dependency, factored out of app.api.orso so it can also
be imported by app.api.guru (for ORSO-scoped chat, Task 6) without creating an
app.api.orso <-> app.api.guru import cycle -- app.api.orso already imports
several names from app.api.guru at module level (GuruDep, ReportOut, etc.).
app.api.orso re-exports get_orso_prices/OrsoPriceDep from here, so existing
imports of `app.api.orso.get_orso_prices` (routes, tests, dependency_overrides)
are unaffected: it's the same function object either way."""

from typing import Annotated

from fastapi import Depends

from app.core.config import settings
from app.services.orso.prices import HsbcFundCentreProvider, OrsoPriceService

# A stored price is "stale" once it is older than this many days -- kept as a
# single module-level singleton (like get_quote_service) so refresh calls and
# reads share one in-memory instance for the process lifetime; tests override
# the dependency itself rather than reaching into this global.
_orso_price_service: OrsoPriceService | None = None


def get_orso_prices() -> OrsoPriceService:
    global _orso_price_service
    if _orso_price_service is None:
        provider = None
        if (
            settings.orso_price_fetch_enabled
            and settings.orso_hsbc_client_id
            and settings.orso_hsbc_client_secret
        ):
            provider = HsbcFundCentreProvider(
                settings.orso_hsbc_client_id, settings.orso_hsbc_client_secret
            )
        _orso_price_service = OrsoPriceService(provider)
    return _orso_price_service


OrsoPriceDep = Annotated[OrsoPriceService, Depends(get_orso_prices)]
