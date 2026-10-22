"""Runtime configuration (environment variables, optionally from server/.env)."""

from __future__ import annotations

import secrets
from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

SERVER_DIR = Path(__file__).resolve().parents[1]
DATA_DIR = SERVER_DIR / "data"


def _persistent_secret() -> str:
    """A random signing key generated once and kept in data/secret.key (dev convenience)."""
    path = DATA_DIR / "secret.key"
    if path.exists():
        return path.read_text().strip()
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    key = secrets.token_urlsafe(48)
    path.write_text(key)
    return key


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=str(SERVER_DIR / ".env"), env_prefix="FBB_", extra="ignore")

    database_url: str = f"sqlite:///{(DATA_DIR / 'app.db').as_posix()}"
    secret_key: str = ""
    token_ttl_hours: int = 24 * 14
    # Comma-separated list of allowed browser origins (the Electron app uses app:// or file://).
    cors_origins: str = "http://localhost:5173,http://127.0.0.1:5173,http://localhost:8000,app://fbb"
    public_url: str = "http://localhost:5173"  # used to build invite links
    sync_interval_hours: float = 1.0  # default refresh cadence for connected leagues (stats change nightly)
    scheduler_enabled: bool = True
    min_manual_sync_minutes: float = 5.0
    retrain_enabled: bool = False
    retrain_interval_days: int = 7
    flaim_mcp_url: str = "https://api.flaim.app/mcp"
    web_dist: str = str(SERVER_DIR.parent / "web" / "dist")
    # Hosting knobs. Scale-to-zero hosts (Cloud Run, Render free) only give CPU while a request is in
    # flight, so syncs must run inside the request and be triggered by an external cron.
    inline_sync: bool = False  # run manual refreshes inside the request instead of a background task
    realtime_enabled: bool = True  # WebSocket push; clients fall back to polling when disabled
    cron_token: str = ""  # enables POST /api/cron/sync when set
    # AI assistant. Direct: a server-held OpenAI key. Proxy: the Cloudflare Worker in cloudflare/ai-proxy.
    openai_api_key: str = ""
    ai_proxy_url: str = ""
    ai_proxy_token: str = ""
    ai_model: str = "gpt-6-luna"
    ai_max_rounds: int = 6
    ai_user_hourly_limit: int = 30
    # Desktop app: a single local user signs in automatically (the engine only listens on 127.0.0.1).
    local_mode: bool = False

    @property
    def is_sqlite(self) -> bool:
        return self.database_url.startswith("sqlite")

    def sqlalchemy_url(self) -> str:
        """Accept provider-style Postgres URLs (postgres://, postgresql://) and use the psycopg 3 driver."""
        url = self.database_url
        for prefix in ("postgres://", "postgresql://"):
            if url.startswith(prefix):
                return "postgresql+psycopg://" + url[len(prefix):]
        return url

    def signing_key(self) -> str:
        if self.secret_key:
            return self.secret_key
        if not self.is_sqlite:
            # With a shared database, a per-instance random key would sign users out on every
            # restart and make stored ESPN cookies unreadable.
            raise RuntimeError("FBB_SECRET_KEY must be set when using a hosted database.")
        return _persistent_secret()

    def origins(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
