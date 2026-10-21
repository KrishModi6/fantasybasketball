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
    sync_interval_hours: float = 4.0  # default refresh cadence for connected leagues
    scheduler_enabled: bool = True
    min_manual_sync_minutes: float = 5.0
    retrain_enabled: bool = False
    retrain_interval_days: int = 7
    flaim_mcp_url: str = "https://api.flaim.app/mcp"
    web_dist: str = str(SERVER_DIR.parent / "web" / "dist")

    def signing_key(self) -> str:
        return self.secret_key or _persistent_secret()

    def origins(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
