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
    # Subscription prices for the internal sales dashboard (price_seller_unit = ваша валюта)
    price_starter: float = 0.0
    price_pro: float = 0.0
    price_business: float = 0.0
    # Public base URL used in emails (e.g. https://aimanager.kg). Empty in dev.
    public_base_url: str = ""
    # Comma-separated emails that become admin on first login (later: DB-driven roles).
    owner_emails: str = ""
    # Comma-separated emails that become read-only support admins
    admin_support_emails: str = ""
    # Email backends: console (dev default) or smtp
    mail_from: str = "AImanager <no-reply@localhost>"
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_password: str = ""
    smtp_starttls: bool = True


@lru_cache
def get_settings() -> Settings:
    return Settings()
