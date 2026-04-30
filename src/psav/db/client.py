"""Singleton Supabase client (service role — backend only)."""

from psav.config import get_settings
from psav.exceptions import ConfigError
from supabase import Client, create_client

_client: Client | None = None


def get_supabase() -> Client:
    """Return a Supabase client authenticated with the service role.

    NEVER expose the result of this function to any client-side surface:
    the service role bypasses Row-Level Security.
    """
    global _client
    if _client is not None:
        return _client

    settings = get_settings()
    if not settings.supabase_url or not settings.supabase_service_role_key:
        raise ConfigError(
            "SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY are required. See .env.example."
        )

    _client = create_client(settings.supabase_url, settings.supabase_service_role_key)
    return _client
