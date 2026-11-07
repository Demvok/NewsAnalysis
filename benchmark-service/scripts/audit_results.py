from __future__ import annotations

import csv
import json
import math
import re
import statistics
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).parents[1]
REPORTS = ROOT / "reports"
SOURCE = REPORTS / "benchmark_report.json"
OUT = REPORTS / "audit"

PROMPT_PREFIX = (
    "Extract structured information from this news excerpt. Return JSON only with this shape:\n"
    '{"citations":[{"exact_quote":"...","speaker":"..."}],"persons":["..."],'
    '"topics":["..."],"stance":[{"topic":"...","score":-1.0}],"contradictions":[]}\n'
    "Use only evidence in the article. Score stance from -1 (opposes) to 1 (supports), 0 neutral or unclear.\n"
    "ARTICLE:\n"
)


def norm(value: object) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^\w\s]", " ", str(value or "").casefold())).strip()


def value_set(values: list[object], key: str | None = None) -> set[str]:
    result = set()
    for value in values or []:
        if isinstance(value, dict):
            value = value.get(key or "name", "")
        if value:
            result.add(norm(value))
    return result


def prf(expected: set[str], actual: set[str]) -> tuple[int, int, int, float, float, float]:
    tp = len(expected & actual)
    fp = len(actual - expected)
    fn = len(expected - actual)
    precision = tp / (tp + fp) if tp + fp else 0.0
    recall = tp / (tp + fn) if tp + fn else (1.0 if not actual else 0.0)
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    return tp, fp, fn, precision, recall, f1


def ranks(values: list[float]) -> list[float]:
    ordered = sorted((value, index) for index, value in enumerate(values))
    result = [0.0] * len(values)
    cursor = 0
    while cursor < len(ordered):
        end = cursor
        while end < len(ordered) and ordered[end][0] == ordered[cursor][0]:
            end += 1
        rank = (cursor + end - 1) / 2 + 1
        for _, index in ordered[cursor:end]:
            result[index] = rank
        cursor = end
    return result


def correlation(left: list[float], right: list[float], method: str) -> float | None:
    if len(left) < 2:
        return None
    if method == "spearman":
        left, right = ranks(left), ranks(right)
    left_mean, right_mean = statistics.mean(left), statistics.mean(right)
    numerator = sum((x - left_mean) * (y - right_mean) for x, y in zip(left, right))
    denominator = math.sqrt(
        sum((x - left_mean) ** 2 for x in left)
        * sum((y - right_mean) ** 2 for y in right)
    )
    return numerator / denominator if denominator else None


def percentile(values: list[float], fraction: float) -> float | None:
    if not values:
        return None
    values = sorted(values)
    index = (len(values) - 1) * fraction
    lower, upper = math.floor(index), math.ceil(index)
    if lower == upper:
        return values[lower]
    return values[lower] + (values[upper] - values[lower]) * (index - lower)


def stance_pairs(items: list[dict]) -> list[tuple[str, float, float]]:
    pairs = []
    for item in items:
        case = item["input"]
        expected = case.get("expected_stance", [])
        predicted = (item.get("parsed_response") or {}).get("stance", [])
        for reference in expected:
            topic = norm(reference.get("topic", ""))
            match = next(
                (
                    value for value in predicted
                    if norm(value.get("topic", value.get("topic_name", ""))) == topic
                    and isinstance(value.get("score"), (int, float))
                ),
                None,
            )
            if match:
                pairs.append((item["test_id"], float(reference["score"]), float(match["score"])))
    return pairs


def main() -> None:
    report = json.loads(SOURCE.read_text(encoding="utf-8"))
    synthetic = report["raw_results"]["synthetic"]
    models = sorted({item["configuration_label"] for item in synthetic})
    OUT.mkdir(parents=True, exist_ok=True)
    model_rows, per_case_rows, examples = [], [], {}

    for model in models:
        items = [item for item in synthetic if item["configuration_label"] == model]
        totals = defaultdict(int)
        case_rows = []
        errors = []
        for item in items:
            case = item["input"]
            parsed = item.get("parsed_response") or {}
            expected_c = value_set(case.get("expected_citations", []), "exact_quote")
            actual_c = value_set(parsed.get("citations", []), "exact_quote")
            expected_p = value_set(case.get("expected_persons", []))
            actual_p = value_set(parsed.get("persons") or parsed.get("speakers") or [])
            expected_t = value_set(case.get("expected_topics", []))
            actual_t = value_set(parsed.get("topics", []))
            row = {"model": model, "test_id": item["test_id"]}
            for name, expected, actual in (
                ("citation", expected_c, actual_c),
                ("person", expected_p, actual_p),
                ("topic", expected_t, actual_t),
            ):
                tp, fp, fn, precision, recall, f1 = prf(expected, actual)
                totals[f"{name}_tp"] += tp
                totals[f"{name}_fp"] += fp
                totals[f"{name}_fn"] += fn
                row.update({f"{name}_tp": tp, f"{name}_fp": fp, f"{name}_fn": fn,
                            f"{name}_precision": precision, f"{name}_recall": recall,
                            f"{name}_f1": f1})
            row["citation_expected"] = " | ".join(sorted(expected_c))
            row["citation_predicted"] = " | ".join(sorted(actual_c))
            row["topic_expected"] = " | ".join(sorted(expected_t))
            row["topic_predicted"] = " | ".join(sorted(actual_t))
            row["json_valid"] = item["evaluation"]["structured_output"]["json_valid"]
            row["schema_valid"] = item["evaluation"]["structured_output"]["schema_compliant"]
            per_case_rows.append(row)
            stance = stance_pairs([item])
            errors.extend(abs(actual - expected) for _, expected, actual in stance)

        model_row = {"model": model, "sample_size": len(items)}
        for name in ("citation", "person", "topic"):
            tp, fp, fn = totals[f"{name}_tp"], totals[f"{name}_fp"], totals[f"{name}_fn"]
            precision = tp / (tp + fp) if tp + fp else 0.0
            recall = tp / (tp + fn) if tp + fn else (1.0 if fp == 0 else 0.0)
            f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
            model_row.update({f"{name}_tp": tp, f"{name}_fp": fp, f"{name}_fn": fn,
                              f"{name}_precision": precision, f"{name}_recall": recall,
                              f"{name}_f1_micro": f1})

        pairs = stance_pairs(items)
        expected_values, predicted_values = [pair[1] for pair in pairs], [pair[2] for pair in pairs]
        baseline_values = [pair[1] for item in items for pair in stance_pairs([item])]
        model_row.update({
            "stance_pairs": len(pairs),
            "stance_mae": statistics.mean(abs(actual - expected) for expected, actual in zip(expected_values, predicted_values)) if pairs else None,
            "stance_rmse": math.sqrt(statistics.mean((actual - expected) ** 2 for expected, actual in zip(expected_values, predicted_values))) if pairs else None,
            "stance_baseline_mae": statistics.mean(abs(value) for value in baseline_values) if baseline_values else None,
            "stance_mae_improvement_over_zero": (
                statistics.mean(abs(value) for value in baseline_values)
                - statistics.mean(abs(actual - expected) for expected, actual in zip(expected_values, predicted_values))
            ) if pairs and baseline_values else None,
            "stance_pearson": correlation(expected_values, predicted_values, "pearson"),
            "stance_spearman": correlation(expected_values, predicted_values, "spearman"),
            "stance_error_mean": statistics.mean(errors) if errors else None,
            "stance_error_stddev": statistics.stdev(errors) if len(errors) > 1 else None,
            "stance_error_min": min(errors) if errors else None,
            "stance_error_max": max(errors) if errors else None,
        })
        model_row["json_validity_rate"] = statistics.mean(
            item["evaluation"]["structured_output"]["json_valid"] for item in items
        )
        for label, low, high in (("positive", 0.2, 1.01), ("neutral", -0.2, 0.2), ("negative", -1.01, -0.2)):
            grouped = [(expected, actual) for _, expected, actual in pairs if low <= expected < high]
            model_row[f"{label}_stance_count"] = len(grouped)
            model_row[f"{label}_stance_mae"] = statistics.mean(abs(actual - expected) for expected, actual in grouped) if grouped else None
        output_stats = []
        for item in items:
            content = ((item.get("raw_response") or {}).get("choices") or [{}])[0].get("message", {}).get("content", "")
            article = item["input"].get("article_text", "")
            usage = ((item.get("raw_response") or {}).get("usage") or {})
            output_tokens = usage.get("completion_tokens", usage.get("output_tokens"))
            input_tokens = usage.get("prompt_tokens", usage.get("input_tokens"))
            output_chars = len(content)
            input_chars = len(PROMPT_PREFIX + article)
            output_stats.append({
                "input_tokens": input_tokens, "output_tokens": output_tokens,
                "reasoning_tokens": ((usage.get("completion_tokens_details") or {}).get("reasoning_tokens")),
                "input_chars": input_chars, "output_chars": output_chars,
                "output_words": len(content.split()), "output_bytes": len(content.encode("utf-8")),
                "output_to_input_token_ratio": output_tokens / input_tokens if output_tokens and input_tokens else None,
                "latency_ms": item["evaluation"]["performance"]["execution_time_ms"],
                "tokens_per_second": item["evaluation"]["performance"]["tokens_per_second"],
            })
        for field in ("input_tokens", "output_tokens", "reasoning_tokens", "input_chars", "output_chars",
                      "output_words", "output_bytes", "output_to_input_token_ratio", "latency_ms", "tokens_per_second"):
            values = [row[field] for row in output_stats if isinstance(row[field], (int, float))]
            model_row[f"{field}_mean"] = statistics.mean(values) if values else None
            model_row[f"{field}_median"] = statistics.median(values) if values else None
            model_row[f"{field}_stddev"] = statistics.stdev(values) if len(values) > 1 else None
            model_row[f"{field}_p95"] = percentile(values, 0.95)
        model_rows.append(model_row)
        examples[model] = {}
        for label, field in (
            ("false_positive_citations", "citation_fp"),
            ("false_negative_citations", "citation_fn"),
            ("false_positive_topics", "topic_fp"),
            ("false_negative_topics", "topic_fn"),
        ):
            selected = [row for row in per_case_rows if row["model"] == model and row[field]][:3]
            examples[model][label] = []
            for row in selected:
                source = next(item for item in items if item["test_id"] == row["test_id"])
                examples[model][label].append({
                    "test_id": row["test_id"],
                    "article_text": source["input"]["article_text"],
                    "expected": row["citation_expected"] if "citation" in label else row["topic_expected"],
                    "predicted": row["citation_predicted"] if "citation" in label else row["topic_predicted"],
                    "tp": row[field.replace("_fp", "_tp").replace("_fn", "_tp")],
                    "fp": row.get(field.replace("_fp", "_fp"), 0),
                    "fn": row.get(field.replace("_fn", "_fn"), 0),
                })

    with (OUT / "per_case_metrics.csv").open("w", newline="", encoding="utf-8") as handle:
        fields = sorted({key for row in per_case_rows for key in row})
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(per_case_rows)
    with (OUT / "model_audit_metrics.csv").open("w", newline="", encoding="utf-8") as handle:
        fields = sorted({key for row in model_rows for key in row})
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(model_rows)

    audit = {
        "source_report": str(SOURCE),
        "models": model_rows,
        "representative_examples": examples,
        "methodology": {
            "normalization": "casefold, remove non-word/non-space characters, collapse whitespace",
            "matching": "exact equality of normalized sets; no substring, span, semantic, alias, or one-to-one matching",
            "aggregation": "original report averages per-case F1; audit additionally reports pooled micro counts and F1",
            "empty_sets": "precision is 0 for no predictions; recall is 1 only when both expected and actual are empty",
            "stance_missing": "missing topic/score pairs are excluded from MAE/RMSE/correlation; zero baseline uses all reference stance values",
            "performance": "wall-clock HTTP request duration including generation and parsing; no warmup isolation; generated tokens/s from provider completion tokens divided by wall time",
        },
        "hardware": {
            "endpoint_models": report.get("models"),
            "quantization_available_from_report": False,
            "vram_available": False,
            "assessment": "Model IDs alone do not establish quantization, GPU offload, VRAM, RAM, context length, or KV-cache usage. These require LM Studio runtime telemetry or process/GPU measurements.",
        },
    }
    (OUT / "benchmark_audit.json").write_text(json.dumps(audit, indent=2, ensure_ascii=False), encoding="utf-8")

    lines = [
        "# Benchmark Methodology Audit",
        "",
        "This is a separate audit of the preserved `benchmark_report.json`; original results are unchanged.",
        "",
        "## Executive findings",
        "",
        "- Citation, person, and topic matching uses normalized exact set equality. It is not substring, span, semantic, alias, or one-to-one matching.",
        "- Punctuation, quote marks, capitalization, and whitespace are normalized; paraphrases and longer/shorter spans are not matched.",
        "- Duplicate predictions are collapsed by sets, so duplicates are not penalized.",
        "- The original report's `topic_micro_f1` is a macro-like mean of per-case F1 values, not pooled micro-F1.",
        "- Missing stance predictions are excluded, which can make MAE optimistic. The zero-stance baseline is calculated below using all reference stance values.",
        "- Wall time includes the HTTP request and generation. Warm-up, backend offload, KV cache, quantization, and GPU/RAM usage were not recorded.",
        "",
        "## Pooled synthetic results",
        "",
        "| Model | Citation TP/FP/FN | Citation P/R/F1 | Person TP/FP/FN | Person P/R/F1 | Topic TP/FP/FN | Topic P/R/F1 | Stance pairs | Model MAE | Zero baseline MAE | JSON validity |",
        "|---|---|---|---|---|---|---|---:|---:|---:|---:|",
    ]
    for row in model_rows:
        lines.append(
            f"| {row['model']} | {row['citation_tp']}/{row['citation_fp']}/{row['citation_fn']} | "
            f"{row['citation_precision']:.3f}/{row['citation_recall']:.3f}/{row['citation_f1_micro']:.3f} | "
            f"{row['person_tp']}/{row['person_fp']}/{row['person_fn']} | "
            f"{row['person_precision']:.3f}/{row['person_recall']:.3f}/{row['person_f1_micro']:.3f} | "
            f"{row['topic_tp']}/{row['topic_fp']}/{row['topic_fn']} | "
            f"{row['topic_precision']:.3f}/{row['topic_recall']:.3f}/{row['topic_f1_micro']:.3f} | "
            f"{row['stance_pairs']} | {row['stance_mae']:.3f} | {row['stance_baseline_mae']:.3f} | "
            f"{row['json_validity_rate'] if 'json_validity_rate' in row else 'see source'} |"
        )
    lines += [
        "",
        "## Representative extraction and topic errors",
        "",
        "These examples are evaluated under the original exact normalized matching rule. A longer predicted sentence containing the expected quote is therefore both a false positive and a false negative, rather than a partial/span match.",
    ]
    for model, model_examples in examples.items():
        lines += ["", f"### {model}"]
        for label, title in (
            ("false_positive_citations", "Citation false positives"),
            ("false_negative_citations", "Citation false negatives"),
            ("false_positive_topics", "Topic false positives"),
            ("false_negative_topics", "Topic false negatives"),
        ):
            lines.append(f"- **{title}:**")
            for example in model_examples.get(label, [])[:2]:
                expected_value = example["expected"]
                predicted_value = example["predicted"]
                expected = (
                    " | ".join(expected_value)
                    if isinstance(expected_value, list)
                    else str(expected_value)
                ) or "(empty)"
                predicted = (
                    " | ".join(predicted_value)
                    if isinstance(predicted_value, list)
                    else str(predicted_value)
                ) or "(empty)"
                lines.append(
                    f"  - `{example['test_id']}` expected `{expected}`; predicted `{predicted}`."
                )
    lines += [
        "",
        "The complete per-case breakdown and all representative examples are in `per_case_metrics.csv` and `benchmark_audit.json`.",
        "",
        "## Stance interpretation",
        "",
        "The evaluator matches stance topics by normalized exact topic label, then computes mean absolute error over only matched numeric pairs. Unmatched, missing, invalid, and uncertain values are not penalized. This is a material source of optimism. Pearson and Spearman correlations are reported only where at least two matched pairs exist; repeated/tied values make them descriptive rather than strong evidence.",
        "",
        "## Performance and output efficiency",
        "",
        "The audit adds mean, median, standard deviation, and p95 for provider input/output tokens, reasoning tokens when exposed, reconstructed prompt/input characters, output characters/words/bytes, output/input token ratio, latency, and generated tokens per second. These are stored in `model_audit_metrics.csv`. The original runner did not persist input/output character counts or TTFT.",
        "",
        "## Hardware assessment",
        "",
        "The preserved report contains only model identifiers: `google/gemma-4-e4b`, `openai/gpt-oss-20b`, and `google/gemma-4-e2b`. It contains no quantization, GPU layer count, VRAM, RAM, context, KV-cache, or concurrency telemetry. Therefore GPT-OSS-20B GPU/offload suitability on a 16 GB RTX 5060 Ti cannot be established from this benchmark. A live `nvidia-smi` check during this audit saw an RTX 5060 Ti with 16,311 MiB total and 2,508 MiB used, but that is idle host telemetry and does not identify model residency or peak usage. EmbeddingGemma coexistence and vector compatibility are not affected by changing the generation model at the vector-schema level, but simultaneous model residency may cause memory contention and must be measured in LM Studio.",
        "",
        "## Recommendation",
        "",
        "Do not switch the production model solely from this report. GPT-OSS-20B is attractive for latency, throughput, person F1, and JSON reliability, while Gemma 4 E4B is better on measured stance MAE. Citation and topic results are low and matching is strict, so a small paired rerun is justified before a production decision: retain exact original prompts/inputs, add explicit attribution-aware and span-aware diagnostics without replacing the original metrics, run the same cases in randomized model order after warm-up, and record LM Studio quantization/offload/VRAM/RAM telemetry. Until then, retaining Gemma 4 E4B is the lower-risk choice for stance-centric SentimentScope analysis; GPT-OSS-20B should be considered for latency-sensitive or person-resolution stages only after validation.",
    ]
    (OUT / "benchmark_audit.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
