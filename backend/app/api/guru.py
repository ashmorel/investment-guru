from contextlib import contextmanager
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select

from app.api.deps import CurrentUser, SessionDep
from app.api.portfolios import get_owned_portfolio
from app.models import GuruReport, InvestorProfile
from app.services.guru.llm.base import LLMError, LLMNotConfigured
from app.services.guru.service import GenerationInProgress, GuruService, get_guru_service

router = APIRouter(prefix="/api/guru", tags=["guru"])


def get_guru() -> GuruService:
    return get_guru_service()


GuruDep = Annotated[GuruService, Depends(get_guru)]


@contextmanager
def map_guru_errors():
    try:
        yield
    except LLMNotConfigured:
        raise HTTPException(status_code=503, detail="llm_unconfigured") from None
    except GenerationInProgress:
        raise HTTPException(status_code=409, detail="generation_in_progress") from None
    except LLMError:
        raise HTTPException(status_code=502, detail="llm_error") from None


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


class ReportOut(BaseModel):
    id: int
    kind: str
    portfolio_id: int | None
    payload: dict
    model: str
    created_at: str


def _report_out(r: GuruReport) -> ReportOut:
    return ReportOut(id=r.id, kind=r.kind, portfolio_id=r.portfolio_id,
                     payload=r.payload, model=r.model,
                     created_at=r.created_at.isoformat())


class ReviewRequest(BaseModel):
    portfolio_id: int


@router.post("/reviews", response_model=ReportOut, status_code=201)
async def create_review(body: ReviewRequest, db: SessionDep, user: CurrentUser, guru: GuruDep):
    pf = await get_owned_portfolio(db, user, body.portfolio_id)
    with map_guru_errors():
        report = await guru.generate_review(db, user, pf)
    return _report_out(report)


class ReviewList(BaseModel):
    reviews: list[ReportOut]


@router.get("/reviews", response_model=ReviewList)
async def list_reviews(db: SessionDep, user: CurrentUser,
                       portfolio_id: int | None = None, limit: int = 20):
    q = (select(GuruReport)
         .where(GuruReport.user_id == user.id, GuruReport.kind == "review")
         .order_by(GuruReport.created_at.desc(), GuruReport.id.desc()).limit(limit))
    if portfolio_id is not None:
        q = q.where(GuruReport.portfolio_id == portfolio_id)
    rows = (await db.execute(q)).scalars().all()
    return ReviewList(reviews=[_report_out(r) for r in rows])


@router.get("/reviews/{report_id}", response_model=ReportOut)
async def read_review(report_id: int, db: SessionDep, user: CurrentUser):
    r = (await db.execute(select(GuruReport).where(
        GuruReport.id == report_id, GuruReport.user_id == user.id,
        GuruReport.kind == "review"))).scalar_one_or_none()
    if r is None:
        raise HTTPException(status_code=404, detail="Not found")
    return _report_out(r)


async def _latest(db, user, kind: str) -> ReportOut:
    r = (await db.execute(select(GuruReport).where(
        GuruReport.user_id == user.id, GuruReport.kind == kind
    ).order_by(GuruReport.created_at.desc(), GuruReport.id.desc()).limit(1))).scalar_one_or_none()
    if r is None:
        raise HTTPException(status_code=404, detail="Not found")
    return _report_out(r)


@router.get("/digest/latest", response_model=ReportOut)
async def read_latest_digest(db: SessionDep, user: CurrentUser):
    return await _latest(db, user, "digest")


@router.post("/digest", response_model=ReportOut, status_code=201)
async def create_digest(db: SessionDep, user: CurrentUser, guru: GuruDep):
    with map_guru_errors():
        report = await guru.generate_digest(db, user)
    return _report_out(report)


@router.get("/take/latest", response_model=ReportOut)
async def read_latest_take(db: SessionDep, user: CurrentUser):
    return await _latest(db, user, "take")


@router.post("/take", response_model=ReportOut, status_code=201)
async def create_take(db: SessionDep, user: CurrentUser, guru: GuruDep):
    with map_guru_errors():
        report = await guru.generate_take(db, user)
    return _report_out(report)
