from decimal import Decimal

import pytest
from pydantic import BaseModel

from app.services.guru.llm.base import LLMError, TextStream, Usage
from app.services.guru.llm.fake import FakeLLMProvider
from app.services.guru.usage import estimate_cost, record_usage


class Out(BaseModel):
    answer: str


@pytest.mark.asyncio(loop_scope="session")
async def test_fake_structured_returns_queued_payload_and_usage():
    fake = FakeLLMProvider()
    fake.structured_queue.append(Out(answer="42"))
    result, usage = await fake.generate_structured(
        system="s", messages=[{"role": "user", "content": "q"}],
        schema=Out, model="m", max_tokens=100,
    )
    assert result.answer == "42"
    assert usage == Usage(input_tokens=100, output_tokens=50)
    assert fake.calls[0]["model"] == "m"


@pytest.mark.asyncio(loop_scope="session")
async def test_fake_structured_failure_injection():
    fake = FakeLLMProvider()
    fake.fail_structured = 1
    fake.structured_queue.append(Out(answer="ok"))
    with pytest.raises(LLMError):
        await fake.generate_structured(system="s", messages=[], schema=Out,
                                       model="m", max_tokens=10)
    result, _ = await fake.generate_structured(system="s", messages=[], schema=Out,
                                               model="m", max_tokens=10)
    assert result.answer == "ok"


@pytest.mark.asyncio(loop_scope="session")
async def test_fake_stream_yields_chunks_then_usage():
    fake = FakeLLMProvider()
    fake.stream_chunks = ["Hel", "lo"]
    stream = fake.stream_text(system="s", messages=[], model="m", max_tokens=10)
    assert isinstance(stream, TextStream)
    text = "".join([chunk async for chunk in stream])
    assert text == "Hello"
    assert stream.usage == Usage(input_tokens=100, output_tokens=50)


def test_estimate_cost_known_and_unknown_models():
    u = Usage(input_tokens=1_000_000, output_tokens=1_000_000)
    assert estimate_cost("claude-opus-4-8", u) == Decimal("30")
    assert estimate_cost("claude-haiku-4-5", u) == Decimal("6")
    assert estimate_cost("mystery-model", u) is None


@pytest.mark.asyncio(loop_scope="session")
async def test_record_usage_persists_row(db_session):
    from app.models import User
    user = User(email="u@test.dev", password_hash="x")
    db_session.add(user)
    await db_session.commit()
    row = await record_usage(db_session, user_id=user.id, mode="digest",
                             model="claude-haiku-4-5",
                             usage=Usage(input_tokens=10, output_tokens=5))
    await db_session.commit()
    assert row.id is not None
    assert row.est_cost_usd is not None
