# Targeted GPT-OSS-20B vs Gemma 4 E4B Comparison

Run: `20261107T205852Z`. Synthetic cases: `20`. Original benchmark artifacts were not modified.

## Method

Both models used the existing benchmark runner, identical prompt text, temperature 0, and max_tokens 1200. Exact normalized-set metrics are retained. Span diagnostics use greedy one-to-one token overlap; character similarity is descriptive and does not turn paraphrases into exact matches.

Missing or invalid stance predictions receive an explicit full-coverage penalty of 1.0, the maximum absolute error on the [-1, 1] scale. Matched-case MAE remains separate, and excluded predictions are counted.

Reference stance distribution: `{'positive': 12, 'neutral': 4, 'negative': 6}` across `22` assessments. The synthetic set contains positive, neutral, and negative examples.

## Results

| Model | Exact citation F1 | Span F1 | Person F1 | Topic F1 | Matched stance MAE | Coverage | Full-coverage MAE | JSON validity | Mean latency ms |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| gemma-4-e4b | 0.304 | 0.826 | 0.842 | 0.625 | 0.157 | 0.636 | 0.464 | 0.900 | 32626.2 |
| gpt-oss-20b | 0.089 | 0.844 | 0.923 | 0.591 | 0.417 | 0.545 | 0.682 | 1.000 | 7407.1 |

The complete paired metrics, per-case stance rows, unmatched spans, and raw responses are in `targeted_report.json`.

## Hardware and deployment

The OpenAI-compatible API exposed model IDs but no portable quantization, offload, context, KV-cache, model-load-time, or model-unload telemetry. nvidia-smi snapshots are host observations, not peak per-request measurements. Production end-to-end execution was not run because no isolated production database/API target was configured; the targeted benchmark is independent and does not modify production state.

No production end-to-end run was performed because an isolated production target was not configured. This avoids changing persistent data or conflating the independent benchmark with production state.

## Recommendation

This small paired experiment is descriptive only; it does not establish statistical significance. Select the primary model using full-coverage stance behavior, citation span quality, structured-output reliability, latency, and deployment telemetry together. If hardware telemetry is unavailable, do not claim that GPT-OSS-20B can replace Gemma 4 E4B in the intended concurrent deployment.
