from __future__ import annotations

import math
import re
from statistics import mean, median
from typing import Any


def normalize(value: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^\w\s]", " ", value.casefold())).strip()


def _names(values: list[Any]) -> set[str]:
    result = set()
    for value in values:
        text = value if isinstance(value, str) else value.get("name", value.get("person", "")) if isinstance(value, dict) else ""
        if text:
            result.add(normalize(text))
    return result


def precision_recall_f1(expected: set[str], actual: set[str]) -> dict[str, float]:
    true_positive = len(expected & actual)
    precision = true_positive / len(actual) if actual else 0.0
    recall = true_positive / len(expected) if expected else (1.0 if not actual else 0.0)
    return {"precision": precision, "recall": recall, "f1": 2 * precision * recall / (precision + recall) if precision + recall else 0.0}


def evaluate(case: dict[str, Any], parsed: dict[str, Any] | None, valid_json: bool, schema_valid: bool, duration_ms: float, tokens: dict[str, Any]) -> dict[str, Any]:
    parsed = parsed or {}
    persons = parsed.get("persons") or parsed.get("speakers") or []
    topics = parsed.get("topics") or []
    citation_values = parsed.get("citations") or []
    citation_text = {
        normalize(item if isinstance(item, str) else item.get("exact_quote", ""))
        for item in citation_values
    }
    expected_citations = {normalize(item.get("exact_quote", "")) for item in case.get("expected_citations", []) if item.get("exact_quote")}
    citation_scores = precision_recall_f1(expected_citations, citation_text)
    person_scores = precision_recall_f1(_names(case.get("expected_persons", [])), _names(persons))
    person_scores["accuracy"] = int(_names(case.get("expected_persons", [])) == _names(persons))
    topic_scores = precision_recall_f1(_names(case.get("expected_topics", [])), _names(topics))
    expected_stance = case.get("expected_stance", [])
    predicted_stance = parsed.get("stance") or []
    pairs = []
    for expected in expected_stance:
        matching = next((item for item in predicted_stance if normalize(str(item.get("topic", item.get("topic_name", "")))) == normalize(str(expected.get("topic", "")))), None)
        if matching and isinstance(matching.get("score"), (int, float)) and isinstance(expected.get("score"), (int, float)):
            pairs.append((float(expected["score"]), float(matching["score"])))
    errors = [actual - expected for expected, actual in pairs]
    spearman = _spearman([expected for expected, _ in pairs], [actual for _, actual in pairs])
    categorical = precision_recall_f1(
        {str(_stance_label(item.get("score"))) for item in expected_stance if isinstance(item.get("score"), (int, float))},
        {str(_stance_label(item.get("score"))) for item in predicted_stance if isinstance(item.get("score"), (int, float))},
    )
    return {
        "citation_extraction": citation_scores,
        "person_identification": person_scores,
        "topic_identification": {**topic_scores, "exact_match": int(_names(case.get("expected_topics", [])) == _names(topics))},
        "stance_estimation": {
            "mae": mean(abs(error) for error in errors) if errors else None,
            "rmse": math.sqrt(mean(error * error for error in errors)) if errors else None,
            "spearman": spearman,
            "categorical_f1": categorical["f1"] if pairs else None,
        },
        "structured_output": {"json_valid": valid_json, "schema_compliant": schema_valid, "failure": not valid_json or not schema_valid},
        "performance": {"execution_time_ms": duration_ms, "peak_vram_usage_mb": None, **tokens},
    }


def _stance_label(score: float) -> str:
    if score > 0.2:
        return "positive"
    if score < -0.2:
        return "negative"
    return "neutral"


def _spearman(expected: list[float], actual: list[float]) -> float | None:
    if len(expected) < 2:
        return None

    def ranks(values: list[float]) -> list[float]:
        ordered = sorted((value, index) for index, value in enumerate(values))
        result = [0.0] * len(values)
        position = 0
        while position < len(ordered):
            end = position
            while end < len(ordered) and ordered[end][0] == ordered[position][0]:
                end += 1
            rank = (position + end - 1) / 2 + 1
            for _, index in ordered[position:end]:
                result[index] = rank
            position = end
        return result

    expected_ranks, actual_ranks = ranks(expected), ranks(actual)
    expected_mean, actual_mean = mean(expected_ranks), mean(actual_ranks)
    numerator = sum((left - expected_mean) * (right - actual_mean) for left, right in zip(expected_ranks, actual_ranks))
    denominator = math.sqrt(sum((left - expected_mean) ** 2 for left in expected_ranks) * sum((right - actual_mean) ** 2 for right in actual_ranks))
    return numerator / denominator if denominator else 1.0


def summarize(evaluations: list[dict[str, Any]]) -> dict[str, Any]:
    records = [item.get("evaluation", item) for item in evaluations]
    durations = [item["performance"]["execution_time_ms"] for item in records]
    signatures: dict[tuple[str, str], list[str]] = {}
    for item in evaluations:
        key = (item.get("model_id", ""), item.get("test_id", ""))
        parsed = item.get("parsed_response") or {}
        signature = repr((parsed.get("persons", parsed.get("speakers", [])), parsed.get("topics", []), parsed.get("stance", [])))
        signatures.setdefault(key, []).append(signature)
    repeated = [values for values in signatures.values() if len(values) > 1]
    stable = sum(len(set(values)) == 1 for values in repeated) / len(repeated) if repeated else None
    return {
        "cases": len(records),
        "average_execution_time_ms": mean(durations) if durations else None,
        "median_execution_time_ms": median(durations) if durations else None,
        "json_validity_rate": mean(item["structured_output"]["json_valid"] for item in records) if records else 0,
        "schema_compliance_rate": mean(item["structured_output"]["schema_compliant"] for item in records) if records else 0,
        "failure_rate": mean(item["structured_output"]["failure"] for item in records) if records else 0,
        "stability": {"repeated_groups": len(repeated), "agreement_rate": stable},
    }
