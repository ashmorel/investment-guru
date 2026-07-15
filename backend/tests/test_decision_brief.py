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
    CandidateDraftItem,
    CandidateIdea,
    DecisionBriefDraft,
    DecisionBriefPayload,
    DecisionNewsDraftItem,
    DecisionNewsItem,
    HoldingDecision,
)
from app.services.guru.service import (
    GenerationInProgress,
    GuruService,
    _decision_invalid_refs,
    _enrich_decision_draft,
)
from tests.conftest import _test_services


def _holding(symbol="AAPL", action="hold", conviction="med", refs=("signal:1",),
             rationale="Grounded rationale"):
    return HoldingDecision(
        symbol=symbol,
        action=action,
        conviction=conviction,
        rationale=rationale,
        evidence_refs=list(refs),
        change_conditions=["Watch the next update"],
    )


def _candidate(symbol="MSFT", refs=("candidate:MSFT:momentum",)):
    """A fully-populated CandidateIdea — for DecisionBriefPayload-level (i.e.
    persisted, frontend-facing) schema contract tests only."""
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
    """A fully-enriched persisted payload — for DecisionBriefPayload schema
    contract tests only (URL/bounds validation)."""
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


def _draft_news(evidence_ref="news:1", importance="watch"):
    return DecisionNewsDraftItem(
        evidence_ref=evidence_ref,
        importance=importance,
        impact="Monitor execution",
    )


def _draft_candidate(symbol="MSFT", refs=("candidate:MSFT:momentum",)):
    return CandidateDraftItem(
        symbol=symbol,
        action="consider",
        conviction="med",
        why_surfaced="Strong score",
        portfolio_fit="Adds diversification",
        principal_risk="Valuation",
        watch_next=["Earnings"],
        evidence_refs=list(refs),
    )


def _draft(*, holdings=None, candidates=None, news=None):
    """What the model is actually asked to produce: stable identifiers only —
    no headline/source/url, no candidate name/market/instrument_type, no
    data_as_of. The backend joins those in from the grounding context."""
    return DecisionBriefDraft(
        summary="A grounded decision brief.",
        holdings=holdings if holdings is not None else [_holding()],
        material_news=news if news is not None else [_draft_news()],
        portfolio_observations=["Concentration remains visible"],
        candidates=candidates if candidates is not None else [_draft_candidate()],
        unavailable_inputs=[],
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


def test_decision_news_draft_excludes_verbatim_facts():
    """The model literally cannot fabricate a headline/source/url: the draft
    schema it targets doesn't have those fields at all."""
    assert set(DecisionNewsDraftItem.model_fields) == {
        "evidence_ref", "importance", "impact",
    }


def test_decision_candidate_draft_excludes_verbatim_facts():
    """The model literally cannot fabricate name/market/instrument_type: the
    draft schema it targets doesn't have those fields at all."""
    assert set(CandidateDraftItem.model_fields) == {
        "symbol", "action", "conviction", "why_surfaced", "portfolio_fit",
        "principal_risk", "watch_next", "evidence_refs",
    }


def test_decision_draft_excludes_data_as_of():
    """The model is never asked for a timestamp — the app sets it server-side
    from the grounding context, so it can't drift or be malformed."""
    assert "data_as_of" not in DecisionBriefDraft.model_fields


def test_decision_draft_enforces_candidate_and_evidence_bounds():
    with pytest.raises(ValidationError):
        _draft(candidates=[_draft_candidate(symbol=f"C{i}") for i in range(6)])
    with pytest.raises(ValidationError):
        _draft(candidates=[_draft_candidate(), _draft_candidate()])
    with pytest.raises(ValidationError):
        _draft(candidates=[_draft_candidate(refs=())])
    with pytest.raises(ValidationError):
        _draft(news=[_draft_news(), _draft_news()])


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
    with pytest.raises(ValidationError):
        CandidateDraftItem(**{**_draft_candidate().model_dump(), "action": "increase"})


def test_decision_contract_keeps_urls_and_evidence_refs_as_strings():
    payload = _payload()
    assert isinstance(payload.material_news[0].url, str)
    assert all(isinstance(ref, str) for ref in payload.holdings[0].evidence_refs)
    assert all(isinstance(ref, str) for ref in payload.candidates[0].evidence_refs)


@pytest.mark.parametrize(
    ("draft", "invalid_symbol", "invalid_ref"),
    [
        (_draft(candidates=[_draft_candidate(symbol="AAPL")]), "AAPL", None),
        (_draft(holdings=[_holding(symbol="MSFT", refs=())]), "MSFT", None),
        (_draft(news=[_draft_news(evidence_ref="news:99")]), None, "news:99"),
        (_draft(holdings=[_holding(refs=("candidate:MSFT:momentum",))]),
         None, "candidate:MSFT:momentum"),
        (_draft(candidates=[_draft_candidate(refs=("signal:1",))]), None, "signal:1"),
    ],
)
def test_decision_validation_rejects_cross_category_symbols_and_refs(
    draft, invalid_symbol, invalid_ref
):
    invalid_symbols, invalid_refs = _decision_invalid_refs(draft, _context())
    if invalid_symbol:
        assert invalid_symbol in invalid_symbols
    if invalid_ref:
        assert invalid_ref in invalid_refs


def test_decision_validation_rejects_cross_symbol_signal_ref():
    ctx = _context()
    ctx["holdings"].append({"symbol": "GOOG"})
    ctx["evidence"].extend([
        {"ref": "signal:2", "kind": "signal", "symbol": "GOOG"},
        {"ref": "news:2", "kind": "news", "symbol": "GOOG", "headline": "Google update"},
    ])
    draft = _draft(holdings=[_holding(refs=("signal:2",))])  # AAPL citing GOOG's signal
    invalid_symbols, invalid_refs = _decision_invalid_refs(draft, ctx)
    assert invalid_symbols == set()
    assert invalid_refs == {"signal:2"}


def test_decision_validation_rejects_news_ref_missing_from_material_news():
    ctx = _context()
    ctx["holdings"].append({"symbol": "GOOG"})
    ctx["evidence"].append({"ref": "news:2", "kind": "news", "symbol": "GOOG"})
    draft = _draft(news=[_draft_news(evidence_ref="news:2")])
    invalid_symbols, invalid_refs = _decision_invalid_refs(draft, ctx)
    assert invalid_symbols == set()
    assert invalid_refs == {"news:2"}


def test_decision_validation_requires_canonical_ref_key():
    ctx = _context()
    ctx["evidence"][0] = {"id": "signal:1", "kind": "signal", "symbol": "AAPL"}
    assert _decision_invalid_refs(_draft(), ctx)[1] == {"signal:1"}


def test_enrich_decision_draft_joins_context_and_sets_data_as_of():
    ctx = _context()
    payload = _enrich_decision_draft(_draft(), ctx)

    assert isinstance(payload, DecisionBriefPayload)
    news = payload.material_news[0]
    assert news.symbol == "AAPL"
    assert news.headline == "Apple update"
    assert news.source == "Example Wire"
    assert news.url == "https://example.com/apple"
    candidate = payload.candidates[0]
    assert candidate.name == "Microsoft"
    assert candidate.market == "US"
    assert candidate.instrument_type == "stock"
    assert payload.data_as_of == datetime(2026, 7, 15, tzinfo=UTC)


def test_enrich_decision_draft_backfills_omitted_holding():
    ctx = _context()
    ctx["holdings"].append({"symbol": "GOOG"})
    payload = _enrich_decision_draft(_draft(), ctx)  # draft only covers AAPL

    holdings_by_symbol = {h.symbol: h for h in payload.holdings}
    assert set(holdings_by_symbol) == {"AAPL", "GOOG"}
    backfilled = holdings_by_symbol["GOOG"]
    assert backfilled.action == "data_incomplete"
    assert backfilled.conviction is None
    assert backfilled.evidence_refs == []


def test_enrich_decision_draft_dedupes_duplicate_holdings_keeping_first():
    ctx = _context()
    draft = _draft(holdings=[
        _holding(rationale="First mention"),
        _holding(rationale="Second mention"),
    ])
    payload = _enrich_decision_draft(draft, ctx)

    assert len(payload.holdings) == 1
    assert payload.holdings[0].rationale == "First mention"


@pytest.mark.asyncio(loop_scope="session")
async def test_generate_decision_brief_persists_encrypted_report(db_session, monkeypatch):
    user = User(email="decision@test.dev", password_hash="x")
    db_session.add(user)
    await db_session.commit()
    fake = FakeLLMProvider()
    fake.structured_queue.append(_draft())

    async def build(*args, **kwargs):
        return _context()

    monkeypatch.setattr("app.services.guru.decision_context.build_decision_context", build)
    report = await _svc(fake).generate_decision_brief(db_session, user)

    assert report.kind == "decision"
    assert report.portfolio_id is None
    assert len(fake.calls) == 1
    assert fake.calls[0]["model"] == "test-advice"
    assert fake.calls[0]["max_tokens"] == 8192

    # The persisted payload validates as the unchanged, frontend-facing schema,
    # with facts joined in from the context — not reproduced by the model.
    payload = DecisionBriefPayload(**report.payload)
    assert payload.material_news[0].headline == "Apple update"
    assert payload.material_news[0].source == "Example Wire"
    assert payload.material_news[0].url == "https://example.com/apple"
    assert payload.candidates[0].name == "Microsoft"
    assert payload.candidates[0].market == "US"
    assert payload.candidates[0].instrument_type == "stock"
    assert payload.data_as_of == datetime(2026, 7, 15, tzinfo=UTC)

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
async def test_generate_decision_brief_backfills_omitted_holding_without_retry(
    db_session, monkeypatch
):
    user = User(email="decision-omit@test.dev", password_hash="x")
    db_session.add(user)
    await db_session.commit()
    ctx = _context()
    ctx["holdings"].append({"symbol": "GOOG"})
    fake = FakeLLMProvider()
    fake.structured_queue.append(_draft())  # only covers AAPL, omits GOOG entirely

    async def build(*args, **kwargs):
        return ctx

    monkeypatch.setattr("app.services.guru.decision_context.build_decision_context", build)
    report = await _svc(fake).generate_decision_brief(db_session, user)

    # Coverage is guaranteed by construction — omitting a holding never retries
    # and never 502s.
    assert len(fake.calls) == 1
    holdings_by_symbol = {row["symbol"]: row for row in report.payload["holdings"]}
    assert set(holdings_by_symbol) == {"AAPL", "GOOG"}
    assert holdings_by_symbol["GOOG"]["action"] == "data_incomplete"
    assert holdings_by_symbol["GOOG"]["conviction"] is None
    assert holdings_by_symbol["GOOG"]["evidence_refs"] == []


@pytest.mark.asyncio(loop_scope="session")
async def test_generate_decision_brief_dedupes_duplicate_holdings_without_retry(
    db_session, monkeypatch
):
    user = User(email="decision-duplicate@test.dev", password_hash="x")
    db_session.add(user)
    await db_session.commit()
    fake = FakeLLMProvider()
    fake.structured_queue.append(_draft(holdings=[
        _holding(rationale="First mention"),
        _holding(rationale="Second mention"),
    ]))

    async def build(*args, **kwargs):
        return _context()

    monkeypatch.setattr("app.services.guru.decision_context.build_decision_context", build)
    report = await _svc(fake).generate_decision_brief(db_session, user)

    assert len(fake.calls) == 1
    holdings = report.payload["holdings"]
    assert len(holdings) == 1
    assert holdings[0]["rationale"] == "First mention"


@pytest.mark.asyncio(loop_scope="session")
async def test_generate_decision_brief_drops_invalid_symbol_and_ref(db_session, monkeypatch):
    user = User(email="decision-drop@test.dev", password_hash="x")
    db_session.add(user)
    await db_session.commit()
    ctx = _context()
    fake = FakeLLMProvider()
    # A hallucinated (not-held) symbol citing an invented evidence ref is DROPPED
    # — a single call, no fragile correction retry; the real held symbol is
    # backfilled as data_incomplete so coverage still holds.
    fake.structured_queue.append(_draft(holdings=[_holding(symbol="NOPE", refs=("invented:1",))]))

    async def build(*args, **kwargs):
        return ctx

    monkeypatch.setattr("app.services.guru.decision_context.build_decision_context", build)
    report = await _svc(fake).generate_decision_brief(db_session, user)

    assert len(fake.calls) == 1
    assert {row["symbol"] for row in report.payload["holdings"]} == {"AAPL"}
    aapl = report.payload["holdings"][0]
    assert aapl["action"] == "data_incomplete"
    assert aapl["evidence_refs"] == []
    assert (await db_session.execute(
        select(LlmUsage).where(LlmUsage.report_id == report.id))).scalar_one() is not None


@pytest.mark.asyncio(loop_scope="session")
async def test_decision_brief_drops_invalid_candidate(db_session, monkeypatch):
    user = User(email="decision-badcand@test.dev", password_hash="x")
    db_session.add(user)
    await db_session.commit()
    fake = FakeLLMProvider()
    # An invented candidate symbol not in the context is DROPPED; the brief still
    # generates and persists (never 502 on unverifiable grounding).
    fake.structured_queue.append(_draft(candidates=[_draft_candidate(symbol="INVENTED")]))

    async def build(*args, **kwargs):
        return _context()

    monkeypatch.setattr("app.services.guru.decision_context.build_decision_context", build)
    report = await _svc(fake).generate_decision_brief(db_session, user)

    assert len(fake.calls) == 1
    assert report.payload["candidates"] == []
    persisted = (await db_session.execute(select(GuruReport))).scalars().all()
    assert len(persisted) == 1 and persisted[0].id == report.id


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
