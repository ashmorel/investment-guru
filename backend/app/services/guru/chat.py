import json
from collections.abc import AsyncIterator

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.portfolios import get_owned_portfolio
from app.core.config import settings
from app.models import ChatMessage, ChatThread, User
from app.services.guru import usage as usage_mod
from app.services.guru.budget import check_budget
from app.services.guru.context import build_context
from app.services.guru.llm.base import LLMError, LLMProvider, Usage
from app.services.guru.persona import PERSONA_V1
from app.services.guru.service import GuruService, _now
from app.services.orso.context import build_orso_context
from app.services.orso.prices import OrsoPriceService
from app.services.valuation import FxService

_HISTORY_LIMIT = 20


class ChatService:
    """Turn-taking chat over a ChatThread, reusing GuruService's provider/quotes/fx."""

    def __init__(self, guru: GuruService):
        self.guru = guru

    async def stream_turn(
        self, db: AsyncSession, user: User, thread: ChatThread, content: str,
        price_service: OrsoPriceService | None = None,
        fx_service: FxService | None = None,
    ) -> AsyncIterator[dict]:
        # Resolved eagerly (not lazily inside the generator below) so LLMNotConfigured
        # and BudgetExhausted are raised the moment this coroutine is awaited/created —
        # before any streaming or persistence happens. See app/api/guru.py::post_chat_message.
        provider = self.guru._require_provider()
        await check_budget(db, user.id)
        return self._stream(provider, db, user, thread, content, price_service, fx_service)

    async def _stream(
        self, provider: LLMProvider, db: AsyncSession, user: User,
        thread: ChatThread, content: str,
        price_service: OrsoPriceService | None = None,
        fx_service: FxService | None = None,
    ) -> AsyncIterator[dict]:
        user_msg = ChatMessage(thread_id=thread.id, role="user", content=content,
                               created_at=_now())
        db.add(user_msg)
        await db.commit()

        system, messages = await self._build_messages(
            db, user, thread, price_service, fx_service)
        stream = provider.stream_text(system=system, messages=messages,
                                      model=settings.guru_advice_model, max_tokens=2048)
        parts: list[str] = []
        try:
            async for chunk in stream:
                parts.append(chunk)
                yield {"event": "delta", "data": {"text": chunk}}
        except LLMError:
            yield {"event": "error", "data": {"detail": "llm_error"}}
            return

        assistant = ChatMessage(thread_id=thread.id, role="assistant",
                                content="".join(parts), created_at=_now())
        db.add(assistant)
        await db.flush()
        usage = stream.usage or Usage(input_tokens=0, output_tokens=0)
        await usage_mod.record_usage(db, user_id=user.id, mode="chat",
                                     model=settings.guru_advice_model, usage=usage,
                                     thread_id=thread.id)
        await db.commit()
        yield {"event": "done", "data": {"message_id": assistant.id,
                                         "input_tokens": usage.input_tokens,
                                         "output_tokens": usage.output_tokens}}

    async def _build_messages(
        self, db: AsyncSession, user: User, thread: ChatThread,
        price_service: OrsoPriceService | None = None,
        fx_service: FxService | None = None,
    ) -> tuple[str, list[dict]]:
        system = PERSONA_V1
        if thread.seed_context is not None:
            system += ("\n\nThe user opened this chat to discuss: "
                       + json.dumps(thread.seed_context))

        if thread.scope == "orso":
            # portfolio_id is ignored for orso-scoped threads: the context is the
            # ORSO fund menu/allocation/projection, not a portfolio valuation.
            # price_service/fx_service must be caller-supplied (see
            # app/api/guru.py::post_chat_message) -- never let this fall back to
            # None here, as build_orso_context's own None-fallback constructs a
            # live Yahoo-backed FxService.
            ctx = await build_orso_context(db, user, price_service, fx_service)
        else:
            if thread.portfolio_id is not None:
                portfolios = [await get_owned_portfolio(db, user, thread.portfolio_id)]
            else:
                portfolios = await self.guru._all_portfolios(db, user)
            profile = await self.guru._profile(db, user)
            ctx = await build_context(db, user, quote_service=self.guru.quotes,
                                      fx=self.guru.fx, portfolios=portfolios,
                                      profile=profile)

        rows = (await db.execute(
            select(ChatMessage).where(ChatMessage.thread_id == thread.id)
            .order_by(ChatMessage.created_at.desc(), ChatMessage.id.desc())
            .limit(_HISTORY_LIMIT)
        )).scalars().all()
        rows = list(reversed(rows))

        messages = [{"role": r.role, "content": r.content} for r in rows]

        # Anthropic's Messages API requires messages[0].role == "user". The naive
        # last-N window can begin with an assistant turn (or, after a failed stream
        # leaves consecutive user rows, could even need more than one message
        # trimmed) -- do not assume strict alternation, just drop leading
        # non-user messages until the window starts on a user turn.
        while messages and messages[0]["role"] != "user":
            messages.pop(0)

        for m in messages:
            if m["role"] == "user":
                m["content"] = json.dumps(ctx) + "\n\n" + m["content"]
                break

        return system, messages
