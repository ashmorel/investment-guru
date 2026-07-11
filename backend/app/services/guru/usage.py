import logging
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy.ext.asyncio import AsyncSession

from app.models import LlmUsage
from app.services.guru.llm.base import Usage

# (input, output) USD per million tokens, keyed by model-id prefix.
_PRICES_PER_MTOK: dict[str, tuple[Decimal, Decimal]] = {
    "claude-opus-4": (Decimal("5"), Decimal("25")),
    "claude-haiku-4-5": (Decimal("1"), Decimal("5")),
    "gpt-4o-mini": (Decimal("0.15"), Decimal("0.60")),
    "gpt-4o": (Decimal("2.5"), Decimal("10")),
    "gpt-4.1-mini": (Decimal("0.40"), Decimal("1.60")),
    "gpt-4.1": (Decimal("2"), Decimal("8")),
    "o1": (Decimal("15"), Decimal("60")),
    "gemini-2.5-flash": (Decimal("0.30"), Decimal("2.50")),
    "gemini-2.5-pro": (Decimal("1.25"), Decimal("10")),
    "gemini-1.5-flash": (Decimal("0.075"), Decimal("0.30")),
    "gemini-1.5-pro": (Decimal("1.25"), Decimal("5")),
}
_MTOK = Decimal("1000000")


def estimate_cost(
    model: str, usage: Usage,
    price: tuple[Decimal, Decimal] | None = None,
) -> Decimal | None:
    if price is not None:
        in_price, out_price = price
        return (usage.input_tokens * in_price + usage.output_tokens * out_price) / _MTOK
    for prefix, (in_price, out_price) in _PRICES_PER_MTOK.items():
        if model.startswith(prefix):
            return (usage.input_tokens * in_price + usage.output_tokens * out_price) / _MTOK
    return None


async def record_usage(
    db: AsyncSession, *, user_id: int, mode: str, model: str, usage: Usage,
    report_id: int | None = None, thread_id: int | None = None,
    price: tuple[Decimal, Decimal] | None = None,
) -> LlmUsage:
    est = estimate_cost(model, usage, price)
    if est is None:
        logging.getLogger(__name__).warning("uncosted model %r — usage not budget-counted", model)
    row = LlmUsage(
        user_id=user_id, mode=mode, model=model,
        input_tokens=usage.input_tokens, output_tokens=usage.output_tokens,
        est_cost_usd=est, report_id=report_id, thread_id=thread_id,
        created_at=datetime.now(UTC).replace(tzinfo=None),
    )
    db.add(row)
    await db.flush()
    return row
