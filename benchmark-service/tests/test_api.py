import os
import tempfile
import unittest
from pathlib import Path

os.environ["BENCHMARK_RESULTS_DIR"] = tempfile.mkdtemp()
os.environ["BENCHMARK_DATA_DIR"] = str(Path(__file__).parents[1] / "data")

from fastapi.testclient import TestClient
from app.main import app


class ApiTests(unittest.TestCase):
    def test_models_and_datasets_are_available(self):
        client = TestClient(app)
        self.assertEqual(client.get("/api/models").status_code, 200)
        datasets = client.get("/api/datasets")
        self.assertEqual(datasets.status_code, 200)
        self.assertTrue(datasets.json()[0]["ground_truth"])


if __name__ == "__main__":
    unittest.main()
