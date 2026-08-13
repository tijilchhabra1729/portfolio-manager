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

    # --- LLM analyst (phase 2) ------------------------------------------------
    # Two providers behind one protocol. Groq (an OpenAI-shaped endpoint) is the free
    # tier for everyone; Claude is the premium tier. Which one runs is decided by the
    # user's plan AND which key is present — no key means the rule agents still run and
    # the LLM features degrade to a "set a key" message rather than erroring.
    groq_api_key: str = ""
    groq_model: str = "llama-3.3-70b-versatile"
    groq_base_url: str = "https://api.groq.com/openai/v1"

    # Gemini's free tier is far more generous on tokens-per-minute than Groq's, so it is
    # the preferred free provider for the fan-out briefing when a key is present. Its
    # OpenAI-compatible endpoint means the same OpenAICompatModel drives it — only the
    # base URL, key, and model differ.
    gemini_api_key: str = ""
    # `gemini-flash-latest` is an alias Google keeps pointed at the current free-tier Flash
    # model. The pinned names (gemini-2.0-flash, 2.5-flash) have had their free quota set to
    # 0 on many keys; the alias keeps working. Override GEMINI_MODEL if you have paid quota.
    gemini_model: str = "gemini-flash-latest"
    gemini_base_url: str = "https://generativelanguage.googleapis.com/v1beta/openai/"

    anthropic_api_key: str = ""
    anthropic_model: str = "claude-opus-4-8"

    # A newly-generated analyst insight is reused for this long before a button is
    # allowed to spend the LLM again — so clicking Analyze twice can't double-bill.
    analyst_cooldown_minutes: int = 30
    # A stock's fundamental analysis is cached this long, shared across all users
    # (fundamentals are public). A cache hit never bills and never counts toward a cap.
    analysis_ttl_hours: int = 6
    # Free plan: this many *fresh* Explore analyses per day. Cache hits are unlimited.
    free_explore_daily_limit: int = 5

    # --- hierarchical briefing (phase 3) --------------------------------------
    # The full briefing is one LLM call per held sector + one per market + the
    # orchestrator, so it runs weekly (the Monday cron slot) and on demand. The cooldown
    # guards the on-demand button from re-billing the whole tree; the sector cap bounds
    # the fan-out; concurrency is held low so Groq's free-tier rate limits don't throttle.
    briefing_cooldown_minutes: int = 720          # 12h between on-demand regenerations
    briefing_max_sectors_per_market: int = 8      # top-N sectors by allocation get an agent
    briefing_max_concurrency: int = 3             # parallel agents per run

    # --- billing (phase 2) ----------------------------------------------------
    # The whole point of the toggle: with STRIPE_ENABLED=false the upgrade button flips
    # the caller's plan locally so the premium path is testable with no Stripe account.
    # With it true, the same button drives real Checkout + webhooks. The local flip is
    # reachable ONLY in test mode — otherwise it would be a free-upgrade hole.
    stripe_enabled: bool = False
    stripe_secret_key: str = ""
    stripe_webhook_secret: str = ""
    stripe_price_id: str = ""
    # Where Stripe returns the browser after Checkout. Falls back to localhost for dev.
    public_url: str = "http://localhost:8000"


@lru_cache
def settings() -> Settings:
    return Settings()
