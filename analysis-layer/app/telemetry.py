from __future__ import annotations

import contextvars
import hashlib
import logging
import os
import re
import threading
import time
from collections import Counter
from queue import Empty, Full, Queue
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
        "attempt_number": None,
        "retry_count": None,
        "queue_wait_ms": None,
    },
)
_active_lock = threading.Lock()
_active_counts = Counter()


@contextmanager
def track_active(kind: str) -> Iterator[dict[str, int]]:
    with _active_lock:
        _active_counts[kind] += 1
        snapshot = dict(_active_counts)
    try:
        yield snapshot
    finally:
        with _active_lock:
            _active_counts[kind] = max(0, _active_counts[kind] - 1)


def concurrency_snapshot() -> dict[str, int]:
    with _active_lock:
        return dict(_active_counts)


class TelemetryDispatcher:
    """Non-blocking, bounded telemetry transport for this process."""

    def __init__(self) -> None:
        self.queue_size = max(1, int(os.getenv("TELEMETRY_QUEUE_SIZE", "1000")))
        self.batch_size = max(1, int(os.getenv("TELEMETRY_BATCH_SIZE", "50")))
        self.flush_interval = max(0.01, float(os.getenv("TELEMETRY_FLUSH_INTERVAL_SECONDS", "0.5")))
        self.send_timeout = max(0.01, float(os.getenv("TELEMETRY_SEND_TIMEOUT_SECONDS", "1")))
        important_capacity = max(1, self.queue_size // 4)
        self._important: Queue[dict[str, Any]] = Queue(maxsize=important_capacity)
        self._normal: Queue[dict[str, Any]] = Queue(maxsize=max(1, self.queue_size - important_capacity))
        self._wake = threading.Event()
        self._stopping = threading.Event()
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()
        self._counters = Counter()

    def counters(self) -> dict[str, int]:
        with self._lock:
            return dict(self._counters)

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stopping.clear()
        self._thread = threading.Thread(target=self._run, name="telemetry-sender", daemon=True)
        self._thread.start()

    @staticmethod
    def _important_event(event: dict[str, Any]) -> bool:
        return event.get("status") in {"failed", "completed"} or event.get("event_type") in {
            "job_completed", "job_failed", "job_finished",
        }

    def enqueue(self, event: dict[str, Any]) -> None:
        self.start()
        target = self._important if self._important_event(event) else self._normal
        try:
            target.put_nowait(event)
            self._wake.set()
            return
        except Full:
            pass
        if target is self._important:
            try:
                self._normal.get_nowait()
                with self._lock:
                    self._counters["evicted_normal_events"] += 1
                self._normal.put_nowait(event)
                self._wake.set()
                return
            except (Empty, Full):
                with self._lock:
                    self._counters["dropped_important_events"] += 1
        else:
            with self._lock:
                self._counters["dropped_events"] += 1

    def _take(self, limit: int) -> list[dict[str, Any]]:
        batch: list[dict[str, Any]] = []
        while len(batch) < limit:
            try:
                batch.append(self._important.get_nowait())
                continue
            except Empty:
                pass
            try:
                batch.append(self._normal.get_nowait())
            except Empty:
                break
        return batch

    def _send(self, batch: list[dict[str, Any]]) -> None:
        endpoint = os.getenv("MANAGEMENT_EVENTS_URL", "").strip()
        if not endpoint:
            return
        if endpoint.endswith("/events"):
            endpoint += "/bulk"
        elif not endpoint.endswith("/events/bulk"):
            endpoint = endpoint.rstrip("/") + "/internal/events/bulk"
        started = time.perf_counter()
        try:
            response = httpx.post(endpoint, json=batch, timeout=self.send_timeout)
            response.raise_for_status()
            with self._lock:
                self._counters["sent_events"] += len(batch)
                self._counters["sent_batches"] += 1
        except httpx.HTTPError:
            with self._lock:
                self._counters["send_failures"] += 1
        finally:
            logging.getLogger("newsanalysis.analysis.telemetry").debug(
                "telemetry_batch_send duration_ms=%d event_count=%d",
                int((time.perf_counter() - started) * 1000), len(batch),
            )

    def _run(self) -> None:
        while not self._stopping.is_set() or not self._important.empty() or not self._normal.empty():
            self._wake.wait(self.flush_interval)
            self._wake.clear()
            while True:
                batch = self._take(self.batch_size)
                if not batch:
                    break
                self._send(batch)

    def flush_and_stop(self) -> None:
        self._stopping.set()
        self._wake.set()
        if self._thread:
            self._thread.join()
            self._thread = None


_dispatcher = TelemetryDispatcher()


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
    attempt_number: int | None = None,
    queue_wait_ms: int | None = None,
    job_duration_ms: int | None = None,
    stage_duration_ms: int | None = None,
    worker_identity: str | None = None,
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
    if retry_count == 0 and context.get("retry_count") is not None:
        retry_count = int(context["retry_count"])
    if attempt_number is None and context.get("attempt_number") is not None:
        attempt_number = int(context["attempt_number"])
    if queue_wait_ms is None and context.get("queue_wait_ms") is not None:
        queue_wait_ms = int(context["queue_wait_ms"])
    if article_id is not None:
        context["article_id"] = str(article_id)
    if context.get("article_id") is None and attributes:
        candidate_article_id = attributes.get("article_id")
        if candidate_article_id:
            context["article_id"] = str(candidate_article_id)
    attributes = dict(attributes or {})
    attributes.setdefault("process_id", os.getpid())
    attributes.setdefault("thread_id", threading.get_ident())
    attributes.setdefault("thread_name", threading.current_thread().name)
    attributes.setdefault(
        "worker_identity",
        worker_identity or f"pid={os.getpid()} thread={threading.current_thread().name}",
    )
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
        "attempt_number": attempt_number,
        "queue_wait_ms": queue_wait_ms,
        "job_duration_ms": job_duration_ms,
        "stage_duration_ms": stage_duration_ms,
        "span_id": span_id,
        "parent_span_id": parent_span_id,
        "error_category": error_category,
        "error_detail": error_detail,
        "request_id": context["request_id"],
        "job_id": context["job_id"],
        "correlation_id": context["correlation_id"],
        "trace_id": context["trace_id"],
        "attributes": attributes,
    }
    prompt_hash = _safe_content(prompt)
    response_hash = _safe_content(response)
    if prompt_hash or response_hash:
        payload["debug_content"] = {"prompt_hash": prompt_hash, "response_hash": response_hash}
    _dispatcher.enqueue(payload)


def shutdown_telemetry() -> None:
    _dispatcher.flush_and_stop()


def telemetry_metrics() -> dict[str, int]:
    return _dispatcher.counters()
