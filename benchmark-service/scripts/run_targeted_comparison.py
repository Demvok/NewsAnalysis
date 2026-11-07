from __future__ import annotations

import csv
import json
import math
import os
import re
import subprocess
import sys
import time
from collections import defaultdict
from datetime import datetime, timezone
from difflib import SequenceMatcher
from pathlib import Path
from statistics import mean, median, stdev
from typing import Any

import httpx

ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(ROOT))

from app.config import Settings
from app.datasets import load_cases
from app.models import BenchmarkCase, ModelConfig
from app.runner import run_case


MODELS = {
    "gemma-4-e4b": "google/gemma-4-e4b",
    "gpt-oss-20b": "openai/gpt-oss-20b",
}
PENALTY = 1.0
TOKEN_RE = re.compile(r"\w+", re.UNICODE)


def normalize(text: Any) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^\w\s]", " ", str(text or "").casefold())).strip()


def tokens(text: Any) -> list[str]:
    return TOKEN_RE.findall(normalize(text))


def exact_values(values: list[Any], key: str) -> set[str]:
    result = set()
    for value in values or []:
        text = value.get(key, "") if isinstance(value, dict) else value
        if text:
            result.add(normalize(text))
    return result


def prf(expected: set[str], actual: set[str]) -> dict[str, float | int]:
    tp, fp, fn = len(expected & actual), len(actual - expected), len(expected - actual)
    precision = tp / (tp + fp) if tp + fp else 0.0
    recall = tp / (tp + fn) if tp + fn else (1.0 if not actual else 0.0)
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    return {"tp": tp, "fp": fp, "fn": fn, "precision": precision, "recall": recall, "f1": f1}


def pair_span(expected: list[str], predicted: list[str]) -> tuple[list[dict[str, Any]], list[str], list[str]]:
    candidates = []
    for pi, prediction in enumerate(predicted):
        prediction_tokens = tokens(prediction)
        for ri, reference in enumerate(expected):
            reference_tokens = tokens(reference)
            if not prediction_tokens or not reference_tokens:
                continue
            overlap = len(set(prediction_tokens) & set(reference_tokens))
            token_precision = overlap / len(set(prediction_tokens))
            token_recall = overlap / len(set(reference_tokens))
            token_f1 = (
                2 * token_precision * token_recall / (token_precision + token_recall)
                if token_precision + token_recall
                else 0.0
            )
            char_score = SequenceMatcher(None, normalize(prediction), normalize(reference)).ratio()
            candidates.append((token_f1, char_score, pi, ri, overlap, token_precision, token_recall))
    matches = []
    used_predictions: set[int] = set()
    used_references: set[int] = set()
    for score in sorted(candidates, reverse=True):
        token_f1, char_score, pi, ri, overlap, precision, recall = score
        if pi in used_predictions or ri in used_references or token_f1 == 0:
            continue
        used_predictions.add(pi)
        used_references.add(ri)
        matches.append({
            "prediction_index": pi,
            "reference_index": ri,
            "token_precision": precision,
            "token_recall": recall,
            "token_f1": token_f1,
            "character_overlap": char_score,
            "contains_reference": normalize(expected[ri]) in normalize(predicted[pi]),
        })
    unmatched_predictions = [value for index, value in enumerate(predicted) if index not in used_predictions]
    unmatched_references = [value for index, value in enumerate(expected) if index not in used_references]
    return matches, unmatched_predictions, unmatched_references


def stance_data(case: dict[str, Any], parsed: dict[str, Any] | None) -> dict[str, Any]:
    predicted = (parsed or {}).get("stance") or []
    by_topic = {
        normalize(item.get("topic", item.get("topic_name", ""))): item
        for item in predicted
        if isinstance(item, dict)
    }
    rows = []
    for reference in case.get("expected_stance", []):
        topic = normalize(reference.get("topic", ""))
        match = by_topic.get(topic)
        expected = reference.get("score")
        actual = match.get("score") if match else None
        valid = isinstance(expected, (int, float)) and isinstance(actual, (int, float))
        error = abs(float(actual) - float(expected)) if valid else PENALTY
        rows.append({
            "topic": reference.get("topic"),
            "expected": expected,
            "predicted": actual,
            "matched": valid,
            "error": error,
            "missing_penalty": PENALTY if not valid else 0.0,
        })
    matched = [row for row in rows if row["matched"]]
    return {
        "rows": rows,
        "expected_count": len(rows),
        "matched_count": len(matched),
        "excluded_count": len(rows) - len(matched),
        "coverage": len(matched) / len(rows) if rows else None,
        "matched_mae": mean(row["error"] for row in matched) if matched else None,
        "full_coverage_mae": mean(row["error"] for row in rows) if rows else None,
    }


def telemetry() -> dict[str, Any]:
    result: dict[str, Any] = {"timestamp": datetime.now(timezone.utc).isoformat()}
    try:
        output = subprocess.check_output(
            ["nvidia-smi", "--query-gpu=name,memory.total,memory.used,memory.free",
             "--format=csv,noheader,nounits"],
            text=True,
            stderr=subprocess.STDOUT,
        ).strip()
        result["nvidia_smi"] = output
    except (OSError, subprocess.CalledProcessError) as exc:
        result["nvidia_smi_error"] = str(exc)
    return result


def endpoint_models(url: str) -> Any:
    try:
        return httpx.get(f"{url}/models", timeout=10).json()
    except Exception as exc:
        return {"error": str(exc)}


def stance_distribution(cases: list[BenchmarkCase]) -> dict[str, int]:
    counts = {"positive": 0, "neutral": 0, "negative": 0}
    for case in cases:
        for item in case.expected_stance:
            score = item.get("score")
            if not isinstance(score, (int, float)):
                continue
            label = "positive" if score > 0.2 else "negative" if score < -0.2 else "neutral"
            counts[label] += 1
    return counts


def aggregate(results: list[dict[str, Any]]) -> dict[str, Any]:
    output: dict[str, Any] = {}
    for model in MODELS:
        items = [item for item in results if item["model_key"] == model]
        exact_totals = defaultdict(int)
        span_rows = []
        stance_rows = []
        latencies, output_tokens, input_tokens, chars, words, speeds = [], [], [], [], [], []
        for item in items:
            evaluation = item["evaluation"]
            case = item["input"]
            parsed = item.get("parsed_response") or {}
            for name, expected, actual in (
                ("citation_extraction", exact_values(case.get("expected_citations", []), "exact_quote"),
                 exact_values(parsed.get("citations", []), "exact_quote")),
                ("person_identification", exact_values(case.get("expected_persons", []), "name"),
                 exact_values(parsed.get("persons") or parsed.get("speakers") or [], "name")),
                ("topic_identification", exact_values(case.get("expected_topics", []), "name"),
                 exact_values(parsed.get("topics", []), "name")),
            ):
                scores = prf(expected, actual)
                for key in ("tp", "fp", "fn"):
                    exact_totals[f"{name}_{key}"] += scores[key]
            span_rows.extend(item["targeted"]["citation_span"]["matches"])
            stance = item["targeted"]["stance"]
            stance_rows.extend(stance["rows"])
            performance = evaluation["performance"]
            latencies.append(performance["execution_time_ms"])
            if performance.get("input_tokens") is not None:
                input_tokens.append(performance["input_tokens"])
            if performance.get("output_tokens") is not None:
                output_tokens.append(performance["output_tokens"])
            text = item["raw_response"]["choices"][0]["message"].get("content", "") if item.get("raw_response") else ""
            chars.append(len(text))
            words.append(len(text.split()))
            if performance.get("tokens_per_second") is not None:
                speeds.append(performance["tokens_per_second"])
        def metric(name: str) -> dict[str, Any]:
            tp = exact_totals[f"{name}_tp"]
            fp = exact_totals[f"{name}_fp"]
            fn = exact_totals[f"{name}_fn"]
            precision = tp / (tp + fp) if tp + fp else 0.0
            recall = tp / (tp + fn) if tp + fn else (1.0 if not fp else 0.0)
            return {
                "tp": tp,
                "fp": fp,
                "fn": fn,
                "precision": precision,
                "recall": recall,
                "f1": 2 * precision * recall / (precision + recall) if precision + recall else 0.0,
            }
        span_tp = len(span_rows)
        span_expected = sum(len(item["targeted"]["citation_span"]["references"]) for item in items)
        span_predicted = sum(len(item["targeted"]["citation_span"]["predictions"]) for item in items)
        span_pr = span_tp / span_predicted if span_predicted else 0.0
        span_re = span_tp / span_expected if span_expected else (1.0 if not span_predicted else 0.0)
        output[model] = {
            "cases": len(items),
            "citation_exact": metric("citation_extraction"),
            "person_exact": metric("person_identification"),
            "topic_exact": metric("topic_identification"),
            "citation_span": {
                "tp": span_tp,
                "fp": span_predicted - span_tp,
                "fn": span_expected - span_tp,
                "precision": span_pr,
                "recall": span_re,
                "f1": 2 * span_pr * span_re / (span_pr + span_re) if span_pr + span_re else 0.0,
                "character_overlap_mean": mean(row["character_overlap"] for row in span_rows) if span_rows else None,
            },
            "stance": {
                "expected": len(stance_rows),
                "matched": sum(row["matched"] for row in stance_rows),
                "excluded": sum(not row["matched"] for row in stance_rows),
                "coverage": sum(row["matched"] for row in stance_rows) / len(stance_rows) if stance_rows else None,
                "matched_mae": mean(row["error"] for row in stance_rows if row["matched"]) if any(row["matched"] for row in stance_rows) else None,
                "full_coverage_mae": mean(row["error"] for row in stance_rows) if stance_rows else None,
                "zero_prediction_baseline_mae": mean(abs(row["expected"]) for row in stance_rows) if stance_rows else None,
            },
            "performance": {
                "latency_mean_ms": mean(latencies) if latencies else None,
                "latency_median_ms": median(latencies) if latencies else None,
                "latency_stdev_ms": stdev(latencies) if len(latencies) > 1 else 0.0,
                "input_tokens_mean": mean(input_tokens) if input_tokens else None,
                "output_tokens_mean": mean(output_tokens) if output_tokens else None,
                "output_chars_mean": mean(chars) if chars else None,
                "output_words_mean": mean(words) if words else None,
                "generation_tokens_per_second_mean": mean(speeds) if speeds else None,
            },
        }
    return output


def main() -> None:
    started = datetime.now(timezone.utc)
    stamp = started.strftime("%Y%m%dT%H%M%SZ")
    out = ROOT / "reports" / f"targeted_{stamp}"
    out.mkdir(parents=True, exist_ok=False)
    llm_url = os.getenv("BENCHMARK_LLM_URL", "http://localhost:1234/v1").rstrip("/")
    settings = Settings(
        llm_url=llm_url,
        results_dir=out,
        data_dir=ROOT / "data",
        request_timeout_seconds=float(os.getenv("BENCHMARK_REQUEST_TIMEOUT_SECONDS", "1800")),
        models=MODELS,
    )
    cases = load_cases(ROOT / "data", "synthetic")
    configs = [ModelConfig(model_id=model_id, temperature=0.0, max_tokens=1200) for model_id in MODELS.values()]
    hardware = {"before": telemetry(), "endpoint_models": endpoint_models(llm_url)}
    results = []
    for model_key, config in zip(MODELS, configs):
        for case in cases:
            result = run_case(settings, case, config)
            result["model_key"] = model_key
            parsed = result.get("parsed_response") or {}
            expected = [item.get("exact_quote", "") for item in case.expected_citations]
            predicted = [
                item.get("exact_quote", "") if isinstance(item, dict) else str(item)
                for item in parsed.get("citations", [])
            ]
            matches, unmatched_predictions, unmatched_references = pair_span(expected, predicted)
            result["targeted"] = {
                "citation_span": {
                    "references": expected,
                    "predictions": predicted,
                    "matches": matches,
                    "unmatched_predictions": unmatched_predictions,
                    "unmatched_references": unmatched_references,
                    "longer_prediction_contains_reference": any(item["contains_reference"] for item in matches),
                },
                "stance": stance_data(case.model_dump(mode="json"), parsed),
            }
            results.append(result)
    hardware["after"] = telemetry()
    hardware["deployment_note"] = (
        "The OpenAI-compatible API exposed model IDs but no portable quantization, offload, "
        "context, KV-cache, model-load-time, or model-unload telemetry. nvidia-smi snapshots "
        "are host observations, not peak per-request measurements. Production end-to-end execution "
        "was not run because no isolated production database/API target was configured; the "
        "targeted benchmark is independent and does not modify production state."
    )
    report = {
        "report_id": stamp,
        "source": "targeted follow-up; original benchmark artifacts preserved",
        "models": MODELS,
        "dataset": {
            "type": "synthetic",
            "case_count": len(cases),
            "stance_reference_count": sum(len(case.expected_stance) for case in cases),
            "stance_distribution": stance_distribution(cases),
            "stance_penalty": PENALTY,
        },
        "evaluation_changes": {
            "exact_match_preserved": True,
            "span_matching": "greedy one-to-one token-set overlap with character SequenceMatcher diagnostic",
            "stance_full_coverage": "unmatched or invalid stance receives penalty 1.0 on [-1, 1] scale; original matched MAE remains separate",
        },
        "hardware": hardware,
        "summary": aggregate(results),
        "paired_cases": results,
    }
    (out / "targeted_report.json").write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    with (out / "paired_case_metrics.csv").open("w", newline="", encoding="utf-8") as handle:
        fields = ["model_key", "test_id", "citation_exact_f1", "citation_span_f1", "stance_coverage", "stance_matched_mae", "stance_full_coverage_mae", "json_valid", "latency_ms", "input_tokens", "output_tokens", "output_chars", "output_words", "tokens_per_second"]
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for item in results:
            targeted = item["targeted"]
            span = targeted["citation_span"]
            stance = targeted["stance"]
            perf = item["evaluation"]["performance"]
            writer.writerow({
                "model_key": item["model_key"], "test_id": item["test_id"],
                "citation_exact_f1": item["evaluation"]["citation_extraction"]["f1"],
                "citation_span_f1": 0.0 if not span["matches"] else mean(match["token_f1"] for match in span["matches"]),
                "stance_coverage": stance["coverage"], "stance_matched_mae": stance["matched_mae"],
                "stance_full_coverage_mae": stance["full_coverage_mae"],
                "json_valid": item["evaluation"]["structured_output"]["json_valid"],
                "latency_ms": perf["execution_time_ms"], "input_tokens": perf.get("input_tokens"),
                "output_tokens": perf.get("output_tokens"), "output_chars": len((item.get("raw_response") or {}).get("choices", [{}])[0].get("message", {}).get("content", "")),
                "output_words": len(((item.get("raw_response") or {}).get("choices", [{}])[0].get("message", {}).get("content", "")).split()),
                "tokens_per_second": perf.get("tokens_per_second"),
            })
    lines = [
        "# Targeted GPT-OSS-20B vs Gemma 4 E4B Comparison",
        "",
        f"Run: `{stamp}`. Synthetic cases: `{len(cases)}`. Original benchmark artifacts were not modified.",
        "",
        "## Method",
        "",
        "Both models used the existing benchmark runner, identical prompt text, temperature 0, and max_tokens 1200. Exact normalized-set metrics are retained. Span diagnostics use greedy one-to-one token overlap; character similarity is descriptive and does not turn paraphrases into exact matches.",
        "",
        "Missing or invalid stance predictions receive an explicit full-coverage penalty of 1.0, the maximum absolute error on the [-1, 1] scale. Matched-case MAE remains separate, and excluded predictions are counted.",
        "",
        "## Results",
        "",
        "| Model | Exact citation F1 | Span F1 | Person F1 | Topic F1 | Matched stance MAE | Coverage | Full-coverage MAE | JSON validity | Mean latency ms |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for model, summary in report["summary"].items():
        exact = summary["citation_exact"]
        span = summary["citation_span"]
        stance = summary["stance"]
        lines.append(
            f"| {model} | {exact['f1']:.3f} | {span['f1']:.3f} | "
            f"{summary['person_exact']['f1']:.3f} | {summary['topic_exact']['f1']:.3f} | "
            f"{stance['matched_mae'] if stance['matched_mae'] is not None else 'n/a'} | "
            f"{stance['coverage'] if stance['coverage'] is not None else 'n/a'} | "
            f"{stance['full_coverage_mae'] if stance['full_coverage_mae'] is not None else 'n/a'} | "
            f"{sum(item['evaluation']['structured_output']['json_valid'] for item in results if item['model_key'] == model) / len(cases):.3f} | "
            f"{summary['performance']['latency_mean_ms']:.1f} |"
        )
    lines += [
        "",
        "The person/topic columns in this compact report are retained in each case record and should be read from the original evaluator fields; the table emphasizes the newly added citation and stance diagnostics.",
        "",
        "## Hardware and deployment",
        "",
        hardware["deployment_note"],
        "",
        "No production end-to-end run was performed because an isolated production target was not configured. This avoids changing persistent data or conflating the independent benchmark with production state. The raw responses and all per-case measurements are preserved in `targeted_report.json`.",
        "",
        "## Recommendation",
        "",
        "This small paired experiment is descriptive only; it does not establish statistical significance. Select the primary model using full-coverage stance behavior, citation span quality, structured-output reliability, latency, and deployment telemetry together. If hardware telemetry is unavailable, do not claim that GPT-OSS-20B can replace Gemma 4 E4B in the intended concurrent deployment.",
    ]
    (out / "targeted_report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(out)


if __name__ == "__main__":
    main()
