from datetime import UTC, datetime
from decimal import Decimal

import pytest

from app.models import LlmConfig
from app.services.guru.config import load_active_config

pytestmark = pytest.mark.asyncio(loop_scope="session")


async def test_load_config_env_fallback_when_no_row(db_session, monkeypatch):
    from app.core.config import settings
    monkeypatch.setattr(settings, "anthropic_api_key", "env-key")
    monkeypatch.setattr(settings, "guru_advice_model", "claude-opus-4-8")
    monkeypatch.setattr(settings, "guru_scan_model", "claude-haiku-4-5")
    cfg = await load_active_config(db_session)
    assert cfg.provider == "anthropic"
    assert cfg.api_key == "env-key"
    assert cfg.advice_model == "claude-opus-4-8"
    assert cfg.scan_model == "claude-haiku-4-5"
    assert cfg.advice_price is None and cfg.scan_price is None


async def test_load_config_row_is_authoritative_and_key_encrypted(db_session):
    db_session.add(LlmConfig(
        provider="openai", advice_model="gpt-4o", scan_model="gpt-4o-mini",
        api_key="sk-secret", advice_input_price=Decimal("2.5"),
        advice_output_price=Decimal("10"), updated_at=datetime.now(UTC).replace(tzinfo=None)))
    await db_session.commit()

    cfg = await load_active_config(db_session)
    assert cfg.provider == "openai" and cfg.api_key == "sk-secret"
    assert cfg.advice_model == "gpt-4o" and cfg.scan_model == "gpt-4o-mini"
    assert cfg.advice_price == (Decimal("2.5"), Decimal("10"))
    assert cfg.scan_price is None   # only one side priced -> None

    # api_key is ciphertext at rest, not plaintext
    from sqlalchemy import text
    raw = (await db_session.execute(text("SELECT api_key FROM llm_config LIMIT 1"))).scalar_one()
    assert raw.startswith("v1:") and "sk-secret" not in raw
