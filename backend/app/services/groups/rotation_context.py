"""Read-time context aggregator for the Guru's sector-rotation advice mode.
Per user-defined holding group, assembles weight+drift (from GroupSnapshot
history), momentum (from the signals engine), recent news themes, and the
user's profile, plus an `availability` block describing which inputs were
actually present. Degrades per-input: a missing feed/history/signal simply
drops that field rather than failing the whole context."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from sqlalchemy import select

from app.models import (
    GroupAssignment,
    GroupSnapshot,
    Instrument,
    NewsItem,
    Portfolio,
    Position,
    Signal,
)
from app.services.groups.exposure import compute_group_exposure

_DRIFT_DAYS = 90
_MOMENTUM_KINDS = ("price_move_day", "price_move_week", "fifty_two_week", "unusual_volume")
_NEWS_PER_GROUP = 5
_Q = Decimal("0.01")


async def _group_instruments(db, user_id: int) -> dict[int | None, list[Instrument]]:
    """group_id (None = Ungrouped) -> the user's real held instruments in it."""
    rows = (await db.execute(
        select(Instrument, GroupAssignment.group_id)
        .join(Position, Position.instrument_id == Instrument.id)
        .join(Portfolio, Portfolio.id == Position.portfolio_id)
        .outerjoin(GroupAssignment, (GroupAssignment.instrument_id == Instrument.id)
                   & (GroupAssignment.user_id == user_id))
        .where(Portfolio.user_id == user_id, Portfolio.kind == "real").distinct()
    )).all()
    out: dict[int | None, list[Instrument]] = {}
    for inst, gid in rows:
        out.setdefault(gid, []).append(inst)
    return out


async def _date_total(db, user_id: int, as_of) -> Decimal:
    """Sum ALL of the user's group snapshots (incl. the null Ungrouped bucket)
    for one date. value_base is EncryptedDecimal so it can't be SUMmed in SQL;
    decrypt + sum in Python (volume is tiny, one row per group per date)."""
    rows = (await db.execute(
        select(GroupSnapshot.value_base).where(
            GroupSnapshot.user_id == user_id, GroupSnapshot.as_of == as_of)
    )).scalars().all()
    return sum((Decimal(v) for v in rows), Decimal("0"))


async def _drift(db, user_id: int, group_id: int | None):
    """Weight-share drift: how the group's share of the whole (all groups)
    changed between its earliest and latest snapshot in the window. Rotation is
    about share, not absolute value (a group can gain value purely from
    market-wide appreciation with its weight flat)."""
    since = datetime.now(UTC).date() - timedelta(days=_DRIFT_DAYS)
    rows = (await db.execute(
        select(GroupSnapshot.as_of, GroupSnapshot.value_base).where(
            GroupSnapshot.user_id == user_id, GroupSnapshot.group_id == group_id,
            GroupSnapshot.as_of >= since).order_by(GroupSnapshot.as_of)
    )).all()
    if len(rows) < 2:
        return None
    first, last = rows[0], rows[-1]
    from_total = await _date_total(db, user_id, first.as_of)
    to_total = await _date_total(db, user_id, last.as_of)
    if from_total <= 0 or to_total <= 0:
        return None
    from_pct = (Decimal(first.value_base) / from_total * 100).quantize(_Q)
    to_pct = (Decimal(last.value_base) / to_total * 100).quantize(_Q)
    return {"days": (last.as_of - first.as_of).days,
            "from_pct": str(from_pct), "to_pct": str(to_pct)}


async def _momentum(db, user_id: int, instruments):
    if not instruments:
        return None
    ids = [i.id for i in instruments]
    rows = (await db.execute(
        select(Signal).join(Portfolio, Portfolio.id == Signal.portfolio_id)
        .where(Portfolio.user_id == user_id, Signal.instrument_id.in_(ids),
               Signal.kind.in_(_MOMENTUM_KINDS))
    )).scalars().all()
    if not rows:
        return None
    by_sym = {i.id: i.symbol for i in instruments}
    movers = sorted({by_sym[s.instrument_id] for s in rows if s.severity in ("watch", "high")})
    notes = [f"{by_sym[s.instrument_id]}: {s.title}" for s in rows][:6]
    return {"summary": "; ".join(notes), "notable_movers": movers}


async def _news(db, instruments):
    if not instruments:
        return []
    ids = [i.id for i in instruments]
    rows = (await db.execute(
        select(NewsItem).where(NewsItem.instrument_id.in_(ids))
        .order_by(NewsItem.published_at.desc().nullslast()).limit(_NEWS_PER_GROUP * 2)
    )).scalars().all()
    seen, out = set(), []
    for n in rows:
        key = (n.title or "").strip().lower()
        if key and key not in seen:
            seen.add(key)
            out.append({"title": n.title, "source": n.source})
        if len(out) >= _NEWS_PER_GROUP:
            break
    return out


async def build_rotation_context(db, user, quote_service, fx) -> dict:
    exposure = await compute_group_exposure(db, user, quote_service, fx)
    from app.api.guru import get_profile_row
    profile = await get_profile_row(db, user)
    members = await _group_instruments(db, user.id)

    groups, any_hist, any_news, any_sig = [], False, False, False
    for g in exposure["groups"]:
        insts = members.get(g["group_id"], [])
        drift = await _drift(db, user.id, g["group_id"])
        momentum = await _momentum(db, user.id, insts)
        news = await _news(db, insts)
        any_hist = any_hist or drift is not None
        any_news = any_news or bool(news)
        any_sig = any_sig or momentum is not None
        groups.append({"name": g["name"], "weight_pct": g["pct"], "value_base": g["value_base"],
                       "holdings": [i.symbol for i in insts], "drift": drift,
                       "momentum": momentum, "news": news})
    return {
        "as_of": datetime.now(UTC).date().isoformat(), "total_base": exposure["total_base"],
        "profile": {"risk_appetite": getattr(profile, "risk_appetite", "balanced"),
                    "horizon": getattr(profile, "horizon", "medium")},
        "groups": groups,
        "availability": {"trend_history": any_hist, "news": any_news, "signals": any_sig},
    }
