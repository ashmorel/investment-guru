import types as _types
from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import func, select

from app.models import GroupSnapshot
from app.services.groups.exposure import local_today
from tests.conftest import TestSession

pytestmark = pytest.mark.asyncio(loop_scope="session")


def _stub_valuation(monkeypatch, prices: dict):
    """Stub value_portfolio (imported into the exposure module) so the snapshot
    job is deterministic without live quotes. prices: symbol -> market_value_base."""
    import app.services.groups.exposure as expo

    async def fake(db, portfolio, quote_service, fx):
        positions = [
            _types.SimpleNamespace(
                symbol=p.instrument.symbol,
                market_value_base=prices.get(p.instrument.symbol),
                day_change_base=(None if prices.get(p.instrument.symbol) is None
                                 else Decimal("1")),
            )
            for p in portfolio.positions
        ]
        return _types.SimpleNamespace(positions=positions)

    monkeypatch.setattr(expo, "value_portfolio", fake)


async def _hold(auth_client, symbol, make_instrument):
    await make_instrument(symbol)
    pid = (await auth_client.post("/api/portfolios",
           json={"name": symbol, "kind": "real", "base_currency": "GBP"})).json()["id"]
    await auth_client.post(
        f"/api/portfolios/{pid}/positions", json={"symbol": symbol, "quantity": "1"})


async def test_write_snapshot_is_idempotent_delete_then_insert(
        auth_client, db_session, make_instrument):
    from app.models.user import User
    from app.services.groups.exposure import write_snapshot
    user = (await db_session.execute(select(User).where(User.email == "lee@test.dev"))).scalar_one()
    await _hold(auth_client, "AAPL", make_instrument)
    result = {"groups": [{"group_id": None, "name": "Ungrouped", "value_base": "42.00"}],
              "total_base": "42.00", "unpriced": []}
    today = date(2026, 7, 12)
    await write_snapshot(db_session, user, result, today)
    await write_snapshot(db_session, user, result, today)   # re-run same day
    n = (await db_session.execute(select(func.count()).select_from(GroupSnapshot)
         .where(GroupSnapshot.user_id == user.id, GroupSnapshot.as_of == today))).scalar_one()
    assert n == 1                                            # not duplicated
    val = (await db_session.execute(select(GroupSnapshot.value_base)
           .where(GroupSnapshot.user_id == user.id))).scalar_one()
    assert val == Decimal("42.00")


async def test_trend_returns_series_with_pct(auth_client, db_session, make_instrument):
    from app.models.user import User
    user = (await db_session.execute(select(User).where(User.email == "lee@test.dev"))).scalar_one()
    g = (await auth_client.post("/api/groups", json={"name": "Tech"})).json()
    db_session.add(GroupSnapshot(user_id=user.id, group_id=g["id"], as_of=date(2026, 7, 11),
                                 value_base=Decimal("80")))
    db_session.add(GroupSnapshot(user_id=user.id, group_id=None, as_of=date(2026, 7, 11),
                                 value_base=Decimal("20")))
    await db_session.commit()
    body = (await auth_client.get("/api/groups/trend?range=90d")).json()
    series = {s["name"]: s for s in body["series"]}
    tech_pt = series["Tech"]["points"][0]
    assert tech_pt["value_base"] == "80.00" and tech_pt["pct"] == "80.00"   # 80/(80+20)


async def test_exposure_opportunistic_write_failure_still_returns_200(
        auth_client, make_instrument, monkeypatch):
    """A failing opportunistic snapshot write (e.g. a concurrency race on the
    unique constraint) must never fail the exposure request — it degrades to a
    plain 200 with the exposure result intact."""
    await _hold(auth_client, "AAPL", make_instrument)

    async def _boom(db, user, result, as_of):
        raise RuntimeError("simulated unique-constraint race")

    monkeypatch.setattr("app.api.groups.write_snapshot", _boom)
    resp = await auth_client.get("/api/groups/exposure")
    assert resp.status_code == 200
    body = resp.json()
    assert "groups" in body and "total_base" in body and "as_of" in body


async def test_run_group_snapshot_job_writes_today_row(
        auth_client, db_session, make_instrument, monkeypatch):
    from app.models.user import User
    from app.services.groups.snapshot import run_group_snapshot_job
    user = (await db_session.execute(select(User).where(User.email == "lee@test.dev"))).scalar_one()
    await _hold(auth_client, "AAPL", make_instrument)
    g = (await auth_client.post("/api/groups", json={"name": "Tech"})).json()
    await auth_client.put("/api/groups/assign", json={"symbol": "AAPL", "group_id": g["id"]})
    _stub_valuation(monkeypatch, {"AAPL": Decimal("100")})

    await run_group_snapshot_job(session_factory=TestSession)

    today = local_today()
    rows = (await db_session.execute(
        select(GroupSnapshot).where(
            GroupSnapshot.user_id == user.id, GroupSnapshot.as_of == today))).scalars().all()
    assert len(rows) == 1
    assert rows[0].group_id == g["id"]
    assert rows[0].value_base == Decimal("100.00")
