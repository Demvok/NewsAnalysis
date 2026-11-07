from __future__ import annotations

import os
import logging
import threading
import time
from collections import Counter
from queue import Empty, Full, Queue
from typing import Any

import httpx


class TelemetryDispatcher:
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
            logging.getLogger("newsanalysis.data_storage.telemetry").debug(
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


def emit_event(
    event_type: str,
    *,
    service: str = "data-storage",
    job_id: str | None = None,
    correlation_id: str | None = None,
    request_id: str | None = None,
    started_at: str | None = None,
    completed_at: str | None = None,
    duration_ms: int | None = None,
    retry_count: int = 0,
    attempt_number: int | None = None,
    queue_wait_ms: int | None = None,
    job_duration_ms: int | None = None,
    stage_duration_ms: int | None = None,
    worker_identity: str | None = None,
    attributes: dict[str, Any] | None = None,
) -> None:
    endpoint = os.getenv("MANAGEMENT_EVENTS_URL", "").strip()
    if not endpoint:
        return
    attributes = dict(attributes or {})
    worker_identity = worker_identity or f"pid={os.getpid()} thread={threading.current_thread().name}"
    attributes.setdefault("worker_identity", worker_identity)
    payload = {
        "service": service,
        "event_type": event_type,
        "job_id": job_id,
        "correlation_id": correlation_id,
        "request_id": request_id,
        "started_at": started_at,
        "completed_at": completed_at,
        "duration_ms": duration_ms,
        "retry_count": retry_count,
        "attempt_number": attempt_number,
        "queue_wait_ms": queue_wait_ms,
        "job_duration_ms": job_duration_ms,
        "stage_duration_ms": stage_duration_ms,
        "worker_identity": worker_identity,
        "attributes": attributes,
    }
    _dispatcher.enqueue(payload)


def shutdown_telemetry() -> None:
    _dispatcher.flush_and_stop()


def telemetry_metrics() -> dict[str, int]:
    return _dispatcher.counters()
