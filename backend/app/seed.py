"""Create the initial user from env config. Run: python -m app.seed"""
import asyncio

from sqlalchemy import select

from app.core.config import settings
from app.core.db import SessionLocal
from app.core.security import hash_password
from app.models.user import User


async def main() -> None:
    async with SessionLocal() as db:
        existing = await db.execute(select(User).where(User.email == settings.initial_user_email))
        if existing.scalar_one_or_none():
            print("User already exists")
            return
        db.add(
            User(
                email=settings.initial_user_email,
                password_hash=hash_password(settings.initial_user_password),
            )
        )
        await db.commit()
        print(f"Created {settings.initial_user_email}")


if __name__ == "__main__":
    asyncio.run(main())
