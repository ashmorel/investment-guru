import asyncio
import json
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import GuruReport, Portfolio, User
from app.services.guru import usage as usage_mod
from app.services.guru.budget import check_budget
from app.services.guru.context import build_context
from app.services.guru.llm.base import LLMError, LLMNotConfigured, LLMProvider, Usage
from app.services.guru.persona import PERSONA_V1
from app.services.guru.schemas import (
    DigestPayload,
    NewsSummaryPayload,
    OrsoAdvicePayload,
    ReviewPayload,
    RotationAdvicePayload,
    TakePayload,
)
from app.services.market_data.quotes import QuoteService
from app.services.orso.prices import OrsoPriceService
from app.services.valuation import FxService

_ORSO_INSTRUCTION = (
    "Advise on this ORSO pension. Only reference fund codes from the fund menu "
    "provided. Give a verdict for every fund currently holding units and a concrete "
    "switch plan. Then, using goal_gap (shortfall/surplus vs the target pot per growth "
    "scenario, in display_currency) and monthly_contribution, give a "
    "contribution_suggestion: a concrete, specific lever to close any gap — e.g. a "
    "revised monthly contribution figure and/or an allocation shift by asset class — "
    "framed as general guidance, not licensed financial advice. Comment on the "
    "projection in projection_comment."
)


_NEWS_INSTRUCTION = (
    "Summarize the recent news for this stock for a retail investor. Return a 2-3 "
    "sentence plain-English summary, a single overall sentiment "
    "(positive/negative/neutral/watch), the key points as short bullets, and a one-line "
    "disclaimer that this is general information, not advice. Base it ONLY on the "
    "headlines provided."
)


def _orso_invalid_codes(payload: OrsoAdvicePayload, fund_menu: set[str]) -> set[str]:
    codes = {v.code for v in payload.fund_verdicts}
    codes |= {s.from_code for s in payload.switch_plan if s.from_code is not None}
    codes |= {s.to_code for s in payload.switch_plan if s.to_code is not None}
    return codes - fund_menu


_ROTATION_INSTRUCTION = (
    "Give a sector/theme ROTATION view across the user's holding groups. Reason ONLY "
    "from the grounding context provided (weights, drift, momentum, news themes, "
    "profile) — do NOT invent live prices, rates, or any figures not in the context; "
    "if the data doesn't support a call, say so in caveats instead of guessing. In "
    "market_view give a short, explicitly-hedged read on how the groups are positioned "
    "now. For every group give an observation and a signal (favour/trim/hold). In "
    "rotations, suggest directional shifts between groups the user actually has "
    "(from_group -> to_group) with a plain rationale and conviction — DIRECTIONAL ONLY: "
    "never state amounts, share counts, or specific prices, and never give a specific "
    "trade instruction. Record thin history / sparse news / high uncertainty in "
    "caveats. Always include the disclaimer that this is general educational "
    "information, not regulated financial advice."
)


def _rotation_invalid_groups(payload: RotationAdvicePayload, group_names: set[str]) -> set[str]:
    names = {r.from_group for r in payload.rotations} | {r.to_group for r in payload.rotations}
    return names - group_names


class GenerationInProgress(Exception):
    pass


def _now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


class GuruService:
    def __init__(self, provider: LLMProvider | None, quotes: QuoteService, fx: FxService, *,
                 advice_model: str, scan_model: str,
                 advice_price: tuple | None = None, scan_price: tuple | None = None):
        self.provider = provider
        self.quotes = quotes
        self.fx = fx
        self.advice_model = advice_model
        self.scan_model = scan_model
        self.advice_price = advice_price
        self.scan_price = scan_price
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
            await check_budget(db, user.id)
            profile = await self._profile(db, user)
            ctx = await build_context(db, user, quote_service=self.quotes, fx=self.fx,
                                      portfolios=[portfolio], profile=profile)
            expected = {p.instrument.symbol for p in portfolio.positions}
            messages = [{"role": "user", "content":
                         "Review this portfolio. Give a verdict for EVERY position.\n\n"
                         + json.dumps(ctx)}]
            payload, usage = await provider.generate_structured(
                system=PERSONA_V1, messages=messages, schema=ReviewPayload,
                model=self.advice_model, max_tokens=4096)
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
                    model=self.advice_model, max_tokens=4096)
                usage = Usage(input_tokens=first_usage.input_tokens + usage.input_tokens,
                              output_tokens=first_usage.output_tokens + usage.output_tokens)
                missing = expected - {p.symbol for p in payload.positions}
                if missing:
                    raise LLMError(f"review still missing positions: {sorted(missing)}")
            report = GuruReport(user_id=user.id, kind="review", portfolio_id=portfolio.id,
                                payload=payload.model_dump(),
                                model=self.advice_model, created_at=_now())
            db.add(report)
            await db.flush()
            await usage_mod.record_usage(db, user_id=user.id, mode="review",
                                         model=self.advice_model,
                                         usage=usage, report_id=report.id,
                                         price=self.advice_price)
            await db.commit()
            return report

    async def generate_orso(self, db: AsyncSession, user: User,
                            price_service: OrsoPriceService,
                            fx_service: FxService | None = None) -> GuruReport:
        from app.services.orso.context import build_orso_context

        provider = self._require_provider()
        lock = self._lock("orso")
        if lock.locked():
            raise GenerationInProgress("orso")
        async with lock:
            await check_budget(db, user.id)
            ctx = await build_orso_context(db, user, price_service, fx_service)
            fund_menu = set(ctx["fund_menu"])
            messages = [{"role": "user", "content":
                         _ORSO_INSTRUCTION + "\n\n" + json.dumps(ctx)}]
            payload, usage = await provider.generate_structured(
                system=PERSONA_V1, messages=messages, schema=OrsoAdvicePayload,
                model=self.advice_model, max_tokens=4096)
            invalid = _orso_invalid_codes(payload, fund_menu)
            if invalid:
                messages += [
                    {"role": "assistant", "content": payload.model_dump_json()},
                    {"role": "user", "content":
                     f"These fund codes are not valid: {sorted(invalid)}. "
                     f"Allowed fund codes are: {sorted(fund_menu)}. Return the complete "
                     "advice again, using only allowed fund codes."},
                ]
                first_usage = usage
                payload, usage = await provider.generate_structured(
                    system=PERSONA_V1, messages=messages, schema=OrsoAdvicePayload,
                    model=self.advice_model, max_tokens=4096)
                usage = Usage(input_tokens=first_usage.input_tokens + usage.input_tokens,
                              output_tokens=first_usage.output_tokens + usage.output_tokens)
                invalid = _orso_invalid_codes(payload, fund_menu)
                if invalid:
                    raise LLMError(f"orso advice referenced invalid fund codes: {sorted(invalid)}")
            report = GuruReport(user_id=user.id, kind="orso", portfolio_id=None,
                                payload=payload.model_dump(),
                                model=self.advice_model, created_at=_now())
            db.add(report)
            await db.flush()
            await usage_mod.record_usage(db, user_id=user.id, mode="orso",
                                         model=self.advice_model,
                                         usage=usage, report_id=report.id,
                                         price=self.advice_price)
            await db.commit()
            return report

    async def generate_rotation(self, db: AsyncSession, user: User) -> GuruReport:
        from app.services.groups.rotation_context import build_rotation_context

        provider = self._require_provider()
        lock = self._lock("rotation")
        if lock.locked():
            raise GenerationInProgress("rotation")
        async with lock:
            await check_budget(db, user.id)
            ctx = await build_rotation_context(db, user, self.quotes, self.fx)
            group_names = {g["name"] for g in ctx["groups"]}
            messages = [{"role": "user", "content":
                         _ROTATION_INSTRUCTION + "\n\n" + json.dumps(ctx)}]
            payload, usage = await provider.generate_structured(
                system=PERSONA_V1, messages=messages, schema=RotationAdvicePayload,
                model=self.advice_model, max_tokens=4096)
            invalid = _rotation_invalid_groups(payload, group_names)
            if invalid:
                messages += [
                    {"role": "assistant", "content": payload.model_dump_json()},
                    {"role": "user", "content":
                     f"These group names are not valid: {sorted(invalid)}. Allowed groups "
                     f"are: {sorted(group_names)}. Return the complete rotation advice again, "
                     "using only these group names."},
                ]
                first_usage = usage
                payload, usage = await provider.generate_structured(
                    system=PERSONA_V1, messages=messages, schema=RotationAdvicePayload,
                    model=self.advice_model, max_tokens=4096)
                usage = Usage(input_tokens=first_usage.input_tokens + usage.input_tokens,
                              output_tokens=first_usage.output_tokens + usage.output_tokens)
                invalid = _rotation_invalid_groups(payload, group_names)
                if invalid:
                    raise LLMError(f"rotation advice referenced invalid groups: {sorted(invalid)}")
            report = GuruReport(user_id=user.id, kind="rotation", portfolio_id=None,
                                payload=payload.model_dump(), model=self.advice_model,
                                created_at=_now())
            db.add(report)
            await db.flush()
            await usage_mod.record_usage(db, user_id=user.id, mode="rotation",
                                         model=self.advice_model, usage=usage,
                                         report_id=report.id, price=self.advice_price)
            await db.commit()
            return report

    async def generate_news_summary(self, db: AsyncSession, user: User,
                                    instrument, headlines: list) -> GuruReport:
        provider = self._require_provider()
        lock = self._lock("news")
        if lock.locked():
            raise GenerationInProgress("news")
        async with lock:
            await check_budget(db, user.id)
            payload_in = [
                {"title": h.title, "source": h.source,
                 "published_at": (h.published_at or h.fetched_at).isoformat()}
                for h in headlines
            ]
            messages = [{"role": "user", "content":
                         f"{_NEWS_INSTRUCTION}\n\n"
                         f"Stock: {instrument.symbol} ({instrument.name})\n\n"
                         + json.dumps(payload_in)}]
            payload, usage = await provider.generate_structured(
                system=PERSONA_V1, messages=messages, schema=NewsSummaryPayload,
                model=self.scan_model, max_tokens=1024)
            report = GuruReport(user_id=user.id, kind="news", portfolio_id=None,
                                instrument_id=instrument.id, payload=payload.model_dump(),
                                model=self.scan_model, created_at=_now())
            db.add(report)
            await db.flush()
            await usage_mod.record_usage(db, user_id=user.id, mode="news",
                                         model=self.scan_model, usage=usage,
                                         report_id=report.id, price=self.scan_price)
            await db.commit()
            return report

    async def _all_portfolios(self, db: AsyncSession, user: User) -> list[Portfolio]:
        return (await db.execute(
            select(Portfolio).where(Portfolio.user_id == user.id).order_by(Portfolio.id)
        )).scalars().all()

    async def _generate_global(self, db: AsyncSession, user: User, *, kind: str, schema,
                               model: str, instruction: str, price: tuple | None = None,
                               extra_context: str = "", max_tokens: int = 2048) -> GuruReport:
        provider = self._require_provider()
        lock = self._lock(kind)
        if lock.locked():
            raise GenerationInProgress(kind)
        async with lock:
            await check_budget(db, user.id)
            profile = await self._profile(db, user)
            portfolios = await self._all_portfolios(db, user)
            ctx = await build_context(db, user, quote_service=self.quotes, fx=self.fx,
                                      portfolios=portfolios, profile=profile)
            content = instruction + "\n\n" + json.dumps(ctx) + extra_context
            payload, usage = await provider.generate_structured(
                system=PERSONA_V1, messages=[{"role": "user", "content": content}],
                schema=schema, model=model, max_tokens=max_tokens)
            report = GuruReport(user_id=user.id, kind=kind, portfolio_id=None,
                                payload=payload.model_dump(), model=model, created_at=_now())
            db.add(report)
            await db.flush()
            await usage_mod.record_usage(db, user_id=user.id, mode=kind, model=model,
                                         usage=usage, report_id=report.id, price=price)
            await db.commit()
            return report

    async def _latest_digest(self, db: AsyncSession, user: User) -> GuruReport | None:
        return (await db.execute(
            select(GuruReport).where(GuruReport.user_id == user.id, GuruReport.kind == "digest")
            .order_by(GuruReport.created_at.desc(), GuruReport.id.desc()).limit(1)
        )).scalar_one_or_none()

    async def generate_digest(self, db: AsyncSession, user: User) -> GuruReport:
        return await self._generate_global(
            db, user, kind="digest", schema=DigestPayload, model=self.scan_model,
            price=self.scan_price,
            instruction="Produce this morning's digest: earnings this week, notable movers, "
                        "flagged news. One-line commentary per item.")

    async def generate_take(self, db: AsyncSession, user: User) -> GuruReport:
        digest = await self._latest_digest(db, user)
        extra_context = (
            "\n\nLatest daily digest:\n" + json.dumps(digest.payload)
            if digest is not None else "")
        return await self._generate_global(
            db, user, kind="take", schema=TakePayload, model=self.advice_model,
            price=self.advice_price,
            instruction="Give your portfolio-level take: what moved and why, key risks vs the "
                        "investor's profile, and rebalance ideas with conviction.",
            extra_context=extra_context, max_tokens=4096)


_service: GuruService | None = None


async def get_guru_service(db: AsyncSession) -> GuruService:
    global _service
    if _service is None:
        from app.services.guru.config import load_active_config
        from app.services.guru.llm.factory import build_provider
        from app.services.market_data.quotes import get_quote_service

        cfg = await load_active_config(db)
        provider = build_provider(cfg.provider, cfg.api_key) if cfg.api_key else None
        # Mirror app.services.signals.engine.get_engine: obtain the shared QuoteService
        # singleton and reuse its underlying provider for FxService, rather than
        # constructing a second YahooProvider.
        qs = get_quote_service()
        _service = GuruService(
            provider, qs, FxService(qs.provider),
            advice_model=cfg.advice_model, scan_model=cfg.scan_model,
            advice_price=cfg.advice_price, scan_price=cfg.scan_price)
    return _service


def invalidate_guru_service() -> None:
    global _service
    _service = None
