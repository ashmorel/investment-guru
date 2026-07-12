from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import text

from app.models import GroupAssignment, GroupSnapshot, HoldingGroup

pytestmark = pytest.mark.asyncio(loop_scope="session")


async def test_group_models_persist_and_snapshot_encrypts(db_session, make_instrument):
    from app.core.security import hash_password
    from app.models.user import User
    u = User(email="grp1@test.dev", password_hash=hash_password("pw123456"))
    db_session.add(u)
    await db_session.commit()
    inst = await make_instrument("AAPL")

    g = HoldingGroup(user_id=u.id, name="Tech", color="#4F46E5", sort_order=0)
    db_session.add(g)
    await db_session.commit()
    db_session.add(GroupAssignment(user_id=u.id, instrument_id=inst.id, group_id=g.id))
    db_session.add(GroupSnapshot(user_id=u.id, group_id=g.id, as_of=date(2026, 7, 12),
                                 value_base=Decimal("1234.56")))
    # Ungrouped snapshot (group_id NULL)
    db_session.add(GroupSnapshot(user_id=u.id, group_id=None, as_of=date(2026, 7, 12),
                                 value_base=Decimal("50.00")))
    await db_session.commit()

    snap = (await db_session.execute(text(
        "SELECT value_base FROM group_snapshots WHERE user_id=:u AND group_id=:g"),
        {"u": u.id, "g": g.id})).scalar_one()
    assert snap.startswith("v1:") and "1234.56" not in snap   # encrypted at rest
    got = (await db_session.execute(text(
        "SELECT value_base FROM group_snapshots WHERE user_id=:u AND group_id IS NULL"),
        {"u": u.id})).scalar_one()
    assert got.startswith("v1:")


async def _hold(auth_client, symbol, make_instrument, sector=None):
    await make_instrument(symbol, sector=sector)
    pid = (await auth_client.post("/api/portfolios",
           json={"name": "P", "kind": "real", "base_currency": "GBP"})).json()["id"]
    await auth_client.post(
        f"/api/portfolios/{pid}/positions", json={"symbol": symbol, "quantity": "1"})


async def test_group_crud_and_assign(auth_client, make_instrument):
    await _hold(auth_client, "AAPL", make_instrument)
    g = (await auth_client.post("/api/groups", json={"name": "Tech", "color": "#4F46E5"})).json()
    assert g["name"] == "Tech"
    # duplicate name -> 409
    assert (await auth_client.post("/api/groups", json={"name": "Tech"})).status_code == 409
    # assign a held symbol
    r = await auth_client.put("/api/groups/assign", json={"symbol": "AAPL", "group_id": g["id"]})
    assert r.status_code == 200
    lst = (await auth_client.get("/api/groups")).json()
    assert lst[0]["holding_count"] == 1
    # assign a symbol not held -> 422
    assert (await auth_client.put("/api/groups/assign",
            json={"symbol": "NVDA", "group_id": g["id"]})).status_code == 422
    # clear assignment (null) -> Ungrouped
    await auth_client.put("/api/groups/assign", json={"symbol": "AAPL", "group_id": None})
    assert (await auth_client.get("/api/groups")).json()[0]["holding_count"] == 0
    # delete group cascades
    assert (await auth_client.delete(f"/api/groups/{g['id']}")).status_code == 204


async def test_seed_from_sectors_idempotent_nondestructive(auth_client, make_instrument):
    await _hold(auth_client, "AAPL", make_instrument, sector="Technology")
    await _hold(auth_client, "XOM", make_instrument, sector="Energy")
    await _hold(auth_client, "ZZZ", make_instrument, sector=None)  # -> "Unclassified"
    r1 = (await auth_client.post("/api/groups/seed-from-sectors")).json()
    assert set(r1["created"]) == {"Technology", "Energy", "Unclassified"} and r1["assigned"] == 3
    # move AAPL into a hand-made "Space" group
    space = (await auth_client.post("/api/groups", json={"name": "Space"})).json()
    await auth_client.put("/api/groups/assign", json={"symbol": "AAPL", "group_id": space["id"]})
    # re-seed: creates nothing new, assigns nothing (all assigned), does NOT move AAPL back
    r2 = (await auth_client.post("/api/groups/seed-from-sectors")).json()
    assert r2["created"] == [] and r2["assigned"] == 0
    groups = {g["name"]: g for g in (await auth_client.get("/api/groups")).json()}
    assert groups["Space"]["holding_count"] == 1        # AAPL stayed in Space
    assert groups["Technology"]["holding_count"] == 0


async def test_holdings_lists_held_instruments_with_current_group(auth_client, make_instrument):
    await _hold(auth_client, "AAPL", make_instrument)
    await _hold(auth_client, "XOM", make_instrument)
    g = (await auth_client.post("/api/groups", json={"name": "Tech", "color": "#4F46E5"})).json()
    await auth_client.put("/api/groups/assign", json={"symbol": "AAPL", "group_id": g["id"]})

    holdings = (await auth_client.get("/api/groups/holdings")).json()
    by_symbol = {h["symbol"]: h for h in holdings}
    assert set(by_symbol) == {"AAPL", "XOM"}
    # ordered by symbol
    assert [h["symbol"] for h in holdings] == ["AAPL", "XOM"]
    assert by_symbol["AAPL"]["group_id"] == g["id"]
    assert by_symbol["AAPL"]["group_name"] == "Tech"
    assert by_symbol["AAPL"]["name"]  # instrument name is populated
    assert by_symbol["XOM"]["group_id"] is None
    assert by_symbol["XOM"]["group_name"] is None


async def test_holdings_are_user_scoped(auth_client, client, db_session, make_instrument):
    # User A holds AAPL.
    await _hold(auth_client, "AAPL", make_instrument)
    # User B logs in and holds XOM only.
    from app.core.security import hash_password
    from app.models.user import User
    db_session.add(User(email="bhold@test.dev", password_hash=hash_password("pw123456")))
    await db_session.commit()
    await client.post("/api/auth/login", json={"email": "bhold@test.dev", "password": "pw123456"})
    await _hold(client, "XOM", make_instrument)

    b_holdings = (await client.get("/api/groups/holdings")).json()
    assert [h["symbol"] for h in b_holdings] == ["XOM"]  # never sees User A's AAPL


async def test_groups_are_user_scoped(auth_client, client, db_session, make_instrument):
    g = (await auth_client.post("/api/groups", json={"name": "Mine"})).json()
    from app.core.security import hash_password
    from app.models.user import User
    db_session.add(User(email="bgrp@test.dev", password_hash=hash_password("pw123456")))
    await db_session.commit()
    await client.post("/api/auth/login", json={"email": "bgrp@test.dev", "password": "pw123456"})
    assert (await client.get("/api/groups")).json() == []
    assert (await client.patch(f"/api/groups/{g['id']}", json={"name": "x"})).status_code == 404
    assert (await client.delete(f"/api/groups/{g['id']}")).status_code == 404
