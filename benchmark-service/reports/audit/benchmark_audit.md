# Benchmark Methodology Audit

This is a separate audit of the preserved `benchmark_report.json`; original results are unchanged.

## Executive findings

- Citation, person, and topic matching uses normalized exact set equality. It is not substring, span, semantic, alias, or one-to-one matching.
- Punctuation, quote marks, capitalization, and whitespace are normalized; paraphrases and longer/shorter spans are not matched.
- Duplicate predictions are collapsed by sets, so duplicates are not penalized.
- The original report's `topic_micro_f1` is a macro-like mean of per-case F1 values, not pooled micro-F1.
- Missing stance predictions are excluded, which can make MAE optimistic. The zero-stance baseline is calculated below using all reference stance values.
- Wall time includes the HTTP request and generation. Warm-up, backend offload, KV cache, quantization, and GPU/RAM usage were not recorded.

## Pooled synthetic results

| Model | Citation TP/FP/FN | Citation P/R/F1 | Person TP/FP/FN | Person P/R/F1 | Topic TP/FP/FN | Topic P/R/F1 | Stance pairs | Model MAE | Zero baseline MAE | JSON validity |
|---|---|---|---|---|---|---|---:|---:|---:|---:|
| gemma-4-e2b | 10/9/15 | 0.526/0.400/0.455 | 15/3/5 | 0.833/0.750/0.789 | 12/18/9 | 0.400/0.571/0.471 | 9 | 0.056 | 0.722 | 0.85 |
| gemma-4-e4b | 5/14/20 | 0.263/0.200/0.227 | 15/3/5 | 0.833/0.750/0.789 | 14/11/7 | 0.560/0.667/0.609 | 11 | 0.045 | 0.773 | 0.85 |
| gpt-oss-20b | 2/18/23 | 0.100/0.080/0.089 | 18/1/2 | 0.947/0.900/0.923 | 13/10/8 | 0.565/0.619/0.591 | 12 | 0.083 | 0.833 | 1 |

## Representative extraction and topic errors

These examples are evaluated under the original exact normalized matching rule. A longer predicted sentence containing the expected quote is therefore both a false positive and a false negative, rather than a partial/span match.

### gemma-4-e2b
- **Citation false positives:**
  - `syn-005` expected `backed rail expansion | opposed cutting bus routes`; predicted `councilor ian vale backed rail expansion and opposed cutting bus routes`.
  - `syn-006` expected `neither supporting nor opposing the museum project`; predicted `nora hill said she was neither supporting nor opposing the museum project`.
- **Citation false negatives:**
  - `syn-003` expected `opposed it | supported the solar plan`; predicted `(empty)`.
  - `syn-005` expected `backed rail expansion | opposed cutting bus routes`; predicted `councilor ian vale backed rail expansion and opposed cutting bus routes`.
- **Topic false positives:**
  - `syn-001` expected `public hospital funding`; predicted `funding | public hospitals`.
  - `syn-002` expected `parking fees`; predicted `city policy | parking fees`.
- **Topic false negatives:**
  - `syn-001` expected `public hospital funding`; predicted `funding | public hospitals`.
  - `syn-003` expected `solar plan`; predicted `(empty)`.

### gemma-4-e4b
- **Citation false positives:**
  - `syn-003` expected `opposed it | supported the solar plan`; predicted `lena park supported the solar plan | omar reed opposed it`.
  - `syn-005` expected `backed rail expansion | opposed cutting bus routes`; predicted `councilor ian vale backed rail expansion and opposed cutting bus routes`.
- **Citation false negatives:**
  - `syn-003` expected `opposed it | supported the solar plan`; predicted `lena park supported the solar plan | omar reed opposed it`.
  - `syn-005` expected `backed rail expansion | opposed cutting bus routes`; predicted `councilor ian vale backed rail expansion and opposed cutting bus routes`.
- **Topic false positives:**
  - `syn-001` expected `public hospital funding`; predicted `funding | public hospitals`.
  - `syn-002` expected `parking fees`; predicted `city policy | parking fees`.
- **Topic false negatives:**
  - `syn-001` expected `public hospital funding`; predicted `funding | public hospitals`.
  - `syn-009` expected `the tax`; predicted `(empty)`.

### gpt-oss-20b
- **Citation false positives:**
  - `syn-002` expected `the city would reduce parking fees next year`; predicted `mayor ben cole said that the city would reduce parking fees next year`.
  - `syn-003` expected `opposed it | supported the solar plan`; predicted `lena park supported the solar plan while omar reed opposed it`.
- **Citation false negatives:**
  - `syn-002` expected `the city would reduce parking fees next year`; predicted `mayor ben cole said that the city would reduce parking fees next year`.
  - `syn-003` expected `opposed it | supported the solar plan`; predicted `lena park supported the solar plan while omar reed opposed it`.
- **Topic false positives:**
  - `syn-004` expected `the proposal`; predicted `proposal`.
  - `syn-007` expected `(empty)`; predicted `climate policy`.
- **Topic false negatives:**
  - `syn-004` expected `the proposal`; predicted `proposal`.
  - `syn-009` expected `the tax`; predicted `tax`.

The complete per-case breakdown and all representative examples are in `per_case_metrics.csv` and `benchmark_audit.json`.

## Stance interpretation

The evaluator matches stance topics by normalized exact topic label, then computes mean absolute error over only matched numeric pairs. Unmatched, missing, invalid, and uncertain values are not penalized. This is a material source of optimism. Pearson and Spearman correlations are reported only where at least two matched pairs exist; repeated/tied values make them descriptive rather than strong evidence.

## Performance and output efficiency

The audit adds mean, median, standard deviation, and p95 for provider input/output tokens, reasoning tokens when exposed, reconstructed prompt/input characters, output characters/words/bytes, output/input token ratio, latency, and generated tokens per second. These are stored in `model_audit_metrics.csv`. The original runner did not persist input/output character counts or TTFT.

## Hardware assessment

The preserved report contains only model identifiers: `google/gemma-4-e4b`, `openai/gpt-oss-20b`, and `google/gemma-4-e2b`. It contains no quantization, GPU layer count, VRAM, RAM, context, KV-cache, or concurrency telemetry. Therefore GPT-OSS-20B GPU/offload suitability on a 16 GB RTX 5060 Ti cannot be established from this benchmark. A live `nvidia-smi` check during this audit saw an RTX 5060 Ti with 16,311 MiB total and 2,508 MiB used, but that is idle host telemetry and does not identify model residency or peak usage. EmbeddingGemma coexistence and vector compatibility are not affected by changing the generation model at the vector-schema level, but simultaneous model residency may cause memory contention and must be measured in LM Studio.

## Recommendation

Do not switch the production model solely from this report. GPT-OSS-20B is attractive for latency, throughput, person F1, and JSON reliability, while Gemma 4 E4B is better on measured stance MAE. Citation and topic results are low and matching is strict, so a small paired rerun is justified before a production decision: retain exact original prompts/inputs, add explicit attribution-aware and span-aware diagnostics without replacing the original metrics, run the same cases in randomized model order after warm-up, and record LM Studio quantization/offload/VRAM/RAM telemetry. Until then, retaining Gemma 4 E4B is the lower-risk choice for stance-centric SentimentScope analysis; GPT-OSS-20B should be considered for latency-sensitive or person-resolution stages only after validation.
