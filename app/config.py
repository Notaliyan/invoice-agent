"""Application settings, loaded from environment / .env."""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    openai_api_key: str = ""
    openai_model: str = "gpt-4o-mini"

    # How many model round-trips the agent gets before we give up.
    agent_max_steps: int = 12
    agent_temperature: float = 0.0

    max_upload_bytes: int = 10 * 1024 * 1024
    max_pdf_pages: int = 25

    # We deliberately show the agent only a slice of page 1 up front, so it has
    # to reach for the reading tools instead of one-shotting from context.
    page_preview_chars: int = 2500

    log_level: str = "INFO"


@lru_cache
def get_settings() -> Settings:
    return Settings()
