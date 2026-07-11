"""Extract an ORSO allocation from a statement screenshot via the Guru LLM
layer's vision path. Reuses generate_structured with an Anthropic image block —
no LLM-layer change. Governed by the per-user daily budget; usage is recorded.
Output is always a reviewable AllocationDraft (never auto-committed)."""
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.services.guru import usage as usage_mod
from app.services.guru.budget import check_budget
from app.services.guru.llm.base import LLMProvider
from app.services.guru.schemas import OrsoStatementExtraction
from app.services.orso.ingest import AllocationDraft, build_draft

_INSTRUCTION = (
    "This image is an HSBC ORSO pension statement. Extract every fund row: the "
    "fund code, fund name, unit holdings, current market value, currency, and "
    "contribution percentage. Use null for any field not visible. Do not invent rows."
)


async def extract_statement(
    provider: LLMProvider, db: AsyncSession, user_id: int,
    image_b64: str, media_type: str,
) -> AllocationDraft:
    await check_budget(db, user_id)
    messages = [{"role": "user", "content": [
        {"type": "image", "source": {"type": "base64",
                                     "media_type": media_type, "data": image_b64}},
        {"type": "text", "text": _INSTRUCTION},
    ]}]
    payload, usage = await provider.generate_structured(
        system="You extract structured data from financial statement images.",
        messages=messages, schema=OrsoStatementExtraction,
        model=settings.guru_advice_model, max_tokens=2048)
    await usage_mod.record_usage(db, user_id=user_id, mode="orso_ingest",
                                 model=settings.guru_advice_model, usage=usage)
    await db.commit()
    parsed = [{"fund_code": r.fund_code, "fund_name": r.fund_name, "units": r.units,
               "value": r.value, "currency": r.currency,
               "contribution_pct": r.contribution_pct} for r in payload.rows]
    return await build_draft(db, user_id, parsed, source="screenshot")
