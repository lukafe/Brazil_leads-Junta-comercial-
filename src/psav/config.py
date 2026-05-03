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
    gemini_api_key: str = ""
    gemini_model: str = "gemini-2.5-flash"
    gemini_search_grounding: bool = True
    enrichment_cache_path: Path = Field(default=Path("./data/enrichment_cache.json"))
    enrichment_max_concurrency: int = 3
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
