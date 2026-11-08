"""Small timing primitives shared by orchestration instrumentation and tests."""

from __future__ import annotations

import os
import threading
import time
from datetime import datetime, timezone


def monotonic_ms(started: float, finished: float | None = None) -> int:
    """Return a non-negative elapsed duration without summing parallel work."""
    end = time.perf_counter() if finished is None else finished
    return max(0, int((end - started) * 1000))


def wall_duration_ms(started_at: str, completed_at: str) -> int:
    """Calculate persisted wall-clock duration for intervals spanning calls."""
    started = datetime.fromisoformat(started_at)
    completed = datetime.fromisoformat(completed_at)
    if started.tzinfo is None:
        started = started.replace(tzinfo=timezone.utc)
    if completed.tzinfo is None:
        completed = completed.replace(tzinfo=timezone.utc)
    return max(0, int((completed - started).total_seconds() * 1000))


def queue_wait_ms(created_at: str, started_at: str) -> int:
    return wall_duration_ms(created_at, started_at)


def worker_identity() -> str:
    return f"pid={os.getpid()} thread={threading.current_thread().name}"
