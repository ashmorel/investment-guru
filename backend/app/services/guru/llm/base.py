from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from dataclasses import dataclass

from pydantic import BaseModel


@dataclass(frozen=True)
class Usage:
    input_tokens: int
    output_tokens: int


class LLMError(Exception):
    """Provider failed or returned unusable output."""


class LLMNotConfigured(LLMError):
    """No API key configured — Guru features unavailable."""


class TextStream:
    """Async iterator of text chunks; .usage is set once the stream completes."""

    def __init__(self, gen: AsyncIterator[str]):
        self._gen = gen
        self.usage: Usage | None = None

    def __aiter__(self) -> AsyncIterator[str]:
        return self._gen.__aiter__()


class LLMProvider(ABC):
    @abstractmethod
    async def generate_structured(
        self, *, system: str, messages: list[dict], schema: type[BaseModel],
        model: str, max_tokens: int,
    ) -> tuple[BaseModel, Usage]: ...

    @abstractmethod
    def stream_text(
        self, *, system: str, messages: list[dict], model: str, max_tokens: int,
    ) -> TextStream: ...
