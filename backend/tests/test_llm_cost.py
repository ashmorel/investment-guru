from decimal import Decimal

from app.services.guru.llm.base import Usage
from app.services.guru.usage import estimate_cost


def test_config_price_override_wins():
    # 1M in @ $3, 1M out @ $6  -> 9
    c = estimate_cost("any-model", Usage(1_000_000, 1_000_000),
                      price=(Decimal("3"), Decimal("6")))
    assert c == Decimal("9")


def test_builtin_table_used_when_no_override():
    c = estimate_cost("gpt-4o-mini", Usage(1_000_000, 0))
    assert c is not None and c > 0


def test_unknown_model_uncosted():
    assert estimate_cost("some-brand-new-model", Usage(1000, 1000)) is None
