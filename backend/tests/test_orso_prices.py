from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path

import pytest

from app.models import OrsoFund, OrsoFundPrice, User
from app.services.orso.prices import (
    FakeOrsoPriceProvider,
    OrsoPriceService,
    PriceDTO,
    parse_fund_prices,
)

FIXTURES = Path(__file__).parent / "fixtures"


def _load_fixture(name: str) -> str:
    return (FIXTURES / name).read_text()


async def _user(db_session) -> User:
    u = User(email="orso-prices@test.dev", password_hash="x")
    db_session.add(u)
    await db_session.commit()
    return u


async def _fund(db_session, user_id: int, code: str) -> OrsoFund:
    f = OrsoFund(user_id=user_id, code=code, name=code, asset_class="equity", risk_rating=3)
    db_session.add(f)
    await db_session.commit()
    return f


# --- parse_fund_prices -------------------------------------------------

def test_parse_fund_prices_from_real_fixture_returns_decimals():
    prices = parse_fund_prices(_load_fixture("hsbc_fund_prices.json"))
    assert prices["MMF"].price == Decimal("130.97000")
    assert prices["MMF"].as_of == date(2026, 6, 29)
    assert isinstance(prices["MMF"].price, Decimal)
    # a real, representative response contains more than one fund
    assert len(prices) == 17


def test_parse_fund_prices_drops_non_finite_zero_and_negative():
    raw = (
        '{"data":[{"fundPriceList":['
        '{"fundIdentifier":"OK","bidAmount":"12.3400","priceDate":"01/07/2026"},'
        '{"fundIdentifier":"NAN","bidAmount":"NaN","priceDate":"01/07/2026"},'
        '{"fundIdentifier":"ZERO","bidAmount":"0","priceDate":"01/07/2026"},'
        '{"fundIdentifier":"NEG","bidAmount":"-1.5","priceDate":"01/07/2026"},'
        '{"fundIdentifier":"MISSING_DATE","bidAmount":"9.99"}'
        "]}]}"
    )
    prices = parse_fund_prices(raw)
    assert set(prices) == {"OK"}
    assert prices["OK"].price == Decimal("12.3400")
    assert prices["OK"].as_of == date(2026, 7, 1)


def test_parse_fund_prices_empty_payload_returns_empty_dict():
    assert parse_fund_prices('{"data":[]}') == {}


# --- FakeOrsoPriceProvider ----------------------------------------------

@pytest.mark.asyncio(loop_scope="session")
async def test_fake_provider_round_trip():
    dto = PriceDTO(price=Decimal("10.00"), as_of=date(2026, 7, 9))
    provider = FakeOrsoPriceProvider(prices={"HK-EQ": dto})
    result = await provider.get_prices(["HK-EQ", "MISSING"])
    assert result == {"HK-EQ": dto}
    assert provider.calls == [["HK-EQ", "MISSING"]]


@pytest.mark.asyncio(loop_scope="session")
async def test_fake_provider_raises_when_fail_set():
    provider = FakeOrsoPriceProvider(fail=True)
    with pytest.raises(Exception):  # noqa: B017 - simulated provider failure
        await provider.get_prices(["HK-EQ"])
    assert provider.calls == [["HK-EQ"]]


# --- OrsoPriceService.refresh --------------------------------------------

@pytest.mark.asyncio(loop_scope="session")
async def test_refresh_writes_todays_rows_and_returns_refreshed_ids(db_session):
    u = await _user(db_session)
    fund = await _fund(db_session, u.id, "HK-EQ")
    today = date.today()
    provider = FakeOrsoPriceProvider(
        prices={"HK-EQ": PriceDTO(price=Decimal("42.5000"), as_of=today)}
    )
    svc = OrsoPriceService(provider)
    refreshed = await svc.refresh(db_session, [fund])
    await db_session.commit()
    assert refreshed == {fund.id}
    prices = await svc.latest_prices(db_session, [fund.id])
    assert prices[fund.id].price == Decimal("42.5000")
    assert prices[fund.id].as_of == today
    assert prices[fund.id].source == "hsbc"


@pytest.mark.asyncio(loop_scope="session")
async def test_refresh_failure_returns_fresh_subset_and_keeps_prior_rows(db_session):
    u = await _user(db_session)
    already_fresh = await _fund(db_session, u.id, "FRESH")
    stale = await _fund(db_session, u.id, "STALE")
    today = date.today()
    now = datetime.now(UTC).replace(tzinfo=None)
    db_session.add(OrsoFundPrice(
        fund_id=already_fresh.id, price=Decimal("5.0000"), as_of=today,
        source="hsbc", fetched_at=now,
    ))
    await db_session.commit()

    provider = FakeOrsoPriceProvider(fail=True)
    svc = OrsoPriceService(provider)
    refreshed = await svc.refresh(db_session, [already_fresh, stale])
    await db_session.commit()

    assert refreshed == {already_fresh.id}
    prices = await svc.latest_prices(db_session, [already_fresh.id, stale.id])
    assert prices[already_fresh.id].price == Decimal("5.0000")
    assert stale.id not in prices


@pytest.mark.asyncio(loop_scope="session")
async def test_refresh_skips_funds_already_priced_today(db_session):
    u = await _user(db_session)
    fresh_fund = await _fund(db_session, u.id, "FRESH")
    stale_fund = await _fund(db_session, u.id, "STALE")
    today = date.today()
    now = datetime.now(UTC).replace(tzinfo=None)
    db_session.add(OrsoFundPrice(
        fund_id=fresh_fund.id, price=Decimal("5.0000"), as_of=today,
        source="hsbc", fetched_at=now,
    ))
    await db_session.commit()

    provider = FakeOrsoPriceProvider(
        prices={"STALE": PriceDTO(price=Decimal("7.7000"), as_of=today)}
    )
    svc = OrsoPriceService(provider)
    refreshed = await svc.refresh(db_session, [fresh_fund, stale_fund])
    await db_session.commit()

    assert refreshed == {fresh_fund.id, stale_fund.id}
    assert provider.calls == [["STALE"]]


@pytest.mark.asyncio(loop_scope="session")
async def test_refresh_with_no_provider_returns_empty_set(db_session):
    u = await _user(db_session)
    fund = await _fund(db_session, u.id, "HK-EQ")
    svc = OrsoPriceService(None)
    refreshed = await svc.refresh(db_session, [fund])
    assert refreshed == set()


# --- upsert_manual_price ---------------------------------------------------

@pytest.mark.asyncio(loop_scope="session")
async def test_upsert_manual_price_inserts_then_overwrites_same_day(db_session):
    u = await _user(db_session)
    fund = await _fund(db_session, u.id, "HK-EQ")
    svc = OrsoPriceService(None)
    as_of = date(2026, 7, 9)

    first = await svc.upsert_manual_price(db_session, fund, Decimal("10.0000"), as_of)
    await db_session.commit()
    assert first.source == "manual"
    assert first.price == Decimal("10.0000")

    second = await svc.upsert_manual_price(db_session, fund, Decimal("11.5000"), as_of)
    await db_session.commit()
    assert second.id == first.id
    assert second.price == Decimal("11.5000")
    assert second.source == "manual"

    prices = await svc.latest_prices(db_session, [fund.id])
    assert prices[fund.id].price == Decimal("11.5000")


# --- latest_prices ----------------------------------------------------------

@pytest.mark.asyncio(loop_scope="session")
async def test_latest_prices_returns_max_as_of_row_per_fund(db_session):
    u = await _user(db_session)
    fund_a = await _fund(db_session, u.id, "A")
    fund_b = await _fund(db_session, u.id, "B")
    now = datetime.now(UTC).replace(tzinfo=None)
    db_session.add_all([
        OrsoFundPrice(fund_id=fund_a.id, price=Decimal("1.0000"), as_of=date(2026, 7, 1),
                     source="hsbc", fetched_at=now),
        OrsoFundPrice(fund_id=fund_a.id, price=Decimal("2.0000"), as_of=date(2026, 7, 8),
                     source="hsbc", fetched_at=now),
        OrsoFundPrice(fund_id=fund_b.id, price=Decimal("3.0000"), as_of=date(2026, 7, 5),
                     source="manual", fetched_at=now),
    ])
    await db_session.commit()

    svc = OrsoPriceService(None)
    prices = await svc.latest_prices(db_session, [fund_a.id, fund_b.id])
    assert prices[fund_a.id].price == Decimal("2.0000")
    assert prices[fund_a.id].as_of == date(2026, 7, 8)
    assert prices[fund_b.id].price == Decimal("3.0000")


@pytest.mark.asyncio(loop_scope="session")
async def test_latest_prices_empty_fund_ids_returns_empty_dict(db_session):
    svc = OrsoPriceService(None)
    assert await svc.latest_prices(db_session, []) == {}
