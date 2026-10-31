from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, ClassVar

import httpx

from .config import Settings
from .models import ServiceDependency


@dataclass
class ServiceClient:
    settings: Settings

    knowledge_resources: ClassVar[tuple[str, ...]] = (
        "articles",
        "people",
        "citations",
        "topics",
        "attitudes",
        "inconsistencies",
    )

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

    def knowledge_summary(self) -> dict[str, Any]:
        response = httpx.get(
            f"{self.settings.storage_url}/summary",
            timeout=self.settings.request_timeout_seconds,
        )
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict) or not isinstance(payload.get("entities"), dict):
            raise ValueError("storage summary response must contain entity counts")
        return payload

    def browse_options(self, resource: str, field: str, query: str = "") -> list[str]:
        response = httpx.get(
            f"{self.settings.storage_url}/browse/options/{resource}/{field}",
            params={"query": query} if query else {},
            timeout=self.settings.request_timeout_seconds,
        )
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, list):
            raise ValueError("storage browse options response must be a list")
        return [str(value) for value in payload]

    def knowledge(self, resource: str, limit: int = 100) -> list[dict[str, Any]]:
        if resource not in self.knowledge_resources:
            raise ValueError("unsupported knowledge resource")
        response = httpx.get(
            f"{self.settings.storage_url}/{resource}",
            params={"limit": limit},
            timeout=self.settings.request_timeout_seconds,
        )
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, list):
            raise ValueError(f"storage {resource} response must be a list")
        return [item for item in payload if isinstance(item, dict)]

    def browse_knowledge(self, resource: str, limit: int = 100, offset: int = 0, filters: dict[str, str] | None = None) -> dict[str, Any]:
        if resource not in {"attitudes", "inconsistencies"}:
            raise ValueError("joined browse supports attitudes and inconsistencies")
        response = httpx.get(
            f"{self.settings.storage_url}/{resource}/browse",
            params={"limit": limit, "offset": offset, **(filters or {})},
            timeout=self.settings.request_timeout_seconds,
        )
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict) or not isinstance(payload.get("items"), list):
            raise ValueError(f"storage {resource} browse response must contain items")
        return payload

    def knowledge_detail(self, resource: str, identifier: str) -> dict[str, Any]:
        if resource not in self.knowledge_resources:
            raise ValueError("unsupported knowledge resource")
        if resource == "attitudes":
            parts = identifier.split("/", 1)
            if len(parts) != 2:
                raise ValueError("attitude identifier must contain topic and citation UUIDs")
            path = f"attitudes/{parts[0]}/{parts[1]}"
        else:
            path = f"{resource}/{identifier}"
        response = httpx.get(
            f"{self.settings.storage_url}/{path}",
            timeout=self.settings.request_timeout_seconds,
        )
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict):
            raise ValueError("storage detail response must be an object")
        return payload

    def mutate_knowledge(self, resource: str, identifier: str, payload: dict[str, Any]) -> dict[str, Any]:
        if resource not in self.knowledge_resources:
            raise ValueError("unsupported knowledge resource")
        if resource == "attitudes":
            parts = identifier.split("/", 1)
            if len(parts) != 2:
                raise ValueError("attitude identifier must contain topic and citation UUIDs")
            path = f"attitudes/{parts[0]}/{parts[1]}"
        else:
            path = f"{resource}/{identifier}"
        response = httpx.put(
            f"{self.settings.storage_url}/{path}",
            json=payload,
            timeout=self.settings.request_timeout_seconds,
        )
        response.raise_for_status()
        result = response.json()
        if not isinstance(result, dict):
            raise ValueError("storage mutation response must be an object")
        return result

    def delete_knowledge(self, resource: str, identifier: str) -> None:
        if resource not in self.knowledge_resources or resource == "inconsistencies":
            raise ValueError("resource cannot be deleted from management")
        if resource == "attitudes":
            parts = identifier.split("/", 1)
            if len(parts) != 2:
                raise ValueError("attitude identifier must contain topic and citation UUIDs")
            path = f"attitudes/{parts[0]}/{parts[1]}"
        else:
            path = f"{resource}/{identifier}"
        response = httpx.delete(
            f"{self.settings.storage_url}/{path}",
            timeout=self.settings.request_timeout_seconds,
        )
        response.raise_for_status()

    def database_action(self, action: str) -> dict[str, Any]:
        if action not in {"create", "recreate"}:
            raise ValueError("unsupported database action")
        response = httpx.post(
            f"{self.settings.storage_url}/database/{action}",
            timeout=self.settings.request_timeout_seconds,
        )
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict):
            raise ValueError("database action response must be an object")
        return payload

    def import_temp_articles(self) -> dict[str, Any]:
        response = httpx.post(
            f"{self.settings.storage_url}/articles/import-temp",
            timeout=self.settings.request_timeout_seconds,
        )
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict):
            raise ValueError("temporary import response must be an object")
        return payload

    def search_knowledge(self, resource: str, query: str, limit: int = 100) -> list[dict[str, Any]]:
        if resource not in self.knowledge_resources:
            raise ValueError("unsupported knowledge resource")
        if resource in {"articles", "citations"}:
            response = httpx.get(
                f"{self.settings.storage_url}/{resource}/search",
                params={"query": query, "limit": limit},
                timeout=self.settings.request_timeout_seconds,
            )
            response.raise_for_status()
            payload = response.json()
            if not isinstance(payload, list):
                raise ValueError(f"storage {resource} search response must be a list")
            return [item for item in payload if isinstance(item, dict)]

        values = self.knowledge(resource, limit)
        needle = query.casefold()
        return [
            item for item in values
            if needle in str(item.get("name", item.get("topic_name", item.get("stance", "")))).casefold()
        ]

    def semantic_search(self, resource: str, query: str, limit: int = 25, min_similarity: float = 0.0) -> list[dict[str, Any]]:
        if resource not in {"citations", "topics"}:
            raise ValueError("semantic search supports citations and topics")
        response = httpx.post(
            f"{self.settings.storage_url}/{resource}/search/text",
            params={"limit": limit, "min_similarity": min_similarity},
            json={"text": query},
            timeout=self.settings.request_timeout_seconds,
        )
        if response.is_error:
            detail = response.text[:500]
            raise RuntimeError(f"{resource} semantic search failed ({response.status_code}): {detail}")
        payload = response.json()
        if not isinstance(payload, list):
            raise ValueError(f"storage {resource} semantic search response must be a list")
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
