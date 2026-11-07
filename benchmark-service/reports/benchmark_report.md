# NewsAnalysis Benchmark Report

Run: `20261107T175813Z`. Endpoint: `http://localhost:1234/v1`.

## Experimental setup
The benchmark used the isolated benchmark-service and identical prompts and inputs for all models. Production analysis code, prompts, and model settings were not modified.

## Dataset
Synthetic dataset: 20 hand-authored cases with explicit references. Real-news dataset: 10 excerpts evaluated separately as automated reference-based exploratory results.

## Model comparison
| Model | Cases | Citation F1 | Person F1 | Topic F1 | Stance MAE | JSON validity | Avg time (ms) | Tokens/s |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| gemma-4-e4b | 20 | 0.225 | 0.6833333333333333 | 0.5333333333333333 | 0.05 | 0.85 | 52078.00615 | 10.485867825781604 |
| gpt-oss-20b | 20 | 0.08333333333333333 | 0.8 | 0.5166666666666666 | 0.1 | 1.0 | 10072.38915 | 16.712551713803034 |
| gemma-4-e2b | 20 | 0.3833333333333333 | 0.6833333333333333 | 0.43333333333333335 | 0.0625 | 0.85 | 68087.6428 | 9.336814725101341 |

## Configuration comparison
The compared configurations use the existing generation temperature option. The production configuration is represented by temperature 0.5; the alternative uses deterministic temperature 0. This stage did not run the production persistence/refinement pipeline, so a fewer-refinement-stage configuration was not evaluated and no claim is made about that alternative. These settings do not change production.

## Stability
Five representative synthetic cases were repeated three times using Gemma 4 E4B. Per-case means, standard deviations, ranges, and structured-output consistency are available in `stability_report.csv`.

## Statistical analysis
Bootstrap 95% confidence intervals are included for case-level citation, person, and topic F1 values in `model_comparison.csv`. These intervals are descriptive; no statistical significance claims are made.

## Limitations and error analysis
Invalid JSON and schema failures are retained in the machine-readable report: 6 of 60 synthetic model-comparison responses failed the JSON/schema checks. The local endpoint did not expose peak VRAM. Real-news cases intentionally have no independently verified labels; their zero reference scores are not accuracy claims. Case-level aggregation avoids treating citations from one article as independent observations.

## Conclusions
On this 20-case synthetic set, Gemma 4 E4B had the lowest stance MAE (0.050) and higher topic F1 than Gemma 4 E2B, but GPT-OSS-20B had the best person F1 (0.800), 100% JSON validity, the lowest mean latency (10,072 ms), and the highest throughput (16.71 tokens/s). Gemma 4 E2B had the highest citation F1 (0.383), but also the slowest mean latency (68,088 ms). The 95% bootstrap intervals overlap substantially, so the sample does not establish statistical significance. The temperature-0.5 configuration had higher citation F1 (0.383 versus 0.258) and topic F1 (0.608 versus 0.533) than temperature 0, at higher mean latency (80,087 versus 70,041 ms); its stance MAE was lower (0.038 versus 0.083). These measurements do not establish a universally optimal model or configuration. GPT-OSS-20B is the strongest measured latency/validity trade-off, while Gemma 4 E4B is competitive on stance quality; larger or alternative pipeline settings require further paired evaluation.
