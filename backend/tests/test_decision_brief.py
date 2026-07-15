from datetime import UTC, datetime
from decimal import Decimal

import pytest
from pydantic import ValidationError
from sqlalchemy import select, text

from app.models import GuruReport, LlmUsage, User
from app.services.guru.decision_context import DecisionContextTooLarge
from app.services.guru.llm.base import LLMError
from app.services.guru.llm.fake import FakeLLMProvider
from app.services.guru.persona import DISCLAIMER
from app.services.guru.schemas import (
    CandidateIdea,
    DecisionBriefPayload,
    DecisionNewsItem,
    HoldingDecision,
)
from app.services.guru.service import GuruService
from tests.conftest import _test_services


def _holding(symbol="AAPL", action="hold", conviction="med", refs=("signal:1",)):
    return HoldingDecision(
        symbol=symbol,
        action=action,
        conviction=conviction,
        rationale="Grounded rationale",
        evidence_refs=list(refs),
        change_conditions=["Watch the next update"],
    )


def _candidate(symbol="MSFT", refs=("candidate:MSFT:momentum",)):
    return CandidateIdea(
        symbol=symbol,
        name="Microsoft",
        instrument_type="stock",
        market="US",
        action="consider",
        conviction="med",
        why_surfaced="Strong score",
        portfolio_fit="Adds diversification",
        principal_risk="Valuation",
        watch_next=["Earnings"],
        evidence_refs=list(refs),
    )


def _payload(*, holdings=None, candidates=None):
    return DecisionBriefPayload(
        summary="A grounded decision brief.",
        holdings=holdings if holdings is not None else [_holding()],
        material_news=[DecisionNewsItem(
            evidence_ref="news:1",
            symbol="AAPL",
            importance="watch",
            headline="Apple update",
            source="Example Wire",
            url="https://example.com/apple",
            impact="Monitor execution",
        )],
        portfolio_observations=["Concentration remains visible"],
        candidates=candidates if candidates is not None else [_candidate()],
        unavailable_inputs=[],
        data_as_of=datetime(2026, 7, 15, tzinfo=UTC),
        disclaimer=DISCLAIMER,
    )


def _context():
    return {
        "holdings": [{"symbol": "AAPL"}],
        "candidates": [{"symbol": "MSFT"}],
        "evidence": [
            {"id": "signal:1"},
            {"id": "news:1"},
            {"id": "candidate:MSFT:momentum"},
        ],
    }


def _svc(fake):
    return GuruService(
        fake,
        *(_test_services()),
        advice_model="test-advice",
        scan_model="test-scan",
        advice_price=(Decimal("1"), Decimal("5")),
        scan_price=(Decimal("1"), Decimal("5")),
    )


def test_holding_decision_conviction_matches_actionability():
    assert _holding(action="data_incomplete", conviction=None).conviction is None
    with pytest.raises(ValidationError):
        _holding(action="data_incomplete", conviction="low")
    with pytest.raises(ValidationError):
        _holding(action="hold", conviction=None)


def test_decision_contract_rejects_unsupported_literals():
    with pytest.raises(ValidationError):
        _holding(action="buy")
    with pytest.raises(ValidationError):
        CandidateIdea(**{**_candidate().model_dump(), "action": "increase"})


def test_decision_contract_keeps_urls_and_evidence_refs_as_strings():
    payload = _payload()
    assert isinstance(payload.material_news[0].url, str)
    assert all(isinstance(ref, str) for ref in payload.holdings[0].evidence_refs)
    assert all(isinstance(ref, str) for ref in payload.candidates[0].evidence_refs)


@pytest.mark.asyncio(loop_scope="session")
async def test_generate_decision_brief_persists_encrypted_report(db_session, monkeypatch):
    user = User(email="decision@test.dev", password_hash="x")
    db_session.add(user)
    await db_session.commit()
    fake = FakeLLMProvider()
    fake.structured_queue.append(_payload())

    async def build(*args, **kwargs):
        return _context()

    monkeypatch.setattr("app.services.guru.decision_context.build_decision_context", build)
    report = await _svc(fake).generate_decision_brief(db_session, user)

    assert report.kind == "decision"
    assert report.portfolio_id is None
    assert fake.calls[0]["model"] == "test-advice"
    raw = (await db_session.execute(
        text("SELECT payload FROM guru_reports WHERE id = :id"), {"id": report.id}
    )).scalar_one()
    assert raw.startswith("v1:")
    assert "grounded decision brief" not in raw
    usage = (await db_session.execute(
        select(LlmUsage).where(LlmUsage.report_id == report.id)
    )).scalar_one()
    assert usage.mode == "decision"


@pytest.mark.asyncio(loop_scope="session")
async def test_generate_decision_brief_corrects_invalid_refs_and_missing_holdings(
    db_session, monkeypatch
):
    user = User(email="decision-retry@test.dev", password_hash="x")
    db_session.add(user)
    await db_session.commit()
    ctx = _context()
    ctx["holdings"].append({"symbol": "GOOG"})
    fake = FakeLLMProvider()
    fake.structured_queue.extend([
        _payload(holdings=[_holding(symbol="NOPE", refs=("invented:1",))]),
        _payload(holdings=[
            _holding(),
            _holding(symbol="GOOG", action="data_incomplete", conviction=None, refs=()),
        ]),
    ])

    async def build(*args, **kwargs):
        return ctx

    monkeypatch.setattr("app.services.guru.decision_context.build_decision_context", build)
    report = await _svc(fake).generate_decision_brief(db_session, user)

    assert len(fake.calls) == 2
    assert {row["symbol"] for row in report.payload["holdings"]} == {"AAPL", "GOOG"}
    correction = fake.calls[1]["messages"][-1]["content"]
    assert "NOPE" in correction
    assert "invented:1" in correction
    assert "GOOG" in correction


@pytest.mark.asyncio(loop_scope="session")
async def test_generate_decision_brief_invalid_twice_persists_nothing(db_session, monkeypatch):
    user = User(email="decision-bad@test.dev", password_hash="x")
    db_session.add(user)
    await db_session.commit()
    fake = FakeLLMProvider()
    fake.structured_queue.extend([
        _payload(holdings=[_holding(symbol="NOPE")]),
        _payload(holdings=[_holding(symbol="STILL-NOPE")]),
    ])

    async def build(*args, **kwargs):
        return _context()

    monkeypatch.setattr("app.services.guru.decision_context.build_decision_context", build)
    with pytest.raises(LLMError):
        await _svc(fake).generate_decision_brief(db_session, user)

    assert len(fake.calls) == 2
    assert (await db_session.execute(select(GuruReport))).scalars().all() == []
    assert (await db_session.execute(select(LlmUsage))).scalars().all() == []


@pytest.mark.asyncio(loop_scope="session")
async def test_generate_decision_brief_maps_oversize_context_to_llm_error(db_session, monkeypatch):
    user = User(email="decision-large@test.dev", password_hash="x")
    db_session.add(user)
    await db_session.commit()
    fake = FakeLLMProvider()

    async def build(*args, **kwargs):
        raise DecisionContextTooLarge("too large")

    monkeypatch.setattr("app.services.guru.decision_context.build_decision_context", build)
    with pytest.raises(LLMError, match="decision context"):
        await _svc(fake).generate_decision_brief(db_session, user)
    assert fake.calls == []
