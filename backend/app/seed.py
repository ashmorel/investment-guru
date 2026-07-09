"""Create the initial user from env config. Run: python -m app.seed"""
import asyncio

from sqlalchemy import select

from app.core.config import settings
from app.core.db import SessionLocal
from app.core.security import hash_password
from app.models.user import User

STARTER_FUNDS = [
    # (code, name, asset_class, risk_rating) — public HSBC/Hang Seng ORSO scheme menu.
    # Verify/extend this list against the public scheme documentation during the task;
    # codes are scheme fund identifiers, not personal data.
    ("HK-EQ", "Hong Kong Equity Fund", "equity", 4),
    ("INTL-EQ", "International Equity Fund", "equity", 4),
    ("BAL", "Balanced Fund", "mixed", 3),
    ("STABLE", "Stable Fund", "mixed", 2),
    ("BOND", "Global Bond Fund", "bond", 2),
    ("MMF", "Money Market Fund", "cash", 1),
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
