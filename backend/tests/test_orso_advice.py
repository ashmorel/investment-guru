from datetime import UTC, date, datetime
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.models import LlmUsage, OrsoFund, OrsoFundPrice, User
from app.services.guru.persona import DISCLAIMER
from app.services.guru.schemas import FundVerdict, OrsoAdvicePayload, SwitchStep
from app.services.guru.service import GuruService
from tests.conftest import _test_services

pytestmark = pytest.mark.asyncio(loop_scope="session")


def _orso_advice(codes, switches=(), disclaimer=DISCLAIMER):
    return OrsoAdvicePayload(
        fund_verdicts=[
            FundVerdict(code=c, action="keep", conviction="med", rationale="steady")
            for c in codes
        ],
        switch_plan=[
            SwitchStep(from_code=f, to_code=t, note=n) for f, t, n in switches
        ],
        projection_comment="broadly on track",
        watch=["market volatility"],
        disclaimer=disclaimer,
    )


async def _current_user(db_session) -> User:
    return (await db_session.execute(
        select(User).where(User.email == "lee@test.dev")
    )).scalar_one()


async def _seed_fund(db_session, user_id: int, code: str, **overrides) -> OrsoFund:
    defaults = dict(name=code, asset_class="equity", risk_rating=3, archived=False)
    fund = OrsoFund(user_id=user_id, code=code, **{**defaults, **overrides})
    db_session.add(fund)
    await db_session.commit()
    await db_session.refresh(fund)
    return fund


async def _add_price(db_session, fund_id: int, price: str, as_of: date,
                     source: str = "hsbc") -> None:
    db_session.add(OrsoFundPrice(
        fund_id=fund_id, price=Decimal(price), as_of=as_of, source=source,
        fetched_at=datetime.now(UTC).replace(tzinfo=None),
    ))
    await db_session.commit()


async def _seed_basic(orso_client, db_session) -> tuple[OrsoFund, OrsoFund]:
    """Two active funds (A, B) with prices, an initial allocation (which
    writes one switch log with note 'initial switch'), and complete
    retirement goals (so build_overview's projection is populated)."""
    user = await _current_user(db_session)
    f1 = await _seed_fund(db_session, user.id, "A")
    f2 = await _seed_fund(db_session, user.id, "B")
    await _add_price(db_session, f1.id, "10.00", date.today())
    await _add_price(db_session, f2.id, "20.00", date.today())
    resp = await orso_client.put("/api/orso/allocation", json={
        "allocations": [
            {"fund_id": f1.id, "units": "10", "contribution_pct": "60"},
            {"fund_id": f2.id, "units": "5", "contribution_pct": "40"},
        ],
        "note": "initial switch",
    })
    assert resp.status_code == 200
    goals = await orso_client.put("/api/orso/goals", json={
        "birth_year": 1990, "retirement_target_age": 65,
        "retirement_target_pot": "1000000", "orso_monthly_contribution": "500",
    })
    assert goals.status_code == 200
    return f1, f2


# --- happy path --------------------------------------------------------------

async def test_advice_generates_and_persists(orso_client, db_session):
    await _seed_basic(orso_client, db_session)
    orso_client.fake_llm.structured_queue.append(_orso_advice(["A", "B"]))

    resp = await orso_client.post("/api/orso/advice")
    assert resp.status_code == 201
    body = resp.json()
    assert body["kind"] == "orso" and body["portfolio_id"] is None
    assert {v["code"] for v in body["payload"]["fund_verdicts"]} == {"A", "B"}

    assert len(orso_client.fake_llm.calls) == 1
    assert orso_client.fake_llm.calls[0]["max_tokens"] == 4096

    rows = (await db_session.execute(
        select(LlmUsage).where(LlmUsage.report_id == body["id"])
    )).scalars().all()
    assert len(rows) == 1
    assert rows[0].mode == "orso"
    assert rows[0].report_id == body["id"]


# --- fund-code validity retry -------------------------------------------------

async def test_advice_invalid_code_retries_then_succeeds(orso_client, db_session):
    await _seed_basic(orso_client, db_session)
    orso_client.fake_llm.structured_queue += [
        _orso_advice(["A", "ZZZ"]),
        _orso_advice(["A", "B"]),
    ]

    resp = await orso_client.post("/api/orso/advice")
    assert resp.status_code == 201
    assert len(orso_client.fake_llm.calls) == 2  # corrective retry happened

    body = resp.json()
    rows = (await db_session.execute(
        select(LlmUsage).where(LlmUsage.report_id == body["id"])
    )).scalars().all()
    assert len(rows) == 1
    assert rows[0].input_tokens == 200  # 100 from first call + 100 from second
    assert rows[0].output_tokens == 100  # 50 from first call + 50 from second


async def test_advice_invalid_switch_plan_code_retries_then_succeeds(orso_client, db_session):
    await _seed_basic(orso_client, db_session)
    orso_client.fake_llm.structured_queue += [
        _orso_advice(["A", "B"], switches=[("A", "NOPE", "switch out of A")]),
        _orso_advice(["A", "B"], switches=[("A", "B", "consolidate into B")]),
    ]

    resp = await orso_client.post("/api/orso/advice")
    assert resp.status_code == 201
    assert len(orso_client.fake_llm.calls) == 2


async def test_advice_invalid_code_twice_is_502(orso_client, db_session):
    await _seed_basic(orso_client, db_session)
    orso_client.fake_llm.structured_queue += [
        _orso_advice(["ZZZ"]),
        _orso_advice(["ZZZ"]),
    ]

    resp = await orso_client.post("/api/orso/advice")
    assert resp.status_code == 502
    assert resp.json()["detail"] == "llm_error"
    # nothing persisted
    listed = (await orso_client.get("/api/orso/advice")).json()
    assert listed["reports"] == []
    usage_rows = (await db_session.execute(select(LlmUsage))).scalars().all()
    assert usage_rows == []


async def test_advice_archived_fund_code_is_valid(orso_client, db_session):
    """Archived funds (even with zero units) must still count as valid codes —
    the Guru can legitimately verdict/reference a fund the user switched out of."""
    user = await _current_user(db_session)
    await _seed_basic(orso_client, db_session)
    await _seed_fund(db_session, user.id, "OLD", archived=True)
    orso_client.fake_llm.structured_queue.append(
        _orso_advice(["A", "B"], switches=[("OLD", "A", "already exited")])
    )

    resp = await orso_client.post("/api/orso/advice")
    assert resp.status_code == 201
    assert len(orso_client.fake_llm.calls) == 1


# --- locking / provider errors ------------------------------------------------

async def test_advice_http_409_when_generation_locked(orso_client, db_session):
    await _seed_basic(orso_client, db_session)
    orso_client.fake_llm.structured_queue.append(_orso_advice(["A", "B"]))

    lock = orso_client.guru_service._lock("orso")
    async with lock:
        resp = await orso_client.post("/api/orso/advice")
        assert resp.status_code == 409
        assert resp.json()["detail"] == "generation_in_progress"


async def test_advice_unconfigured_503(orso_client, db_session):
    from app.api.guru import get_guru

    await _seed_basic(orso_client, db_session)
    svc = GuruService(None, *(_test_services()))
    orso_client.app.dependency_overrides[get_guru] = lambda: svc

    resp = await orso_client.post("/api/orso/advice")
    assert resp.status_code == 503
    assert resp.json()["detail"] == "llm_unconfigured"


# --- latest / list -------------------------------------------------------------

async def test_advice_latest_404_when_none(orso_client):
    resp = await orso_client.get("/api/orso/advice/latest")
    assert resp.status_code == 404


async def test_advice_latest_and_list(orso_client, db_session):
    await _seed_basic(orso_client, db_session)
    orso_client.fake_llm.structured_queue += [
        _orso_advice(["A", "B"]),
        _orso_advice(["A", "B"]),
    ]

    r1 = await orso_client.post("/api/orso/advice")
    r2 = await orso_client.post("/api/orso/advice")
    assert r1.status_code == 201 and r2.status_code == 201

    latest = await orso_client.get("/api/orso/advice/latest")
    assert latest.status_code == 200
    assert latest.json()["id"] == r2.json()["id"]

    listed = (await orso_client.get("/api/orso/advice?limit=20")).json()
    assert [r["id"] for r in listed["reports"]] == [r2.json()["id"], r1.json()["id"]]


# --- context content -----------------------------------------------------------

async def test_advice_context_includes_fund_menu_projection_and_switch(orso_client, db_session):
    await _seed_basic(orso_client, db_session)
    orso_client.fake_llm.structured_queue.append(_orso_advice(["A", "B"]))

    resp = await orso_client.post("/api/orso/advice")
    assert resp.status_code == 201

    msg = orso_client.fake_llm.calls[0]["messages"][0]["content"]
    # fund menu codes
    assert '"A"' in msg and '"B"' in msg
    # projection rates (deterministic 2%/5%/8% scenarios from app.services.orso.projection)
    assert "0.02" in msg and "0.05" in msg and "0.08" in msg
    # recent switch note carried through from the allocation replace
    assert "initial switch" in msg
