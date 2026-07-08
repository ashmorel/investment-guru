from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql+asyncpg://guru:guru@localhost:5433/guru"
    secret_key: str = "dev-secret-not-for-production"
    initial_user_email: str = "you@example.com"
    initial_user_password: str = "change-me"

    anthropic_api_key: str = ""
    guru_advice_model: str = "claude-opus-4-8"
    guru_scan_model: str = "claude-haiku-4-5"
    guru_digest_hour: int = 7
    guru_timezone: str = "Europe/London"


settings = Settings()
