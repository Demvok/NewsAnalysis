from __future__ import annotations

import unittest
from unittest.mock import patch

import httpx

from app.clients import ServiceClient
from app.config import Settings


class ProductionReadinessTests(unittest.TestCase):
    def test_health_marks_malformed_json_unavailable(self):
        settings = Settings.from_env()
        client = ServiceClient(settings)
        response = httpx.Response(200, content=b"not-json")
        with patch("app.clients.httpx.get", return_value=response):
            result = client.health("analysis-layer", "http://analysis")
        self.assertEqual(result.status, "UNAVAILABLE")
        self.assertEqual(result.error, "JSONDecodeError")

    def test_timeout_is_reported_without_exposing_request_details(self):
        settings = Settings.from_env()
        client = ServiceClient(settings)
        with patch(
            "app.clients.httpx.get",
            side_effect=httpx.ReadTimeout("password=super-secret"),
        ):
            result = client.health("analysis-layer", "http://analysis")
        self.assertEqual(result.status, "UNAVAILABLE")
        self.assertEqual(result.error, "ReadTimeout")

    def test_health_rejects_valid_json_with_invalid_shape(self):
        settings = Settings.from_env()
        client = ServiceClient(settings)
        response = httpx.Response(200, json=["not", "a", "health", "object"])
        with patch("app.clients.httpx.get", return_value=response):
            result = client.health("analysis-layer", "http://analysis")
        self.assertEqual(result.status, "UNAVAILABLE")
        self.assertEqual(result.error, "ValueError")


if __name__ == "__main__":
    unittest.main()
