from anthropic import AsyncAnthropic

from app.services.guru.llm.base import LLMError, LLMProvider, TextStream, Usage


class AnthropicProvider(LLMProvider):
    def __init__(self, api_key: str):
        self._client = AsyncAnthropic(api_key=api_key)

    async def generate_structured(self, *, system, messages, schema, model, max_tokens):
        try:
            resp = await self._client.messages.parse(
                model=model, max_tokens=max_tokens, system=system,
                messages=messages, output_format=schema,
            )
        except Exception as exc:  # SDK/network/validation errors → uniform LLMError
            raise LLMError(str(exc)) from exc
        if resp.parsed_output is None:
            raise LLMError("model returned no parseable output")
        usage = Usage(resp.usage.input_tokens, resp.usage.output_tokens)
        return resp.parsed_output, usage

    def stream_text(self, *, system, messages, model, max_tokens) -> TextStream:
        stream_holder: list[TextStream] = []

        async def gen():
            try:
                async with self._client.messages.stream(
                    model=model, max_tokens=max_tokens, system=system, messages=messages,
                ) as s:
                    async for text in s.text_stream:
                        yield text
                    final = await s.get_final_message()
                    stream_holder[0].usage = Usage(
                        final.usage.input_tokens, final.usage.output_tokens
                    )
            except LLMError:
                raise
            except Exception as exc:
                raise LLMError(str(exc)) from exc

        stream = TextStream(gen())
        stream_holder.append(stream)
        return stream
