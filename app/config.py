from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict

# Used as the owner of every row when AUTH_ENABLED is false, so local development needs
# no login and the user_id column still behaves exactly as it will in production.
LOCAL_USER_ID = "local"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql+psycopg://pm:pm@localhost:5433/portfolio"

    auth_enabled: bool = False
    supabase_url: str = ""
    supabase_anon_key: str = ""
    supabase_jwt_secret: str = ""

    refresh_token: str = "dev-refresh-token"
    price_cache_ttl_minutes: int = 15


@lru_cache
def settings() -> Settings:
    return Settings()
