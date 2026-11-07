from __future__ import annotations

import os
import sys
import time
import threading
import unittest
from pathlib import Path
from unittest.mock import Mock, patch
from queue import Queue

sys.path.insert(0, str(Path(__file__).parents[1]))

from app.telemetry import TelemetryDispatcher, concurrency_snapshot, track_active


class NoStartDispatcher(TelemetryDispatcher):
    def start(self) -> None:
        return


class TelemetryDispatcherTests(unittest.TestCase):
    def test_enqueue_does_not_wait_for_stalled_http_sender(self):
        dispatcher = TelemetryDispatcher()
        sender_started = threading.Event()
        release_sender = threading.Event()

        def stalled_sender(*args, **kwargs):
            sender_started.set()
            release_sender.wait(timeout=2)
            response = Mock()
            response.raise_for_status.return_value = None
            return response

        try:
            with patch.dict(os.environ, {"MANAGEMENT_EVENTS_URL": "http://management/internal/events"}), \
                 patch("app.telemetry.httpx.post", side_effect=stalled_sender):
                dispatcher.enqueue({"event_type": "analysis_request"})
                self.assertTrue(sender_started.wait(timeout=1))
                started = time.perf_counter()
                dispatcher.enqueue({"event_type": "analysis_request"})
                self.assertLess(time.perf_counter() - started, 0.2)
        finally:
            release_sender.set()
            dispatcher.flush_and_stop()

    def test_normal_queue_overflow_is_visible(self):
        dispatcher = NoStartDispatcher()
        dispatcher.start = lambda: None
        dispatcher._normal = Queue(maxsize=1)
        dispatcher._important = Queue(maxsize=1)
        dispatcher.enqueue({"event_type": "first"})
        dispatcher.enqueue({"event_type": "second"})
        self.assertEqual(dispatcher.counters()["dropped_events"], 1)

    def test_batch_send_uses_configured_batch(self):
        dispatcher = TelemetryDispatcher()
        response = Mock()
        response.raise_for_status.return_value = None
        with patch.dict(os.environ, {"MANAGEMENT_EVENTS_URL": "http://management/internal/events"}), \
             patch("app.telemetry.httpx.post", return_value=response) as post:
            dispatcher._send([{"event_type": "one"}, {"event_type": "two"}])
        post.assert_called_once()
        self.assertEqual(post.call_args.args[0], "http://management/internal/events/bulk")
        self.assertEqual(len(post.call_args.kwargs["json"]), 2)

    def test_full_queue_evicts_normal_event_for_important_event(self):
        dispatcher = NoStartDispatcher()
        dispatcher._important = Queue(maxsize=1)
        dispatcher._normal = Queue(maxsize=1)
        dispatcher._important.put({"event_type": "existing", "status": "failed"})
        dispatcher._normal.put({"event_type": "normal"})
        important = {"event_type": "job_failed", "status": "failed"}
        dispatcher.enqueue(important)
        self.assertEqual(dispatcher._important.get_nowait()["event_type"], "existing")
        self.assertEqual(dispatcher._normal.get_nowait(), important)
        self.assertEqual(dispatcher.counters()["evicted_normal_events"], 1)

    def test_shutdown_flushes_queued_events(self):
        dispatcher = TelemetryDispatcher()
        response = Mock()
        response.raise_for_status.return_value = None
        with patch.dict(os.environ, {"MANAGEMENT_EVENTS_URL": "http://management/internal/events"}), \
             patch("app.telemetry.httpx.post", return_value=response) as post:
            dispatcher.enqueue({"event_type": "job_completed", "status": "completed"})
            dispatcher.flush_and_stop()
        self.assertEqual(post.call_count, 1)

    def test_active_call_snapshot_tracks_nested_work(self):
        self.assertEqual(concurrency_snapshot().get("llm_calls", 0), 0)
        with track_active("llm_calls") as snapshot:
            self.assertEqual(snapshot["llm_calls"], 1)
            self.assertEqual(concurrency_snapshot()["llm_calls"], 1)
        self.assertEqual(concurrency_snapshot().get("llm_calls", 0), 0)
