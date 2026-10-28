from __future__ import annotations

import contextvars
import hashlib
import os
import re
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Iterator
from uuid import UUID, uuid4

import httpx

_context: contextvars.ContextVar[dict[str, str | None]] = contextvars.ContextVar(
    "analysis_telemetry_context",
    default={
        "request_id": None,
        "job_id": None,
        "correlation_id": None,
        "trace_id": None,
        "article_id": None,
    },
)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def current_context() -> dict[str, str | None]:
    return dict(_context.get())


@contextmanager
def bind_context(**values: str | UUID | None) -> Iterator[None]:
    current = current_context()
    for key, value in values.items():
        current[key] = str(value) if value is not None else None
    token = _context.set(current)
    try:
        yield
    finally:
        _context.reset(token)


def sanitized_error(error: BaseException) -> str:
    message = re.sub(r"(?i)(password|api[_-]?key|token|secret)=\S+", r"\1=[REDACTED]", str(error))
    return message[:500]


def _safe_content(value: Any) -> str | None:
    if os.getenv("MANAGEMENT_CAPTURE_LLM_CONTENT", "false").lower() not in {"1", "true", "yes", "on"}:
        return None
    digest = hashlib.sha256(str(value).encode("utf-8")).hexdigest()
    return f"sha256:{digest}"


def emit_event(
    event_type: str,
    *,
    service: str = "analysis",
    operation: str | None = None,
    stage: str | None = None,
    event_status: str | None = None,
    started_at: str | None = None,
    completed_at: str | None = None,
    duration_ms: int | None = None,
    time_to_first_token_ms: int | None = None,
    input_tokens: int | None = None,
    output_tokens: int | None = None,
    input_tokens_estimated: int | None = None,
    output_tokens_estimated: int | None = None,
    throughput_tokens_per_second: float | None = None,
    model: str | None = None,
    provider: str | None = None,
    finish_reason: str | None = None,
    retry_count: int = 0,
    span_id: str | None = None,
    parent_span_id: str | None = None,
    article_id: str | UUID | None = None,
    error_category: str | None = None,
    error_detail: str | None = None,
    attributes: dict[str, Any] | None = None,
    prompt: Any = None,
    response: Any = None,
) -> None:
    endpoint = os.getenv("MANAGEMENT_EVENTS_URL", "").strip()
    if not endpoint:
        return
    context = current_context()
    if article_id is not None:
        context["article_id"] = str(article_id)
    if context.get("article_id") is None and attributes:
        candidate_article_id = attributes.get("article_id")
        if candidate_article_id:
            context["article_id"] = str(candidate_article_id)
    payload = {
        "event_id": str(uuid4()),
        "timestamp": _utc_now(),
        "service": service,
        "event_type": event_type,
        "operation": operation,
        "stage": stage,
        "status": event_status,
        "started_at": started_at,
        "completed_at": completed_at,
        "duration_ms": duration_ms,
        "time_to_first_token_ms": time_to_first_token_ms,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "input_tokens_estimated": input_tokens_estimated,
        "output_tokens_estimated": output_tokens_estimated,
        "throughput_tokens_per_second": throughput_tokens_per_second,
        "model": model,
        "provider": provider,
        "finish_reason": finish_reason,
        "retry_count": retry_count,
        "span_id": span_id,
        "parent_span_id": parent_span_id,
        "error_category": error_category,
        "error_detail": error_detail,
        "request_id": context["request_id"],
        "job_id": context["job_id"],
        "correlation_id": context["correlation_id"],
        "trace_id": context["trace_id"],
        "attributes": attributes or {},
    }
    prompt_hash = _safe_content(prompt)
    response_hash = _safe_content(response)
    if prompt_hash or response_hash:
        payload["debug_content"] = {"prompt_hash": prompt_hash, "response_hash": response_hash}
    try:
        httpx.post(endpoint, json=payload, timeout=1.0)
    except httpx.HTTPError:
        # Observability must never change analytical behavior.
        return
