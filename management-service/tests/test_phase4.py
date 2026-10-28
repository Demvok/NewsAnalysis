from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from uuid import uuid4

os.environ["MANAGEMENT_DATA_DIR"] = tempfile.mkdtemp()
os.environ["MANAGEMENT_DATABASE_PATH"] = str(Path(os.environ["MANAGEMENT_DATA_DIR"]) / "management.db")

from fastapi.testclient import TestClient  # noqa: E402

from app import main  # noqa: E402
from app.database import ManagementDatabase  # noqa: E402


class ManagementPhase4Tests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        main.database = ManagementDatabase(Path(self.directory.name) / "management.db")
        main.database.initialize()
        self.client = TestClient(main.app)

    def tearDown(self):
        self.directory.cleanup()

    def create_experiment(self, description):
        response = self.client.post(
            "/api/experiments",
            json={
                "description": description,
                "article_ids": [str(uuid4())],
                "model_config_snapshot": {"model": "test-model", "api_key": "must-not-persist"},
                "prompt_versions": {"extraction": "v1"},
                "pipeline_parameters": {"inconsistency_window_days": 30},
            },
        )
        self.assertEqual(response.status_code, 201)
        return response.json()

    def test_experiment_snapshot_is_safe_and_exportable(self):
        experiment = self.create_experiment("baseline")
        self.assertNotIn("api_key", experiment["model_config_snapshot"])
        detail = self.client.get(f"/api/experiments/{experiment['experiment_id']}")
        self.assertEqual(detail.status_code, 200)
        csv = self.client.get(f"/api/experiments/{experiment['experiment_id']}/export?format=csv")
        self.assertEqual(csv.status_code, 200)
        self.assertIn("processing_time_ms_per_article", csv.text)

    def test_comparison_does_not_invent_quality_metrics(self):
        first = self.create_experiment("first")
        second = self.create_experiment("second")
        response = self.client.post(
            "/api/experiments/compare",
            json={"experiment_ids": [first["experiment_id"], second["experiment_id"]]},
        )
        self.assertEqual(response.status_code, 200)
        self.assertIsNone(response.json()["experiments"][0]["metrics"]["quality"])

    def test_ground_truth_evaluation_enables_quality_metrics(self):
        experiment = self.create_experiment("evaluated")
        response = self.client.post(
            f"/api/experiments/{experiment['experiment_id']}/evaluation",
            json={
                "experiment_id": experiment["experiment_id"],
                "evaluated_items": 10,
                "true_positives": 6,
                "false_positives": 2,
                "false_negatives": 2,
            },
        )
        self.assertEqual(response.status_code, 200)
        quality = response.json()["metrics"]["quality"]
        self.assertEqual(quality["evaluated_items"], 10)
        self.assertAlmostEqual(quality["precision"], 0.75)
        self.assertAlmostEqual(quality["recall"], 0.75)
        self.assertAlmostEqual(quality["f1"], 0.75)


if __name__ == "__main__":
    unittest.main()
