from decimal import Decimal

from sqlalchemy import select

from app.models import GroupAssignment, HoldingGroup, Portfolio
from app.services.valuation import value_portfolio

_Q = Decimal("0.01")


async def compute_group_exposure(db, user, quote_service, fx, portfolio_id=None) -> dict:
    """Aggregate current market value by user group across the user's real
    portfolios (or a single owned portfolio_id). Unassigned holdings → the
    Ungrouped bucket (group_id=None, name='Ungrouped'). Degrades per-position:
    an unpriced position contributes 0 and its symbol goes in `unpriced`."""
    q = select(Portfolio).where(Portfolio.user_id == user.id, Portfolio.kind == "real")
    if portfolio_id is not None:
        q = q.where(Portfolio.id == portfolio_id)
    portfolios = (await db.execute(q)).scalars().all()

    groups = {g.id: g for g in (await db.execute(
        select(HoldingGroup).where(HoldingGroup.user_id == user.id))).scalars().all()}
    rows = (await db.execute(
        select(GroupAssignment.group_id, GroupAssignment.instrument_id)
        .where(GroupAssignment.user_id == user.id))).all()
    # instrument_id -> symbol via the portfolios' positions (loaded below)
    inst_to_group = {iid: gid for gid, iid in rows}

    agg_val: dict[int | None, Decimal] = {}
    agg_day: dict[int | None, Decimal] = {}
    unpriced: list[str] = []
    total = Decimal("0")
    for pf in portfolios:
        summary = await value_portfolio(db, pf, quote_service, fx)
        pos_inst = {p.instrument.symbol: p.instrument_id for p in pf.positions}
        for pv in summary.positions:
            if pv.market_value_base is None:
                unpriced.append(pv.symbol)
                continue
            gid = inst_to_group.get(pos_inst.get(pv.symbol))
            agg_val[gid] = agg_val.get(gid, Decimal("0")) + pv.market_value_base
            if pv.day_change_base is not None:
                agg_day[gid] = agg_day.get(gid, Decimal("0")) + pv.day_change_base
            total += pv.market_value_base

    out_groups = []
    for gid, val in agg_val.items():
        name = groups[gid].name if gid in groups else "Ungrouped"
        color = groups[gid].color if gid in groups else ""
        pct = (val / total * 100).quantize(_Q) if total > 0 else Decimal("0.00")
        out_groups.append({
            "group_id": gid, "name": name, "color": color,
            "value_base": str(val.quantize(_Q)), "pct": str(pct),
            "day_change_base": str(agg_day.get(gid, Decimal("0")).quantize(_Q)),
        })
    out_groups.sort(key=lambda x: Decimal(x["value_base"]), reverse=True)
    return {"groups": out_groups, "total_base": str(total.quantize(_Q)),
            "unpriced": sorted(set(unpriced))}
