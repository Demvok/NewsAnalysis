from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(ROOT))

from run_targeted_comparison import aggregate


def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit("usage: repair_targeted_report.py <targeted-report.json>")
    report_path = Path(sys.argv[1])
    report = json.loads(report_path.read_text(encoding="utf-8"))
    cases = {
        item["test_id"]: item["input"]
        for item in report["paired_cases"]
    }
    stance_values = [
        item.get("score")
        for case in cases.values()
        for item in case.get("expected_stance", [])
        if isinstance(item.get("score"), (int, float))
    ]
    report["dataset"]["stance_reference_count"] = len(stance_values)
    report["dataset"]["stance_distribution"] = {
        "positive": sum(value > 0.2 for value in stance_values),
        "neutral": sum(-0.2 <= value <= 0.2 for value in stance_values),
        "negative": sum(value < -0.2 for value in stance_values),
    }
    report["summary"] = aggregate(report["paired_cases"])
    report_path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    output = report_path.with_name("targeted_report.md")
    lines = [
        "# Targeted GPT-OSS-20B vs Gemma 4 E4B Comparison",
        "",
        f"Run: `{report['report_id']}`. Synthetic cases: `{report['dataset']['case_count']}`. Original benchmark artifacts were not modified.",
        "",
        "## Method",
        "",
        "Both models used the existing benchmark runner, identical prompt text, temperature 0, and max_tokens 1200. Exact normalized-set metrics are retained. Span diagnostics use greedy one-to-one token overlap; character similarity is descriptive and does not turn paraphrases into exact matches.",
        "",
        "Missing or invalid stance predictions receive an explicit full-coverage penalty of 1.0, the maximum absolute error on the [-1, 1] scale. Matched-case MAE remains separate, and excluded predictions are counted.",
        "",
        f"Reference stance distribution: `{report['dataset'].get('stance_distribution', {})}` across `{report['dataset'].get('stance_reference_count', 'unknown')}` assessments. The synthetic set contains positive, neutral, and negative examples.",
        "",
        "## Results",
        "",
        "| Model | Exact citation F1 | Span F1 | Person F1 | Topic F1 | Matched stance MAE | Coverage | Full-coverage MAE | JSON validity | Mean latency ms |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    cases = report["dataset"]["case_count"]
    for model, summary in report["summary"].items():
        stance = summary["stance"]
        valid = sum(
            item["evaluation"]["structured_output"]["json_valid"]
            for item in report["paired_cases"]
            if item["model_key"] == model
        ) / cases
        lines.append(
            f"| {model} | {summary['citation_exact']['f1']:.3f} | {summary['citation_span']['f1']:.3f} | "
            f"{summary['person_exact']['f1']:.3f} | {summary['topic_exact']['f1']:.3f} | "
            f"{stance['matched_mae']:.3f} | {stance['coverage']:.3f} | {stance['full_coverage_mae']:.3f} | "
            f"{valid:.3f} | {summary['performance']['latency_mean_ms']:.1f} |"
        )
    lines += [
        "",
        "The complete paired metrics, per-case stance rows, unmatched spans, and raw responses are in `targeted_report.json`.",
        "",
        "## Hardware and deployment",
        "",
        report["hardware"]["deployment_note"],
        "",
        "No production end-to-end run was performed because an isolated production target was not configured. This avoids changing persistent data or conflating the independent benchmark with production state.",
        "",
        "## Recommendation",
        "",
        "This small paired experiment is descriptive only; it does not establish statistical significance. Select the primary model using full-coverage stance behavior, citation span quality, structured-output reliability, latency, and deployment telemetry together. If hardware telemetry is unavailable, do not claim that GPT-OSS-20B can replace Gemma 4 E4B in the intended concurrent deployment.",
    ]
    output.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(report_path)


if __name__ == "__main__":
    main()
