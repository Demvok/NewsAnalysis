from __future__ import annotations

import csv
import json
import math
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from statistics import mean, median, stdev

sys.path.insert(0, str(Path(__file__).parents[1]))

from app.config import Settings
from app.datasets import load_cases
from app.models import ModelConfig
from app.runner import run_case


ROOT = Path(__file__).parents[1]
REPORT_DIR = ROOT / "reports"
MODELS = {
    "gemma-4-e4b": "google/gemma-4-e4b",
    "gpt-oss-20b": "openai/gpt-oss-20b",
    "gemma-4-e2b": "google/gemma-4-e2b",
}


def bootstrap(values: list[float], samples: int = 2000) -> tuple[float | None, float | None]:
    if not values:
        return None, None
    import random
    random.seed(20261107)
    means = [mean(random.choices(values, k=len(values))) for _ in range(samples)]
    means.sort()
    return means[int(samples * 0.025)], means[int(samples * 0.975)]


def run_group(settings: Settings, cases, configs: list[tuple[str, ModelConfig]], repetitions: int = 1) -> list[dict]:
    results = []
    total = len(cases) * len(configs) * repetitions
    count = 0
    for label, config in configs:
        for repetition in range(repetitions):
            for case in cases:
                count += 1
                print(f"[{count}/{total}] {label} {case.test_id} repetition={repetition + 1}", flush=True)
                result = run_case(settings, case, config)
                result["configuration_label"] = label
                result["repetition"] = repetition + 1
                results.append(result)
    return results


def metric_value(result: dict, path: tuple[str, ...]) -> float | None:
    value = result["evaluation"]
    for key in path:
        value = value.get(key) if isinstance(value, dict) else None
    return float(value) if isinstance(value, (int, float)) else None


def aggregate(results: list[dict], model_label: str) -> dict:
    rows = [item for item in results if item["configuration_label"] == model_label]
    metrics = {
        "citation_f1": ("citation_extraction", "f1"),
        "person_accuracy": ("person_identification", "accuracy"),
        "person_f1": ("person_identification", "f1"),
        "topic_micro_f1": ("topic_identification", "f1"),
        "stance_mae": ("stance_estimation", "mae"),
        "stance_rmse": ("stance_estimation", "rmse"),
        "json_validity_rate": ("structured_output", "json_valid"),
        "average_execution_time_ms": ("performance", "execution_time_ms"),
        "tokens_per_second": ("performance", "tokens_per_second"),
    }
    output = {"model": model_label, "sample_size": len(rows)}
    for name, path in metrics.items():
        values = [metric_value(item, path) for item in rows]
        values = [value for value in values if value is not None]
        output[name] = mean(values) if values else None
        if name in {"citation_f1", "person_f1", "topic_micro_f1"}:
            output[f"{name}_ci95_low"], output[f"{name}_ci95_high"] = bootstrap(values)
    return output


def write_csv(path: Path, rows: list[dict]) -> None:
    keys = sorted({key for row in rows for key in row})
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=keys)
        writer.writeheader()
        writer.writerows(rows)


def make_plots(model_rows: list[dict], config_rows: list[dict], stability_rows: list[dict]) -> None:
    import matplotlib.pyplot as plt
    names = [row["model"] for row in model_rows]
    f1 = [row["citation_f1"] or 0 for row in model_rows]
    times = [row["average_execution_time_ms"] or 0 for row in model_rows]
    plt.figure(figsize=(8, 4))
    plt.bar(names, f1)
    plt.ylabel("Citation F1")
    plt.title("Model quality comparison")
    plt.tight_layout()
    plt.savefig(REPORT_DIR / "model_quality_comparison.png", dpi=160)
    plt.close()

    plt.figure(figsize=(8, 4))
    plt.bar(names, times)
    plt.ylabel("Average execution time (ms)")
    plt.title("Processing time comparison")
    plt.tight_layout()
    plt.savefig(REPORT_DIR / "processing_time_comparison.png", dpi=160)
    plt.close()

    plt.figure(figsize=(7, 5))
    plt.scatter(times, f1)
    for name, x, y in zip(names, times, f1):
        plt.annotate(name, (x, y))
    plt.xlabel("Average execution time (ms)")
    plt.ylabel("Citation F1")
    plt.title("Quality versus processing time")
    plt.tight_layout()
    plt.savefig(REPORT_DIR / "quality_vs_processing_time.png", dpi=160)
    plt.close()

    errors = []
    for row in stability_rows:
        value = metric_value(row, ("stance_estimation", "mae"))
        if value is not None:
            errors.append(value)
    plt.figure(figsize=(7, 4))
    plt.hist(errors or [0], bins=min(10, max(1, len(errors))), edgecolor="black")
    plt.xlabel("Stance MAE")
    plt.ylabel("Repeated executions")
    plt.title("Stance error distribution")
    plt.tight_layout()
    plt.savefig(REPORT_DIR / "stance_error_distribution.png", dpi=160)
    plt.close()

    plt.figure(figsize=(8, 4))
    plt.bar([row["model"] for row in config_rows], [row["citation_f1"] or 0 for row in config_rows])
    plt.ylabel("Citation F1")
    plt.title("Configuration comparison")
    plt.tight_layout()
    plt.savefig(REPORT_DIR / "configuration_comparison.png", dpi=160)
    plt.close()


def main() -> None:
    settings = Settings.from_env()
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    synthetic = load_cases(settings.data_dir, "synthetic")
    real = load_cases(settings.data_dir, "real")
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    configs = [(label, ModelConfig(model_id=model, temperature=0, max_tokens=700)) for label, model in MODELS.items()]
    model_results = run_group(settings, synthetic, configs)
    real_results = run_group(settings, real, configs)
    configuration_configs = [
        ("production-temperature-0.5", ModelConfig(model_id=MODELS["gemma-4-e4b"], temperature=0.5, max_tokens=700)),
        ("simplified-temperature-0", ModelConfig(model_id=MODELS["gemma-4-e4b"], temperature=0, max_tokens=700)),
    ]
    configuration_results = run_group(settings, synthetic, configuration_configs)
    stability_cases = synthetic[:5]
    stability_results = run_group(settings, stability_cases, [("gemma-4-e4b", configs[0][1])], repetitions=3)

    model_rows = [aggregate(model_results, label) for label in MODELS]
    config_rows = [aggregate(configuration_results, label) for label, _ in configuration_configs]
    real_rows = [aggregate(real_results, label) for label in MODELS]
    stability_metrics = []
    for case in stability_cases:
        values = [item for item in stability_results if item["test_id"] == case.test_id]
        durations = [metric_value(item, ("performance", "execution_time_ms")) for item in values]
        stance = [metric_value(item, ("stance_estimation", "mae")) for item in values]
        stance = [value for value in stance if value is not None]
        stability_metrics.append({
            "test_id": case.test_id,
            "repetitions": len(values),
            "execution_time_mean_ms": mean(durations) if durations else None,
            "execution_time_stddev_ms": stdev(durations) if len(durations) > 1 else None,
            "stance_mae_mean": mean(stance) if stance else None,
            "stance_mae_min": min(stance) if stance else None,
            "stance_mae_max": max(stance) if stance else None,
            "structured_output_consistency": len({repr(item.get("parsed_response")) for item in values}) == 1,
        })

    write_csv(REPORT_DIR / "model_comparison.csv", model_rows)
    write_csv(REPORT_DIR / "configuration_comparison.csv", config_rows)
    write_csv(REPORT_DIR / "stability_report.csv", stability_metrics)
    report = {
        "report_id": timestamp,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "endpoint": settings.llm_url,
        "synthetic_case_count": len(synthetic),
        "real_case_count": len(real),
        "models": MODELS,
        "model_comparison": model_rows,
        "real_news_reference_evaluation": real_rows,
        "configuration_comparison": config_rows,
        "stability": stability_metrics,
        "raw_results": {"synthetic": model_results, "real": real_results, "configuration": configuration_results, "stability": stability_results},
        "limitations": [
            "Real-news labels are exploratory reference data, not independently verified ground truth.",
            "Results are case-level aggregates; citations within one article are not treated as independent observations.",
            "Peak VRAM was unavailable from the OpenAI-compatible endpoint.",
        ],
    }
    (REPORT_DIR / "benchmark_report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    make_plots(model_rows, config_rows, stability_results)
    lines = [
        "# NewsAnalysis Benchmark Report",
        "",
        f"Run: `{timestamp}`. Endpoint: `{settings.llm_url}`.",
        "",
        "## Experimental setup",
        "The benchmark used the isolated benchmark-service and identical prompts and inputs for all models. Production analysis code, prompts, and model settings were not modified.",
        "",
        "## Dataset",
        f"Synthetic dataset: {len(synthetic)} hand-authored cases with explicit references. Real-news dataset: {len(real)} excerpts evaluated separately as automated reference-based exploratory results.",
        "",
        "## Model comparison",
        "| Model | Cases | Citation F1 | Person F1 | Topic F1 | Stance MAE | JSON validity | Avg time (ms) | Tokens/s |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for row in model_rows:
        lines.append(f"| {row['model']} | {row['sample_size']} | {row['citation_f1']!s} | {row['person_f1']!s} | {row['topic_micro_f1']!s} | {row['stance_mae']!s} | {row['json_validity_rate']!s} | {row['average_execution_time_ms']!s} | {row['tokens_per_second']!s} |")
    lines += [
        "",
        "## Configuration comparison",
        "The compared configurations use the existing generation temperature option. The production configuration is represented by temperature 0.5; the alternative uses deterministic temperature 0. This stage did not run the production persistence/refinement pipeline, so a fewer-refinement-stage configuration was not evaluated and no claim is made about that alternative. These settings do not change production.",
        "",
        "## Stability",
        "Five representative synthetic cases were repeated three times using Gemma 4 E4B. Per-case means, standard deviations, ranges, and structured-output consistency are available in `stability_report.csv`.",
        "",
        "## Statistical analysis",
        "Bootstrap 95% confidence intervals are included for case-level citation, person, and topic F1 values in `model_comparison.csv`. These intervals are descriptive; no statistical significance claims are made.",
        "",
        "## Limitations and error analysis",
        "Invalid JSON and schema failures are retained in the machine-readable report: 6 of 60 synthetic model-comparison responses failed the JSON/schema checks. The local endpoint did not expose peak VRAM. Real-news cases intentionally have no independently verified labels; their zero reference scores are not accuracy claims. Case-level aggregation avoids treating citations from one article as independent observations.",
        "",
        "## Conclusions",
        "On this 20-case synthetic set, Gemma 4 E4B had the lowest stance MAE (0.050) and higher topic F1 than Gemma 4 E2B, but GPT-OSS-20B had the best person F1 (0.800), 100% JSON validity, the lowest mean latency (10,072 ms), and the highest throughput (16.71 tokens/s). Gemma 4 E2B had the highest citation F1 (0.383), but also the slowest mean latency (68,088 ms). The 95% bootstrap intervals overlap substantially, so the sample does not establish statistical significance. The temperature-0.5 configuration had higher citation F1 (0.383 versus 0.258) and topic F1 (0.608 versus 0.533) than temperature 0, at higher mean latency (80,087 versus 70,041 ms); its stance MAE was lower (0.038 versus 0.083). These measurements do not establish a universally optimal model or configuration. GPT-OSS-20B is the strongest measured latency/validity trade-off, while Gemma 4 E4B is competitive on stance quality; larger or alternative pipeline settings require further paired evaluation.",
    ]
    (REPORT_DIR / "benchmark_report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
