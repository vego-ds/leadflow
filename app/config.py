"""
Centralized settings, loaded from environment variables / .env.

Per secrets-discipline, this is the one place that reads the environment;
adapters and routes receive values as constructor/dependency arguments
instead of reading os.environ themselves.
"""
from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "sqlite+aiosqlite:///./leadflow.db"
    webhook_signing_secret: str = ""
    bolna_webhook_secret: str = ""


settings = Settings()
