import pytest
from pydantic import BaseModel

from app.services.guru.llm.base import LLMError
from app.services.guru.llm.google import GoogleProvider, _to_google_contents


class _Schema(BaseModel):
    verdict: str


def test_translates_text_and_image_parts():
    msgs = [{"role": "user", "content": [
        {"type": "text", "text": "hi"},
        {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": "QUFB"}},
    ]}]
    turns = _to_google_contents(msgs)
    assert len(turns) == 1 and turns[0]["role"] == "user"
    parts = turns[0]["parts"]
    kinds = {p["kind"] for p in parts}          # helper returns a neutral, inspectable shape
    assert kinds == {"text", "image"}
    img = next(p for p in parts if p["kind"] == "image")
    assert img["mime_type"] == "image/png" and img["data"] == b"AAA"   # base64 QUFB -> b"AAA"


def test_preserves_role_per_turn_multi_turn():
    turns = _to_google_contents([
        {"role": "user", "content": "a"},
        {"role": "assistant", "content": "b"},
        {"role": "user", "content": "c"},
    ])
    # Multi-turn conversation must survive as 3 distinct turns, not collapse into one.
    assert [t["role"] for t in turns] == ["user", "model", "user"]   # assistant -> "model"
    assert [t["parts"][0]["text"] for t in turns] == ["a", "b", "c"]


@pytest.mark.asyncio(loop_scope="session")
async def test_generate_structured_parses_and_usage(monkeypatch):
    prov = GoogleProvider("g-key")

    class _UM:
        prompt_token_count = 9
        candidates_token_count = 4

    class _Resp:
        parsed = _Schema(verdict="ok")
        usage_metadata = _UM()

    async def fake_gen(**kwargs):
        assert kwargs["model"] == "gemini-2.5-pro"
        return _Resp()
    monkeypatch.setattr(prov._client.aio.models, "generate_content", fake_gen)

    payload, usage = await prov.generate_structured(
        system="s", messages=[{"role": "user", "content": "x"}],
        schema=_Schema, model="gemini-2.5-pro", max_tokens=100)
    assert payload.verdict == "ok"
    assert usage.input_tokens == 9 and usage.output_tokens == 4


@pytest.mark.asyncio(loop_scope="session")
async def test_generate_structured_handles_missing_usage(monkeypatch):
    prov = GoogleProvider("g-key")

    class _Resp:
        parsed = _Schema(verdict="ok")
        usage_metadata = None   # SDK marks usage_metadata Optional — must not raise

    async def fake_gen(**kwargs):
        return _Resp()
    monkeypatch.setattr(prov._client.aio.models, "generate_content", fake_gen)

    payload, usage = await prov.generate_structured(
        system="s", messages=[{"role": "user", "content": "x"}],
        schema=_Schema, model="gemini-2.5-pro", max_tokens=100)
    assert payload.verdict == "ok"
    assert usage.input_tokens == 0 and usage.output_tokens == 0


@pytest.mark.asyncio(loop_scope="session")
async def test_wraps_errors(monkeypatch):
    prov = GoogleProvider("g-key")

    async def boom(**kwargs):
        raise RuntimeError("quota")
    monkeypatch.setattr(prov._client.aio.models, "generate_content", boom)
    with pytest.raises(LLMError):
        await prov.generate_structured(system="s", messages=[{"role": "user", "content": "x"}],
                                       schema=_Schema, model="gemini-2.5-pro", max_tokens=100)


@pytest.mark.asyncio(loop_scope="session")
async def test_generate_structured_scrubs_key_and_breaks_chain(monkeypatch):
    """A provider error whose message embeds a key (e.g. genai's ?key=<full key>
    query param) must never leak the key, and the raw SDK exception must not
    remain in __cause__ (traceback-safe)."""
    prov = GoogleProvider("g-key")

    async def boom(**kwargs):
        raise RuntimeError("auth failed key=AIzaSECRET123 sk-supersecret456")
    monkeypatch.setattr(prov._client.aio.models, "generate_content", boom)
    with pytest.raises(LLMError) as excinfo:
        await prov.generate_structured(system="s", messages=[{"role": "user", "content": "x"}],
                                       schema=_Schema, model="gemini-2.5-pro", max_tokens=100)
    err = excinfo.value
    assert "AIzaSECRET123" not in str(err)
    assert "sk-supersecret456" not in str(err)
    assert err.__cause__ is None
    assert err.__suppress_context__ is True
