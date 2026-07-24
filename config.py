from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # App
    APP_NAME: str = "HB Hotel Mapping Engine"
    APP_VERSION: str = "1.0.0"
    DEBUG: bool = True

    # Database
    DATABASE_URL: str
    SYNC_DATABASE_URL: str

    # Connection pool. Keep (POOL_SIZE + MAX_OVERFLOW) x worker_count below the
    # server's max_connections — 4 workers x 15 = 60 against a default of 100.
    DB_POOL_SIZE: int = 5
    DB_MAX_OVERFLOW: int = 10
    DB_POOL_TIMEOUT: int = 30

    # Queue processing
    QUEUE_CLAIM_BATCH: int = 500

    # ── publication gate ────────────────────────────────────────────────────
    # Which confidence tiers may reach a consumer without a human looking.
    #
    # Measured against the reference mapping on 1,714 hotels: publishing TIER1
    # and TIER2 produces ZERO false merges, while TIER3 introduces two. A merge
    # is unrecoverable in a booking path — a guest is sent to the wrong property
    # — so the tiers that have never produced one are the ones allowed through.
    #
    # This holds 540 of 5,209 auto-matches (10.4%) for review. They are still
    # mapped, so later records attach to them normally and recall is unaffected;
    # they are simply withheld from exports until confirmed.
    PUBLISH_TIERS: str = "TIER1,TIER2"

    # Redis / Celery
    REDIS_URL: str = "redis://localhost:6379/0"
    CELERY_BROKER_URL: str = "redis://localhost:6379/0"
    CELERY_RESULT_BACKEND: str = "redis://localhost:6379/1"

    # ── LLM integration (Phase 1) ────────────────────────────────────────────
    # An LLM adjudicates the borderline cases the embedding layer can't reason
    # about — brand distinctions, rebrands, transliterations. Disabled by
    # default: with LLM_ENABLED False the service is a no-op and behaviour is
    # identical to today. See LLM_INTEGRATION_PLAN.md.
    LLM_ENABLED: bool = False
    LLM_PROVIDER: str = "openai"
    # gpt-4o-mini — the cheap/fast tier, ~$0.15/$0.60 per 1M tokens in/out.
    LLM_MODEL: str = "gpt-4o-mini"
    # Blank → the OpenAI SDK resolves the key from OPENAI_API_KEY. Set it here
    # (via .env) only to inject a specific key.
    LLM_API_KEY: str = ""
    # Deterministic matching. Leave unset (None) only if you point LLM_MODEL at a
    # model that rejects sampling params.
    LLM_TEMPERATURE: float | None = 0.0
    LLM_MAX_TOKENS: int = 1024
    LLM_TIMEOUT_SECONDS: float = 30.0
    LLM_MAX_RETRIES: int = 2
    # Ceiling on parallel calls in the batch paths, to stay inside rate limits.
    LLM_BATCH_CONCURRENCY: int = 5
    # Confidence the model must clear before its verdict is allowed to act.
    # Below this the decision stays MANUAL_REVIEW regardless of what it says.
    LLM_MIN_CONFIDENCE: float = 0.90

    # ── Geocoding validation (data quality) ──────────────────────────────────
    # Cross-checks supplier coordinates against a geocoding provider. Bad
    # coordinates are the single biggest source of duplicate masters — two
    # records for one hotel that disagree by >1 km never become candidates and
    # silently split. Off by default; Nominatim (OpenStreetMap) needs no key.
    GEOCODING_ENABLED: bool = False
    GEOCODING_PROVIDER: str = "nominatim"          # "nominatim" | "google"
    GEOCODING_API_KEY: str = ""                     # required for google
    GEOCODING_USER_AGENT: str = "hb-hotel-mapping"  # Nominatim requires one
    GEOCODING_TIMEOUT_SECONDS: float = 10.0
    # Nominatim's public server allows at most ~1 request/second — keep >= 1.0
    # unless you self-host or use a paid provider.
    GEOCODING_MIN_INTERVAL_SECONDS: float = 1.0
    # Supplied coordinate this far (m) from the geocoded location is 'suspect'.
    GEOCODING_MISMATCH_METERS: float = 2000.0

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"


settings = Settings()
