from app.services.guru.llm.anthropic import AnthropicProvider
from app.services.guru.llm.base import LLMProvider
from app.services.guru.llm.google import GoogleProvider
from app.services.guru.llm.openai import OpenAIProvider


def build_provider(provider: str, api_key: str) -> LLMProvider:
    if provider == "anthropic":
        return AnthropicProvider(api_key)
    if provider == "openai":
        return OpenAIProvider(api_key)
    if provider == "google":
        return GoogleProvider(api_key)
    raise ValueError(f"unknown LLM provider: {provider!r}")
