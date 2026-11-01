from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass
class Settings:
    data_dir: Path
    database_path: Path
    analysis_url: str
    storage_url: str
    llm_url: str
    management_events_url: str
    max_concurrent_jobs: int
    request_timeout_seconds: float
    analysis_timeout_seconds: float
    analysis_log_dir: Path
    storage_log_dir: Path
    log_dir: Path
    telemetry_retention_days: int

    @classmethod
    def from_env(cls) -> "Settings":
        data_dir = Path(os.getenv("MANAGEMENT_DATA_DIR", "/app/data"))
        return cls(
            data_dir=data_dir,
            database_path=Path(os.getenv("MANAGEMENT_DATABASE_PATH", str(data_dir / "management.db"))),
            analysis_url=os.getenv("ANALYSIS_URL", "http://analysis-layer:7000").rstrip("/"),
            storage_url=os.getenv("STORAGE_URL", "http://data-storage:8000").rstrip("/"),
            llm_url=os.getenv("LLM_URL", "http://host.docker.internal:1234/v1").rstrip("/"),
            management_events_url=os.getenv("MANAGEMENT_EVENTS_URL", "").strip(),
            max_concurrent_jobs=max(1, int(os.getenv("MANAGEMENT_MAX_CONCURRENT_JOBS", "4"))),
            request_timeout_seconds=float(os.getenv("MANAGEMENT_REQUEST_TIMEOUT_SECONDS", "10")),
            analysis_timeout_seconds=float(os.getenv("MANAGEMENT_ANALYSIS_TIMEOUT_SECONDS", "1800")),
            analysis_log_dir=Path(os.getenv("ANALYSIS_LOG_DIR", "/var/log/newsanalysis/analysis")),
            storage_log_dir=Path(os.getenv("STORAGE_LOG_DIR", "/var/log/newsanalysis/data-storage")),
            log_dir=Path(os.getenv("LOG_DIR", "/app/logs")),
            telemetry_retention_days=max(1, int(os.getenv("MANAGEMENT_TELEMETRY_RETENTION_DAYS", "7"))),
        )
