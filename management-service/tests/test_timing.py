from datetime import datetime, timezone

from app.timing import monotonic_ms, queue_wait_ms, wall_duration_ms


def test_monotonic_duration_is_one_wall_interval():
    assert monotonic_ms(10.0, 10.250) == 250


def test_parallel_operations_are_not_summed():
    started = 100.0
    finished = 101.0
    operation_durations = [1.0, 1.0]
    assert monotonic_ms(started, finished) == 1000
    assert sum(operation_durations) != monotonic_ms(started, finished) / 1000


def test_persisted_queue_wait_uses_utc_wall_clock():
    created = datetime(2026, 11, 7, 10, 0, tzinfo=timezone.utc).isoformat()
    started = datetime(2026, 11, 7, 10, 0, 2, 500000, tzinfo=timezone.utc).isoformat()
    assert queue_wait_ms(created, started) == 2500
    assert wall_duration_ms(created, started) == 2500
