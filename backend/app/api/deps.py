from typing import Annotated

from fastapi import Cookie, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.db import get_session
from app.core.security import read_session
from app.models.user import User

SessionDep = Annotated[AsyncSession, Depends(get_session)]


async def get_current_user(
    db: SessionDep, session: Annotated[str | None, Cookie()] = None
) -> User:
    user_id = read_session(session) if session else None
    if user_id is None:
        raise HTTPException(status_code=401, detail="Not authenticated")
    user = await db.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=401, detail="Not authenticated")
    return user


CurrentUser = Annotated[User, Depends(get_current_user)]


def is_admin(user: User) -> bool:
    """Check if user is in the admin email allowlist (case-insensitive)."""
    return user.email.lower() in [email.lower() for email in settings.admin_emails]


async def get_admin_user(user: CurrentUser) -> User:
    """Dependency that checks admin authorization."""
    if not is_admin(user):
        raise HTTPException(status_code=403, detail="admin_only")
    return user


AdminUser = Annotated[User, Depends(get_admin_user)]
