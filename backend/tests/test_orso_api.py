from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.models import OrsoAllocation, OrsoFund, OrsoFundPrice, OrsoSwitchLog, User
from app.services.orso.prices import PriceDTO

pytestmark = pytest.mark.asyncio(loop_scope="session")


# --- helpers ---------------------------------------------------------------

async def _seed_fund(db_session, user_id: int, code: str, **overrides) -> OrsoFund:
    defaults = dict(name=code, asset_class="equity", risk_rating=3, archived=False)
    fund = OrsoFund(user_id=user_id, code=code, **{**defaults, **overrides})
    db_session.add(fund)
    await db_session.commit()
    await db_session.refresh(fund)
    return fund


async def _current_user(db_session) -> User:
    return (await db_session.execute(
        select(User).where(User.email == "lee@test.dev")
    )).scalar_one()


async def _add_price(db_session, fund_id: int, price: str, as_of: date,
                     source: str = "hsbc") -> None:
    db_session.add(OrsoFundPrice(
        fund_id=fund_id, price=Decimal(price), as_of=as_of, source=source,
        fetched_at=datetime.now(UTC).replace(tzinfo=None),
    ))
    await db_session.commit()


# --- auth ------------------------------------------------------------------

async def test_endpoints_require_auth(client):
    assert (await client.get("/api/orso/funds")).status_code == 401
    assert (await client.get("/api/orso/overview")).status_code == 401
    assert (await client.get("/api/orso/goals")).status_code == 401


# --- funds CRUD + ownership ------------------------------------------------

async def test_create_and_list_funds(orso_client):
    resp = await orso_client.post("/api/orso/funds", json={
        "code": "HK-EQ", "name": "HK Equity", "asset_class": "equity", "risk_rating": 4})
    assert resp.status_code == 201
    body = resp.json()
    assert body["code"] == "HK-EQ" and body["archived"] is False
    listing = (await orso_client.get("/api/orso/funds")).json()
    assert [f["code"] for f in listing] == ["HK-EQ"]


async def test_create_fund_normalises_code_upper(orso_client):
    resp = await orso_client.post("/api/orso/funds", json={
        "code": "hk-eq", "name": "HK Equity", "asset_class": "equity", "risk_rating": 4})
    assert resp.status_code == 201
    assert resp.json()["code"] == "HK-EQ"
    # a differently-cased resubmit collides with the same normalised code
    dup = await orso_client.post("/api/orso/funds", json={
        "code": "HK-eq", "name": "dup", "asset_class": "equity", "risk_rating": 4})
    assert dup.status_code == 409 and dup.json()["detail"] == "fund_code_exists"


async def test_get_owned_fund_404_on_foreign(orso_client, db_session):
    other = User(email="other@test.dev", password_hash="x")
    db_session.add(other)
    await db_session.commit()
    foreign = await _seed_fund(db_session, other.id, "FOR")
    resp = await orso_client.patch(f"/api/orso/funds/{foreign.id}", json={"name": "x"})
    assert resp.status_code == 404


async def test_archive_fund_with_units_409(orso_client, db_session):
    user = await _current_user(db_session)
    fund = await _seed_fund(db_session, user.id, "BAL")
    db_session.add(OrsoAllocation(user_id=user.id, fund_id=fund.id,
                                  units=Decimal("5"), contribution_pct=Decimal("100")))
    await db_session.commit()
    resp = await orso_client.patch(f"/api/orso/funds/{fund.id}", json={"archived": True})
    assert resp.status_code == 409 and resp.json()["detail"] == "fund_has_units"


async def test_archive_fund_without_units_ok(orso_client, db_session):
    user = await _current_user(db_session)
    fund = await _seed_fund(db_session, user.id, "BAL")
    resp = await orso_client.patch(f"/api/orso/funds/{fund.id}", json={"archived": True})
    assert resp.status_code == 200 and resp.json()["archived"] is True


# --- allocation full-replace + switch log ----------------------------------

async def test_allocation_replace_writes_one_switch_log(orso_client, db_session):
    user = await _current_user(db_session)
    f1 = await _seed_fund(db_session, user.id, "A")
    f2 = await _seed_fund(db_session, user.id, "B")
    resp = await orso_client.put("/api/orso/allocation", json={"allocations": [
        {"fund_id": f1.id, "units": "10", "contribution_pct": "60"},
        {"fund_id": f2.id, "units": "5", "contribution_pct": "40"},
    ], "note": "initial"})
    assert resp.status_code == 200 and resp.json()["switched"] is True
    logs = (await db_session.execute(select(OrsoSwitchLog))).scalars().all()
    assert len(logs) == 1
    assert logs[0].note == "initial"
    assert {e["code"] for e in logs[0].new_state} == {"A", "B"}
    allocs = (await db_session.execute(select(OrsoAllocation))).scalars().all()
    assert len(allocs) == 2


async def test_allocation_noop_writes_no_switch_log(orso_client, db_session):
    user = await _current_user(db_session)
    f1 = await _seed_fund(db_session, user.id, "A")
    payload = {"allocations": [
        {"fund_id": f1.id, "units": "10", "contribution_pct": "100"}]}
    r1 = await orso_client.put("/api/orso/allocation", json=payload)
    assert r1.json()["switched"] is True
    # identical resubmit (note the trailing-zero variance) is a no-op
    payload2 = {"allocations": [
        {"fund_id": f1.id, "units": "10.0000", "contribution_pct": "100.00"}]}
    r2 = await orso_client.put("/api/orso/allocation", json=payload2)
    assert r2.json()["switched"] is False
    logs = (await db_session.execute(select(OrsoSwitchLog))).scalars().all()
    assert len(logs) == 1


async def test_allocation_rejects_negative_units(orso_client, db_session):
    user = await _current_user(db_session)
    f1 = await _seed_fund(db_session, user.id, "A")
    resp = await orso_client.put("/api/orso/allocation", json={"allocations": [
        {"fund_id": f1.id, "units": "-1", "contribution_pct": "50"}]})
    assert resp.status_code == 422


async def test_allocation_rejects_pct_out_of_range(orso_client, db_session):
    user = await _current_user(db_session)
    f1 = await _seed_fund(db_session, user.id, "A")
    resp = await orso_client.put("/api/orso/allocation", json={"allocations": [
        {"fund_id": f1.id, "units": "1", "contribution_pct": "150"}]})
    assert resp.status_code == 422


async def test_allocation_rejects_unknown_fund(orso_client, db_session):
    resp = await orso_client.put("/api/orso/allocation", json={"allocations": [
        {"fund_id": 999999, "units": "1", "contribution_pct": "50"}]})
    assert resp.status_code == 422


async def test_allocation_rejects_foreign_fund(orso_client, db_session):
    other = User(email="foreign-alloc@test.dev", password_hash="x")
    db_session.add(other)
    await db_session.commit()
    foreign = await _seed_fund(db_session, other.id, "FOR")
    resp = await orso_client.put("/api/orso/allocation", json={"allocations": [
        {"fund_id": foreign.id, "units": "1", "contribution_pct": "50"}]})
    assert resp.status_code == 422


async def test_allocation_rejects_archived_fund_with_units(orso_client, db_session):
    user = await _current_user(db_session)
    fund = await _seed_fund(db_session, user.id, "ARC", archived=True)
    resp = await orso_client.put("/api/orso/allocation", json={"allocations": [
        {"fund_id": fund.id, "units": "1", "contribution_pct": "50"}]})
    assert resp.status_code == 422 and resp.json()["detail"] == "fund_archived"
    # Verify nothing was persisted
    allocs = (await db_session.execute(select(OrsoAllocation))).scalars().all()
    assert len(allocs) == 0
    logs = (await db_session.execute(select(OrsoSwitchLog))).scalars().all()
    assert len(logs) == 0


async def test_allocation_allows_archived_fund_with_zero_units(orso_client, db_session):
    user = await _current_user(db_session)
    normal = await _seed_fund(db_session, user.id, "NORM")
    archived = await _seed_fund(db_session, user.id, "ARC", archived=True)
    resp = await orso_client.put("/api/orso/allocation", json={"allocations": [
        {"fund_id": normal.id, "units": "10", "contribution_pct": "100"},
        {"fund_id": archived.id, "units": "0", "contribution_pct": "0"},
    ], "note": "clear archived"})
    assert resp.status_code == 200
    allocs = (await db_session.execute(select(OrsoAllocation))).scalars().all()
    assert len(allocs) == 2
    logs = (await db_session.execute(select(OrsoSwitchLog))).scalars().all()
    assert len(logs) == 1
    assert logs[0].note == "clear archived"


# --- prices ----------------------------------------------------------------

async def test_manual_price_upsert(orso_client, db_session):
    user = await _current_user(db_session)
    fund = await _seed_fund(db_session, user.id, "MMF")
    resp = await orso_client.put("/api/orso/prices/manual", json={
        "fund_id": fund.id, "price": "12.5", "as_of": "2026-07-01"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["source"] == "manual" and body["price"] == "12.5"
    row = (await db_session.execute(select(OrsoFundPrice))).scalar_one()
    assert row.price == Decimal("12.5")


async def test_manual_price_rejects_non_positive(orso_client, db_session):
    user = await _current_user(db_session)
    fund = await _seed_fund(db_session, user.id, "MMF")
    resp = await orso_client.put("/api/orso/prices/manual", json={
        "fund_id": fund.id, "price": "0", "as_of": "2026-07-01"})
    assert resp.status_code == 422


async def test_refresh_unavailable_without_provider(guru_client, db_session):
    # guru_client has no orso price override -> singleton provider is None in tests
    from app.api.orso import get_orso_prices
    from app.services.orso.prices import OrsoPriceService
    guru_client.app.dependency_overrides[get_orso_prices] = \
        lambda: OrsoPriceService(None)
    resp = await guru_client.post("/api/orso/prices/refresh")
    assert resp.status_code == 200
    assert resp.json() == {"refreshed": [], "unavailable": True}


async def test_refresh_with_fake_provider_writes_prices(orso_client, db_session):
    user = await _current_user(db_session)
    fund = await _seed_fund(db_session, user.id, "HK-EQ")
    orso_client.fake_orso_prices.prices = {
        "HK-EQ": PriceDTO(price=Decimal("101.2345"), as_of=date.today())}
    resp = await orso_client.post("/api/orso/prices/refresh")
    assert resp.status_code == 200
    body = resp.json()
    assert body["unavailable"] is False and body["refreshed"] == [fund.id]
    row = (await db_session.execute(select(OrsoFundPrice))).scalar_one()
    assert row.price == Decimal("101.2345") and row.source == "hsbc"


# --- goals -----------------------------------------------------------------

async def test_goals_roundtrip_and_partial(orso_client):
    assert (await orso_client.get("/api/orso/goals")).json() == {
        "birth_year": None, "retirement_target_age": None,
        "retirement_target_pot": None, "orso_monthly_contribution": None}
    r = await orso_client.put("/api/orso/goals", json={
        "birth_year": 1985, "retirement_target_age": 65,
        "retirement_target_pot": "5000000", "orso_monthly_contribution": "3000"})
    assert r.status_code == 200
    assert r.json()["birth_year"] == 1985
    # partial update leaves other fields intact
    r2 = await orso_client.put("/api/orso/goals", json={"retirement_target_age": 60})
    body = r2.json()
    assert body["retirement_target_age"] == 60 and body["birth_year"] == 1985
    assert body["retirement_target_pot"] == "5000000.00"


# --- overview --------------------------------------------------------------

async def test_overview_values_and_flags(orso_client, db_session):
    user = await _current_user(db_session)
    f_priced = await _seed_fund(db_session, user.id, "A")
    f_stale = await _seed_fund(db_session, user.id, "B")
    f_unpriced = await _seed_fund(db_session, user.id, "C")
    db_session.add_all([
        OrsoAllocation(user_id=user.id, fund_id=f_priced.id,
                       units=Decimal("10"), contribution_pct=Decimal("50")),
        OrsoAllocation(user_id=user.id, fund_id=f_stale.id,
                       units=Decimal("2"), contribution_pct=Decimal("30")),
        OrsoAllocation(user_id=user.id, fund_id=f_unpriced.id,
                       units=Decimal("1"), contribution_pct=Decimal("10")),
    ])
    await db_session.commit()
    await _add_price(db_session, f_priced.id, "5.00", date.today())
    await _add_price(db_session, f_stale.id, "3.00", date.today() - timedelta(days=8))

    ov = (await orso_client.get("/api/orso/overview")).json()
    by_code = {f["code"]: f for f in ov["funds"]}
    assert by_code["A"]["value_hkd"] == "50.00"   # 10 * 5
    assert by_code["C"]["value_hkd"] is None
    assert ov["total_hkd"] == "56.00"             # 50 + (2*3)
    assert ov["flags"]["stale"] == ["B"]
    assert ov["flags"]["unpriced"] == ["C"]
    # contribution 50+30+10 = 90 != 100
    assert ov["flags"]["split_sum_off"] is True
    # goals not set -> incomplete, no projection
    assert ov["flags"]["goals_incomplete"] is True
    assert ov["projection"] is None
    # FX has no HKDGBP rate in tests -> total_base null, no error
    assert ov["total_base"] is None


async def test_overview_projection_when_goals_complete(orso_client, db_session):
    user = await _current_user(db_session)
    fund = await _seed_fund(db_session, user.id, "A")
    db_session.add(OrsoAllocation(user_id=user.id, fund_id=fund.id,
                                  units=Decimal("100"), contribution_pct=Decimal("100")))
    await db_session.commit()
    await _add_price(db_session, fund.id, "10.00", date.today())
    await orso_client.put("/api/orso/goals", json={
        "birth_year": 1990, "retirement_target_age": 65,
        "retirement_target_pot": "1000000", "orso_monthly_contribution": "2000"})

    ov = (await orso_client.get("/api/orso/overview")).json()
    assert ov["flags"]["goals_incomplete"] is False
    assert ov["projection"] is not None
    assert [s["rate"] for s in ov["projection"]] == ["0.02", "0.05", "0.08"]
    for s in ov["projection"]:
        assert s["on_track"] in (True, False)
        assert s["gap"] is not None


async def test_overview_includes_archived_fund_with_units(orso_client, db_session):
    user = await _current_user(db_session)
    active = await _seed_fund(db_session, user.id, "ACT")
    archived_held = await _seed_fund(db_session, user.id, "ARCH-H", archived=True)
    await _seed_fund(db_session, user.id, "ARCH-E", archived=True)
    db_session.add_all([
        OrsoAllocation(user_id=user.id, fund_id=active.id,
                       units=Decimal("1"), contribution_pct=Decimal("100")),
        OrsoAllocation(user_id=user.id, fund_id=archived_held.id,
                       units=Decimal("3"), contribution_pct=Decimal("0")),
    ])
    await db_session.commit()
    ov = (await orso_client.get("/api/orso/overview")).json()
    codes = {f["code"] for f in ov["funds"]}
    assert "ACT" in codes and "ARCH-H" in codes
    assert "ARCH-E" not in codes
