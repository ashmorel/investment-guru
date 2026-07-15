"""Daily per-portfolio signal refresh (feeds the dashboard "needs your attention"
panel). Cheap, no LLM, so no budget gate; per-user AND per-portfolio failure
isolated -- one down feed or one bad portfolio must never abort the rest."""
import logging

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.core.db import SessionLocal
from app.models import Portfolio, Position, User
from app.services.signals.engine import get_engine

logger = logging.getLogger(__name__)


async def _users_with_real_portfolios(db) -> list[int]:
    return list((await db.execute(
        select(User.id).distinct()
        .join(Portfolio, Portfolio.user_id == User.id)
        .where(Portfolio.kind == "real")
    )).scalars().all())


async def _analyze_user_portfolios(db, user: User) -> None:
    """Analyze every real portfolio for one user, in this user's session. Each
    portfolio is re-queried fresh (rather than reusing an instance loaded before
    a prior portfolio's rollback) with its positions/instruments eagerly loaded,
    so a sibling portfolio's failure can never leave attribute access needing an
    unavailable lazy load. A single portfolio's failure -- down feed, bad data,
    a corrupt-snapshot flush error -- must not abort the rest of this user's
    portfolios."""
    engine = get_engine()
    portfolio_ids = (await db.execute(
        select(Portfolio.id).where(Portfolio.user_id == user.id, Portfolio.kind == "real")
    )).scalars().all()
    for portfolio_id in portfolio_ids:
        try:
            pf = (await db.execute(
                select(Portfolio).where(Portfolio.id == portfolio_id)
                .options(selectinload(Portfolio.positions).selectinload(Position.instrument))
            )).scalar_one_or_none()
            if pf is None:
                continue
            await engine.analyze(db, pf)
            await db.commit()
            logger.info("signals refresh: portfolio %s analyzed", portfolio_id)
        except Exception:
            await db.rollback()
            logger.exception("signals refresh: portfolio %s failed", portfolio_id)


async def run_analysis_job(session_factory=None) -> None:
    factory = session_factory or SessionLocal
    async with factory() as db:
        user_ids = await _users_with_real_portfolios(db)
    if not user_ids:
        logger.info("signals refresh: no users with real portfolios, skipping")
        return
    # Fresh session per user (like the guru scheduler and group snapshot job): a
    # failure that leaves one user's session in a pending-rollback state must not
    # cascade into every subsequent user in the loop.
    for user_id in user_ids:
        try:
            async with factory() as db:
                user = await db.get(User, user_id)
                if user is None:
                    continue
                await _analyze_user_portfolios(db, user)
        except Exception:
            logger.exception("signals refresh: user %s failed", user_id)


async def analysis_catch_up(session_factory=None) -> None:
    """On startup, always run the analysis job so signals are fresh before the
    digest/take (which read them) run. Runs at app STARTUP (including in the
    test app's lifespan), so it must never let an exception escape and must
    no-op cheaply when there is nothing to analyze."""
    try:
        await run_analysis_job(session_factory)
    except Exception:
        logger.exception("signals analysis catch-up failed")
