import unittest

from app.scoring import evaluate, precision_recall_f1


class ScoringTests(unittest.TestCase):
    def test_set_metrics_are_reproducible(self):
        self.assertEqual(
            precision_recall_f1({"ada", "ben"}, {"ada", "cara"}),
            {"precision": 0.5, "recall": 0.5, "f1": 0.5},
        )

    def test_invalid_structured_output_is_failure(self):
        result = evaluate(
            {"expected_citations": [], "expected_persons": [], "expected_topics": [], "expected_stance": []},
            None,
            False,
            False,
            12,
            {"input_tokens": None, "output_tokens": None, "tokens_per_second": None},
        )
        self.assertTrue(result["structured_output"]["failure"])
        self.assertEqual(result["performance"]["execution_time_ms"], 12)


if __name__ == "__main__":
    unittest.main()
