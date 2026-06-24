"""
Centralized settings, loaded from environment variables / .env.

Per secrets-discipline, this is the one place that reads the environment;
adapters and routes receive values as constructor/dependency arguments
instead of reading os.environ themselves.
"""
from __future__ import annotations

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "sqlite+aiosqlite:///./leadflow.db"
    webhook_signing_secret: str = ""
    bolna_webhook_secret: str = ""
    google_creds_path: str | None = None
    spreadsheet_id: str | None = None

    @model_validator(mode="after")
    def _check_google_sheets_config(self) -> "Settings":
        has_creds = bool(self.google_creds_path)
        has_sheet_id = bool(self.spreadsheet_id)
        if has_creds != has_sheet_id:
            raise ValueError(
                "GOOGLE_CREDS_PATH and SPREADSHEET_ID must be set together "
                "(or both left empty to skip the Sheets projection). Got "
                f"GOOGLE_CREDS_PATH={'set' if has_creds else 'empty'}, "
                f"SPREADSHEET_ID={'set' if has_sheet_id else 'empty'}."
            )
        return self


settings = Settings()
