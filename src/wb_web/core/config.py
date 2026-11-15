"""Application settings from environment / .env."""

from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_env: str = "development"
    # Dev default is SQLite; production uses postgresql+asyncpg://...
    database_url: str = "sqlite+aiosqlite:///./dev.db"
    # Fernet key for encrypting WB tokens and AI provider keys at rest.
    fernet_key: str = ""
    # Secret for signing session cookies. Dev-only default — override in .env / VPS.
    session_secret: str = "dev-only-change-me"
    # Magic link lifetime in minutes.
    magic_link_ttl_minutes: int = 15
    sentry_dsn: str = ""
    # Comma-separated emails that become admin on first login (later: DB-driven roles).
    owner_emails: str = ""


@lru_cache
def get_settings() -> Settings:
    return Settings()
