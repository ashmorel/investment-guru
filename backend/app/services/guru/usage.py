from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy.ext.asyncio import AsyncSession

from app.models import LlmUsage
from app.services.guru.llm.base import Usage

# (input, output) USD per million tokens, keyed by model-id prefix.
_PRICES_PER_MTOK: dict[str, tuple[Decimal, Decimal]] = {
    "claude-opus-4": (Decimal("5"), Decimal("25")),
    "claude-haiku-4-5": (Decimal("1"), Decimal("5")),
}
_MTOK = Decimal("1000000")


def estimate_cost(model: str, usage: Usage) -> Decimal | None:
    for prefix, (in_price, out_price) in _PRICES_PER_MTOK.items():
        if model.startswith(prefix):
            return (usage.input_tokens * in_price + usage.output_tokens * out_price) / _MTOK
    return None


async def record_usage(
    db: AsyncSession, *, user_id: int, mode: str, model: str, usage: Usage,
    report_id: int | None = None, thread_id: int | None = None,
) -> LlmUsage:
    row = LlmUsage(
        user_id=user_id, mode=mode, model=model,
        input_tokens=usage.input_tokens, output_tokens=usage.output_tokens,
        est_cost_usd=estimate_cost(model, usage),
        report_id=report_id, thread_id=thread_id,
        created_at=datetime.now(UTC).replace(tzinfo=None),
    )
    db.add(row)
    await db.flush()
    return row
