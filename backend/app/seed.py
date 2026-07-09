"""Create the initial user from env config. Run: python -m app.seed"""
import asyncio

from sqlalchemy import select

from app.core.config import settings
from app.core.db import SessionLocal
from app.core.security import hash_password
from app.models.user import User

STARTER_FUNDS = [
    # (code, name, asset_class, risk_rating) — real HSBC ORSO (WMFS) scheme fund
    # menu, HKD-denominated share classes only (their USD twins, codes ending
    # "U", are skipped — same underlying fund, different currency, not a
    # distinct menu entry). Sourced from the HSBC fund-centre price feed (see
    # backend/tests/fixtures/hsbc_fund_prices.json). Public scheme data only —
    # no personal units/splits are ever seeded.
    ("NAEF", "North American Equity Fund", "equity", 4),
    ("IGF", "International Growth Fund", "equity", 4),
    ("HKEF", "Hong Kong Equity Fund", "equity", 5),
    ("WBF", "World Bond Fund", "bond", 2),
    ("MMF", "Money Market Fund", "cash", 1),
    ("NABF", "North American Bond Fund", "bond", 2),
    ("ISF", "International Stable Fund", "mixed", 2),
    ("ISGF", "International Stable Growth Fund", "mixed", 3),
    ("APEF", "Asia Pacific Equity Fund", "equity", 4),
    ("EEF", "European Equity Fund", "equity", 4),
    ("CNEF", "Chinese Equity Fund", "equity", 5),
    ("HSITF", "Hang Seng Index Tracker Fund", "equity", 5),
    ("CGF", "Capital Guaranteed Fund", "guaranteed", 1),
    ("CPF", "Central Provident Fund", "guaranteed", 1),
]


async def seed_orso_funds(db, user_id: int) -> int:
    from app.models import OrsoFund
    existing = (await db.execute(
        select(OrsoFund.code).where(OrsoFund.user_id == user_id))).scalars().all()
    created = 0
    for code, name, asset_class, risk in STARTER_FUNDS:
        if code not in existing:
            db.add(OrsoFund(user_id=user_id, code=code, name=name,
                            asset_class=asset_class, risk_rating=risk))
            created += 1
    await db.commit()
    return created


async def main() -> None:
    if settings.is_production and (
        settings.initial_user_email == "you@example.com"
        or settings.initial_user_password == "change-me"
    ):
        raise RuntimeError("Set real INITIAL_USER_EMAIL/INITIAL_USER_PASSWORD in production")

    async with SessionLocal() as db:
        existing = await db.execute(select(User).where(User.email == settings.initial_user_email))
        user = existing.scalar_one_or_none()
        if user is None:
            user = User(
                email=settings.initial_user_email,
                password_hash=hash_password(settings.initial_user_password),
            )
            db.add(user)
            await db.commit()
            print(f"Created {settings.initial_user_email}")
        else:
            print("User already exists")

        count = await seed_orso_funds(db, user.id)
        print(f"Seeded {count} ORSO funds")


if __name__ == "__main__":
    asyncio.run(main())
