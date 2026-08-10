"""Application configuration loaded from environment variables.

All runtime configuration flows through the singleton ``settings`` object so
that no module reads ``os.environ`` directly. This keeps configuration
testable (override via env) and documents every knob in one place.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Literal
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from pydantic import field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Shipped placeholders. Usable locally, refused anywhere else — see
# ``_reject_default_secrets``.
DEV_JWT_SECRET = "dev-only-change-me-please-32bytes-minimum-secret"
DEV_ENCRYPTION_KEY = "u3nQ2wZ9r5t7y1o0p4a6s8d2f4g6h8j0k2l4z6x8c0v="

# libpq connection options that asyncpg rejects outright. Managed Postgres
# providers (Neon, Supabase, Render) put them in the URL they hand you.
_LIBPQ_ONLY_PARAMS = {"sslmode", "channel_binding", "options", "target_session_attrs"}

_LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1", "postgres", "db", ""}


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # --- App ---
    app_name: str = "AI Personal Finance"
    environment: Literal["local", "test", "staging", "production"] = "local"
    debug: bool = True
    api_v1_prefix: str = "/api/v1"

    # --- Database ---
    # async driver URL, e.g. postgresql+asyncpg://user:pass@localhost:5432/finance
    database_url: str = "postgresql+asyncpg://finance:finance@localhost:5432/finance"

    # --- Auth / JWT ---
    jwt_secret: str = DEV_JWT_SECRET
    jwt_algorithm: str = "HS256"
    access_token_expire_minutes: int = 30
    refresh_token_expire_days: int = 30

    # --- Encryption (Fernet key, base64 urlsafe 32 bytes) ---
    # Generate a key with: Fernet.generate_key().decode()  (see README).
    encryption_key: str = DEV_ENCRYPTION_KEY

    # --- Plaid ---
    plaid_client_id: str = ""
    plaid_secret: str = ""
    plaid_env: Literal["sandbox", "development", "production"] = "sandbox"
    plaid_products: str = "transactions"
    plaid_country_codes: str = "US,CA"
    plaid_redirect_uri: str = ""
    # Android package name — REQUIRED for OAuth institutions (most major banks,
    # e.g. TD/Chase) to hand the OAuth flow back to the installed app. Must match
    # the app's package and be registered in the Plaid Dashboard's allowed list.
    plaid_android_package_name: str = "com.aifinance.app"
    # When true (or when credentials are missing) the mock Plaid client is used.
    plaid_use_mock: bool = False

    # --- SnapTrade (brokerage link + live order execution) ---
    # Wealthsimple/Questrade have no public API of their own; SnapTrade is the
    # aggregator that fronts them. Keys come from dashboard.snaptrade.com.
    snaptrade_client_id: str = ""
    snaptrade_consumer_key: str = ""
    # Live execution is OFF unless explicitly enabled, even with valid keys.
    # Guards against a misconfigured env sending real orders to a real broker.
    snaptrade_trading_enabled: bool = False
    # When true (or when credentials are missing) the mock client is used.
    snaptrade_use_mock: bool = False

    # --- AI (hosted, OpenAI-compatible endpoint) ---
    # Defaults target OpenRouter's free tier. Any OpenAI-compatible host works
    # by changing these three values — Groq, Cerebras, the Hugging Face router.
    llm_base_url: str = "https://openrouter.ai/api/v1"
    llm_model: str = "nvidia/nemotron-3-super-120b-a12b:free"
    llm_api_key: str = ""
    llm_timeout_seconds: float = 45.0
    # Free tiers cap requests per minute; the committee fans out over tickers
    # concurrently, so in-flight calls are capped process-wide.
    llm_max_concurrency: int = 4
    llm_max_tokens: int = 900
    llm_json_mode: bool = True
    # Sent as HTTP-Referer / X-Title; OpenRouter uses them to attribute usage.
    llm_app_url: str = ""
    llm_app_title: str = "AI Personal Finance"
    ai_enabled: bool = True

    # --- Invest ---
    # Annualized risk-free rate used in Sharpe/Sortino; a stated assumption,
    # not fetched — see the risk_free_rate explainer.
    risk_free_rate: float = 0.04
    # Market data comes from Yahoo (free, keyless, works from Canada, prices
    # TSX listings). Mock provider is used in tests.
    market_data_use_mock: bool = False
    mc_paths_default: int = 2000
    mc_paths_max: int = 5000

    # --- CORS ---
    cors_origins: str = "*"

    @field_validator("database_url")
    @classmethod
    def _normalize_database_url(cls, value: str) -> str:
        """Accept the URL a managed Postgres provider actually hands you.

        Neon/Supabase/Render issue ``postgres://…?sslmode=require``. SQLAlchemy
        would resolve that to the sync psycopg driver (not installed), and
        asyncpg raises on ``sslmode`` because it is a libpq-only parameter. Both
        are silent-at-import, fatal-at-first-query failures, so fix them here
        rather than asking every deploy to hand-edit the URL.
        """
        scheme, netloc, path, query, fragment = urlsplit(value)
        if scheme in ("postgres", "postgresql"):
            scheme = "postgresql+asyncpg"
        params = [(k, v) for k, v in parse_qsl(query) if k.lower() not in _LIBPQ_ONLY_PARAMS]
        return urlunsplit((scheme, netloc, path, urlencode(params), fragment))

    @model_validator(mode="after")
    def _reject_default_secrets(self) -> Settings:
        """Refuse to boot outside local with the placeholder secrets.

        These have working defaults so `docker compose up` needs no setup, which
        also means a forgotten env var would deploy silently insecure instead of
        failing loudly.
        """
        if self.environment in ("staging", "production"):
            insecure = [
                name
                for name, value, default in (
                    ("JWT_SECRET", self.jwt_secret, DEV_JWT_SECRET),
                    ("ENCRYPTION_KEY", self.encryption_key, DEV_ENCRYPTION_KEY),
                )
                if value == default
            ]
            if insecure:
                raise ValueError(
                    f"{', '.join(insecure)} still set to the development default; "
                    f"generate real values before deploying to {self.environment}."
                )
        return self

    @property
    def database_ssl_required(self) -> bool:
        """Managed Postgres always wants TLS; a local container does not offer it.

        Driver-gated because the flag becomes an asyncpg ``connect_args``, which
        SQLite (the test database) would reject.
        """
        if "asyncpg" not in self.database_url:
            return False
        return (urlsplit(self.database_url).hostname or "") not in _LOCAL_HOSTS

    @property
    def ai_available(self) -> bool:
        """AI paths run only when enabled *and* actually configured.

        Without this an unconfigured deploy would attempt every LLM call and
        fall back after a wasted round trip, instead of going straight to the
        rules-based answer.
        """
        return self.ai_enabled and bool(self.llm_api_key)

    @property
    def plaid_country_code_list(self) -> list[str]:
        return [c.strip() for c in self.plaid_country_codes.split(",") if c.strip()]

    @property
    def plaid_product_list(self) -> list[str]:
        return [p.strip() for p in self.plaid_products.split(",") if p.strip()]

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def use_mock_plaid(self) -> bool:
        """Fall back to the mock client if explicitly requested or unconfigured."""
        return self.plaid_use_mock or not (self.plaid_client_id and self.plaid_secret)

    @property
    def use_mock_snaptrade(self) -> bool:
        """Fall back to the mock client if explicitly requested or unconfigured."""
        return self.snaptrade_use_mock or not (
            self.snaptrade_client_id and self.snaptrade_consumer_key
        )

    @property
    def live_trading_enabled(self) -> bool:
        """Real orders require BOTH the flag and a real (non-mock) client.

        Two independent conditions so that enabling the flag while unconfigured
        degrades to the mock broker rather than erroring at order time.
        """
        return self.snaptrade_trading_enabled and not self.use_mock_snaptrade


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
