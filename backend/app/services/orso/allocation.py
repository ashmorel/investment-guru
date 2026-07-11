"""Shared, transactional ORSO allocation writes. Both PUT /allocation (form
path) and POST /allocation/apply (reviewed ingest draft) funnel through
_replace_core so every write is validated and switch-logged identically. The
service flushes but does not commit — the caller owns the transaction boundary."""
from datetime import UTC, date, datetime
from decimal import Decimal

from fastapi import HTTPException
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import OrsoAllocation, OrsoFund, OrsoSwitchLog

_UNITS_Q = Decimal("0.0001")
_PCT_Q = Decimal("0.01")


def _canonical(entries: list[tuple[str, Decimal, Decimal]]) -> list[dict]:
    items = [{"code": code, "units": str(Decimal(units).quantize(_UNITS_Q)),
              "contribution_pct": str(Decimal(pct).quantize(_PCT_Q))}
             for code, units, pct in entries]
    return sorted(items, key=lambda x: x["code"])


async def _current_entries(db: AsyncSession, user_id: int) -> list[tuple[str, Decimal, Decimal]]:
    rows = (await db.execute(
        select(OrsoFund.code, OrsoAllocation.units, OrsoAllocation.contribution_pct)
        .join(OrsoAllocation, OrsoAllocation.fund_id == OrsoFund.id)
        .where(OrsoAllocation.user_id == user_id)
    )).all()
    return [(c, u, p) for c, u, p in rows]


async def _replace_core(
    db: AsyncSession, user_id: int,
    items: list[tuple[int, str, Decimal, Decimal]],   # (fund_id, code, units, pct)
    note: str | None,
) -> bool:
    """Full-replace the allocation; write a switch-log entry iff it changed.
    Returns `switched`. Assumes fund ownership/validity already checked."""
    previous = _canonical(await _current_entries(db, user_id))
    await db.execute(delete(OrsoAllocation).where(OrsoAllocation.user_id == user_id))
    for fund_id, _code, units, pct in items:
        db.add(OrsoAllocation(user_id=user_id, fund_id=fund_id, units=units,
                              contribution_pct=pct))
    new_state = _canonical([(code, units, pct) for _fid, code, units, pct in items])
    switched = new_state != previous
    if switched:
        db.add(OrsoSwitchLog(
            user_id=user_id, changed_at=datetime.now(UTC).replace(tzinfo=None),
            old_state=previous, new_state=new_state, note=note))
    await db.flush()
    return switched


async def apply_allocation(
    db: AsyncSession, user, *, new_funds: list[dict], allocations: list[dict],
    note: str | None, price_service,
) -> dict:
    """Create confirmed new funds, write derived manual prices, and replace the
    allocation — one transaction (caller commits). Raises HTTPException(422) on
    any validation failure (nothing is committed)."""
    # 1. create new funds (code unique per user)
    existing_codes = {c for (c,) in (await db.execute(
        select(OrsoFund.code).where(OrsoFund.user_id == user.id))).all()}
    created: dict[str, OrsoFund] = {}
    for nf in new_funds:
        code = nf["code"].upper()
        if code in existing_codes or code in created:
            raise HTTPException(status_code=422, detail=f"duplicate_new_fund:{code}")
        fund = OrsoFund(user_id=user.id, code=code, name=nf["name"],
                        asset_class=nf.get("asset_class", "unknown"),
                        risk_rating=nf.get("risk_rating", 4),
                        currency=nf.get("currency", "HKD"))
        db.add(fund)
        created[code] = fund
    await db.flush()   # assigns ids to created funds

    # 2. resolve allocation rows to (fund_id, code, units, pct)
    owned = {f.id: f for f in (await db.execute(
        select(OrsoFund).where(OrsoFund.user_id == user.id))).scalars().all()}
    items: list[tuple[int, str, Decimal, Decimal]] = []
    seen: set[int] = set()
    for a in allocations:
        if a.get("new_fund_code"):
            fund = created.get(a["new_fund_code"].upper())
            if fund is None:
                raise HTTPException(status_code=422, detail="unknown_new_fund_code")
        else:
            fund = owned.get(a.get("fund_id"))
            if fund is None:
                raise HTTPException(status_code=422, detail="unknown_fund_id")
        if fund.id in seen:
            raise HTTPException(status_code=422, detail="duplicate_fund_id")
        seen.add(fund.id)
        units = Decimal(str(a["units"]))
        pct = Decimal(str(a["contribution_pct"]))
        if units < 0 or pct < 0 or pct > 100:
            raise HTTPException(status_code=422, detail="out_of_range")
        if fund.archived and units > 0:
            raise HTTPException(status_code=422, detail="fund_archived")
        items.append((fund.id, fund.code, units, pct))

        # 3. derive + write manual price when market_value provided
        price = a.get("price")
        if price and price.get("market_value") and units != 0:
            mv = Decimal(str(price["market_value"]))
            as_of_raw = price["as_of"]
            # route callers pass model_dump() output, where a nested date
            # field stays a `date` object rather than an ISO string; accept
            # both so the service also works with plain JSON-shaped dicts.
            as_of = as_of_raw if isinstance(as_of_raw, date) else date.fromisoformat(as_of_raw)
            await price_service.upsert_manual_price(db, fund, (mv / units), as_of)

    switched = await _replace_core(db, user.id, items, note)
    return {"created_funds": sorted(created), "switched": switched}
