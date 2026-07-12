import types as _types
from decimal import Decimal

import pytest
from sqlalchemy import select, text

from app.models import (
    GroupAssignment,
    GuruReport,
    HoldingGroup,
    Instrument,
    LlmUsage,
    Portfolio,
    Position,
    User,
)
from app.services.guru.llm.base import LLMError
from app.services.guru.llm.fake import FakeLLMProvider
from app.services.guru.persona import DISCLAIMER
from app.services.guru.schemas import GroupObservation, Rotation, RotationAdvicePayload
from app.services.guru.service import GuruService
from tests.conftest import _test_services

pytestmark = pytest.mark.asyncio(loop_scope="session")


def _rotation(groups, rotations=(), disclaimer=DISCLAIMER):
    return RotationAdvicePayload(
        market_view="Balanced markets; no major dislocations right now.",
        groups=[
            GroupObservation(name=n, weight_pct=w, observation="steady", signal="hold")
            for n, w in groups
        ],
        rotations=[
            Rotation(from_group=f, to_group=t, rationale="momentum shift", conviction="med")
            for f, t in rotations
        ],
        caveats=["thin history"],
        disclaimer=disclaimer,
    )


def _stub_valuation(monkeypatch, prices: dict) -> None:
    """Mirrors tests/test_rotation_context.py::_stub_valuation exactly — patches
    the name imported INTO the exposure module so build_rotation_context's
    weighting is deterministic without live quotes."""
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


def _svc(fake: FakeLLMProvider) -> GuruService:
    return GuruService(fake, *(_test_services()),
                       advice_model="test-advice", scan_model="test-scan",
                       advice_price=(Decimal("1"), Decimal("5")),
                       scan_price=(Decimal("1"), Decimal("5")))


async def _seed_two_groups(db_session, monkeypatch) -> User:
    """One GBP portfolio with two held instruments, each in its own group, both
    priced (no FX conversion needed since the portfolio's base_currency is GBP,
    the same currency exposure aggregates into) — gives build_rotation_context
    two non-empty groups to ground the rotation advice on."""
    user = User(email="rotation@test.dev", password_hash="x")
    db_session.add(user)
    await db_session.flush()
    pf = Portfolio(user_id=user.id, name="UK", kind="real", base_currency="GBP")
    db_session.add(pf)
    await db_session.flush()

    aapl = Instrument(symbol="AAPL", name="Apple", exchange="NMS", market="US", currency="USD")
    xom = Instrument(symbol="XOM", name="Exxon", exchange="NYQ", market="US", currency="USD")
    db_session.add_all([aapl, xom])
    await db_session.flush()
    db_session.add_all([
        Position(portfolio_id=pf.id, instrument_id=aapl.id,
                quantity=Decimal("1"), avg_cost=Decimal("1")),
        Position(portfolio_id=pf.id, instrument_id=xom.id,
                quantity=Decimal("1"), avg_cost=Decimal("1")),
    ])
    await db_session.flush()

    big_tech = HoldingGroup(user_id=user.id, name="Big Tech")
    energy = HoldingGroup(user_id=user.id, name="Energy")
    db_session.add_all([big_tech, energy])
    await db_session.flush()
    db_session.add_all([
        GroupAssignment(user_id=user.id, instrument_id=aapl.id, group_id=big_tech.id),
        GroupAssignment(user_id=user.id, instrument_id=xom.id, group_id=energy.id),
    ])
    await db_session.commit()

    _stub_valuation(monkeypatch, {"AAPL": Decimal("70"), "XOM": Decimal("30")})
    return user


# --- happy path --------------------------------------------------------------

async def test_generate_rotation_persists_encrypted_report(db_session, monkeypatch):
    user = await _seed_two_groups(db_session, monkeypatch)
    fake = FakeLLMProvider()
    fake.structured_queue.append(_rotation(
        [("Big Tech", "70.00"), ("Energy", "30.00")],
        rotations=[("Energy", "Big Tech")],
    ))

    report = await _svc(fake).generate_rotation(db_session, user)

    assert report.kind == "rotation"
    assert report.portfolio_id is None
    assert report.payload["market_view"]
    assert {g["name"] for g in report.payload["groups"]} == {"Big Tech", "Energy"}
    assert len(fake.calls) == 1
    assert fake.calls[0]["max_tokens"] == 4096

    # raw ciphertext in DB, plaintext absent (mirror test_encrypted_columns.py's
    # test_position_amounts_encrypted_at_rest ciphertext-at-rest assertion)
    row = (await db_session.execute(
        text("SELECT payload FROM guru_reports WHERE id = :id"), {"id": report.id}
    )).one()
    assert row.payload.startswith("v1:")
    assert report.payload["market_view"] not in row.payload
    assert "Big Tech" not in row.payload

    rows = (await db_session.execute(
        select(LlmUsage).where(LlmUsage.report_id == report.id)
    )).scalars().all()
    assert len(rows) == 1
    assert rows[0].mode == "rotation"
    assert rows[0].report_id == report.id


# --- group-name validity retry ------------------------------------------------

async def test_generate_rotation_reprompts_on_unknown_group_then_succeeds(
        db_session, monkeypatch):
    user = await _seed_two_groups(db_session, monkeypatch)
    fake = FakeLLMProvider()
    fake.structured_queue += [
        _rotation([("Big Tech", "70.00"), ("Energy", "30.00")],
                 rotations=[("Energy", "Nonexistent")]),
        _rotation([("Big Tech", "70.00"), ("Energy", "30.00")],
                 rotations=[("Energy", "Big Tech")]),
    ]

    report = await _svc(fake).generate_rotation(db_session, user)

    assert len(fake.calls) == 2  # corrective retry happened
    assert report.payload["rotations"] == [
        {"from_group": "Energy", "to_group": "Big Tech",
         "rationale": "momentum shift", "conviction": "med"}
    ]

    rows = (await db_session.execute(
        select(LlmUsage).where(LlmUsage.report_id == report.id)
    )).scalars().all()
    assert len(rows) == 1
    assert rows[0].input_tokens == 200  # 100 from first call + 100 from second
    assert rows[0].output_tokens == 100  # 50 from first call + 50 from second


async def test_generate_rotation_invalid_group_twice_raises_llm_error(db_session, monkeypatch):
    user = await _seed_two_groups(db_session, monkeypatch)
    fake = FakeLLMProvider()
    fake.structured_queue += [
        _rotation([("Big Tech", "70.00"), ("Energy", "30.00")],
                 rotations=[("Energy", "Nonexistent")]),
        _rotation([("Big Tech", "70.00"), ("Energy", "30.00")],
                 rotations=[("Energy", "StillNope")]),
    ]

    with pytest.raises(LLMError):
        await _svc(fake).generate_rotation(db_session, user)

    assert len(fake.calls) == 2
    # nothing persisted
    reports = (await db_session.execute(select(GuruReport))).scalars().all()
    assert reports == []
    usage_rows = (await db_session.execute(select(LlmUsage))).scalars().all()
    assert usage_rows == []
