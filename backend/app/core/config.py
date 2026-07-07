from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql+asyncpg://guru:guru@localhost:5433/guru"
    secret_key: str = "dev-secret-not-for-production"
    initial_user_email: str = "you@example.com"
    initial_user_password: str = "change-me"


settings = Settings()
