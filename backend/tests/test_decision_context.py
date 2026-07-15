import json
from datetime import UTC, datetime
from decimal import Decimal

import pytest

from app.models import InvestorProfile, NewsItem, Portfolio, Position, Signal, User
from app.services.guru import decision_context
from app.services.guru.decision_context import build_decision_context
from app.services.market_data.base import Quote
from app.services.market_data.quotes import QuoteService
from app.services.valuation import FxService

pytestmark = pytest.mark.asyncio(loop_scope="session")


class Provider:
    def __init__(self, quotes=None):
        self.quotes = quotes or {}

    async def get_quotes(self, symbols):
        return {symbol: self.quotes[symbol] for symbol in symbols if symbol in self.quotes}

    async def get_fx_rate(self, base, quote):
        return Decimal("0.8")

    async def get_history(self, symbol, days=400):
        return []

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
    assert {item["id"] for item in context["evidence"]} == {
        f"signal:{signal.id}", f"news:{news.id}"
    }


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


async def _no_candidates(*args, **kwargs):
    return []
