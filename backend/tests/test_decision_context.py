import json
from datetime import UTC, datetime
from decimal import Decimal

import pytest
from sqlalchemy import func, select

from app.models import Instrument, InvestorProfile, NewsItem, Portfolio, Position, Signal, User
from app.services.guru import decision_context
from app.services.guru.decision_context import DecisionContextTooLarge, build_decision_context
from app.services.market_data.base import Bar, Quote
from app.services.market_data.quotes import QuoteService
from app.services.recommendations.candidates import CandidateSeed
from app.services.valuation import FxService

pytestmark = pytest.mark.asyncio(loop_scope="session")


class Provider:
    def __init__(self, quotes=None, *, fail_quotes=False, fail_history=False):
        self.quotes = quotes or {}
        self.fail_quotes = fail_quotes
        self.fail_history = fail_history

    async def get_quotes(self, symbols):
        if self.fail_quotes:
            raise RuntimeError("quotes down")
        return {symbol: self.quotes[symbol] for symbol in symbols if symbol in self.quotes}

    async def get_fx_rate(self, base, quote):
        return Decimal("0.8")

    async def get_history(self, symbol, days=400):
        if self.fail_history:
            raise RuntimeError("history down")
        return [Bar(datetime.now(UTC).date(), Decimal("10"), Decimal("11"),
                    Decimal("9"), Decimal("10"), 100)]

    async def get_earnings_date(self, symbol):
        return None


def quote(symbol, price="10"):
    return Quote(symbol, Decimal(price), "GBP", Decimal(price), datetime.now(UTC))


async def make_user(db, email):
    user = User(email=email, password_hash="x")
    db.add(user)
    await db.flush()
    return user


async def test_aggregates_real_portfolios_and_is_user_scoped(
    db_session, make_instrument, monkeypatch
):
    user = await make_user(db_session, "decision@test.dev")
    other = await make_user(db_session, "other-decision@test.dev")
    held = await make_instrument("HELD", sector="Technology")
    watch = await make_instrument("WATCH")
    portfolios = [
        Portfolio(user_id=user.id, name="One", kind="real", base_currency="GBP"),
        Portfolio(user_id=user.id, name="Two", kind="real", base_currency="GBP"),
        Portfolio(user_id=user.id, name="Watch", kind="watchlist", base_currency="GBP"),
        Portfolio(user_id=other.id, name="Foreign", kind="real", base_currency="GBP"),
    ]
    db_session.add_all(portfolios)
    await db_session.flush()
    db_session.add_all([
        Position(portfolio_id=portfolios[0].id, instrument_id=held.id, quantity=Decimal("2")),
        Position(portfolio_id=portfolios[1].id, instrument_id=held.id, quantity=Decimal("3")),
        Position(portfolio_id=portfolios[2].id, instrument_id=watch.id, quantity=Decimal("99")),
        Position(portfolio_id=portfolios[3].id, instrument_id=held.id, quantity=Decimal("100")),
    ])
    await db_session.commit()
    monkeypatch.setattr(decision_context, "assemble_candidates", _no_candidates)

    provider = Provider({"HELD": quote("HELD")})
    context = await build_decision_context(
        db_session, user, QuoteService(provider), FxService(provider)
    )

    assert len(context["holdings"]) == 1
    assert context["holdings"][0]["symbol"] == "HELD"
    assert context["holdings"][0]["quantity"] == "5.000000"
    assert context["holdings"][0]["source_portfolio_ids"] == [portfolios[0].id, portfolios[1].id]
    assert context["holdings"][0]["market_value"] == "50.00"
    assert "WATCH" not in {row["symbol"] for row in context["holdings"]}
    assert context["availability"]["signals"] is True
    assert context["availability"]["news"] is True
    assert context["availability"]["candidates"] is True
    json.dumps(context)


async def test_stable_evidence_urls_and_independent_valuation_degradation(
    db_session, make_instrument, monkeypatch
):
    user = await make_user(db_session, "evidence@test.dev")
    foreign = await make_user(db_session, "foreign-evidence@test.dev")
    held = await make_instrument("FAIL", sector="Industrials")
    pf = Portfolio(user_id=user.id, name="Mine", kind="real", base_currency="GBP")
    foreign_pf = Portfolio(user_id=foreign.id, name="Theirs", kind="real", base_currency="GBP")
    db_session.add_all([pf, foreign_pf])
    await db_session.flush()
    db_session.add_all([
        Position(portfolio_id=pf.id, instrument_id=held.id, quantity=Decimal("4")),
        Position(portfolio_id=foreign_pf.id, instrument_id=held.id, quantity=Decimal("4")),
    ])
    now = datetime.now(UTC).replace(tzinfo=None)
    signal = Signal(portfolio_id=pf.id, instrument_id=held.id, kind="price_move_day",
                    severity="high", title="Moved", detail="Material move", data={},
                    computed_at=now)
    foreign_signal = Signal(portfolio_id=foreign_pf.id, instrument_id=held.id,
                            kind="price_move_day", severity="high", title="Foreign",
                            detail="Do not leak", data={}, computed_at=now)
    news = NewsItem(instrument_id=held.id, title="Factory update", source="Wire",
                    url="https://example.test/story", published_at=now, fetched_at=now)
    db_session.add_all([signal, foreign_signal, news, InvestorProfile(
        user_id=user.id, risk_appetite="balanced", horizon="long",
        sector_interests=[], free_text="",
    )])
    await db_session.commit()
    monkeypatch.setattr(decision_context, "assemble_candidates", _no_candidates)

    provider = Provider()
    context = await build_decision_context(
        db_session, user, QuoteService(provider), FxService(provider)
    )

    assert context["holdings"][0]["availability"]["valuation"] is False
    assert context["holdings"][0]["market_value"] is None
    assert context["availability"]["valuation"] is False
    assert "valuation" in context["availability"]["unavailable_inputs"]
    assert [row["evidence_ref"] for row in context["signals"]] == [f"signal:{signal.id}"]
    assert context["material_news"][0]["evidence_ref"] == f"news:{news.id}"
    assert context["material_news"][0]["url"] == "https://example.test/story"
    assert {item["ref"] for item in context["evidence"]} == {
        f"signal:{signal.id}", f"news:{news.id}"
    }
    assert all("id" not in item for item in context["evidence"])


async def test_normalises_each_real_portfolio_value_to_gbp(
    db_session, make_instrument, monkeypatch
):
    user = await make_user(db_session, "currency-decision@test.dev")
    held = await make_instrument("USDHELD", currency="USD")
    portfolio = Portfolio(
        user_id=user.id, name="Dollar account", kind="real", base_currency="USD"
    )
    db_session.add(portfolio)
    await db_session.flush()
    db_session.add(
        Position(
            portfolio_id=portfolio.id,
            instrument_id=held.id,
            quantity=Decimal("5"),
        )
    )
    await db_session.commit()
    monkeypatch.setattr(decision_context, "assemble_candidates", _no_candidates)
    provider = Provider({"USDHELD": Quote(
        "USDHELD", Decimal("10"), "USD", Decimal("10"), datetime.now(UTC)
    )})

    context = await build_decision_context(
        db_session, user, QuoteService(provider), FxService(provider)
    )

    assert context["holdings"][0]["market_value"] == "40.00"


async def test_query_failures_are_distinct_from_successful_empty_inputs(
    db_session, monkeypatch
):
    user = await make_user(db_session, "query-outage@test.dev")
    monkeypatch.setattr(decision_context, "assemble_candidates", _no_candidates)

    async def fail(*args, **kwargs):
        raise RuntimeError("read down")

    monkeypatch.setattr(decision_context, "_signals", fail)
    monkeypatch.setattr(decision_context, "_news", fail)
    provider = Provider()
    context = await build_decision_context(
        db_session, user, QuoteService(provider), FxService(provider)
    )

    assert context["availability"]["signals"] is False
    assert context["availability"]["news"] is False


async def test_candidate_outage_is_reported_but_empty_pass_is_available(
    db_session, monkeypatch
):
    user = await make_user(db_session, "candidate-outage@test.dev")
    seed = CandidateSeed("NEW", "New Co", "US", "USD", "stock",
                         "Technology", ("AI",), ("catalog",))

    async def one_candidate(*args, **kwargs):
        return [seed]

    monkeypatch.setattr(decision_context, "assemble_candidates", one_candidate)
    outage = Provider(fail_quotes=True, fail_history=True)
    failed = await build_decision_context(
        db_session, user, QuoteService(outage), FxService(outage)
    )
    assert failed["candidates"] == []
    assert failed["availability"]["candidate_inputs"]["quotes"] is False
    assert failed["availability"]["candidate_inputs"]["history"] is False
    assert failed["availability"]["candidates"] is False

    monkeypatch.setattr(decision_context, "assemble_candidates", _no_candidates)
    empty = await build_decision_context(
        db_session, user, QuoteService(Provider()), FxService(Provider())
    )
    assert empty["candidates"] == []
    assert empty["availability"]["candidates"] is True


async def test_catalogue_candidate_evidence_does_not_create_instrument(
    db_session, monkeypatch
):
    user = await make_user(db_session, "catalogue-evidence@test.dev")
    seed = CandidateSeed("CATONLY", "Catalogue Only", "US", "USD", "stock",
                         "Technology", ("AI",), ("catalog",))

    async def one_candidate(*args, **kwargs):
        return [seed]

    monkeypatch.setattr(decision_context, "assemble_candidates", one_candidate)
    before = await db_session.scalar(select(func.count()).select_from(Instrument))
    provider = Provider({"CATONLY": Quote(
        "CATONLY", Decimal("10"), "USD", Decimal("9"), datetime.now(UTC)
    )})
    context = await build_decision_context(
        db_session, user, QuoteService(provider), FxService(provider)
    )

    refs = set(context["candidates"][0]["evidence_refs"])
    assert "candidate:CATONLY:news" in refs
    assert "candidate:CATONLY:diversification" in refs
    assert refs <= {item["ref"] for item in context["evidence"]}
    after = await db_session.scalar(select(func.count()).select_from(Instrument))
    assert after == before


async def test_hard_ceiling_compacts_verbose_fields_without_dropping_holdings(
    db_session, make_instrument, monkeypatch
):
    monkeypatch.setattr(decision_context, "MAX_CONTEXT_CHARS", 1800)
    user = await make_user(db_session, "ceiling@test.dev")
    instruments = [
        await make_instrument(f"KEEP{i}", name="Verbose " + "n" * 200)
        for i in range(2)
    ]
    portfolio = Portfolio(user_id=user.id, name="P", kind="real", base_currency="GBP")
    db_session.add(portfolio)
    await db_session.flush()
    db_session.add_all([
        Position(portfolio_id=portfolio.id, instrument_id=inst.id, quantity=Decimal("1"))
        for inst in instruments
    ])
    db_session.add(InvestorProfile(
        user_id=user.id, risk_appetite="balanced", horizon="long",
        sector_interests=[], free_text="x" * 10_000,
    ))
    now = datetime.now(UTC).replace(tzinfo=None)
    db_session.add_all([
        Signal(
            portfolio_id=portfolio.id, instrument_id=instruments[0].id,
            kind="price_move_day", severity="high", title="s" * 200,
            detail="d" * 500, data={}, computed_at=now,
        ),
        NewsItem(
            instrument_id=instruments[0].id, title="h" * 500, source="Wire",
            url="https://example.test/" + "u" * 900,
            published_at=now, fetched_at=now,
        ),
    ])
    await db_session.commit()
    candidate = CandidateSeed(
        "VERBOSE", "c" * 10_000, "US", "USD", "stock", "Technology",
        ("theme" * 1000,), ("catalog",),
    )

    async def verbose_candidate(*args, **kwargs):
        return [candidate]

    monkeypatch.setattr(decision_context, "assemble_candidates", verbose_candidate)

    async def verbose_exposure(*args, **kwargs):
        return {"groups": [{"name": "g" * 10_000}], "unpriced": [], "total_base": "20"}

    monkeypatch.setattr(decision_context, "compute_group_exposure", verbose_exposure)
    provider = Provider({
        **{inst.symbol: quote(inst.symbol) for inst in instruments},
        "VERBOSE": quote("VERBOSE"),
    })
    context = await build_decision_context(
        db_session, user, QuoteService(provider), FxService(provider)
    )

    assert len(json.dumps(context)) <= decision_context.MAX_CONTEXT_CHARS
    assert {row["symbol"] for row in context["holdings"]} == {"KEEP0", "KEEP1"}
    assert all({"symbol", "source_portfolio_ids", "availability"} <= row.keys()
               for row in context["holdings"])


async def test_irreducible_context_raises_named_error(monkeypatch):
    monkeypatch.setattr(decision_context, "MAX_CONTEXT_CHARS", 1)
    with pytest.raises(DecisionContextTooLarge):
        decision_context._truncate({
            "profile": {},
            "holdings": [{"symbol": "A", "source_portfolio_ids": [1],
                          "availability": {"valuation": True}}],
            "signals": [], "material_news": [], "portfolio_context": {},
            "candidates": [], "evidence": [],
            "availability": {"context_truncated": False}, "data_as_of": "",
        })


async def _no_candidates(*args, **kwargs):
    return []
