"""Project configuration via pydantic-settings (reads .env)."""

from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # Supabase
    supabase_url: str = ""
    supabase_anon_key: str = ""
    supabase_service_role_key: str = ""
    supabase_db_url: str = ""

    # External APIs
    anthropic_api_key: str = ""
    apify_token: str = ""
    serpapi_key: str = ""
    tavily_api_key: str = ""
    casa_dos_dados_token: str = ""

    # Notifications
    slack_webhook_url: str = ""
    resend_api_key: str = ""

    # General
    environment: str = "development"
    log_level: str = "INFO"
    receita_dump_dir: Path = Field(default=Path("./data/receita"))

    @property
    def is_production(self) -> bool:
        return self.environment.lower() == "production"


_settings: Settings | None = None


def get_settings() -> Settings:
    """Singleton — avoids re-reading .env on each call."""
    global _settings
    if _settings is None:
        _settings = Settings()
    return _settings
