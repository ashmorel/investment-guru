import pytest
from pydantic import BaseModel

from app.services.guru.llm.base import LLMError
from app.services.guru.llm.openai import OpenAIProvider, _to_openai_messages


class _Schema(BaseModel):
    verdict: str


def test_translates_text_and_image_blocks():
    msgs = [{"role": "user", "content": [
        {"type": "text", "text": "hi"},
        {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": "AAA"}},
    ]}]
    out = _to_openai_messages("SYS", msgs)
    assert out[0] == {"role": "system", "content": "SYS"}
    parts = out[1]["content"]
    assert {"type": "text", "text": "hi"} in parts
    img = next(p for p in parts if p["type"] == "image_url")
    assert img["image_url"]["url"] == "data:image/png;base64,AAA"


@pytest.mark.asyncio(loop_scope="session")
async def test_generate_structured_parses_and_reports_usage(monkeypatch):
    prov = OpenAIProvider("sk-test")

    class _Msg:
        parsed = _Schema(verdict="ok")
        refusal = None

    class _Choice:
        message = _Msg()

    class _Usage:
        prompt_tokens = 11
        completion_tokens = 7

    class _Resp:
        choices = [_Choice()]
        usage = _Usage()

    async def fake_parse(**kwargs):
        assert kwargs["model"] == "gpt-4o" and kwargs["response_format"] is _Schema
        return _Resp()
    monkeypatch.setattr(prov._client.beta.chat.completions, "parse", fake_parse)

    payload, usage = await prov.generate_structured(
        system="s", messages=[{"role": "user", "content": "x"}],
        schema=_Schema, model="gpt-4o", max_tokens=100)
    assert payload.verdict == "ok"
    assert usage.input_tokens == 11 and usage.output_tokens == 7


@pytest.mark.asyncio(loop_scope="session")
async def test_generate_structured_wraps_errors(monkeypatch):
    prov = OpenAIProvider("sk-test")

    async def boom(**kwargs):
        raise RuntimeError("api down")
    monkeypatch.setattr(prov._client.beta.chat.completions, "parse", boom)
    with pytest.raises(LLMError):
        await prov.generate_structured(system="s", messages=[{"role": "user", "content": "x"}],
                                       schema=_Schema, model="gpt-4o", max_tokens=100)
