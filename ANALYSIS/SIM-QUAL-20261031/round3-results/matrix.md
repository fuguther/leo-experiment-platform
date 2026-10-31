# Round-3 six-policy matrix — behavioural analysis

Schema `sq-round3/v1`. 6 cells.

- **flip semantics**: `truth_substituted_rerank_not_resimulated_counterfactual` — a truth-substituted re-ranking of the same decision row, **not** a re-simulated counterfactual.
- geometry is the negative control: its J carries no state term, so `flip` and `regret` must be 0 on the evaluable subset.
- `W` is a consecutive-wrong-decision duration; it is not packet queueing time.
- response-sensitivity columns are **behavioural proxies**, not a strict mathematical sensitivity.
- paired deltas only compare cells sharing `trace-manifest:source_trace_sha256`.

## Per-cell metrics

| cell | policy | sync ms | seed | multi | eval | AoI p50 | AoI p90 | m p50 | flip rule | flip truth | R mean | R p90 | switch | run | E2E mean | E2E p95 | E2E p99 | PDR | hops | isl_data | util max | ctrl Mbit | ctrl share |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| m3-c2-geometry-h1000-s7 | geometry | 1000 | 7 | 23497 | 23420 | 506.0 | 908.9 | 13.00 | 0.0000 | 0.0000 | 0.00 | 0.00 | 0.223 | 4.37 | 687.2 | 1188.8 | 1216.8 | 1.00000 | 17.32 | 830691492 | 0.539 | 24.4 | 0.0116 |
| m3-c2-geometry-h1000-s8 | geometry | 1000 | 8 | 22969 | 22888 | 507.1 | 902.6 | 13.05 | 0.0000 | 0.0000 | 0.00 | 0.00 | 0.220 | 4.41 | 648.0 | 1096.8 | 1125.8 | 1.00000 | 17.25 | 734517704 | 0.530 | 24.4 | 0.0116 |
| m3-c2-geometry-h1000-s9 | geometry | 1000 | 9 | 23000 | 22927 | 502.4 | 901.4 | 13.03 | 0.0000 | 0.0000 | 0.00 | 0.00 | 0.223 | 4.37 | 685.4 | 1167.2 | 1183.0 | 1.00000 | 17.43 | 798283280 | 0.540 | 24.4 | 0.0116 |
| m3-c2-queue_greedy-h1000-s7 | queue_greedy | 1000 | 7 | 25757 | 25415 | 510.0 | 909.1 | 10.56 | 0.2777 | 0.2777 | 2.03 | 8.01 | 0.308 | 3.19 | 408.1 | 664.4 | 964.7 | 0.98876 | 23.65 | 35862704 | 0.472 | 24.4 | 0.0116 |
| m3-c2-queue_greedy_ewma-h1000-s7 | queue_greedy_ewma | 1000 | 7 | 25384 | 25252 | 505.8 | 907.7 | 10.68 | 0.2635 | 0.2635 | 1.67 | 6.58 | 0.158 | 6.07 | 396.1 | 526.6 | 680.2 | 0.99658 | 23.04 | 32439279 | 0.472 | 24.4 | 0.0116 |
| m3-c2-queue_greedy_hysteresis-h1000-s9 | queue_greedy_hysteresis | 1000 | 9 | 24855 | 24627 | 497.2 | 904.6 | 10.81 | 0.1788 | 0.2842 | 2.03 | 7.96 | 0.115 | 8.23 | 425.9 | 656.4 | 713.3 | 0.98640 | 24.37 | 44610936 | 0.473 | 24.4 | 0.0116 |

## Policy x regime x sync aggregates (mean [min..max] over seeds)

| group | cells | flip rule | flip truth | R mean ms | AoI p50 | AoI p90 | switch | run | E2E mean | E2E p99 | PDR |
|---|---|---|---|---|---|---|---|---|---|---|---|
| m3|sync|c2|geometry|h1000 | 3 | 0.0000 [0.0000..0.0000] | 0.0000 [0.0000..0.0000] | 0.00 [0.00..0.00] | 505.2 [502.4..507.1] | 904.3 [901.4..908.9] | 0.222 [0.220..0.223] | 4.38 [4.37..4.41] | 673.5 [648.0..687.2] | 1175.2 [1125.8..1216.8] | 1.00000 [1.00000..1.00000] |
| m3|sync|c2|queue_greedy_ewma|h1000 | 1 | 0.2635 [0.2635..0.2635] | 0.2635 [0.2635..0.2635] | 1.67 [1.67..1.67] | 505.8 [505.8..505.8] | 907.7 [907.7..907.7] | 0.158 [0.158..0.158] | 6.07 [6.07..6.07] | 396.1 [396.1..396.1] | 680.2 [680.2..680.2] | 0.99658 [0.99658..0.99658] |
| m3|sync|c2|queue_greedy_hysteresis|h1000 | 1 | 0.1788 [0.1788..0.1788] | 0.2842 [0.2842..0.2842] | 2.03 [2.03..2.03] | 497.2 [497.2..497.2] | 904.6 [904.6..904.6] | 0.115 [0.115..0.115] | 8.23 [8.23..8.23] | 425.9 [425.9..425.9] | 713.3 [713.3..713.3] | 0.98640 [0.98640..0.98640] |
| m3|sync|c2|queue_greedy|h1000 | 1 | 0.2777 [0.2777..0.2777] | 0.2777 [0.2777..0.2777] | 2.03 [2.03..2.03] | 510.0 [510.0..510.0] | 909.1 [909.1..909.1] | 0.308 [0.308..0.308] | 3.19 [3.19..3.19] | 408.1 [408.1..408.1] | 964.7 [964.7..964.7] | 0.98876 [0.98876..0.98876] |

## Matrix identity cross-checks

- seed replicates sharing one trace (must be empty): none
- seeds mapping to several distinct traces (must be empty): none
- cells sharing one config sha256: 0
- cell-id seed vs `scenario.seed`: the replicate seed lives in `trace_program.seed` in this contract; per-cell values are in `meta.seed_note`.

## Consistency cross-checks

- geometry `chosen != recorded policy top` (must be 0): 0 in every cell
- audit identity `resource_work == q_used_bits / rate` violations (must be 0): 0 in every cell
- `chosen != used-state argmin` is expected only for hysteresis/sticky/softmin; per cell values are in the JSON.
- cells whose committed action differed from the used-state argmin: `m3-c2-queue_greedy_hysteresis-h1000-s9` (2954)

## Gamma bins — flip rate (largest regime/sync group)

Cells: `m3-c2-geometry-h1000-s7`, `m3-c2-geometry-h1000-s8`, `m3-c2-geometry-h1000-s9`, `m3-c2-queue_greedy-h1000-s7`, `m3-c2-queue_greedy_ewma-h1000-s7`, `m3-c2-queue_greedy_hysteresis-h1000-s9` (full per-cell bins in the JSON).

| cell | lt_0.5 | 0.5_1 | 1_2 | 2_5 | gt_5 |
|---|---|---|---|---|---|
| m3-c2-geometry-h1000-s7 | 0.000 (15770) | 0.000 (1089) | 0.000 (1012) | 0.000 (1372) | 0.000 (1637) |
| m3-c2-geometry-h1000-s8 | 0.000 (14987) | 0.000 (1260) | 0.000 (1078) | 0.000 (1267) | 0.000 (1872) |
| m3-c2-geometry-h1000-s9 | 0.000 (15348) | 0.000 (1258) | 0.000 (962) | 0.000 (1188) | 0.000 (1692) |
| m3-c2-queue_greedy-h1000-s7 | 0.158 (17434) | 0.463 (2272) | 0.599 (1741) | 0.656 (1388) | 0.760 (1249) |
| m3-c2-queue_greedy_ewma-h1000-s7 | 0.148 (18089) | 0.426 (2022) | 0.567 (1488) | 0.693 (1282) | 0.776 (1449) |
| m3-c2-queue_greedy_hysteresis-h1000-s9 | 0.097 (16412) | 0.279 (2244) | 0.362 (1756) | 0.431 (1624) | 0.456 (1508) |

## Paired E2E inside one trace (delta = policy - geometry)

| group | policy | same trace | paired | mean delta ms | p50 | p95 | faster | slower |
|---|---|---|---|---|---|---|---|---|
| m3|sync|c2|h1000|s7 | queue_greedy | True | 2024 | -277.684 | -156.280 | 215.372 | 1303 | 528 |
| m3|sync|c2|h1000|s7 | queue_greedy_ewma | True | 2040 | -289.760 | -211.829 | 150.196 | 1287 | 540 |
| m3|sync|c2|h1000|s9 | queue_greedy_hysteresis | True | 1958 | -256.921 | -229.868 | 234.388 | 1216 | 555 |

## Response sensitivity (behavioural proxy)

| group | policy | sync | switch | run | flip rule | flip truth | entropy | near-tie 1ms | E2E mean | E2E p99 |
|---|---|---|---|---|---|---|---|---|---|---|
| m3|sync|c2|geometry|h1000 | geometry | 1000 | 0.222 | 4.38 | 0.0000 | 0.0000 | n/a | 0.094 | 673.5 | 1175.2 |
| m3|sync|c2|queue_greedy_ewma|h1000 | queue_greedy_ewma | 1000 | 0.158 | 6.07 | 0.2635 | 0.2635 | n/a | 0.072 | 396.1 | 680.2 |
| m3|sync|c2|queue_greedy_hysteresis|h1000 | queue_greedy_hysteresis | 1000 | 0.115 | 8.23 | 0.1788 | 0.2842 | n/a | 0.080 | 425.9 | 713.3 |
| m3|sync|c2|queue_greedy|h1000 | queue_greedy | 1000 | 0.308 | 3.19 | 0.2777 | 0.2777 | n/a | 0.066 | 408.1 | 964.7 |

_BEHAVIOURAL PROXIES (switch rate, mean run length, flip, entropy, near-tie fraction); not a strict mathematical sensitivity of J_

## Metric gaps and known limitations

- cells without evaluable decisions: none
- cells without an action-entropy audit: 6 of 6
- cells without a sticky audit: 6 of 6
- cells with no Gamma samples: 0 of 6
- cells using the legacy scorer fallback: none
- cells whose id disagrees with the config: none
- cells whose id could not be parsed: none
- action entropy: only queue_softmin audits expose selection.probabilities/entropy
- sticky keep/override: only queue_greedy_sticky audits expose them
- path stretch: no shortest-path baseline in the evidence (hops only)
- window-summed control service time: link_service_windows carry no kind=='ctrl' marker, so occupied.ctrl_isl_s / stop_time is used
- control packet delay: control.instances[3] is an absolute received_at, so advertisement age is taken from the probe (state_received_at_s - state_measured_at_s)
- Gamma needs >=3 truth samples inside +-0.25 s on BOTH resources
