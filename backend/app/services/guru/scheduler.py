import logging
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from sqlalchemy import select

from app.core.config import settings
from app.core.db import SessionLocal
from app.models import GuruReport, User
from app.services.guru.llm.base import LLMNotConfigured
from app.services.guru.service import get_guru_service

logger = logging.getLogger(__name__)


def _today_start_utc(now: datetime | None = None) -> datetime:
    tz = ZoneInfo(settings.guru_timezone)
    local_now = (now or datetime.now(UTC)).astimezone(tz)
    local_start = local_now.replace(hour=0, minute=0, second=0, microsecond=0)
    return local_start.astimezone(UTC).replace(tzinfo=None)


async def _report_exists_today(
    db, user_id: int, kind: str, now: datetime | None = None
) -> bool:
    row = (await db.execute(
        select(GuruReport.id).where(
            GuruReport.user_id == user_id, GuruReport.kind == kind,
            GuruReport.created_at >= _today_start_utc(now),
        ).limit(1)
    )).scalar_one_or_none()
    return row is not None


async def digest_exists_today(db, user_id: int, *, now: datetime | None = None) -> bool:
    return await _report_exists_today(db, user_id, "digest", now)


async def take_exists_today(db, user_id: int, *, now: datetime | None = None) -> bool:
    return await _report_exists_today(db, user_id, "take", now)


async def run_daily_job(session_factory=None) -> None:
    factory = session_factory or SessionLocal
    svc = get_guru_service()
    async with factory() as db:
        user = (await db.execute(select(User).order_by(User.id).limit(1))).scalar_one_or_none()
        if user is None:
            logger.info("guru scheduler: no user, skipping")
            return
        try:
            await svc.generate_digest(db, user)
            await svc.generate_take(db, user)
            logger.info("guru scheduler: digest + take generated")
        except LLMNotConfigured:
            logger.info("guru scheduler: no api key, skipping")
        except Exception:
            logger.exception("guru scheduler: daily job failed")


async def _run_guarded(db, user: User, coro_factory, *, log_ok: str, log_fail: str) -> None:
    """Run a guru-service call, never letting it raise past the scheduler."""
    try:
        await coro_factory()
        logger.info(log_ok)
    except LLMNotConfigured:
        logger.info("guru scheduler: no api key, skipping")
    except Exception:
        logger.exception(log_fail)


async def catch_up(session_factory=None) -> None:
    factory = session_factory or SessionLocal
    async with factory() as db:
        user = (await db.execute(select(User).order_by(User.id).limit(1))).scalar_one_or_none()
        if user is None:
            return
        digest_missing = not await digest_exists_today(db, user.id)
        take_missing = digest_missing or not await take_exists_today(db, user.id)

    if digest_missing:
        await run_daily_job(session_factory)
        return
    if not take_missing:
        return

    svc = get_guru_service()
    async with factory() as db:
        user = (await db.execute(select(User).order_by(User.id).limit(1))).scalar_one_or_none()
        if user is None:
            return
        await _run_guarded(
            db, user, lambda: svc.generate_take(db, user),
            log_ok="guru scheduler: take generated (catch-up)",
            log_fail="guru scheduler: catch-up take failed",
        )


def create_scheduler() -> AsyncIOScheduler:
    sched = AsyncIOScheduler(timezone=settings.guru_timezone)
    sched.add_job(run_daily_job, CronTrigger(
        hour=settings.guru_digest_hour, timezone=settings.guru_timezone))
    return sched
