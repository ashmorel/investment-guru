import asyncio
import json
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models import GuruReport, Portfolio, User
from app.services.guru import usage as usage_mod
from app.services.guru.context import build_context
from app.services.guru.llm.anthropic import AnthropicProvider
from app.services.guru.llm.base import LLMError, LLMNotConfigured, LLMProvider, Usage
from app.services.guru.persona import PERSONA_V1
from app.services.guru.schemas import DigestPayload, ReviewPayload, TakePayload
from app.services.market_data.quotes import QuoteService
from app.services.valuation import FxService


class GenerationInProgress(Exception):
    pass


def _now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


class GuruService:
    def __init__(self, provider: LLMProvider | None, quotes: QuoteService, fx: FxService):
        self.provider = provider
        self.quotes = quotes
        self.fx = fx
        self._locks: dict[str, asyncio.Lock] = {}

    def _lock(self, kind: str) -> asyncio.Lock:
        return self._locks.setdefault(kind, asyncio.Lock())

    def _require_provider(self) -> LLMProvider:
        if self.provider is None:
            raise LLMNotConfigured("anthropic_api_key not set")
        return self.provider

    async def _profile(self, db: AsyncSession, user: User):
        from app.api.guru import get_profile_row

        return await get_profile_row(db, user)

    async def generate_review(self, db: AsyncSession, user: User,
                              portfolio: Portfolio) -> GuruReport:
        provider = self._require_provider()
        lock = self._lock("review")
        if lock.locked():
            raise GenerationInProgress("review")
        async with lock:
            profile = await self._profile(db, user)
            ctx = await build_context(db, user, quote_service=self.quotes, fx=self.fx,
                                      portfolios=[portfolio], profile=profile)
            expected = {p.instrument.symbol for p in portfolio.positions}
            messages = [{"role": "user", "content":
                         "Review this portfolio. Give a verdict for EVERY position.\n\n"
                         + json.dumps(ctx)}]
            payload, usage = await provider.generate_structured(
                system=PERSONA_V1, messages=messages, schema=ReviewPayload,
                model=settings.guru_advice_model, max_tokens=4096)
            missing = expected - {p.symbol for p in payload.positions}
            if missing:
                messages += [
                    {"role": "assistant", "content": payload.model_dump_json()},
                    {"role": "user", "content":
                     f"You omitted these positions: {sorted(missing)}. "
                     "Return the complete review covering every position."},
                ]
                first_usage = usage
                payload, usage = await provider.generate_structured(
                    system=PERSONA_V1, messages=messages, schema=ReviewPayload,
                    model=settings.guru_advice_model, max_tokens=4096)
                usage = Usage(input_tokens=first_usage.input_tokens + usage.input_tokens,
                              output_tokens=first_usage.output_tokens + usage.output_tokens)
                missing = expected - {p.symbol for p in payload.positions}
                if missing:
                    raise LLMError(f"review still missing positions: {sorted(missing)}")
            report = GuruReport(user_id=user.id, kind="review", portfolio_id=portfolio.id,
                                payload=payload.model_dump(),
                                model=settings.guru_advice_model, created_at=_now())
            db.add(report)
            await db.flush()
            await usage_mod.record_usage(db, user_id=user.id, mode="review",
                                         model=settings.guru_advice_model,
                                         usage=usage, report_id=report.id)
            await db.commit()
            return report

    async def _all_portfolios(self, db: AsyncSession, user: User) -> list[Portfolio]:
        return (await db.execute(
            select(Portfolio).where(Portfolio.user_id == user.id).order_by(Portfolio.id)
        )).scalars().all()

    async def _generate_global(self, db: AsyncSession, user: User, *, kind: str, schema,
                               model: str, instruction: str,
                               extra_context: str = "") -> GuruReport:
        provider = self._require_provider()
        lock = self._lock(kind)
        if lock.locked():
            raise GenerationInProgress(kind)
        async with lock:
            profile = await self._profile(db, user)
            portfolios = await self._all_portfolios(db, user)
            ctx = await build_context(db, user, quote_service=self.quotes, fx=self.fx,
                                      portfolios=portfolios, profile=profile)
            content = instruction + "\n\n" + json.dumps(ctx) + extra_context
            payload, usage = await provider.generate_structured(
                system=PERSONA_V1, messages=[{"role": "user", "content": content}],
                schema=schema, model=model, max_tokens=2048)
            report = GuruReport(user_id=user.id, kind=kind, portfolio_id=None,
                                payload=payload.model_dump(), model=model, created_at=_now())
            db.add(report)
            await db.flush()
            await usage_mod.record_usage(db, user_id=user.id, mode=kind, model=model,
                                         usage=usage, report_id=report.id)
            await db.commit()
            return report

    async def _latest_digest(self, db: AsyncSession, user: User) -> GuruReport | None:
        return (await db.execute(
            select(GuruReport).where(GuruReport.user_id == user.id, GuruReport.kind == "digest")
            .order_by(GuruReport.created_at.desc(), GuruReport.id.desc()).limit(1)
        )).scalar_one_or_none()

    async def generate_digest(self, db: AsyncSession, user: User) -> GuruReport:
        return await self._generate_global(
            db, user, kind="digest", schema=DigestPayload, model=settings.guru_scan_model,
            instruction="Produce this morning's digest: earnings this week, notable movers, "
                        "flagged news. One-line commentary per item.")

    async def generate_take(self, db: AsyncSession, user: User) -> GuruReport:
        digest = await self._latest_digest(db, user)
        extra_context = (
            "\n\nLatest daily digest:\n" + json.dumps(digest.payload)
            if digest is not None else "")
        return await self._generate_global(
            db, user, kind="take", schema=TakePayload, model=settings.guru_advice_model,
            instruction="Give your portfolio-level take: what moved and why, key risks vs the "
                        "investor's profile, and rebalance ideas with conviction.",
            extra_context=extra_context)


_service: GuruService | None = None


def get_guru_service() -> GuruService:
    global _service
    if _service is None:
        # Mirror app.services.signals.engine.get_engine: obtain the shared QuoteService
        # singleton and reuse its underlying provider for FxService, rather than
        # constructing a second YahooProvider.
        from app.services.market_data.quotes import get_quote_service

        provider = (AnthropicProvider(settings.anthropic_api_key)
                    if settings.anthropic_api_key else None)
        qs = get_quote_service()
        _service = GuruService(provider, qs, FxService(qs.provider))
    return _service
