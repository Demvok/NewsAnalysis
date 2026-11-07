from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Settings:
    llm_url: str
    results_dir: Path
    data_dir: Path
    request_timeout_seconds: float
    models: dict[str, str]

    @classmethod
    def from_env(cls) -> "Settings":
        data_dir = Path(os.getenv("BENCHMARK_DATA_DIR", "/app/data"))
        results_dir = Path(os.getenv("BENCHMARK_RESULTS_DIR", "/app/results"))
        return cls(
            llm_url=os.getenv("BENCHMARK_LLM_URL", os.getenv("LLM_URL", "http://host.docker.internal:1234/v1")).rstrip("/"),
            results_dir=results_dir,
            data_dir=data_dir,
            request_timeout_seconds=max(1.0, float(os.getenv("BENCHMARK_REQUEST_TIMEOUT_SECONDS", "1800"))),
            models={
                "gemma-4-e4b": os.getenv("BENCHMARK_MODEL_GEMMA_E4B", "google/gemma-4-e4b"),
                "gpt-oss-20b": os.getenv("BENCHMARK_MODEL_GPT_OSS_20B", "openai/gpt-oss-20b"),
                "gemma-4-e2b": os.getenv("BENCHMARK_MODEL_GEMMA_E2B", "google/gemma-4-e2b"),
            },
        )

