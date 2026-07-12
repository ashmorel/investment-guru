from datetime import UTC, datetime

import pytest

from app.models import LlmConfig
from app.services.guru import service as service_mod

pytestmark = pytest.mark.asyncio(loop_scope="session")


async def test_factory_rebuilds_provider_from_config_on_invalidate(db_session, monkeypatch):
    from app.core.config import settings
    monkeypatch.setattr(settings, "anthropic_api_key", "env-key")
    service_mod.invalidate_guru_service()

    svc1 = await service_mod.get_guru_service(db_session)
    from app.services.guru.llm.anthropic import AnthropicProvider
    assert isinstance(svc1.provider, AnthropicProvider)  # env fallback
    assert svc1.advice_model == settings.guru_advice_model

    db_session.add(LlmConfig(provider="openai", advice_model="gpt-4o", scan_model="gpt-4o-mini",
                             api_key="sk-x", updated_at=datetime.now(UTC).replace(tzinfo=None)))
    await db_session.commit()
    service_mod.invalidate_guru_service()

    svc2 = await service_mod.get_guru_service(db_session)
    from app.services.guru.llm.openai import OpenAIProvider
    assert isinstance(svc2.provider, OpenAIProvider)
    assert svc2.advice_model == "gpt-4o" and svc2.scan_model == "gpt-4o-mini"
