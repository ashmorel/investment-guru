from datetime import UTC, datetime
from decimal import Decimal

import pytest

from app.services.market_data.base import Bar, Quote
from app.services.recommendations.candidates import CandidateSeed
from app.services.recommendations.scoring import (
    CandidateInputs,
    score_candidates,
    score_inputs,
)


def _seed(symbol: str) -> CandidateSeed:
    return CandidateSeed(
        symbol=symbol,
        name=symbol,
        market="US",
        currency="USD",
        instrument_type="stock",
        sector="Technology",
        themes=(),
        sources=("catalog",),
    )


def test_rank_candidate_prefers_complete_positive_inputs():
    strong = CandidateInputs(
        momentum="positive",
        valuation="reasonable",
        news_count=3,
        diversification_fit="high",
        stale=False,
    )
    weak = CandidateInputs(
        momentum=None,
        valuation=None,
        news_count=0,
        diversification_fit="low",
        stale=True,
    )

    assert score_inputs(strong) > score_inputs(weak)


def test_stale_and_missing_inputs_reduce_score():
    complete = CandidateInputs("positive", "reasonable", 2, "high", False)
    stale = CandidateInputs("positive", "reasonable", 2, "high", True)
    missing = CandidateInputs(None, None, 2, "high", False)

    assert score_inputs(complete) > score_inputs(stale)
    assert score_inputs(complete) > score_inputs(missing)


def test_all_numeric_factor_outputs_are_decimal_strings():
    result = score_inputs(
        CandidateInputs("neutral", "expensive", 1, "medium", False),
        include_factors=True,
    )

    assert all(value is None or str(Decimal(value)) == value for value in result.factors.values())


@pytest.mark.asyncio
async def test_score_candidates_degrades_per_input_and_enforces_evidence():
    seeds = [_seed("AAA"), _seed("BBB"), _seed("CCC")]
    now = datetime.now(UTC)

    async def quote(seed):
        if seed.symbol == "CCC":
            raise RuntimeError("quote unavailable")
        return Quote(seed.symbol, Decimal("100"), "USD", Decimal("99"), now)

    async def history(seed):
        if seed.symbol == "AAA":
            raise RuntimeError("history unavailable")
        return [Bar(now.date(), Decimal("90"), Decimal("101"), Decimal("89"), Decimal("100"), 1)]

    async def fundamentals(seed):
        return {"valuation": "reasonable"} if seed.symbol == "AAA" else None

    async def news(seed):
        return []

    async def signal(seed):
        return "positive"

    results = await score_candidates(
        seeds,
        quote_reader=quote,
        history_reader=history,
        fundamentals_reader=fundamentals,
        news_reader=news,
        signal_reader=signal,
        diversification_reader=lambda seed: "high",
    )

    assert [item.seed.symbol for item in results] == ["AAA", "BBB"]
    assert results[0].availability["history"] is False
    assert results[0].availability["fundamentals"] is True
    assert results[0].availability["quote"] is True
    assert {e.kind for e in results[0].evidence} == {"quote", "fundamentals"}


@pytest.mark.asyncio
async def test_score_candidates_limits_and_sorts_by_score_then_symbol():
    seeds = [_seed("ZZZ"), _seed("AAA"), _seed("MMM")]
    now = datetime.now(UTC)

    async def quote(seed):
        return Quote(seed.symbol, Decimal("100"), "USD", Decimal("99"), now)

    async def history(seed):
        return [Bar(now.date(), Decimal("99"), Decimal("101"), Decimal("98"), Decimal("100"), 1)]

    async def absent(seed):
        return None

    results = await score_candidates(
        seeds,
        quote_reader=quote,
        history_reader=history,
        fundamentals_reader=absent,
        news_reader=absent,
        signal_reader=absent,
        diversification_reader=lambda seed: "medium",
        limit=2,
    )

    assert [item.seed.symbol for item in results] == ["AAA", "MMM"]
