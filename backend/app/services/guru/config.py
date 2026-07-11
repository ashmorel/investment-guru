"""Resolve the active LLM config: the single llm_config row if present, else a
fallback synthesised from env settings (so the app runs before the panel is
ever saved)."""
from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models import LlmConfig


@dataclass(frozen=True)
class ResolvedLlmConfig:
    provider: str
    api_key: str
    advice_model: str
    scan_model: str
    advice_price: tuple[Decimal, Decimal] | None
    scan_price: tuple[Decimal, Decimal] | None


def _price(in_p: Decimal | None, out_p: Decimal | None) -> tuple[Decimal, Decimal] | None:
    return (in_p, out_p) if in_p is not None and out_p is not None else None


async def load_active_config(db: AsyncSession) -> ResolvedLlmConfig:
    row = (await db.execute(select(LlmConfig).order_by(LlmConfig.id).limit(1))).scalar_one_or_none()
    if row is None:
        return ResolvedLlmConfig(
            provider="anthropic", api_key=settings.anthropic_api_key,
            advice_model=settings.guru_advice_model, scan_model=settings.guru_scan_model,
            advice_price=None, scan_price=None)
    return ResolvedLlmConfig(
        provider=row.provider, api_key=row.api_key or "",
        advice_model=row.advice_model, scan_model=row.scan_model,
        advice_price=_price(row.advice_input_price, row.advice_output_price),
        scan_price=_price(row.scan_input_price, row.scan_output_price))
