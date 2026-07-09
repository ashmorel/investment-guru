from pydantic import BaseModel

from app.services.guru.llm.base import LLMError, LLMProvider, TextStream, Usage

_FIXED_USAGE = Usage(input_tokens=100, output_tokens=50)


class FakeLLMProvider(LLMProvider):
    def __init__(self):
        self.structured_queue: list[BaseModel] = []
        self.stream_chunks: list[str] = ["Hello ", "from the Guru."]
        self.fail_structured = 0
        self.fail_stream = False
        self.calls: list[dict] = []

    async def generate_structured(self, *, system, messages, schema, model, max_tokens):
        self.calls.append({"kind": "structured", "system": system,
                           "messages": messages, "model": model, "max_tokens": max_tokens})
        if self.fail_structured > 0:
            self.fail_structured -= 1
            raise LLMError("injected failure")
        assert self.structured_queue, "FakeLLMProvider queue empty — test forgot to seed it"
        result = self.structured_queue.pop(0)
        assert isinstance(result, schema), f"queued {type(result)} != requested {schema}"
        return result, _FIXED_USAGE

    def stream_text(self, *, system, messages, model, max_tokens) -> TextStream:
        self.calls.append({"kind": "stream", "system": system,
                           "messages": messages, "model": model, "max_tokens": max_tokens})
        stream_holder: list[TextStream] = []

        async def gen():
            if self.fail_stream:
                raise LLMError("injected stream failure")
            for chunk in self.stream_chunks:
                yield chunk
            stream_holder[0].usage = _FIXED_USAGE

        stream = TextStream(gen())
        stream_holder.append(stream)
        return stream
