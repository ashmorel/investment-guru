from typing import Literal

from fastapi import APIRouter
from pydantic import BaseModel
from sqlalchemy import select

from app.api.deps import CurrentUser, SessionDep
from app.models import InvestorProfile

router = APIRouter(prefix="/api/guru", tags=["guru"])


class ProfileOut(BaseModel):
    risk_appetite: str
    horizon: str
    sector_interests: list[str]
    free_text: str


class ProfileIn(BaseModel):
    risk_appetite: Literal["cautious", "balanced", "adventurous"]
    horizon: Literal["short", "medium", "long"]
    sector_interests: list[str]
    free_text: str


async def get_profile_row(db, user) -> InvestorProfile | None:
    return (await db.execute(
        select(InvestorProfile).where(InvestorProfile.user_id == user.id)
    )).scalar_one_or_none()


@router.get("/profile", response_model=ProfileOut)
async def read_profile(db: SessionDep, user: CurrentUser):
    row = await get_profile_row(db, user)
    if row is None:
        return ProfileOut(risk_appetite="balanced", horizon="medium",
                          sector_interests=[], free_text="")
    return ProfileOut(risk_appetite=row.risk_appetite, horizon=row.horizon,
                      sector_interests=row.sector_interests, free_text=row.free_text)


@router.put("/profile", response_model=ProfileOut)
async def write_profile(body: ProfileIn, db: SessionDep, user: CurrentUser):
    row = await get_profile_row(db, user)
    if row is None:
        row = InvestorProfile(user_id=user.id)
        db.add(row)
    row.risk_appetite = body.risk_appetite
    row.horizon = body.horizon
    row.sector_interests = body.sector_interests
    row.free_text = body.free_text
    await db.commit()
    return ProfileOut(**body.model_dump())
