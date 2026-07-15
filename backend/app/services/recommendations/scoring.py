from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any, Literal, overload

from app.services.recommendations.candidates import CandidateSeed

MOMENTUM_WEIGHTS = {
    "positive": Decimal("30"),
    "neutral": Decimal("10"),
    "negative": Decimal("-15"),
}
VALUATION_WEIGHTS = {
    "reasonable": Decimal("25"),
    "cheap": Decimal("30"),
    "expensive": Decimal("-10"),
}
DIVERSIFICATION_WEIGHTS = {
    "high": Decimal("20"),
    "medium": Decimal("10"),
    "low": Decimal("0"),
}
NEWS_ITEM_WEIGHT = Decimal("5")
MAX_NEWS_SCORE = Decimal("15")
STALE_PENALTY = Decimal("20")
MISSING_INPUT_PENALTY = Decimal("5")
QUOTE_STALE_AFTER = timedelta(days=1)


@dataclass(frozen=True)
class CandidateInputs:
    momentum: str | None
    valuation: str | None
    news_count: int
    diversification_fit: str | None
    stale: bool


@dataclass(frozen=True)
class ScoreBreakdown:
    score: Decimal
    factors: dict[str, str | None]


@dataclass(frozen=True)
class CandidateEvidence:
    kind: Literal["quote", "history", "fundamentals", "news"]
    value: str


@dataclass(frozen=True)
class ScoredCandidate:
    seed: CandidateSeed
    score: Decimal
    factors: dict[str, str | None]
    availability: dict[str, bool]
    evidence: list[CandidateEvidence]


def _decimal_string(value: Decimal) -> str:
    return str(value)


@overload
def score_inputs(
    inputs: CandidateInputs, *, include_factors: Literal[False] = False
) -> Decimal: ...


@overload
def score_inputs(inputs: CandidateInputs, *, include_factors: Literal[True]) -> ScoreBreakdown: ...


def score_inputs(
    inputs: CandidateInputs, *, include_factors: bool = False
) -> Decimal | ScoreBreakdown:
    momentum = MOMENTUM_WEIGHTS.get(inputs.momentum) if inputs.momentum else None
    valuation = VALUATION_WEIGHTS.get(inputs.valuation) if inputs.valuation else None
    diversification = (
        DIVERSIFICATION_WEIGHTS.get(inputs.diversification_fit)
        if inputs.diversification_fit
        else None
    )
    news = min(Decimal(max(inputs.news_count, 0)) * NEWS_ITEM_WEIGHT, MAX_NEWS_SCORE)
    stale = -STALE_PENALTY if inputs.stale else Decimal("0")
    missing_count = sum(
        value is None for value in (momentum, valuation, diversification)
    )
    missing = -(Decimal(missing_count) * MISSING_INPUT_PENALTY)
    factors = {
        "momentum": _decimal_string(momentum) if momentum is not None else None,
        "valuation": _decimal_string(valuation) if valuation is not None else None,
        "news": _decimal_string(news),
        "diversification": (
            _decimal_string(diversification) if diversification is not None else None
        ),
        "stale": _decimal_string(stale),
        "missing": _decimal_string(missing),
    }
    score = sum(
        (Decimal(value) for value in factors.values() if value is not None),
        Decimal("0"),
    )
    return ScoreBreakdown(score, factors) if include_factors else score


Reader = Callable[[CandidateSeed], Awaitable[Any]]


async def _read(reader: Reader, seed: CandidateSeed) -> tuple[Any, bool]:
    try:
        value = await reader(seed)
    except Exception:
        return None, False
    available = value is not None
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        available = bool(value)
    return value, available


def _quote_stale(quote: Any) -> bool:
    as_of = quote.get("as_of") if isinstance(quote, dict) else getattr(quote, "as_of", None)
    if not isinstance(as_of, datetime):
        return False
    if as_of.tzinfo is None:
        as_of = as_of.replace(tzinfo=UTC)
    return datetime.now(UTC) - as_of > QUOTE_STALE_AFTER


def _usable_quote(quote: Any) -> bool:
    if isinstance(quote, dict):
        price = quote.get("price")
        currency = quote.get("currency")
        as_of = quote.get("as_of")
    else:
        price = getattr(quote, "price", None)
        currency = getattr(quote, "currency", None)
        as_of = getattr(quote, "as_of", None)
    return (
        isinstance(price, Decimal)
        and price > 0
        and isinstance(currency, str)
        and bool(currency.strip())
        and isinstance(as_of, datetime)
    )


def _usable_fundamentals(fundamentals: Any) -> bool:
    if isinstance(fundamentals, dict):
        valuation = fundamentals.get("valuation")
        earnings_date = fundamentals.get("next_earnings_date")
        fetched_at = fundamentals.get("fetched_at")
        return (
            isinstance(valuation, str)
            and bool(valuation.strip())
            or isinstance(earnings_date, date)
            or isinstance(fetched_at, datetime)
        )
    fetched_at = getattr(fundamentals, "fetched_at", None)
    valuation = getattr(fundamentals, "valuation", None)
    return isinstance(fetched_at, datetime) or (
        isinstance(valuation, str) and bool(valuation.strip())
    )


def _valuation(fundamentals: Any) -> str | None:
    if isinstance(fundamentals, dict):
        value = fundamentals.get("valuation")
        return value if isinstance(value, str) else None
    value = getattr(fundamentals, "valuation", None)
    return value if isinstance(value, str) else None


def _evidence_value(value: Any) -> str:
    if hasattr(value, "price"):
        return str(value.price)
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        return str(len(value))
    return "available"


async def score_candidates(
    seeds: list[CandidateSeed],
    *,
    quote_reader: Reader,
    history_reader: Reader,
    fundamentals_reader: Reader,
    news_reader: Reader,
    signal_reader: Reader,
    diversification_reader: Callable[[CandidateSeed], str | None],
    limit: int = 12,
) -> list[ScoredCandidate]:
    scored: list[ScoredCandidate] = []
    for seed in seeds:
        quote, quote_read = await _read(quote_reader, seed)
        history, history_ok = await _read(history_reader, seed)
        fundamentals, fundamentals_read = await _read(fundamentals_reader, seed)
        news, news_ok = await _read(news_reader, seed)
        signal, signal_ok = await _read(signal_reader, seed)
        quote_ok = quote_read and _usable_quote(quote)
        fundamentals_ok = fundamentals_read and _usable_fundamentals(fundamentals)

        if not quote_ok or not (history_ok or fundamentals_ok or news_ok):
            continue

        try:
            diversification = diversification_reader(seed)
        except Exception:
            diversification = None
        news_count = (
            len(news)
            if isinstance(news, Sequence) and not isinstance(news, (str, bytes))
            else 0
        )
        inputs = CandidateInputs(
            momentum=signal if signal_ok and isinstance(signal, str) else None,
            valuation=_valuation(fundamentals) if fundamentals_ok else None,
            news_count=news_count,
            diversification_fit=diversification,
            stale=_quote_stale(quote),
        )
        breakdown = score_inputs(inputs, include_factors=True)
        availability = {
            "quote": quote_ok,
            "history": history_ok,
            "fundamentals": fundamentals_ok,
            "news": news_ok,
            "signal": signal_ok,
            "diversification": diversification is not None,
        }
        evidence = [CandidateEvidence("quote", _evidence_value(quote))]
        for kind, value, available in (
            ("history", history, history_ok),
            ("fundamentals", fundamentals, fundamentals_ok),
            ("news", news, news_ok),
        ):
            if available:
                evidence.append(CandidateEvidence(kind, _evidence_value(value)))
        scored.append(
            ScoredCandidate(
                seed=seed,
                score=breakdown.score,
                factors=breakdown.factors,
                availability=availability,
                evidence=evidence,
            )
        )

    scored.sort(key=lambda item: (-item.score, item.seed.symbol))
    return scored[: max(limit, 0)]
