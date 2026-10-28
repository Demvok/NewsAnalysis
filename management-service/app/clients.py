from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

import httpx

from .config import Settings
from .models import ServiceDependency


@dataclass
class ServiceClient:
    settings: Settings

    def article(self, article_id: str, headers: dict[str, str]) -> dict[str, Any]:
        response = httpx.get(
            f"{self.settings.storage_url}/articles/{article_id}",
            headers=headers,
            timeout=self.settings.request_timeout_seconds,
        )
        response.raise_for_status()
        return response.json()

    def articles(self, limit: int = 100) -> list[dict[str, Any]]:
        response = httpx.get(
            f"{self.settings.storage_url}/articles",
            params={"limit": limit},
            timeout=self.settings.request_timeout_seconds,
        )
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, list):
            raise ValueError("storage articles response must be a list")
        return [item for item in payload if isinstance(item, dict)]

    def analyze(self, payload: dict[str, Any], headers: dict[str, str]) -> dict[str, Any]:
        response = httpx.post(
            f"{self.settings.analysis_url}/analysis/citations",
            json=payload,
            headers=headers,
            timeout=self.settings.analysis_timeout_seconds,
        )
        response.raise_for_status()
        return response.json()

    def health(self, name: str, url: str) -> ServiceDependency:
        started = time.perf_counter()
        checked_at = datetime.now(timezone.utc)
        try:
            response = httpx.get(f"{url}/health", timeout=self.settings.request_timeout_seconds)
            details = response.json()
            if not isinstance(details, dict):
                raise ValueError("health response must be a JSON object")
            availability = "AVAILABLE" if response.is_success else "UNAVAILABLE"
            status = "HEALTHY" if response.is_success else "UNHEALTHY"
            if name == "data-storage" and details.get("db_status") != "ok":
                status = "DEGRADED"
                availability = "API_AVAILABLE_DATABASE_UNAVAILABLE"
            return ServiceDependency(
                name=name, status=status, availability=availability,
                latency_ms=int((time.perf_counter() - started) * 1000),
                details=details, checked_at=checked_at,
            )
        except (httpx.HTTPError, ValueError) as exc:
            return ServiceDependency(
                name=name, status="UNAVAILABLE", availability="UNAVAILABLE",
                latency_ms=int((time.perf_counter() - started) * 1000),
                error=type(exc).__name__, checked_at=checked_at,
            )

    def dependencies(self) -> list[ServiceDependency]:
        result = [
            self.health("analysis-layer", self.settings.analysis_url),
            self.health("data-storage", self.settings.storage_url),
        ]
        started = time.perf_counter()
        try:
            response = httpx.get(
                f"{self.settings.llm_url}/models",
                timeout=self.settings.request_timeout_seconds,
            )
            details = response.json()
            if not isinstance(details, dict):
                raise ValueError("LM Studio response must be a JSON object")
            result.append(ServiceDependency(
                name="llm-provider",
                status="HEALTHY" if response.is_success else "UNHEALTHY",
                availability="AVAILABLE" if response.is_success else "UNAVAILABLE",
                latency_ms=int((time.perf_counter() - started) * 1000),
                details={"endpoint": f"{self.settings.llm_url}/models", "models": details.get("data", [])},
                checked_at=datetime.now(timezone.utc),
            ))
        except (httpx.HTTPError, ValueError) as exc:
            result.append(ServiceDependency(
                name="llm-provider",
                status="UNAVAILABLE",
                availability="UNAVAILABLE",
                latency_ms=int((time.perf_counter() - started) * 1000),
                error=type(exc).__name__,
                details={"endpoint": f"{self.settings.llm_url}/models"},
                checked_at=datetime.now(timezone.utc),
            ))
        return result
