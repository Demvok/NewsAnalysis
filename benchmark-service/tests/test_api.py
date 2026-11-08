import os
import tempfile
import unittest
from pathlib import Path

os.environ["BENCHMARK_RESULTS_DIR"] = tempfile.mkdtemp()
os.environ["BENCHMARK_DATA_DIR"] = str(Path(__file__).parents[1] / "data")

from fastapi.testclient import TestClient
from app.main import app
from app.storage import save_run


class ApiTests(unittest.TestCase):
    def test_models_and_datasets_are_available(self):
        client = TestClient(app)
        self.assertEqual(client.get("/api/models").status_code, 200)
        datasets = client.get("/api/datasets")
        self.assertEqual(datasets.status_code, 200)
        self.assertTrue(datasets.json()[0]["ground_truth"])

    def test_report_json_is_not_exposed_as_a_run(self):
        report_path = Path(os.environ["BENCHMARK_RESULTS_DIR"]) / "benchmark_report.json"
        report_path.write_text('{"report_id":"preserved-report","models":{}}', encoding="utf-8")
        save_run(Path(os.environ["BENCHMARK_RESULTS_DIR"]), {
            "run_id": "run-1",
            "experiment_type": "EXP-01",
            "status": "CREATED",
            "dataset": "synthetic",
            "model_ids": ["gemma-4-e4b"],
            "repetitions": 1,
            "prompt_version": "test",
            "created_at": "2026-01-01T00:00:00Z",
            "completed_at": None,
            "started_at": None,
            "progress": {},
            "error": None,
            "results": [],
            "summary": {},
            "configurations": [],
        })
        runs = TestClient(app).get("/api/runs")
        self.assertEqual(runs.status_code, 200)
        self.assertEqual([item["run_id"] for item in runs.json()], ["run-1"])


if __name__ == "__main__":
    unittest.main()
