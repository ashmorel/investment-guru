import logging
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from sqlalchemy import select

from app.core.config import settings
from app.core.db import SessionLocal
from app.models import GuruReport, InvestorProfile, User
from app.services.guru.budget import BudgetExhausted
from app.services.guru.llm.base import LLMError, LLMNotConfigured
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


async def _opted_in_users(db) -> list[User]:
    return (await db.execute(
        select(User).join(InvestorProfile, InvestorProfile.user_id == User.id)
        .where(InvestorProfile.digest_enabled.is_(True))
        .order_by(User.id)
    )).scalars().all()


async def _generate_daily_for_user(db, svc, user: User) -> None:
    """Generate digest + take for one user. Never lets an exception escape --
    one user's failure (missing key, over budget, LLM error, or anything else)
    must not abort the loop over the other opted-in users."""
    try:
        await svc.generate_digest(db, user)
        await svc.generate_take(db, user)
        logger.info("guru scheduler: digest + take generated for user %s", user.id)
    except LLMNotConfigured:
        logger.info("guru scheduler: no api key, skipping")
    except BudgetExhausted:
        logger.info("guru scheduler: budget exhausted for user %s, skipping", user.id)
    except LLMError:
        logger.exception("guru scheduler: daily job failed for user %s", user.id)
    except Exception:
        logger.exception("guru scheduler: daily job failed for user %s", user.id)


async def run_daily_job(session_factory=None) -> None:
    factory = session_factory or SessionLocal
    async with factory() as db:
        svc = await get_guru_service(db)
        user_ids = [u.id for u in await _opted_in_users(db)]
    if not user_ids:
        logger.info("guru scheduler: no opted-in users, skipping")
        return
    # Fresh session per user (like catch_up): a failure that leaves one user's
    # session in a pending-rollback state must not cascade into every
    # subsequent user in the loop.
    for user_id in user_ids:
        async with factory() as db:
            user = await db.get(User, user_id)
            if user is None:
                continue
            await _generate_daily_for_user(db, svc, user)


async def _run_guarded(db, user: User, coro_factory, *, log_ok: str, log_fail: str) -> None:
    """Run a guru-service call, never letting it raise past the scheduler."""
    try:
        await coro_factory()
        logger.info(log_ok)
    except LLMNotConfigured:
        logger.info("guru scheduler: no api key, skipping")
    except BudgetExhausted:
        logger.info("guru scheduler: budget exhausted for user %s, skipping", user.id)
    except Exception:
        logger.exception(log_fail)


async def catch_up(session_factory=None) -> None:
    factory = session_factory or SessionLocal
    async with factory() as db:
        svc = await get_guru_service(db)
        users = await _opted_in_users(db)
        statuses = []
        for user in users:
            digest_missing = not await digest_exists_today(db, user.id)
            take_missing = digest_missing or not await take_exists_today(db, user.id)
            statuses.append((user.id, digest_missing, take_missing))

    for user_id, digest_missing, take_missing in statuses:
        if not take_missing:
            continue
        async with factory() as db:
            user = await db.get(User, user_id)
            if user is None:
                continue
            if digest_missing:
                await _generate_daily_for_user(db, svc, user)
            else:
                await _run_guarded(
                    db, user, lambda u=user: svc.generate_take(db, u),
                    log_ok="guru scheduler: take generated (catch-up)",
                    log_fail="guru scheduler: catch-up take failed",
                )


def create_scheduler() -> AsyncIOScheduler:
    from app.services.groups.snapshot import run_group_snapshot_job
    from app.services.signals.refresh import run_analysis_job

    sched = AsyncIOScheduler(timezone=settings.guru_timezone)
    # Ordered so each morning's jobs run on fresh inputs: signals refresh first
    # (:00), then digest/take which read those signals (:15), then the group
    # snapshot (:30).
    sched.add_job(run_analysis_job, CronTrigger(
        hour=settings.guru_digest_hour, minute=0, timezone=settings.guru_timezone))
    sched.add_job(run_daily_job, CronTrigger(
        hour=settings.guru_digest_hour, minute=15, timezone=settings.guru_timezone))
    sched.add_job(run_group_snapshot_job, CronTrigger(
        hour=settings.guru_digest_hour, minute=30, timezone=settings.guru_timezone))
    return sched
