from openai import AsyncOpenAI

from app.services.guru.llm.base import LLMError, LLMProvider, TextStream, Usage


def _to_openai_messages(system: str, messages: list[dict]) -> list[dict]:
    """Translate Anthropic-style messages (+ separate system string) into
    OpenAI chat messages. Text blocks pass through; Anthropic base64 image
    blocks become data-URL image_url parts."""
    out: list[dict] = [{"role": "system", "content": system}]
    for m in messages:
        content = m["content"]
        if isinstance(content, str):
            out.append({"role": m["role"], "content": content})
            continue
        parts = []
        for block in content:
            if block.get("type") == "text":
                parts.append({"type": "text", "text": block["text"]})
            elif block.get("type") == "image":
                src = block["source"]
                url = f"data:{src['media_type']};base64,{src['data']}"
                parts.append({"type": "image_url", "image_url": {"url": url}})
        out.append({"role": m["role"], "content": parts})
    return out


class OpenAIProvider(LLMProvider):
    def __init__(self, api_key: str):
        self._client = AsyncOpenAI(api_key=api_key)

    async def generate_structured(self, *, system, messages, schema, model, max_tokens):
        try:
            resp = await self._client.beta.chat.completions.parse(
                model=model, max_tokens=max_tokens,
                messages=_to_openai_messages(system, messages),
                response_format=schema,
            )
        except Exception as exc:  # SDK/network/validation errors → uniform LLMError
            raise LLMError(str(exc)) from exc
        msg = resp.choices[0].message
        if getattr(msg, "refusal", None):
            raise LLMError(f"model refused: {msg.refusal}")
        if msg.parsed is None:
            raise LLMError("model returned no parseable output")
        u = resp.usage
        return msg.parsed, Usage(u.prompt_tokens, u.completion_tokens)

    def stream_text(self, *, system, messages, model, max_tokens) -> TextStream:
        client, translate = self._client, _to_openai_messages
        stream_holder: list[TextStream] = []

        async def gen():
            try:
                stream = await client.chat.completions.create(
                    model=model, max_tokens=max_tokens,
                    messages=translate(system, messages), stream=True,
                    stream_options={"include_usage": True},
                )
                usage = None
                async for chunk in stream:
                    if chunk.usage is not None:
                        usage = chunk.usage
                    if chunk.choices and chunk.choices[0].delta.content:
                        yield chunk.choices[0].delta.content
                if usage is not None:
                    stream_holder[0].usage = Usage(usage.prompt_tokens, usage.completion_tokens)
            except LLMError:
                raise
            except Exception as exc:
                raise LLMError(str(exc)) from exc

        stream = TextStream(gen())
        stream_holder.append(stream)
        return stream
