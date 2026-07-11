import json
from decimal import Decimal
from typing import Annotated

from pydantic import field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql+asyncpg://guru:guru@localhost:5433/guru"
    secret_key: str = "dev-secret-not-for-production"
    data_encryption_key: str = ""
    initial_user_email: str = "you@example.com"
    initial_user_password: str = "change-me"

    anthropic_api_key: str = ""
    guru_advice_model: str = "claude-opus-4-8"
    guru_scan_model: str = "claude-haiku-4-5"
    guru_digest_hour: int = 7
    guru_timezone: str = "Europe/London"
    guru_daily_budget_usd: Decimal = Decimal("1.00")

    # Discovery (Phase 4 Task 2) confirmed a stable, unauthenticated public JSON
    # endpoint for HSBC WMFS ORSO fund prices — see app/services/orso/prices.py.
    orso_price_fetch_enabled: bool = True

    # HSBC WMFS unit-price widget's own front-end gateway headers (public,
    # browser-visible — not a privileged credential). Empty defaults mean "no
    # provider configured". See app/services/orso/prices.py for how to find
    # the current values.
    orso_hsbc_client_id: str = ""
    orso_hsbc_client_secret: str = ""

    env: str = "dev"
    # NoDecode: pydantic-settings would otherwise require this env var to be
    # valid JSON (`ADMIN_EMAILS=["a@x.com"]`) and hard-error on plain CSV
    # before our validator ever runs. Skipping its decode hands the raw
    # string to `_parse_admin_emails` below instead.
    admin_emails: Annotated[list[str], NoDecode] = ["lee_ashmore@hotmail.co.uk"]

    @field_validator("database_url")
    @classmethod
    def _normalise_db_url(cls, v: str) -> str:
        if v.startswith("postgres://"):
            return "postgresql+asyncpg://" + v[len("postgres://"):]
        if v.startswith("postgresql://") and not v.startswith("postgresql+asyncpg://"):
            return "postgresql+asyncpg://" + v[len("postgresql://"):]
        return v

    @field_validator("admin_emails", mode="before")
    @classmethod
    def _parse_admin_emails(cls, v):
        # Accept a plain comma-separated string, the operator-friendly form
        # (`ADMIN_EMAILS=a@x.com,b@x.com`), a JSON array string, or an
        # already-parsed list (the Python-side default, or a constructor
        # kwarg) — whatever shows up once NoDecode has skipped
        # pydantic-settings' default JSON-only env parsing.
        if isinstance(v, str):
            stripped = v.strip()
            if stripped.startswith("["):
                try:
                    return json.loads(stripped)
                except json.JSONDecodeError:
                    pass
            return [email.strip() for email in stripped.split(",") if email.strip()]
        return v

    @property
    def is_production(self) -> bool:
        return self.env == "production"


settings = Settings()
