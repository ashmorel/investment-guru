import base64

from google import genai
from google.genai import types

from app.services.guru.llm.base import LLMError, LLMProvider, TextStream, Usage

# Anthropic/OpenAI-style roles → Gemini roles. Gemini only knows "user"/"model".
_ROLE_MAP = {"user": "user", "assistant": "model", "model": "model"}


def _to_google_contents(messages: list[dict]) -> list[dict]:
    """Flatten Anthropic-style messages into a neutral, per-turn part list that
    PRESERVES message role (so multi-turn conversations survive):
    [{"role": "user"|"model", "parts": [
        {"kind": "text", "text": ...} | {"kind": "image", "mime_type": ..., "data": <bytes>}]}].
    """
    turns: list[dict] = []
    for m in messages:
        role = _ROLE_MAP.get(m["role"], "user")
        content = m["content"]
        parts: list[dict] = []
        if isinstance(content, str):
            parts.append({"kind": "text", "text": content})
        else:
            for block in content:
                if block.get("type") == "text":
                    parts.append({"kind": "text", "text": block["text"]})
                elif block.get("type") == "image":
                    src = block["source"]
                    parts.append({"kind": "image", "mime_type": src["media_type"],
                                  "data": base64.b64decode(src["data"])})
        turns.append({"role": role, "parts": parts})
    return turns


def _to_contents(neutral: list[dict]) -> list:
    """Map the neutral per-turn list to real `types.Content` objects, one per turn."""
    out = []
    for turn in neutral:
        parts = []
        for p in turn["parts"]:
            if p["kind"] == "text":
                parts.append(types.Part.from_text(text=p["text"]))
            else:
                parts.append(types.Part.from_bytes(data=p["data"], mime_type=p["mime_type"]))
        out.append(types.Content(role=turn["role"], parts=parts))
    return out


class GoogleProvider(LLMProvider):
    def __init__(self, api_key: str):
        self._client = genai.Client(api_key=api_key)

    async def generate_structured(self, *, system, messages, schema, model, max_tokens):
        try:
            resp = await self._client.aio.models.generate_content(
                model=model, contents=_to_contents(_to_google_contents(messages)),
                config=types.GenerateContentConfig(
                    system_instruction=system, max_output_tokens=max_tokens,
                    response_mime_type="application/json", response_schema=schema),
            )
            if resp.parsed is None:
                raise LLMError("model returned no parseable output")
            um = getattr(resp, "usage_metadata", None)
            usage = (Usage(um.prompt_token_count or 0, um.candidates_token_count or 0)
                     if um is not None else Usage(0, 0))
            return resp.parsed, usage
        except LLMError:
            raise
        except Exception as exc:  # SDK/network/validation errors → uniform LLMError
            raise LLMError(str(exc)) from exc

    def stream_text(self, *, system, messages, model, max_tokens) -> TextStream:
        client = self._client
        stream_holder: list[TextStream] = []

        async def gen():
            try:
                stream = await client.aio.models.generate_content_stream(
                    model=model, contents=_to_contents(_to_google_contents(messages)),
                    config=types.GenerateContentConfig(
                        system_instruction=system, max_output_tokens=max_tokens),
                )
                usage = None
                async for chunk in stream:
                    if getattr(chunk, "usage_metadata", None):
                        usage = chunk.usage_metadata
                    if chunk.text:
                        yield chunk.text
                if usage is not None:
                    stream_holder[0].usage = Usage(
                        usage.prompt_token_count or 0, usage.candidates_token_count or 0)
            except LLMError:
                raise
            except Exception as exc:
                raise LLMError(str(exc)) from exc

        stream = TextStream(gen())
        stream_holder.append(stream)
        return stream
