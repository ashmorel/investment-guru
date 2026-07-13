"""Seed the HSBC LSRBS DC Scheme fund menu for the configured user.

Run: python -m app.seed_orso_lsrbs   (idempotent; safe to re-run)

The user is on the HSBC Group Hong Kong Local Staff Retirement Benefit Scheme
(LSRBS) DC Scheme, NOT the WMFS scheme that app.seed's STARTER_FUNDS installs.
This seeds the real LSRBS fund menu and archives any leftover zero-allocation
WMFS starter funds so the menu shows only the correct scheme.

Fund list sourced from the scheme's Quarterly Performance Summary factsheet
(fund name, currency, asset-class grouping). `risk_rating` values are INFERRED
from asset class as a starting point and are marked TO VERIFY against the
per-fund factsheets. Public scheme data only — no personal units/allocations
are ever seeded. Fund codes are app-local short codes (the factsheet has none);
statement ingest matches by code first, then by normalized fund name, so codes
need not match the scheme's own identifiers.
"""
import asyncio

from sqlalchemy import select

from app.core.config import settings
from app.core.db import SessionLocal
from app.models import OrsoAllocation, OrsoFund, User

# (code, name, asset_class, risk_rating [INFERRED — verify], currency)
# Names for funds present on the user's HSBC LSRBS statement are the FULL
# statement name (base name + share-class suffix, e.g. "... Inst Acc USD")
# so ingest.build_draft's exact normalized-name match succeeds against real
# statement rows. Funds not on that statement keep their base factsheet name.
LSRBS_FUNDS = [
    ("HGMF", "HSBC Global Money Fund", "cash", 1, "HKD"),
    ("WEMD", "Wellington Opportunistic Emerging Markets Debt Fund USD S Acc U",
     "bond", 4, "USD"),
    ("CGCB", "Capital Group Global Corporate Bond Fund", "bond", 3, "USD"),
    ("JPAB", "JP Morgan Aggregate Bond Fund", "bond", 3, "USD"),
    ("SCST", "Schroder Capital Stable Fund", "multi_asset", 2, "HKD"),
    ("SSTG", "Schroder Stable Growth Fund", "multi_asset", 3, "HKD"),
    ("SBAL", "Schroder Balanced Investment Fund C Accumulation HKD",
     "multi_asset", 4, "HKD"),
    ("SGRO", "Schroder Growth Fund C Accumulation HKD", "multi_asset", 5, "HKD"),
    ("LGDU", "L&G Diversified USD Fund", "multi_asset", 4, "USD"),
    ("IDRE", "iShares Developed Real Estate Index Fund (IE) Inst Acc USD",
     "real_estate", 5, "USD"),
    ("IDWI", "iShares Developed World Index Fund (IE) Inst Acc USD",
     "equity", 5, "USD"),
    ("IEUI", "iShares Europe Index Fund (IE) Inst Acc EUR", "equity", 5, "EUR"),
    ("MSEM", "Morgan Stanley Investment Funds - Emerging Markets Equity Fund",
     "equity", 6, "USD"),
    ("WASO", "Wellington Asian Opportunities Fund USD S Acc U", "equity", 6, "USD"),
    ("IUSI", "iShares US Index Fund (IE) USD Institutional Accumulating Class",
     "equity", 5, "USD"),
    ("IJPI", "iShares Japan Index Fund (IE) USD Institutional Accumulating Class",
     "equity", 5, "USD"),
    ("HSIF", "Hang Seng Index Fund Income Unit - A", "equity", 6, "HKD"),
    ("ACHE", "Allianz China Equity - WT - HKD", "equity", 6, "HKD"),
]

# Legacy WMFS starter-fund codes (app.seed.STARTER_FUNDS) — wrong scheme for
# this user; archived if present with no held units.
_WMFS_LEGACY_CODES = {
    "NAEF", "IGF", "HKEF", "WBF", "MMF", "NABF", "ISF", "ISGF",
    "APEF", "EEF", "CNEF", "HSITF", "CGF", "CPF",
}

_LSRBS_CODES = {code for code, *_ in LSRBS_FUNDS}


async def seed_lsrbs_funds(db, user_id: int) -> dict:
    """Idempotently install the LSRBS menu for `user_id` and archive leftover
    zero-allocation WMFS funds. Returns {created, updated, archived}."""
    funds = (await db.execute(
        select(OrsoFund).where(OrsoFund.user_id == user_id)
    )).scalars().all()
    by_code = {f.code: f for f in funds}

    # fund_ids that appear in the current allocation ("in use" — never archive).
    # Existence-only (no units read) so this seeder never needs the encryption
    # key to decrypt OrsoAllocation.units.
    allocated_ids = set((await db.execute(
        select(OrsoAllocation.fund_id).where(OrsoAllocation.user_id == user_id)
    )).scalars().all())

    created = updated = archived = 0
    for code, name, asset_class, risk, currency in LSRBS_FUNDS:
        fund = by_code.get(code)
        if fund is None:
            db.add(OrsoFund(user_id=user_id, code=code, name=name,
                            asset_class=asset_class, risk_rating=risk,
                            currency=currency, archived=False))
            created += 1
        else:
            changed = (
                fund.name != name or fund.asset_class != asset_class
                or fund.risk_rating != risk or fund.currency != currency
                or fund.archived
            )
            if changed:
                fund.name, fund.asset_class = name, asset_class
                fund.risk_rating, fund.currency = risk, currency
                fund.archived = False
                updated += 1

    # Archive wrong-scheme leftovers that hold no units.
    for fund in funds:
        if (fund.code in _WMFS_LEGACY_CODES and fund.code not in _LSRBS_CODES
                and not fund.archived and fund.id not in allocated_ids):
            fund.archived = True
            archived += 1

    await db.commit()
    return {"created": created, "updated": updated, "archived": archived}


async def main() -> None:
    async with SessionLocal() as db:
        user = (await db.execute(
            select(User).where(User.email == settings.initial_user_email)
        )).scalar_one_or_none()
        if user is None:
            raise RuntimeError(
                f"No user {settings.initial_user_email!r} — run `python -m app.seed` first")
        result = await seed_lsrbs_funds(db, user.id)
        print(f"LSRBS seed for {settings.initial_user_email}: "
              f"{result['created']} created, {result['updated']} updated, "
              f"{result['archived']} legacy WMFS funds archived.")
        print("NOTE: risk_rating values are inferred — verify against the per-fund factsheets.")


if __name__ == "__main__":
    asyncio.run(main())
