from __future__ import annotations

from functools import lru_cache

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Used as the owner of every row when AUTH_ENABLED is false, so local development needs
# no login and the user_id column still behaves exactly as it will in production.
LOCAL_USER_ID = "local"

# Supabase's dashboard labels one field "API URL" and its value carries a service path
# already: https://<ref>.supabase.co/rest/v1. Paste that in and every URL the app builds
# doubles up (/rest/v1/rest/v1/...), which surfaces as a baffling 404 or 401 rather than
# anything pointing at the real cause. Strip the service path back off.
SERVICE_PATHS = ("/rest/v1", "/auth/v1", "/storage/v1", "/realtime/v1", "/functions/v1")


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql+psycopg://pm:pm@localhost:5433/portfolio"

    auth_enabled: bool = False
    supabase_url: str = ""
    supabase_anon_key: str = ""
    supabase_jwt_secret: str = ""

    @field_validator("supabase_url")
    @classmethod
    def _project_root_only(cls, value: str) -> str:
        value = value.strip().rstrip("/")
        for path in SERVICE_PATHS:
            if value.endswith(path):
                value = value[: -len(path)]
        return value.rstrip("/")

    refresh_token: str = "dev-refresh-token"
    price_cache_ttl_minutes: int = 15


@lru_cache
def settings() -> Settings:
    return Settings()
