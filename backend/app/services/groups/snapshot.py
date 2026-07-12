"""Daily per-group value snapshots (forward-only trend history). Cheap, no LLM;
per-user failure-isolated; idempotent (write_snapshot delete-then-inserts)."""
import logging
from datetime import UTC, datetime

from sqlalchemy import select

from app.api.valuation import get_services
from app.core.db import SessionLocal
from app.models import Portfolio, User
from app.services.groups.exposure import compute_group_exposure, write_snapshot

logger = logging.getLogger(__name__)


async def _users_with_real_holdings(db) -> list[int]:
    return list((await db.execute(
        select(User.id).distinct()
        .join(Portfolio, Portfolio.user_id == User.id)
        .where(Portfolio.kind == "real")
    )).scalars().all())


async def run_group_snapshot_job(session_factory=None) -> None:
    factory = session_factory or SessionLocal
    quotes, fx = get_services()
    async with factory() as db:
        user_ids = await _users_with_real_holdings(db)
    today = datetime.now(UTC).date()
    for uid in user_ids:
        try:
            async with factory() as db:
                user = await db.get(User, uid)
                if user is None:
                    continue
                result = await compute_group_exposure(db, user, quotes, fx)
                await write_snapshot(db, user, result, today)
                await db.commit()
        except Exception:
            logger.exception("group snapshot failed for user %s", uid)


async def snapshot_catch_up(session_factory=None) -> None:
    """On startup, always run the snapshot job. The job is idempotent per-user
    (write_snapshot delete-then-inserts today's rows) and cheap, so re-running
    is safe — no global existence gate, which would wrongly skip every other
    user once any single user already has a row for today. Runs at app STARTUP
    (including in the test app's lifespan), so it must never let an exception
    escape and must no-op cheaply when there is nothing to snapshot."""
    try:
        await run_group_snapshot_job(session_factory)
    except Exception:
        logger.exception("group snapshot catch-up failed")
