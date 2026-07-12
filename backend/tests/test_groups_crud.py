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
