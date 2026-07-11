from datetime import UTC, datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models import LlmUsage


class BudgetExhausted(Exception):
    """Raised when a user has hit (or exceeded) their daily Guru LLM spend cap."""


def _today_start_utc(now: datetime | None = None) -> datetime:
    # Duplicated (not imported) from app.services.guru.scheduler._today_start_utc:
    # scheduler.py imports app.services.guru.service, and service.py needs to call
    # check_budget from every generate_* path, so importing scheduler here would
    # create a budget -> scheduler -> service -> budget import cycle. This helper
    # is a few lines and has no other dependencies, so a small duplication is
    # cheaper than restructuring the import graph.
    tz = ZoneInfo(settings.guru_timezone)
    local_now = (now or datetime.now(UTC)).astimezone(tz)
    local_start = local_now.replace(hour=0, minute=0, second=0, microsecond=0)
    return local_start.astimezone(UTC).replace(tzinfo=None)


async def check_budget(db: AsyncSession, user_id: int, *, now: datetime | None = None) -> None:
    """Raise BudgetExhausted if the user has spent >= settings.guru_daily_budget_usd
    since local midnight (in settings.guru_timezone) today. Null est_cost_usd rows
    count as 0.
    """
    start = _today_start_utc(now)
    total = (await db.execute(
        select(func.coalesce(func.sum(LlmUsage.est_cost_usd), 0)).where(
            LlmUsage.user_id == user_id, LlmUsage.created_at >= start)
    )).scalar_one()
    if Decimal(total) >= settings.guru_daily_budget_usd:
        raise BudgetExhausted(f"daily LLM budget exhausted for user {user_id}")
