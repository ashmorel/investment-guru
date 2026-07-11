import json as _json
import logging
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.api.deps import CurrentUser, SessionDep
from app.api.portfolios import get_owned_portfolio
from app.api.valuation import get_services
from app.models import ChatMessage, ChatThread, GuruReport, InvestorProfile, LlmUsage
from app.services.guru.budget import BudgetExhausted
from app.services.guru.chat import ChatService
from app.services.guru.llm.base import LLMError, LLMNotConfigured
from app.services.guru.service import GenerationInProgress, GuruService, get_guru_service
from app.services.orso.deps import OrsoPriceDep

logger = logging.getLogger(__name__)

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
    except BudgetExhausted:
        raise HTTPException(status_code=429, detail="budget_exhausted") from None


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
    # Race-safe upsert: two concurrent PUTs from the same user (e.g. a double
    # submit) would otherwise both see no existing row, both INSERT, and the
    # second hits the investor_profiles.user_id unique constraint -> 500.
    values = dict(
        risk_appetite=body.risk_appetite, horizon=body.horizon,
        sector_interests=body.sector_interests, free_text=body.free_text,
    )
    stmt = pg_insert(InvestorProfile).values(user_id=user.id, **values)
    stmt = stmt.on_conflict_do_update(index_elements=["user_id"], set_=values)
    await db.execute(stmt)
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
    # Spec: the take runs after each digest run. A stale/missing take must never
    # fail the digest response -- log and move on. BudgetExhausted is included
    # here (not just LLMError/GenerationInProgress): the digest call just above
    # can itself push the user over the cap, so the take retry hitting the cap
    # is an expected outcome, not a server error.
    try:
        await guru.generate_take(db, user)
    except (LLMError, GenerationInProgress, BudgetExhausted):
        logger.exception("guru: take refresh after manual digest failed")
    return _report_out(report)


@router.get("/take/latest", response_model=ReportOut)
async def read_latest_take(db: SessionDep, user: CurrentUser):
    return await _latest(db, user, "take")


@router.post("/take", response_model=ReportOut, status_code=201)
async def create_take(db: SessionDep, user: CurrentUser, guru: GuruDep):
    with map_guru_errors():
        report = await guru.generate_take(db, user)
    return _report_out(report)


class ThreadOut(BaseModel):
    id: int
    title: str
    portfolio_id: int | None
    scope: str | None
    created_at: str


class ThreadList(BaseModel):
    threads: list[ThreadOut]


class ThreadCreate(BaseModel):
    title: str
    portfolio_id: int | None = None
    seed_context: dict | None = None
    scope: Literal["orso"] | None = None


class ChatMessageOut(BaseModel):
    id: int
    role: str
    content: str
    created_at: str


class ThreadDetail(ThreadOut):
    messages: list[ChatMessageOut]


class ChatMessageIn(BaseModel):
    content: str


def _thread_out(t: ChatThread) -> ThreadOut:
    return ThreadOut(id=t.id, title=t.title, portfolio_id=t.portfolio_id,
                     scope=t.scope, created_at=t.created_at.isoformat())


def _message_out(m: ChatMessage) -> ChatMessageOut:
    return ChatMessageOut(id=m.id, role=m.role, content=m.content,
                          created_at=m.created_at.isoformat())


async def _get_owned_thread(db: SessionDep, user: CurrentUser, thread_id: int) -> ChatThread:
    thread = await db.get(ChatThread, thread_id)
    if thread is None or thread.user_id != user.id:
        raise HTTPException(status_code=404, detail="Not found")
    return thread


@router.get("/chat/threads", response_model=ThreadList)
async def list_threads(db: SessionDep, user: CurrentUser):
    rows = (await db.execute(
        select(ChatThread).where(ChatThread.user_id == user.id)
        .order_by(ChatThread.created_at.desc(), ChatThread.id.desc())
    )).scalars().all()
    return ThreadList(threads=[_thread_out(t) for t in rows])


@router.post("/chat/threads", response_model=ThreadOut, status_code=201)
async def create_thread(body: ThreadCreate, db: SessionDep, user: CurrentUser):
    if body.portfolio_id is not None:
        await get_owned_portfolio(db, user, body.portfolio_id)
    thread = ChatThread(user_id=user.id, title=body.title, portfolio_id=body.portfolio_id,
                        seed_context=body.seed_context, scope=body.scope)
    db.add(thread)
    await db.commit()
    await db.refresh(thread)
    return _thread_out(thread)


@router.get("/chat/threads/{thread_id}", response_model=ThreadDetail)
async def read_thread(thread_id: int, db: SessionDep, user: CurrentUser):
    thread = await _get_owned_thread(db, user, thread_id)
    rows = (await db.execute(
        select(ChatMessage).where(ChatMessage.thread_id == thread.id)
        .order_by(ChatMessage.created_at.asc(), ChatMessage.id.asc())
    )).scalars().all()
    return ThreadDetail(**_thread_out(thread).model_dump(),
                        messages=[_message_out(m) for m in rows])


def _sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {_json.dumps(data)}\n\n"


@router.post("/chat/threads/{thread_id}/messages")
async def post_chat_message(thread_id: int, body: ChatMessageIn, db: SessionDep,
                            user: CurrentUser, guru: GuruDep, prices: OrsoPriceDep,
                            services: Annotated[tuple, Depends(get_services)]):
    thread = await _get_owned_thread(db, user, thread_id)
    chat = ChatService(guru)
    # prices/fx are only used for orso-scoped threads (ChatService._build_messages
    # branches on thread.scope), but are always obtained here via the overridden
    # dependencies -- get_orso_prices()/get_services() are cheap (no network) and
    # this is the only way tests' dependency_overrides reach ChatService without
    # it falling back to a live-Yahoo-backed FxService.
    _quotes, fx = services
    with map_guru_errors():
        # Awaiting here (rather than merely calling stream_turn) forces the eager
        # provider check to run now, so LLMNotConfigured is mapped to 503 before the
        # StreamingResponse — and its 200 status line — is ever returned.
        gen = await chat.stream_turn(db, user, thread, body.content,
                                     price_service=prices, fx_service=fx)

    async def event_source():
        async for frame in gen:
            yield _sse(frame["event"], frame["data"])

    return StreamingResponse(event_source(), media_type="text/event-stream")


class UsageByMode(BaseModel):
    mode: str
    calls: int
    input_tokens: int
    output_tokens: int
    est_cost_usd: str | None


class UsageSummary(BaseModel):
    by_mode: list[UsageByMode]
    total_cost_30d: str | None


@router.get("/usage/summary", response_model=UsageSummary)
async def usage_summary(db: SessionDep, user: CurrentUser):
    rows = (await db.execute(
        select(LlmUsage.mode, func.count(), func.sum(LlmUsage.input_tokens),
              func.sum(LlmUsage.output_tokens), func.sum(LlmUsage.est_cost_usd))
        .where(LlmUsage.user_id == user.id)
        .group_by(LlmUsage.mode)
    )).all()
    by_mode = [
        UsageByMode(mode=mode, calls=calls, input_tokens=int(in_tok or 0),
                   output_tokens=int(out_tok or 0),
                   est_cost_usd=str(cost) if cost is not None else None)
        for mode, calls, in_tok, out_tok, cost in rows
    ]

    cutoff = datetime.now(UTC).replace(tzinfo=None) - timedelta(days=30)
    total_30d = (await db.execute(
        select(func.sum(LlmUsage.est_cost_usd)).where(
            LlmUsage.user_id == user.id, LlmUsage.created_at >= cutoff)
    )).scalar_one_or_none()

    return UsageSummary(by_mode=by_mode,
                        total_cost_30d=str(total_30d) if total_30d is not None else None)
