from __future__ import annotations

from pathlib import Path
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    project_root: Path = Field(default_factory=lambda: Path(__file__).resolve().parents[2])
    database_url: str = Field(
        default="postgresql+psycopg://news:POSTGRES_PASSWORD@postgres-db:5432/news"
    )
    storage_url: str = Field(default="http://data-storage:8000")
    ingestion_concurrency: int = Field(default=15)
    request_timeout: float = Field(default=15.0)
    min_content_length: int = Field(default=150)
    log_level: str = Field(default="INFO")

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )


settings = Settings()
