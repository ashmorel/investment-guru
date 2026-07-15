from datetime import UTC, datetime
from decimal import Decimal

import pytest
from pydantic import ValidationError
from sqlalchemy import select, text

from app.core.security import hash_password
from app.models import GuruReport, LlmUsage, User
from app.services.guru.budget import BudgetExhausted
from app.services.guru.decision_context import DecisionContextTooLarge
from app.services.guru.llm.base import LLMError, LLMNotConfigured
from app.services.guru.llm.fake import FakeLLMProvider
from app.services.guru.persona import DISCLAIMER
from app.services.guru.schemas import (
    CandidateIdea,
    DecisionBriefPayload,
    DecisionNewsItem,
    HoldingDecision,
)
from app.services.guru.service import (
    GenerationInProgress,
    GuruService,
    _decision_invalid_refs,
)
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
        "material_news": [{
            "evidence_ref": "news:1", "symbol": "AAPL", "headline": "Apple update",
            "source": "Example Wire", "url": "https://example.com/apple",
        }],
        "candidates": [{
            "symbol": "MSFT", "name": "Microsoft", "market": "US",
            "instrument_type": "stock",
            "evidence_refs": ["candidate:MSFT:momentum"]
        }],
        "evidence": [
            {"ref": "signal:1", "kind": "signal", "symbol": "AAPL"},
            {"ref": "news:1", "kind": "news", "symbol": "AAPL",
             "headline": "Apple update"},
            {"ref": "candidate:MSFT:momentum", "kind": "candidate", "symbol": "MSFT"},
        ],
        "data_as_of": "2026-07-15T00:00:00+00:00",
    }


def test_decision_contract_rejects_non_http_news_urls():
    for url in ("javascript:alert(1)", "/relative", "ftp://example.com/story"):
        with pytest.raises(ValidationError):
            DecisionNewsItem(**{**_payload().material_news[0].model_dump(), "url": url})


def test_decision_contract_enforces_candidate_and_evidence_bounds():
    with pytest.raises(ValidationError):
        _payload(candidates=[_candidate(symbol=f"C{i}") for i in range(6)])
    with pytest.raises(ValidationError):
        _payload(candidates=[_candidate(), _candidate()])
    with pytest.raises(ValidationError):
        _payload(candidates=[_candidate(refs=())])
    with pytest.raises(ValidationError):
        DecisionBriefPayload(**{
            **_payload().model_dump(),
            "material_news": [_payload().material_news[0].model_dump()] * 2,
        })


@pytest.mark.parametrize(
    "changes",
    [
        {"source": "Invented Wire"},
        {"url": "https://evil.example/invented"},
        {"headline": "Altered headline"},
    ],
)
def test_decision_validation_binds_news_to_canonical_context(changes):
    news = DecisionNewsItem(**{**_payload().material_news[0].model_dump(), **changes})
    payload = DecisionBriefPayload(**{
        **_payload().model_dump(), "material_news": [news.model_dump()]
    })
    assert _decision_invalid_refs(payload, _context())[1] == {"news:1"}


@pytest.mark.parametrize(
    "changes",
    [
        {"name": "Invented Corp"},
        {"market": "UK"},
        {"instrument_type": "etf"},
    ],
)
def test_decision_validation_binds_candidate_metadata(changes):
    candidate = CandidateIdea(**{**_candidate().model_dump(), **changes})
    payload = _payload(candidates=[candidate])
    assert "MSFT" in _decision_invalid_refs(payload, _context())[0]


def test_decision_validation_binds_data_as_of():
    payload = DecisionBriefPayload(**{
        **_payload().model_dump(), "data_as_of": datetime(2026, 7, 16, tzinfo=UTC)
    })
    assert "data_as_of" in _decision_invalid_refs(payload, _context())[1]


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


@pytest.mark.parametrize(
    ("payload", "invalid_symbol", "invalid_ref"),
    [
        (_payload(candidates=[_candidate(symbol="AAPL")]), "AAPL", None),
        (_payload(holdings=[_holding(symbol="MSFT", refs=())]), "MSFT", None),
        (DecisionBriefPayload(**{
            **_payload().model_dump(),
            "material_news": [{
                **_payload().material_news[0].model_dump(), "symbol": "MSFT"
            }],
        }), "MSFT", "news:1"),
        (_payload(holdings=[_holding(refs=("candidate:MSFT:momentum",))]),
         None, "candidate:MSFT:momentum"),
        (_payload(candidates=[_candidate(refs=("signal:1",))]), None, "signal:1"),
    ],
)
def test_decision_validation_rejects_cross_category_symbols_and_refs(
    payload, invalid_symbol, invalid_ref
):
    invalid_symbols, invalid_refs = _decision_invalid_refs(payload, _context())
    if invalid_symbol:
        assert invalid_symbol in invalid_symbols
    if invalid_ref:
        assert invalid_ref in invalid_refs


def test_decision_validation_rejects_cross_symbol_and_mismatched_news_refs():
    ctx = _context()
    ctx["holdings"].append({"symbol": "GOOG"})
    ctx["evidence"].extend([
        {"ref": "signal:2", "kind": "signal", "symbol": "GOOG"},
        {"ref": "news:2", "kind": "news", "symbol": "GOOG", "headline": "Google update"},
    ])
    payload = _payload(holdings=[_holding(refs=("signal:2",))])
    invalid_symbols, invalid_refs = _decision_invalid_refs(payload, ctx)
    assert invalid_symbols == set()
    assert invalid_refs == {"signal:2"}

    wrong_news = DecisionBriefPayload(**{
        **_payload().model_dump(),
        "material_news": [{
            **_payload().material_news[0].model_dump(), "evidence_ref": "news:2"
        }],
    })
    assert _decision_invalid_refs(wrong_news, ctx)[1] == {"news:2"}


def test_decision_validation_requires_canonical_ref_key():
    ctx = _context()
    ctx["evidence"][0] = {"id": "signal:1", "kind": "signal", "symbol": "AAPL"}
    assert _decision_invalid_refs(_payload(), ctx)[1] == {"signal:1"}


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
    invented_news = DecisionNewsItem(**{
        **_payload().material_news[0].model_dump(),
        "source": "Invented Wire",
        "url": "https://invented.example/story",
    })
    fake.structured_queue.extend([
        DecisionBriefPayload(**{
            **_payload(holdings=[_holding(symbol="NOPE", refs=("invented:1",))]).model_dump(),
            "material_news": [invented_news.model_dump()],
            "data_as_of": datetime(2026, 7, 16, tzinfo=UTC),
        }),
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
    assert "news:1" in correction
    assert "data_as_of" in correction
    assert "GOOG" in correction
    usage = (await db_session.execute(
        select(LlmUsage).where(LlmUsage.report_id == report.id)
    )).scalar_one()
    assert usage.input_tokens == 200
    assert usage.output_tokens == 100


@pytest.mark.asyncio(loop_scope="session")
async def test_generate_decision_brief_rejects_duplicate_holdings_twice(
    db_session, monkeypatch
):
    user = User(email="decision-duplicate@test.dev", password_hash="x")
    db_session.add(user)
    await db_session.commit()
    fake = FakeLLMProvider()
    duplicate = _payload(holdings=[_holding(), _holding()])
    fake.structured_queue.extend([duplicate, duplicate])

    async def build(*args, **kwargs):
        return _context()

    monkeypatch.setattr("app.services.guru.decision_context.build_decision_context", build)
    with pytest.raises(LLMError):
        await _svc(fake).generate_decision_brief(db_session, user)
    assert len(fake.calls) == 2
    assert (await db_session.execute(select(GuruReport))).scalars().all() == []
    assert (await db_session.execute(select(LlmUsage))).scalars().all() == []


@pytest.mark.asyncio(loop_scope="session")
async def test_generate_decision_brief_invalid_twice_persists_nothing(db_session, monkeypatch):
    user = User(email="decision-bad@test.dev", password_hash="x")
    db_session.add(user)
    await db_session.commit()
    fake = FakeLLMProvider()
    altered_candidate = CandidateIdea(**{
        **_candidate().model_dump(), "name": "Invented Corporation"
    })
    invalid = _payload(candidates=[altered_candidate])
    fake.structured_queue.extend([invalid, invalid])

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


def _report(user_id: int, *, summary: str, created_at: datetime | None = None) -> GuruReport:
    return GuruReport(
        user_id=user_id,
        kind="decision",
        portfolio_id=None,
        payload={"summary": summary},
        model="test-advice",
        created_at=created_at or datetime.now(UTC).replace(tzinfo=None),
    )


@pytest.mark.asyncio(loop_scope="session")
async def test_decision_brief_latest_endpoint_returns_null_when_empty(auth_client):
    response = await auth_client.get("/api/guru/decision-brief/latest")

    assert response.status_code == 200
    assert response.json() is None


@pytest.mark.asyncio(loop_scope="session")
async def test_decision_brief_post_endpoint_returns_created_report(
    guru_client, monkeypatch
):
    async def generate(db, user):
        report = _report(user.id, summary="Fresh brief")
        db.add(report)
        await db.commit()
        await db.refresh(report)
        return report

    monkeypatch.setattr(guru_client.guru_service, "generate_decision_brief", generate)

    response = await guru_client.post("/api/guru/decision-brief")

    assert response.status_code == 201
    assert response.json()["kind"] == "decision"
    assert response.json()["payload"] == {"summary": "Fresh brief"}


@pytest.mark.asyncio(loop_scope="session")
async def test_decision_brief_latest_endpoint_returns_newest_current_user_report(
    auth_client, db_session
):
    user = (await db_session.execute(
        select(User).where(User.email == "lee@test.dev")
    )).scalar_one()
    created_at = datetime(2026, 7, 15, tzinfo=UTC).replace(tzinfo=None)
    older = _report(user.id, summary="Older", created_at=created_at)
    newer = _report(user.id, summary="Newer", created_at=created_at)
    db_session.add_all([older, newer])
    await db_session.commit()

    response = await auth_client.get("/api/guru/decision-brief/latest")

    assert response.status_code == 200
    assert response.json()["id"] == newer.id
    assert response.json()["payload"] == {"summary": "Newer"}


@pytest.mark.asyncio(loop_scope="session")
async def test_decision_brief_latest_endpoint_hides_other_users_report(
    auth_client, db_session
):
    other = User(email="decision-other@test.dev", password_hash=hash_password("pw123456"))
    db_session.add(other)
    await db_session.flush()
    db_session.add(_report(other.id, summary="Private brief"))
    await db_session.commit()

    response = await auth_client.get("/api/guru/decision-brief/latest")

    assert response.status_code == 200
    assert response.json() is None


@pytest.mark.parametrize(
    ("error", "status_code", "detail"),
    [
        (BudgetExhausted(), 429, "budget_exhausted"),
        (GenerationInProgress(), 409, "generation_in_progress"),
        (LLMError(), 502, "llm_error"),
        (LLMNotConfigured(), 503, "llm_unconfigured"),
    ],
)
@pytest.mark.asyncio(loop_scope="session")
async def test_decision_brief_post_endpoint_maps_guru_errors(
    guru_client, monkeypatch, error, status_code, detail
):
    async def generate(db, user):
        raise error

    monkeypatch.setattr(guru_client.guru_service, "generate_decision_brief", generate)

    response = await guru_client.post("/api/guru/decision-brief")

    assert response.status_code == status_code
    assert response.json()["detail"] == detail
