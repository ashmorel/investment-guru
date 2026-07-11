"""ORSO core API: funds CRUD, allocation full-replace with switch log, goals,
prices (refresh / manual entry), and the overview payload (values + projection
+ integrity flags)."""

import base64
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy import delete, func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.api.deps import CurrentUser, SessionDep
from app.api.guru import GuruDep, ReportOut, _report_out, get_profile_row, map_guru_errors
from app.api.valuation import get_services
from app.core.hardening import MAX_UPLOAD_BYTES
from app.models import GuruReport, InvestorProfile, OrsoAllocation, OrsoFund, OrsoSwitchLog
from app.services.market_data.quotes import get_quote_service
from app.services.orso.allocation import apply_allocation
from app.services.orso.deps import OrsoPriceDep, get_orso_prices  # noqa: F401  (re-exported)
from app.services.orso.ingest import (
    AllocationDraft,
    CsvHeaderError,
    build_draft,
    parse_csv,
)
from app.services.orso.prices import OrsoPriceService
from app.services.orso.projection import project
from app.services.orso.vision import extract_statement
from app.services.valuation import FxService

router = APIRouter(prefix="/api/orso", tags=["orso"])

# A stored price is "stale" once it is older than this many days. An 8-day-old
# price is stale; a same-day/recent one is not.
_STALE_AFTER_DAYS = 7

# Display base currency for the ORSO total_base line (Phase 1 default base).
_BASE_CURRENCY = "GBP"

_UNITS_Q = Decimal("0.0001")
_PCT_Q = Decimal("0.01")


# --- ownership helper (reused by Task 5) -----------------------------------

async def get_owned_fund(db: SessionDep, user: CurrentUser, fund_id: int) -> OrsoFund:
    fund = await db.get(OrsoFund, fund_id)
    if fund is None or fund.user_id != user.id:
        raise HTTPException(status_code=404, detail="Fund not found")
    return fund


# --- ingest (read-only draft; Tasks 4/5 consume AllocationDraft) -----------

@router.post("/ingest/csv", response_model=AllocationDraft)
async def ingest_csv(db: SessionDep, user: CurrentUser, file: UploadFile):
    data = await file.read()
    if len(data) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail="upload_too_large")
    try:
        parsed = parse_csv(data.decode("utf-8-sig"))
    except CsvHeaderError as exc:
        raise HTTPException(status_code=422, detail=f"missing_headers:{exc.args[0]}") from None
    except UnicodeDecodeError:
        raise HTTPException(status_code=422, detail="not_utf8_csv") from None
    return await build_draft(db, user.id, parsed, source="csv")


_ALLOWED_IMAGE_TYPES = {"image/png", "image/jpeg", "image/webp", "image/gif"}


@router.post("/ingest/screenshot", response_model=AllocationDraft)
async def ingest_screenshot(db: SessionDep, user: CurrentUser, guru: GuruDep,
                            file: UploadFile):
    if file.content_type not in _ALLOWED_IMAGE_TYPES:
        raise HTTPException(status_code=415, detail="unsupported_image_type")
    data = await file.read()
    if len(data) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail="upload_too_large")
    provider = guru.provider
    if provider is None:
        raise HTTPException(status_code=503, detail="llm_unconfigured")
    b64 = base64.b64encode(data).decode()
    with map_guru_errors():
        return await extract_statement(provider, db, user.id, b64, file.content_type)


# --- funds CRUD ------------------------------------------------------------

class FundOut(BaseModel):
    id: int
    code: str
    name: str
    asset_class: str
    risk_rating: int
    archived: bool
    currency: str


class FundCreate(BaseModel):
    code: str = Field(min_length=1, max_length=16)
    name: str = Field(min_length=1, max_length=120)
    asset_class: str = Field(min_length=1, max_length=32)
    risk_rating: int = Field(ge=1, le=7)
    currency: str = Field(default="HKD", min_length=3, max_length=3)


class FundUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    asset_class: str | None = Field(default=None, min_length=1, max_length=32)
    risk_rating: int | None = Field(default=None, ge=1, le=7)
    archived: bool | None = None
    currency: str | None = Field(default=None, min_length=3, max_length=3)


def _fund_out(f: OrsoFund) -> FundOut:
    return FundOut(id=f.id, code=f.code, name=f.name, asset_class=f.asset_class,
                   risk_rating=f.risk_rating, archived=f.archived, currency=f.currency)


@router.get("/funds", response_model=list[FundOut])
async def list_funds(db: SessionDep, user: CurrentUser):
    rows = (await db.execute(
        select(OrsoFund).where(OrsoFund.user_id == user.id).order_by(OrsoFund.id)
    )).scalars().all()
    return [_fund_out(f) for f in rows]


@router.get("/funds/search", response_model=list[FundOut])
async def search_funds(db: SessionDep, user: CurrentUser, q: str = ""):
    stmt = select(OrsoFund).where(OrsoFund.user_id == user.id)
    term = q.strip().lower()
    if term:
        like = f"%{term}%"
        stmt = stmt.where(
            func.lower(OrsoFund.code).like(like) | func.lower(OrsoFund.name).like(like))
    rows = (await db.execute(stmt.order_by(OrsoFund.code))).scalars().all()
    return [_fund_out(f) for f in rows]


@router.post("/funds", response_model=FundOut, status_code=201)
async def create_fund(body: FundCreate, db: SessionDep, user: CurrentUser):
    # Fund codes are normalised upper so they line up with the HSBC fund-centre
    # feed's own code casing (see app/services/orso/prices.py) and with
    # user-typed variants ("hk-eq" vs "HK-EQ") never colliding at the DB
    # UniqueConstraint("user_id", "code") level.
    code = body.code.upper()
    existing = (await db.execute(
        select(OrsoFund).where(OrsoFund.user_id == user.id, OrsoFund.code == code)
    )).scalar_one_or_none()
    if existing is not None:
        raise HTTPException(status_code=409, detail="fund_code_exists")
    fund = OrsoFund(user_id=user.id, **{**body.model_dump(), "code": code})
    db.add(fund)
    await db.commit()
    await db.refresh(fund)
    return _fund_out(fund)


async def _fund_units(db: SessionDep, fund_id: int) -> Decimal:
    alloc = (await db.execute(
        select(OrsoAllocation).where(OrsoAllocation.fund_id == fund_id)
    )).scalar_one_or_none()
    return alloc.units if alloc is not None else Decimal("0")


@router.patch("/funds/{fund_id}", response_model=FundOut)
async def update_fund(fund_id: int, body: FundUpdate, db: SessionDep, user: CurrentUser):
    fund = await get_owned_fund(db, user, fund_id)
    fields = body.model_dump(exclude_unset=True)
    if fields.get("archived") is True and not fund.archived:
        if (await _fund_units(db, fund.id)) > 0:
            raise HTTPException(status_code=409, detail="fund_has_units")
    for key, value in fields.items():
        setattr(fund, key, value)
    await db.commit()
    await db.refresh(fund)
    return _fund_out(fund)


# --- allocation full-replace + switch log ----------------------------------

class AllocationItem(BaseModel):
    fund_id: int
    units: Decimal = Field(ge=0)
    contribution_pct: Decimal = Field(ge=0, le=100)


class AllocationReplace(BaseModel):
    allocations: list[AllocationItem]
    note: str | None = Field(default=None, max_length=300)


class AllocationOut(BaseModel):
    fund_id: int
    code: str
    units: str
    contribution_pct: str


class AllocationResult(BaseModel):
    allocations: list[AllocationOut]
    switched: bool


def _canonical(entries: list[tuple[str, Decimal, Decimal]]) -> list[dict]:
    """Canonical switch-log state: a list of {code, units, contribution_pct}
    (Decimals rendered as strings, quantized so 10 and 10.0000 compare equal),
    sorted by fund code."""
    items = [
        {
            "code": code,
            "units": str(Decimal(units).quantize(_UNITS_Q)),
            "contribution_pct": str(Decimal(pct).quantize(_PCT_Q)),
        }
        for code, units, pct in entries
    ]
    return sorted(items, key=lambda x: x["code"])


async def _current_alloc_entries(
    db: SessionDep, user_id: int
) -> list[tuple[str, Decimal, Decimal]]:
    rows = (await db.execute(
        select(OrsoFund.code, OrsoAllocation.units, OrsoAllocation.contribution_pct)
        .join(OrsoAllocation, OrsoAllocation.fund_id == OrsoFund.id)
        .where(OrsoAllocation.user_id == user_id)
    )).all()
    return [(code, units, pct) for code, units, pct in rows]


class SwitchLogEntryOut(BaseModel):
    id: int
    changed_at: str
    note: str | None


class SwitchLogList(BaseModel):
    entries: list[SwitchLogEntryOut]


@router.get("/switchlog", response_model=SwitchLogList)
async def list_switch_log(db: SessionDep, user: CurrentUser, limit: int = 20):
    rows = (await db.execute(
        select(OrsoSwitchLog).where(OrsoSwitchLog.user_id == user.id)
        .order_by(OrsoSwitchLog.changed_at.desc(), OrsoSwitchLog.id.desc())
        .limit(limit)
    )).scalars().all()
    return SwitchLogList(entries=[
        SwitchLogEntryOut(id=r.id, changed_at=r.changed_at.isoformat(), note=r.note)
        for r in rows
    ])


@router.get("/allocation", response_model=list[AllocationOut])
async def read_allocation(db: SessionDep, user: CurrentUser):
    rows = (await db.execute(
        select(OrsoAllocation.fund_id, OrsoFund.code,
               OrsoAllocation.units, OrsoAllocation.contribution_pct)
        .join(OrsoFund, OrsoFund.id == OrsoAllocation.fund_id)
        .where(OrsoAllocation.user_id == user.id)
        .order_by(OrsoFund.code)
    )).all()
    return [AllocationOut(fund_id=fid, code=code, units=str(units),
                          contribution_pct=str(pct))
            for fid, code, units, pct in rows]


@router.put("/allocation", response_model=AllocationResult)
async def replace_allocation(body: AllocationReplace, db: SessionDep, user: CurrentUser):
    # Validate every fund_id belongs to the user (unknown/foreign -> 422).
    fund_ids = [a.fund_id for a in body.allocations]
    if len(set(fund_ids)) != len(fund_ids):
        raise HTTPException(status_code=422, detail="duplicate_fund_id")

    # Batch fetch all referenced funds in one query
    fund_rows = (await db.execute(
        select(OrsoFund).where(OrsoFund.id.in_(fund_ids))
    )).scalars().all()
    funds: dict[int, OrsoFund] = {f.id: f for f in fund_rows}

    # Validate: each fund exists, belongs to the user, and isn't archived with units > 0
    for a in body.allocations:
        fund = funds.get(a.fund_id)
        if fund is None or fund.user_id != user.id:
            raise HTTPException(status_code=422, detail="unknown_fund_id")
        if fund.archived and a.units > 0:
            raise HTTPException(status_code=422, detail="fund_archived")

    previous = _canonical(await _current_alloc_entries(db, user.id))

    await db.execute(delete(OrsoAllocation).where(OrsoAllocation.user_id == user.id))
    for a in body.allocations:
        db.add(OrsoAllocation(user_id=user.id, fund_id=a.fund_id,
                              units=a.units, contribution_pct=a.contribution_pct))

    new_entries = [
        (funds[a.fund_id].code, a.units, a.contribution_pct) for a in body.allocations
    ]
    new_state = _canonical(new_entries)

    switched = new_state != previous
    if switched:
        db.add(OrsoSwitchLog(
            user_id=user.id,
            changed_at=datetime.now(UTC).replace(tzinfo=None),
            old_state=previous,
            new_state=new_state,
            note=body.note,
        ))
    await db.commit()

    out = [AllocationOut(fund_id=a.fund_id, code=funds[a.fund_id].code,
                         units=str(a.units), contribution_pct=str(a.contribution_pct))
           for a in sorted(body.allocations, key=lambda x: funds[x.fund_id].code)]
    return AllocationResult(allocations=out, switched=switched)


# --- allocation apply (reviewed ingest draft; Task 4) -----------------------

class ApplyPriceIn(BaseModel):
    market_value: Decimal = Field(gt=0)
    as_of: date


class ApplyItem(BaseModel):
    fund_id: int | None = None
    new_fund_code: str | None = None
    units: Decimal = Field(ge=0)
    contribution_pct: Decimal = Field(ge=0, le=100)
    price: ApplyPriceIn | None = None


class ApplyNewFund(BaseModel):
    code: str = Field(min_length=1, max_length=16)
    name: str = Field(min_length=1, max_length=120)
    currency: str = Field(min_length=3, max_length=3)
    asset_class: str = Field(default="unknown", max_length=32)
    risk_rating: int = Field(default=4, ge=1, le=7)


class ApplyRequest(BaseModel):
    new_funds: list[ApplyNewFund] = []
    allocations: list[ApplyItem]
    note: str | None = Field(default=None, max_length=300)


class ApplyResult(BaseModel):
    created_funds: list[str]
    switched: bool


@router.post("/allocation/apply", response_model=ApplyResult)
async def apply_reviewed(body: ApplyRequest, db: SessionDep, user: CurrentUser,
                         prices: OrsoPriceDep):
    result = await apply_allocation(
        db, user,
        new_funds=[f.model_dump() for f in body.new_funds],
        allocations=[a.model_dump() for a in body.allocations],
        note=body.note, price_service=prices)
    await db.commit()
    return ApplyResult(**result)


# --- goals -----------------------------------------------------------------

_GOAL_FIELDS = (
    "birth_year", "retirement_target_age",
    "retirement_target_pot", "orso_monthly_contribution",
)


class GoalsOut(BaseModel):
    birth_year: int | None
    retirement_target_age: int | None
    retirement_target_pot: str | None
    orso_monthly_contribution: str | None


class GoalsIn(BaseModel):
    birth_year: int | None = Field(default=None, ge=1900, le=2100)
    retirement_target_age: int | None = Field(default=None, ge=30, le=100)
    retirement_target_pot: Decimal | None = Field(default=None, ge=0)
    orso_monthly_contribution: Decimal | None = Field(default=None, ge=0)


def _goals_out(row: InvestorProfile | None) -> GoalsOut:
    if row is None:
        return GoalsOut(birth_year=None, retirement_target_age=None,
                        retirement_target_pot=None, orso_monthly_contribution=None)
    return GoalsOut(
        birth_year=row.birth_year,
        retirement_target_age=row.retirement_target_age,
        retirement_target_pot=(None if row.retirement_target_pot is None
                               else str(row.retirement_target_pot)),
        orso_monthly_contribution=(None if row.orso_monthly_contribution is None
                                   else str(row.orso_monthly_contribution)),
    )


@router.get("/goals", response_model=GoalsOut)
async def read_goals(db: SessionDep, user: CurrentUser):
    return _goals_out(await get_profile_row(db, user))


@router.put("/goals", response_model=GoalsOut)
async def write_goals(body: GoalsIn, db: SessionDep, user: CurrentUser):
    values = body.model_dump(exclude_unset=True)
    if values:
        stmt = pg_insert(InvestorProfile).values(user_id=user.id, **values)
        stmt = stmt.on_conflict_do_update(index_elements=["user_id"], set_=values)
        await db.execute(stmt)
        await db.commit()
    return _goals_out(await get_profile_row(db, user))


# --- prices ----------------------------------------------------------------

class RefreshOut(BaseModel):
    refreshed: list[int]
    unavailable: bool


@router.post("/prices/refresh", response_model=RefreshOut)
async def refresh_prices(db: SessionDep, user: CurrentUser, prices: OrsoPriceDep):
    if prices.provider is None:
        return RefreshOut(refreshed=[], unavailable=True)
    funds = (await db.execute(
        select(OrsoFund).where(OrsoFund.user_id == user.id)
    )).scalars().all()
    refreshed = await prices.refresh(db, list(funds))
    await db.commit()
    return RefreshOut(refreshed=sorted(refreshed), unavailable=False)


class ManualPriceIn(BaseModel):
    fund_id: int
    price: Decimal = Field(gt=0)
    as_of: date


class PriceOut(BaseModel):
    fund_id: int
    price: str
    as_of: str
    source: str
    fetched_at: str


@router.put("/prices/manual", response_model=PriceOut)
async def manual_price(body: ManualPriceIn, db: SessionDep, user: CurrentUser,
                       prices: OrsoPriceDep):
    fund = await get_owned_fund(db, user, body.fund_id)
    row = await prices.upsert_manual_price(db, fund, body.price, body.as_of)
    await db.commit()
    return PriceOut(fund_id=row.fund_id, price=str(row.price),
                    as_of=row.as_of.isoformat(), source=row.source,
                    fetched_at=row.fetched_at.isoformat())


# --- overview --------------------------------------------------------------

async def _convert(fx, db, amount: Decimal | None, src: str, dst: str,
                   failed: list[str], code: str) -> Decimal | None:
    """Convert amount src->dst; None on FX failure (records `code` in `failed`)."""
    if amount is None:
        return None
    if src == dst:
        return amount.quantize(Decimal("0.01"))
    try:
        rate = await fx.get_rate(db, src, dst)
    except Exception:
        if code not in failed:
            failed.append(code)
        return None
    return (amount * rate).quantize(Decimal("0.01"))


async def build_overview(db, user, price_service: OrsoPriceService,
                         fx_service: FxService | None = None) -> dict:
    """GET /overview payload builder (also imported by Task 5's context
    builder). funds = active funds + any archived fund still holding units>0."""
    if fx_service is None:
        fx_service = FxService(get_quote_service().provider)

    funds = (await db.execute(
        select(OrsoFund).where(OrsoFund.user_id == user.id).order_by(OrsoFund.id)
    )).scalars().all()
    allocs = (await db.execute(
        select(OrsoAllocation).where(OrsoAllocation.user_id == user.id)
    )).scalars().all()
    alloc_by_fund = {a.fund_id: a for a in allocs}
    latest = await price_service.latest_prices(db, [f.id for f in funds])

    profile = await get_profile_row(db, user)
    display_ccy = (profile.orso_display_currency if profile
                   and profile.orso_display_currency else _BASE_CURRENCY)
    contrib_ccy = (profile.orso_contribution_currency if profile
                   and profile.orso_contribution_currency else "HKD")

    today = datetime.now(UTC).date()
    fund_rows: list[dict] = []
    stale: list[str] = []
    unpriced: list[str] = []
    fx_unavailable: list[str] = []
    total_hkd = Decimal("0")
    total_display = Decimal("0")
    contribution_sum = Decimal("0")

    for f in funds:
        alloc = alloc_by_fund.get(f.id)
        units = alloc.units if alloc is not None else Decimal("0")
        contribution_pct = alloc.contribution_pct if alloc is not None else Decimal("0")

        # Skip archived funds that no longer hold any units.
        if f.archived and units <= 0:
            continue

        price_row = latest.get(f.id)
        if price_row is None:
            price = None
            price_as_of = None
            price_source = None
            value_native = None
            value_hkd = None
            value_display = None
            unpriced.append(f.code)
        else:
            price = price_row.price
            price_as_of = price_row.as_of.isoformat()
            price_source = price_row.source
            value_native = (units * price).quantize(Decimal("0.01"))
            value_hkd = await _convert(fx_service, db, value_native, f.currency, "HKD",
                                       fx_unavailable, f.code)
            value_display = await _convert(fx_service, db, value_native, f.currency,
                                           display_ccy, fx_unavailable, f.code)
            if value_hkd is not None:
                total_hkd += value_hkd
            if value_display is not None:
                total_display += value_display
            if (today - price_row.as_of).days > _STALE_AFTER_DAYS:
                stale.append(f.code)

        if not f.archived:
            contribution_sum += contribution_pct

        fund_rows.append({
            "id": f.id,
            "code": f.code,
            "name": f.name,
            "asset_class": f.asset_class,
            "risk_rating": f.risk_rating,
            "archived": f.archived,
            "units": str(units),
            "contribution_pct": str(contribution_pct),
            "currency": f.currency,
            "value_native": (None if value_native is None else str(value_native)),
            "value_hkd": (None if value_hkd is None else str(value_hkd)),
            "value_display": (None if value_display is None else str(value_display)),
            "price": (None if price is None else str(price)),
            "price_as_of": price_as_of,
            "price_source": price_source,
        })

    active_count = sum(1 for f in funds if not f.archived)
    split_sum_off = active_count > 0 and contribution_sum != Decimal("100")

    # legacy total_base (HKD -> GBP) kept for the not-yet-migrated frontend
    total_base = None
    gbp = await _convert(fx_service, db, total_hkd, "HKD", _BASE_CURRENCY, [], "__total__")
    if gbp is not None:
        total_base = {"currency": _BASE_CURRENCY, "value": str(gbp)}

    # goals + projection
    goal_values = None if profile is None else {
        "birth_year": profile.birth_year,
        "retirement_target_age": profile.retirement_target_age,
        "retirement_target_pot": profile.retirement_target_pot,
        "orso_monthly_contribution": profile.orso_monthly_contribution,
    }
    goals_incomplete = goal_values is None or any(
        goal_values[k] is None for k in _GOAL_FIELDS
    )

    # projection runs in the display currency
    projection = None
    if not goals_incomplete:
        current_year = datetime.now(UTC).year
        years = goal_values["retirement_target_age"] - (
            current_year - goal_values["birth_year"]
        )
        monthly_display = await _convert(
            fx_service, db, goal_values["orso_monthly_contribution"],
            contrib_ccy, display_ccy, [], "__contrib__")
        if monthly_display is not None:
            scenarios = project(
                total_display,
                monthly_display,
                years,
                goal_values["retirement_target_pot"],
            )
            projection = [
                {
                    "rate": str(s.rate),
                    "projected_pot": str(s.projected_pot),
                    "on_track": s.on_track,
                    "gap": (None if s.gap is None else str(s.gap)),
                }
                for s in scenarios
            ]

    return {
        "funds": fund_rows,
        "total_hkd": str(total_hkd),
        "total_base": total_base,
        "total_display": str(total_display),
        "display_currency": display_ccy,
        "projection": projection,
        "flags": {
            "stale": stale,
            "unpriced": unpriced,
            "split_sum_off": split_sum_off,
            "goals_incomplete": goals_incomplete,
            "fx_unavailable": fx_unavailable,
        },
        "as_of": datetime.now(UTC).isoformat(),
    }


@router.get("/overview")
async def overview(db: SessionDep, user: CurrentUser, prices: OrsoPriceDep,
                   services: Annotated[tuple, Depends(get_services)]):
    _quotes, fx = services
    return await build_overview(db, user, prices, fx)


class DisplayCurrencyIn(BaseModel):
    currency: str = Field(min_length=3, max_length=3)


class DisplayCurrencyOut(BaseModel):
    currency: str


@router.put("/display-currency", response_model=DisplayCurrencyOut)
async def set_display_currency(body: DisplayCurrencyIn, db: SessionDep, user: CurrentUser):
    ccy = body.currency.upper()
    stmt = pg_insert(InvestorProfile).values(
        user_id=user.id, orso_display_currency=ccy)
    stmt = stmt.on_conflict_do_update(
        index_elements=["user_id"], set_={"orso_display_currency": ccy})
    await db.execute(stmt)
    await db.commit()
    return DisplayCurrencyOut(currency=ccy)


# --- switching advice (Guru ORSO mode) --------------------------------------

class AdviceList(BaseModel):
    reports: list[ReportOut]


@router.post("/advice", response_model=ReportOut, status_code=201)
async def create_advice(db: SessionDep, user: CurrentUser, guru: GuruDep, prices: OrsoPriceDep,
                        services: Annotated[tuple, Depends(get_services)]):
    _quotes, fx = services
    with map_guru_errors():
        report = await guru.generate_orso(db, user, prices, fx)
    return _report_out(report)


@router.get("/advice/latest", response_model=ReportOut)
async def read_latest_advice(db: SessionDep, user: CurrentUser):
    r = (await db.execute(select(GuruReport).where(
        GuruReport.user_id == user.id, GuruReport.kind == "orso"
    ).order_by(GuruReport.created_at.desc(), GuruReport.id.desc()).limit(1))).scalar_one_or_none()
    if r is None:
        raise HTTPException(status_code=404, detail="Not found")
    return _report_out(r)


@router.get("/advice", response_model=AdviceList)
async def list_advice(db: SessionDep, user: CurrentUser, limit: int = 20):
    rows = (await db.execute(
        select(GuruReport).where(GuruReport.user_id == user.id, GuruReport.kind == "orso")
        .order_by(GuruReport.created_at.desc(), GuruReport.id.desc()).limit(limit)
    )).scalars().all()
    return AdviceList(reports=[_report_out(r) for r in rows])
