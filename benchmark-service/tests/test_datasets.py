import unittest
from pathlib import Path

from app.datasets import load_cases


class DatasetTests(unittest.TestCase):
    def test_synthetic_dataset_has_explicit_labels(self):
        cases = load_cases(Path(__file__).parents[1] / "data", "synthetic")
        self.assertGreaterEqual(len(cases), 10)
        self.assertTrue(all(case.test_id and case.article_text for case in cases))
        self.assertTrue(any(case.expected_citations for case in cases))


if __name__ == "__main__":
    unittest.main()
