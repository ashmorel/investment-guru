import asyncio
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.core.security import hash_password
from app.models import LlmUsage, Portfolio, Position
from app.models.user import User
from app.services.guru.llm.fake import FakeLLMProvider
from app.services.guru.persona import DISCLAIMER
from app.services.guru.schemas import PositionVerdict, ReviewPayload
from app.services.guru.service import GenerationInProgress, GuruService
from tests.conftest import _test_services

pytestmark = pytest.mark.asyncio(loop_scope="session")


def _review(symbols, extra=()):
    return ReviewPayload(
        positions=[PositionVerdict(symbol=s, action="hold", conviction="med",
                                   rationale="steady") for s in [*symbols, *extra]],
        observations=["concentrated in tech"], watch_next=["AAPL earnings"],
        disclaimer=DISCLAIMER)


async def _seed_portfolio(client, make_instrument) -> int:
    await make_instrument("AAPL")
    await make_instrument("MSFT")
    pid = (await client.post(
        "/api/portfolios", json={"name": "P", "kind": "real", "base_currency": "USD"}
    )).json()["id"]
    await client.post(
        f"/api/portfolios/{pid}/positions",
        json={"symbol": "AAPL", "quantity": "10", "avg_cost": "100"},
    )
    await client.post(
        f"/api/portfolios/{pid}/positions",
        json={"symbol": "MSFT", "quantity": "5", "avg_cost": "200"},
    )
    return pid


async def test_review_generates_and_persists(guru_client, db_session, make_instrument):
    pf_id = await _seed_portfolio(guru_client, make_instrument)
    guru_client.fake_llm.structured_queue.append(_review(["AAPL", "MSFT"]))

    resp = await guru_client.post("/api/guru/reviews", json={"portfolio_id": pf_id})
    assert resp.status_code == 201
    body = resp.json()
    assert body["kind"] == "review" and body["portfolio_id"] == pf_id
    assert {p["symbol"] for p in body["payload"]["positions"]} == {"AAPL", "MSFT"}

    # persisted + listable
    listed = (await guru_client.get(f"/api/guru/reviews?portfolio_id={pf_id}")).json()
    assert listed["reviews"][0]["id"] == body["id"]

    # readable by id
    read = await guru_client.get(f"/api/guru/reviews/{body['id']}")
    assert read.status_code == 200
    assert read.json()["id"] == body["id"]

    # usage row written
    rows = (await db_session.execute(
        select(LlmUsage).where(LlmUsage.report_id == body["id"])
    )).scalars().all()
    assert len(rows) == 1
    assert rows[0].mode == "review"
    assert rows[0].report_id == body["id"]


async def test_review_missing_position_retries_then_succeeds(guru_client, make_instrument):
    pf_id = await _seed_portfolio(guru_client, make_instrument)
    guru_client.fake_llm.structured_queue += [_review(["AAPL"]), _review(["AAPL", "MSFT"])]

    resp = await guru_client.post("/api/guru/reviews", json={"portfolio_id": pf_id})
    assert resp.status_code == 201
    assert len(guru_client.fake_llm.calls) == 2  # corrective retry happened


async def test_review_missing_position_twice_is_502(guru_client, make_instrument):
    pf_id = await _seed_portfolio(guru_client, make_instrument)
    guru_client.fake_llm.structured_queue += [_review(["AAPL"]), _review(["AAPL"])]

    resp = await guru_client.post("/api/guru/reviews", json={"portfolio_id": pf_id})
    assert resp.status_code == 502
    assert resp.json()["detail"] == "llm_error"
    # nothing persisted
    listed = (await guru_client.get(f"/api/guru/reviews?portfolio_id={pf_id}")).json()
    assert listed["reviews"] == []


async def test_review_unconfigured_503(auth_client, make_instrument):
    from app.api.guru import get_guru

    pf_id = await _seed_portfolio(auth_client, make_instrument)
    svc = GuruService(None, *(_test_services()))
    auth_client.app.dependency_overrides[get_guru] = lambda: svc

    resp = await auth_client.post("/api/guru/reviews", json={"portfolio_id": pf_id})
    assert resp.status_code == 503
    assert resp.json()["detail"] == "llm_unconfigured"


async def test_generate_review_second_call_while_locked_raises(db_session, make_instrument):
    # Exercises the per-kind asyncio.Lock directly at the service layer: this avoids the
    # nondeterminism of racing two real HTTP requests (auth + get_owned_portfolio each do
    # their own DB round trip, so which request's synchronous prefix — the lock check —
    # runs first isn't guaranteed). Calling the service twice back-to-back is deterministic:
    # the first call's synchronous prefix (require_provider/_lock/locked-check/lock-enter)
    # runs to completion before it hits its first real await (build_context's DB query), at
    # which point control yields back to us and the second call is guaranteed to observe the
    # lock as held.
    user = User(email="lockuser@test.dev", password_hash="x")
    db_session.add(user)
    await db_session.flush()
    aapl = await make_instrument("AAPL")
    pf = Portfolio(user_id=user.id, name="P", kind="real", base_currency="USD")
    db_session.add(pf)
    await db_session.flush()
    db_session.add(Position(portfolio_id=pf.id, instrument_id=aapl.id,
                            quantity=Decimal("1"), avg_cost=Decimal("1")))
    await db_session.commit()
    await db_session.refresh(pf, ["positions"])

    fake = FakeLLMProvider()
    fake.structured_queue.append(_review(["AAPL"]))
    svc = GuruService(fake, *(_test_services()))

    first = asyncio.create_task(svc.generate_review(db_session, user, pf))
    await asyncio.sleep(0)  # let `first` run its synchronous prefix and enter the lock

    with pytest.raises(GenerationInProgress):
        await svc.generate_review(db_session, user, pf)

    report = await first
    assert report.kind == "review"


async def test_review_other_users_portfolio_404(guru_client, client, db_session, make_instrument):
    pf_id = await _seed_portfolio(guru_client, make_instrument)

    # a second user logs in on the same underlying client
    other = User(email="other-guru@test.dev", password_hash=hash_password("pw123456"))
    db_session.add(other)
    await db_session.commit()
    login = await client.post(
        "/api/auth/login", json={"email": "other-guru@test.dev", "password": "pw123456"}
    )
    assert login.status_code == 204

    resp = await client.post("/api/guru/reviews", json={"portfolio_id": pf_id})
    assert resp.status_code == 404
