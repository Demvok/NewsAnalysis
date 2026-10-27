from __future__ import annotations

import os
from typing import Any

import httpx


def emit_event(
    event_type: str,
    *,
    service: str = "analysis",
    job_id: str | None = None,
    correlation_id: str | None = None,
    request_id: str | None = None,
    article_id: str | None = None,
    stage: str | None = None,
    duration_ms: int | None = None,
    attributes: dict[str, Any] | None = None,
) -> None:
    endpoint = os.getenv("MANAGEMENT_EVENTS_URL", "").strip()
    if not endpoint:
        return
    try:
        httpx.post(
            endpoint,
            json={
                "service": service,
                "event_type": event_type,
                "job_id": job_id,
                "correlation_id": correlation_id,
                "request_id": request_id,
                "article_id": article_id,
                "stage": stage,
                "duration_ms": duration_ms,
                "attributes": attributes or {},
            },
            timeout=1.0,
        )
    except httpx.HTTPError:
        # Telemetry must never make the analytical request fail.
        return
