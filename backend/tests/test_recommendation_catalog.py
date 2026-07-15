from decimal import Decimal

import pytest

from app.models import Instrument, InvestorProfile, Portfolio, Position, User
from app.services.recommendations.catalog import load_catalog, parse_catalog


def test_catalog_is_unique_and_supported():
    entries = load_catalog()

    assert entries
    assert len({entry.symbol for entry in entries}) == len(entries)
    assert {entry.market for entry in entries} <= {"US", "UK", "HK"}
    assert {entry.instrument_type for entry in entries} <= {"stock", "etf"}
    assert all(entry.name and entry.currency for entry in entries)


@pytest.mark.parametrize(
    "raw",
    [
        [
            {
                "symbol": "DUP",
                "name": "First",
                "market": "US",
                "currency": "USD",
                "instrument_type": "stock",
            },
            {
                "symbol": "dup",
                "name": "Second",
                "market": "US",
                "currency": "USD",
                "instrument_type": "stock",
            },
        ],
        [
            {
                "symbol": "BAD",
                "name": "Unsupported market",
                "market": "CA",
                "currency": "CAD",
                "instrument_type": "stock",
            }
        ],
        [
            {
                "symbol": "EMPTY",
                "market": "US",
                "currency": "USD",
                "instrument_type": "stock",
            }
        ],
    ],
)
def test_parse_catalog_rejects_invalid_entries(raw):
    with pytest.raises(ValueError):
        parse_catalog(raw)


@pytest.mark.asyncio(loop_scope="session")
async def test_assemble_candidates_is_user_scoped_and_excludes_holdings(db_session):
    from app.services.recommendations.candidates import assemble_candidates

    user = User(email="candidate@test.dev", password_hash="x")
    other = User(email="candidate-other@test.dev", password_hash="x")
    db_session.add_all([user, other])
    await db_session.flush()

    aapl = Instrument(
        symbol="AAPL", name="Apple", exchange="NMS", market="US",
        sector="Technology", currency="USD",
    )
    msft = Instrument(
        symbol="MSFT", name="Microsoft", exchange="NMS", market="US",
        sector="Technology", currency="USD",
    )
    secret = Instrument(
        symbol="SECRET", name="Other user watch", exchange="NMS", market="US",
        sector="Technology", currency="USD",
    )
    db_session.add_all([aapl, msft, secret])
    await db_session.flush()

    real = Portfolio(user_id=user.id, name="Held", kind="real", base_currency="USD")
    watch = Portfolio(user_id=user.id, name="Watch", kind="watchlist", base_currency="USD")
    other_watch = Portfolio(
        user_id=other.id, name="Private", kind="watchlist", base_currency="USD"
    )
    db_session.add_all([real, watch, other_watch])
    await db_session.flush()
    db_session.add_all(
        [
            Position(
                portfolio_id=real.id, instrument_id=aapl.id,
                quantity=Decimal("1"), avg_cost=Decimal("100"),
            ),
            Position(portfolio_id=watch.id, instrument_id=msft.id),
            Position(portfolio_id=other_watch.id, instrument_id=secret.id),
        ]
    )
    profile = InvestorProfile(
        user_id=user.id, risk_appetite="balanced", horizon="long",
        sector_interests=["technology"], free_text="",
    )
    db_session.add(profile)
    await db_session.commit()

    candidates = await assemble_candidates(db_session, user, profile)
    by_symbol = {candidate.symbol: candidate for candidate in candidates}

    assert [candidate.symbol for candidate in candidates] == sorted(by_symbol)
    assert "AAPL" not in by_symbol
    assert "SECRET" not in by_symbol
    assert "watchlist" in by_symbol["MSFT"].sources
    assert any(
        "profile_interest" in candidate.sources
        for candidate in candidates
        if candidate.symbol != "MSFT"
    )


@pytest.mark.asyncio(loop_scope="session")
async def test_assemble_candidates_ignores_another_users_profile(db_session):
    from app.services.recommendations.candidates import assemble_candidates

    user = User(email="profile-owner@test.dev", password_hash="x")
    other = User(email="foreign-profile@test.dev", password_hash="x")
    db_session.add_all([user, other])
    await db_session.flush()
    foreign_profile = InvestorProfile(
        user_id=other.id, risk_appetite="balanced", horizon="long",
        sector_interests=["technology"], free_text="",
    )
    db_session.add(foreign_profile)
    await db_session.commit()

    candidates = await assemble_candidates(db_session, user, foreign_profile)

    assert all("profile_interest" not in candidate.sources for candidate in candidates)
