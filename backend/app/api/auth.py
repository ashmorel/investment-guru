from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.api.deps import CurrentUser, SessionDep
from app.core.config import settings
from app.core.hardening import login_throttle, register_throttle
from app.core.security import (
    SESSION_MAX_AGE_SECONDS,
    hash_password,
    sign_session,
    verify_password,
)
from app.models.user import User

router = APIRouter(prefix="/api/auth", tags=["auth"])


class LoginIn(BaseModel):
    email: str
    password: str


class RegisterIn(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8)


class MeOut(BaseModel):
    id: int
    email: str
    is_admin: bool


@router.post("/login", status_code=204)
async def login(body: LoginIn, response: Response, db: SessionDep) -> None:
    # Verify credentials before consulting the throttle: a correct password
    # must always be able to log in, even if the account is currently
    # lockout-eligible from an attacker's failed guesses (owner-lockout
    # mitigation). Only the failure path is throttle-gated, which preserves
    # the enumeration-safe 401/429 shapes for wrong passwords.
    result = await db.execute(select(User).where(User.email == body.email))
    user = result.scalar_one_or_none()
    if user is not None and verify_password(body.password, user.password_hash):
        login_throttle.record_success(body.email)
        response.set_cookie(
            "session",
            sign_session(user.id),
            max_age=SESSION_MAX_AGE_SECONDS,
            httponly=True,
            samesite="lax",
            secure=settings.is_production,
        )
        return
    login_throttle.check(body.email)
    login_throttle.record_failure(body.email)
    raise HTTPException(status_code=401, detail="Invalid credentials")


@router.post("/register", status_code=204)
async def register(body: RegisterIn, request: Request, response: Response, db: SessionDep) -> None:
    ip = request.client.host if request.client else "unknown"
    register_throttle.check(ip)
    register_throttle.record_failure(ip)

    result = await db.execute(select(User).where(User.email == body.email))
    if result.scalar_one_or_none() is not None:
        raise HTTPException(status_code=409, detail="email_taken")

    user = User(email=body.email, password_hash=hash_password(body.password))
    db.add(user)
    try:
        await db.commit()
    except IntegrityError:
        # Two concurrent registrations for the same email can both pass the
        # SELECT above; the loser's commit hits the unique constraint. Map
        # that race to the same 409 the pre-check returns instead of a 500.
        await db.rollback()
        raise HTTPException(status_code=409, detail="email_taken") from None
    await db.refresh(user)

    response.set_cookie(
        "session",
        sign_session(user.id),
        max_age=SESSION_MAX_AGE_SECONDS,
        httponly=True,
        samesite="lax",
        secure=settings.is_production,
    )


@router.post("/logout", status_code=204)
async def logout(response: Response) -> None:
    response.delete_cookie(
        "session",
        httponly=True,
        samesite="lax",
        secure=settings.is_production,
    )


@router.get("/me", response_model=MeOut)
async def me(user: CurrentUser) -> MeOut:
    is_admin = user.email.lower() in {e.lower() for e in settings.admin_emails}
    return MeOut(id=user.id, email=user.email, is_admin=is_admin)
