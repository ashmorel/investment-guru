"""Context builder for the Guru's ORSO switching-advice mode. Reuses
app.api.orso.build_overview for the bulk of the payload (funds/valuation/
projection/flags) and layers on the pieces the advice prompt additionally
needs: the full fund menu (including archived funds with zero units, since
those codes must still be rejected as invalid switch targets rather than
merely absent from the valuation view), retirement goals, and recent switch
history."""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import OrsoFund, OrsoSwitchLog, User
from app.services.orso.prices import OrsoPriceService
from app.services.valuation import FxService

_RECENT_SWITCHES_LIMIT = 10


async def _profile_currency(db, user):
    from app.api.guru import get_profile_row
    return await get_profile_row(db, user)


async def _fund_menu(db: AsyncSession, user_id: int) -> list[str]:
    rows = (await db.execute(
        select(OrsoFund.code).where(OrsoFund.user_id == user_id).order_by(OrsoFund.code)
    )).scalars().all()
    return list(rows)


async def _recent_switches(db: AsyncSession, user_id: int) -> list[dict]:
    rows = (await db.execute(
        select(OrsoSwitchLog).where(OrsoSwitchLog.user_id == user_id)
        .order_by(OrsoSwitchLog.changed_at.desc(), OrsoSwitchLog.id.desc())
        .limit(_RECENT_SWITCHES_LIMIT)
    )).scalars().all()
    return [
        {
            "changed_at": row.changed_at.isoformat(),
            "note": row.note,
            "new_state": row.new_state,
        }
        for row in rows
    ]


async def _goals(db: AsyncSession, user: User) -> dict | None:
    from app.api.guru import get_profile_row

    profile = await get_profile_row(db, user)
    if profile is None:
        return None
    return {
        "birth_year": profile.birth_year,
        "retirement_target_age": profile.retirement_target_age,
        "retirement_target_pot": (
            None if profile.retirement_target_pot is None
            else str(profile.retirement_target_pot)
        ),
        "orso_monthly_contribution": (
            None if profile.orso_monthly_contribution is None
            else str(profile.orso_monthly_contribution)
        ),
    }


async def build_orso_context(
    db: AsyncSession,
    user: User,
    price_service: OrsoPriceService,
    fx_service: FxService | None = None,
) -> dict:
    """JSON-serializable context (Decimals rendered as str) for the ORSO
    advice prompt. `fx_service` mirrors build_overview's optional pattern —
    pass it through explicitly (never let this fall back to None in tests, as
    the None-fallback constructs a live Yahoo-backed FxService)."""
    from app.api.orso import build_overview

    overview = await build_overview(db, user, price_service, fx_service)
    ctx = dict(overview)
    ctx["fund_menu"] = await _fund_menu(db, user.id)
    ctx["goals"] = await _goals(db, user)
    ctx["recent_switches"] = await _recent_switches(db, user.id)
    proj = overview.get("projection")
    goals = ctx.get("goals")
    ctx["display_currency"] = overview.get("display_currency")
    ctx["total_display"] = overview.get("total_display")
    ctx["monthly_contribution"] = (
        None if goals is None else goals.get("orso_monthly_contribution"))
    ctx["contribution_currency"] = getattr(
        await _profile_currency(db, user), "orso_contribution_currency", "HKD")
    ctx["goal_gap"] = None if proj is None else [
        {"rate": s["rate"], "gap": s["gap"], "on_track": s["on_track"]} for s in proj]
    return ctx
