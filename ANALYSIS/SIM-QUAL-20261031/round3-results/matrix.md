# Round-3 six-policy matrix — behavioural analysis

Schema `sq-round3/v1`. 22 cells.

- **flip semantics**: `truth_substituted_rerank_not_resimulated_counterfactual` — a truth-substituted re-ranking of the same decision row, **not** a re-simulated counterfactual.
- geometry is the negative control: its J carries no state term, so `flip` and `regret` must be 0 on the evaluable subset.
- `W` is a consecutive-wrong-decision duration; it is not packet queueing time.
- response-sensitivity columns are **behavioural proxies**, not a strict mathematical sensitivity.
- paired deltas only compare cells sharing `trace-manifest:source_trace_sha256`.

## Per-cell metrics

| cell | policy | sync ms | seed | multi | eval | AoI p50 | AoI p90 | m p50 | flip rule | flip truth | R mean | R p90 | switch | run | E2E mean | E2E p95 | E2E p99 | PDR | hops | isl_data | util max | ctrl Mbit | ctrl share |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| m3-b-geometry-h1000-s8 | geometry | 1000 | 8 | 18627 | 18557 | 505.2 | 901.9 | 13.05 | 0.0000 | 0.0000 | 0.00 | 0.00 | 0.218 | 4.44 | 627.3 | 986.2 | 1005.2 | 1.00000 | 16.73 | 570412077 | 0.529 | 24.4 | 0.0116 |
| m3-b-geometry-h1000-s9 | geometry | 1000 | 9 | 18698 | 18637 | 516.2 | 901.9 | 13.06 | 0.0000 | 0.0000 | 0.00 | 0.00 | 0.227 | 4.27 | 683.0 | 1053.3 | 1074.1 | 1.00000 | 16.90 | 651840853 | 0.534 | 24.4 | 0.0116 |
| m3-b-queue_greedy-h1000-s7 | queue_greedy | 1000 | 7 | 20006 | 19891 | 493.3 | 898.0 | 11.33 | 0.2716 | 0.2716 | 1.75 | 7.24 | 0.240 | 4.04 | 364.3 | 581.0 | 667.9 | 0.99758 | 21.46 | 18100983 | 0.439 | 24.4 | 0.0116 |
| m3-b-queue_greedy-h1000-s8 | queue_greedy | 1000 | 8 | 19448 | 19331 | 494.3 | 906.6 | 11.94 | 0.2752 | 0.2752 | 1.79 | 7.45 | 0.230 | 4.21 | 379.0 | 588.6 | 687.5 | 0.99938 | 22.29 | 18471957 | 0.438 | 24.4 | 0.0116 |
| m3-b-queue_greedy-h1000-s9 | queue_greedy | 1000 | 9 | 19749 | 19625 | 507.3 | 898.8 | 11.36 | 0.2824 | 0.2824 | 1.95 | 8.04 | 0.252 | 3.85 | 379.6 | 601.1 | 717.9 | 0.99749 | 22.35 | 18167381 | 0.431 | 24.4 | 0.0116 |
| m3-b-queue_greedy_hysteresis-h1000-s7 | queue_greedy_hysteresis | 1000 | 7 | 22066 | 21566 | 495.9 | 901.4 | 11.08 | 0.2144 | 0.3052 | 2.27 | 8.40 | 0.148 | 6.43 | 428.4 | 775.5 | 942.7 | 0.97636 | 24.99 | 26342165 | 0.445 | 24.4 | 0.0116 |
| m3-c2-geometry-h1000-s7 | geometry | 1000 | 7 | 23497 | 23420 | 506.0 | 908.9 | 13.00 | 0.0000 | 0.0000 | 0.00 | 0.00 | 0.223 | 4.37 | 687.2 | 1188.8 | 1216.8 | 1.00000 | 17.32 | 830691492 | 0.539 | 24.4 | 0.0116 |
| m3-c2-geometry-h1000-s8 | geometry | 1000 | 8 | 22969 | 22888 | 507.1 | 902.6 | 13.05 | 0.0000 | 0.0000 | 0.00 | 0.00 | 0.220 | 4.41 | 648.0 | 1096.8 | 1125.8 | 1.00000 | 17.25 | 734517704 | 0.530 | 24.4 | 0.0116 |
| m3-c2-geometry-h1000-s9 | geometry | 1000 | 9 | 23000 | 22927 | 502.4 | 901.4 | 13.03 | 0.0000 | 0.0000 | 0.00 | 0.00 | 0.223 | 4.37 | 685.4 | 1167.2 | 1183.0 | 1.00000 | 17.43 | 798283280 | 0.540 | 24.4 | 0.0116 |
| m3-c2-queue_greedy-h1000-s7 | queue_greedy | 1000 | 7 | 25757 | 25415 | 510.0 | 909.1 | 10.56 | 0.2777 | 0.2777 | 2.03 | 8.01 | 0.308 | 3.19 | 408.1 | 664.4 | 964.7 | 0.98876 | 23.65 | 35862704 | 0.472 | 24.4 | 0.0116 |
| m3-c2-queue_greedy_ewma-h1000-s7 | queue_greedy_ewma | 1000 | 7 | 25384 | 25252 | 505.8 | 907.7 | 10.68 | 0.2635 | 0.2635 | 1.67 | 6.58 | 0.158 | 6.07 | 396.1 | 526.6 | 680.2 | 0.99658 | 23.04 | 32439279 | 0.472 | 24.4 | 0.0116 |
| m3-c2-queue_greedy_ewma-h1000-s8 | queue_greedy_ewma | 1000 | 8 | 24194 | 24078 | 508.2 | 907.2 | 10.60 | 0.2660 | 0.2660 | 1.74 | 7.40 | 0.218 | 4.46 | 364.5 | 521.3 | 674.3 | 0.99800 | 21.36 | 26165832 | 0.471 | 24.4 | 0.0116 |
| m3-c2-queue_greedy_ewma-h1000-s9 | queue_greedy_ewma | 1000 | 9 | 25045 | 24905 | 493.8 | 902.5 | 10.95 | 0.2756 | 0.2756 | 1.82 | 7.57 | 0.212 | 4.59 | 382.2 | 538.9 | 684.2 | 0.99698 | 22.38 | 27193231 | 0.470 | 24.4 | 0.0116 |
| m3-c2-queue_greedy_hysteresis-h1000-s7 | queue_greedy_hysteresis | 1000 | 7 | 26612 | 26200 | 505.1 | 908.8 | 11.41 | 0.1927 | 0.2899 | 2.12 | 8.07 | 0.167 | 5.78 | 446.5 | 776.1 | 993.4 | 0.98290 | 25.51 | 50544066 | 0.472 | 24.4 | 0.0116 |
| m3-c2-queue_greedy_hysteresis-h1000-s8 | queue_greedy_hysteresis | 1000 | 8 | 25827 | 25367 | 504.4 | 906.3 | 10.97 | 0.1872 | 0.2839 | 2.07 | 7.87 | 0.149 | 6.41 | 441.1 | 701.4 | 807.8 | 0.96446 | 25.34 | 42956224 | 0.476 | 24.4 | 0.0116 |
| m3-c2-queue_greedy_hysteresis-h1000-s9 | queue_greedy_hysteresis | 1000 | 9 | 24855 | 24627 | 497.2 | 904.6 | 10.81 | 0.1788 | 0.2842 | 2.03 | 7.96 | 0.115 | 8.23 | 425.9 | 656.4 | 713.3 | 0.98640 | 24.37 | 44610936 | 0.473 | 24.4 | 0.0116 |
| m3-c2-queue_greedy_sticky-h1000-s7 | queue_greedy_sticky | 1000 | 7 | 25663 | 25083 | 502.1 | 902.8 | 12.22 | 0.1864 | 0.4631 | 10.37 | 34.68 | 0.181 | 5.29 | 484.1 | 975.9 | 1159.7 | 0.93747 | 24.39 | 157494777 | 0.395 | 24.4 | 0.0116 |
| m3-c2-queue_greedy_sticky-h1000-s8 | queue_greedy_sticky | 1000 | 8 | 24149 | 23713 | 500.9 | 909.4 | 12.27 | 0.1905 | 0.4545 | 9.18 | 30.21 | 0.174 | 5.49 | 455.3 | 881.5 | 1085.5 | 0.94745 | 23.50 | 125885861 | 0.370 | 24.4 | 0.0116 |
| m3-c2-queue_greedy_sticky-h1000-s9 | queue_greedy_sticky | 1000 | 9 | 24900 | 24310 | 502.2 | 904.0 | 12.09 | 0.2005 | 0.4841 | 10.95 | 36.24 | 0.177 | 5.41 | 480.7 | 965.5 | 1134.8 | 0.92997 | 24.13 | 155805241 | 0.381 | 24.4 | 0.0116 |
| m3-c2-queue_softmin-h1000-s7 | queue_softmin | 1000 | 7 | 30149 | 29135 | 504.2 | 908.2 | 11.07 | 0.2668 | 0.3991 | 4.66 | 16.54 | 0.630 | 1.58 | 497.2 | 901.9 | 1011.1 | 0.81290 | 28.57 | 44300610 | 0.452 | 24.4 | 0.0116 |
| m3-c2-queue_softmin-h1000-s8 | queue_softmin | 1000 | 8 | 29053 | 28056 | 512.4 | 909.9 | 11.19 | 0.2712 | 0.3992 | 4.62 | 16.28 | 0.625 | 1.59 | 487.0 | 916.0 | 1053.7 | 0.82833 | 28.16 | 34412915 | 0.444 | 24.4 | 0.0116 |
| m3-c2-queue_softmin-h1000-s9 | queue_softmin | 1000 | 9 | 28971 | 28095 | 500.6 | 903.7 | 11.33 | 0.2760 | 0.4061 | 4.74 | 16.63 | 0.627 | 1.59 | 494.8 | 906.5 | 1047.7 | 0.82972 | 28.51 | 38664846 | 0.448 | 24.4 | 0.0116 |

## Policy x regime x sync aggregates (mean [min..max] over seeds)

| group | cells | flip rule | flip truth | R mean ms | AoI p50 | AoI p90 | switch | run | E2E mean | E2E p99 | PDR |
|---|---|---|---|---|---|---|---|---|---|---|---|
| m3|sync|b|geometry|h1000 | 2 | 0.0000 [0.0000..0.0000] | 0.0000 [0.0000..0.0000] | 0.00 [0.00..0.00] | 510.7 [505.2..516.2] | 901.9 [901.9..901.9] | 0.222 [0.218..0.227] | 4.35 [4.27..4.44] | 655.2 [627.3..683.0] | 1039.7 [1005.2..1074.1] | 1.00000 [1.00000..1.00000] |
| m3|sync|b|queue_greedy_hysteresis|h1000 | 1 | 0.2144 [0.2144..0.2144] | 0.3052 [0.3052..0.3052] | 2.27 [2.27..2.27] | 495.9 [495.9..495.9] | 901.4 [901.4..901.4] | 0.148 [0.148..0.148] | 6.43 [6.43..6.43] | 428.4 [428.4..428.4] | 942.7 [942.7..942.7] | 0.97636 [0.97636..0.97636] |
| m3|sync|b|queue_greedy|h1000 | 3 | 0.2764 [0.2716..0.2824] | 0.2764 [0.2716..0.2824] | 1.83 [1.75..1.95] | 498.3 [493.3..507.3] | 901.1 [898.0..906.6] | 0.240 [0.230..0.252] | 4.03 [3.85..4.21] | 374.3 [364.3..379.6] | 691.1 [667.9..717.9] | 0.99815 [0.99749..0.99938] |
| m3|sync|c2|geometry|h1000 | 3 | 0.0000 [0.0000..0.0000] | 0.0000 [0.0000..0.0000] | 0.00 [0.00..0.00] | 505.2 [502.4..507.1] | 904.3 [901.4..908.9] | 0.222 [0.220..0.223] | 4.38 [4.37..4.41] | 673.5 [648.0..687.2] | 1175.2 [1125.8..1216.8] | 1.00000 [1.00000..1.00000] |
| m3|sync|c2|queue_greedy_ewma|h1000 | 3 | 0.2684 [0.2635..0.2756] | 0.2684 [0.2635..0.2756] | 1.74 [1.67..1.82] | 502.6 [493.8..508.2] | 905.8 [902.5..907.7] | 0.196 [0.158..0.218] | 5.04 [4.46..6.07] | 380.9 [364.5..396.1] | 679.6 [674.3..684.2] | 0.99719 [0.99658..0.99800] |
| m3|sync|c2|queue_greedy_hysteresis|h1000 | 3 | 0.1862 [0.1788..0.1927] | 0.2860 [0.2839..0.2899] | 2.07 [2.03..2.12] | 502.2 [497.2..505.1] | 906.6 [904.6..908.8] | 0.144 [0.115..0.167] | 6.81 [5.78..8.23] | 437.8 [425.9..446.5] | 838.2 [713.3..993.4] | 0.97792 [0.96446..0.98640] |
| m3|sync|c2|queue_greedy_sticky|h1000 | 3 | 0.1925 [0.1864..0.2005] | 0.4672 [0.4545..0.4841] | 10.17 [9.18..10.95] | 501.7 [500.9..502.2] | 905.4 [902.8..909.4] | 0.178 [0.174..0.181] | 5.40 [5.29..5.49] | 473.3 [455.3..484.1] | 1126.7 [1085.5..1159.7] | 0.93830 [0.92997..0.94745] |
| m3|sync|c2|queue_greedy|h1000 | 1 | 0.2777 [0.2777..0.2777] | 0.2777 [0.2777..0.2777] | 2.03 [2.03..2.03] | 510.0 [510.0..510.0] | 909.1 [909.1..909.1] | 0.308 [0.308..0.308] | 3.19 [3.19..3.19] | 408.1 [408.1..408.1] | 964.7 [964.7..964.7] | 0.98876 [0.98876..0.98876] |
| m3|sync|c2|queue_softmin|h1000 | 3 | 0.2713 [0.2668..0.2760] | 0.4015 [0.3991..0.4061] | 4.67 [4.62..4.74] | 505.8 [500.6..512.4] | 907.3 [903.7..909.9] | 0.627 [0.625..0.630] | 1.58 [1.58..1.59] | 493.0 [487.0..497.2] | 1037.5 [1011.1..1053.7] | 0.82365 [0.81290..0.82972] |

## Matrix identity cross-checks

- seed replicates sharing one trace (must be empty): none
- seeds mapping to several distinct traces (must be empty): none
- cells sharing one config sha256: 0
- cell-id seed vs `scenario.seed`: the replicate seed lives in `trace_program.seed` in this contract; per-cell values are in `meta.seed_note`.

## Consistency cross-checks

- geometry `chosen != recorded policy top` (must be 0): 0 in every cell
- audit identity `resource_work == q_used_bits / rate` violations (must be 0): 0 in every cell
- `chosen != used-state argmin` is expected only for hysteresis/sticky/softmin; per cell values are in the JSON.
- cells whose committed action differed from the used-state argmin: `m3-b-queue_greedy_hysteresis-h1000-s7` (2123), `m3-c2-queue_greedy_hysteresis-h1000-s7` (2756), `m3-c2-queue_greedy_hysteresis-h1000-s8` (2546), `m3-c2-queue_greedy_hysteresis-h1000-s9` (2954), `m3-c2-queue_greedy_sticky-h1000-s7` (5694), `m3-c2-queue_greedy_sticky-h1000-s8` (4939), `m3-c2-queue_greedy_sticky-h1000-s9` (5448), `m3-c2-queue_softmin-h1000-s7` (8211), `m3-c2-queue_softmin-h1000-s8` (7816), `m3-c2-queue_softmin-h1000-s9` (7925)

## Gamma bins — flip rate (largest regime/sync group)

Cells: `m3-c2-geometry-h1000-s7`, `m3-c2-geometry-h1000-s8`, `m3-c2-geometry-h1000-s9`, `m3-c2-queue_greedy-h1000-s7`, `m3-c2-queue_greedy_ewma-h1000-s7`, `m3-c2-queue_greedy_ewma-h1000-s8`, `m3-c2-queue_greedy_ewma-h1000-s9`, `m3-c2-queue_greedy_hysteresis-h1000-s7`, `m3-c2-queue_greedy_hysteresis-h1000-s8`, `m3-c2-queue_greedy_hysteresis-h1000-s9`, `m3-c2-queue_greedy_sticky-h1000-s7`, `m3-c2-queue_greedy_sticky-h1000-s8`, `m3-c2-queue_greedy_sticky-h1000-s9`, `m3-c2-queue_softmin-h1000-s7`, `m3-c2-queue_softmin-h1000-s8`, `m3-c2-queue_softmin-h1000-s9` (full per-cell bins in the JSON).

| cell | lt_0.5 | 0.5_1 | 1_2 | 2_5 | gt_5 |
|---|---|---|---|---|---|
| m3-c2-geometry-h1000-s7 | 0.000 (15770) | 0.000 (1089) | 0.000 (1012) | 0.000 (1372) | 0.000 (1637) |
| m3-c2-geometry-h1000-s8 | 0.000 (14987) | 0.000 (1260) | 0.000 (1078) | 0.000 (1267) | 0.000 (1872) |
| m3-c2-geometry-h1000-s9 | 0.000 (15348) | 0.000 (1258) | 0.000 (962) | 0.000 (1188) | 0.000 (1692) |
| m3-c2-queue_greedy-h1000-s7 | 0.158 (17434) | 0.463 (2272) | 0.599 (1741) | 0.656 (1388) | 0.760 (1249) |
| m3-c2-queue_greedy_ewma-h1000-s7 | 0.148 (18089) | 0.426 (2022) | 0.567 (1488) | 0.693 (1282) | 0.776 (1449) |
| m3-c2-queue_greedy_ewma-h1000-s8 | 0.123 (16952) | 0.503 (1758) | 0.631 (1449) | 0.751 (1368) | 0.858 (1446) |
| m3-c2-queue_greedy_ewma-h1000-s9 | 0.142 (17666) | 0.531 (1951) | 0.646 (1527) | 0.755 (1269) | 0.777 (1481) |
| m3-c2-queue_greedy_hysteresis-h1000-s7 | 0.109 (17981) | 0.302 (2316) | 0.389 (1968) | 0.461 (1621) | 0.546 (1305) |
| m3-c2-queue_greedy_hysteresis-h1000-s8 | 0.104 (16815) | 0.297 (2499) | 0.368 (1824) | 0.412 (1569) | 0.517 (1468) |
| m3-c2-queue_greedy_hysteresis-h1000-s9 | 0.097 (16412) | 0.279 (2244) | 0.362 (1756) | 0.431 (1624) | 0.456 (1508) |
| m3-c2-queue_greedy_sticky-h1000-s7 | 0.093 (13118) | 0.189 (3039) | 0.295 (2594) | 0.346 (2712) | 0.402 (2403) |
| m3-c2-queue_greedy_sticky-h1000-s8 | 0.107 (13356) | 0.238 (2710) | 0.281 (2352) | 0.313 (2039) | 0.454 (2047) |
| m3-c2-queue_greedy_sticky-h1000-s9 | 0.109 (13033) | 0.233 (2756) | 0.300 (2396) | 0.327 (2446) | 0.435 (2384) |
| m3-c2-queue_softmin-h1000-s7 | 0.191 (19612) | 0.381 (3413) | 0.426 (2375) | 0.474 (1921) | 0.542 (1025) |
| m3-c2-queue_softmin-h1000-s8 | 0.199 (18726) | 0.371 (3308) | 0.424 (2382) | 0.477 (1838) | 0.498 (1018) |
| m3-c2-queue_softmin-h1000-s9 | 0.202 (18837) | 0.383 (3249) | 0.444 (2381) | 0.490 (1804) | 0.525 (954) |

## Paired E2E inside one trace (delta = policy - geometry)

| group | policy | same trace | paired | mean delta ms | p50 | p95 | faster | slower |
|---|---|---|---|---|---|---|---|---|
| m3|sync|b|h1000|s8 | queue_greedy | True | 1602 | -248.120 | -239.089 | 170.833 | 1166 | 292 |
| m3|sync|b|h1000|s9 | queue_greedy | True | 1590 | -303.112 | -378.472 | 144.623 | 1209 | 232 |
| m3|sync|c2|h1000|s7 | queue_greedy | True | 2024 | -277.684 | -156.280 | 215.372 | 1303 | 528 |
| m3|sync|c2|h1000|s7 | queue_greedy_ewma | True | 2040 | -289.760 | -211.829 | 150.196 | 1287 | 540 |
| m3|sync|c2|h1000|s7 | queue_greedy_hysteresis | True | 2012 | -237.107 | -117.674 | 289.946 | 1217 | 628 |
| m3|sync|c2|h1000|s7 | queue_greedy_sticky | True | 1919 | -196.242 | -97.640 | 441.156 | 1215 | 533 |
| m3|sync|c2|h1000|s7 | queue_softmin | True | 1664 | -180.328 | -96.580 | 484.379 | 991 | 620 |
| m3|sync|c2|h1000|s8 | queue_greedy_ewma | True | 1994 | -283.376 | -220.650 | 143.512 | 1330 | 446 |
| m3|sync|c2|h1000|s8 | queue_greedy_hysteresis | True | 1927 | -199.167 | -104.782 | 276.354 | 1197 | 579 |
| m3|sync|c2|h1000|s8 | queue_greedy_sticky | True | 1893 | -183.859 | -82.325 | 378.500 | 1184 | 574 |
| m3|sync|c2|h1000|s8 | queue_softmin | True | 1655 | -144.081 | -70.971 | 435.121 | 947 | 655 |
| m3|sync|c2|h1000|s9 | queue_greedy_ewma | True | 1979 | -302.389 | -310.803 | 145.333 | 1290 | 480 |
| m3|sync|c2|h1000|s9 | queue_greedy_hysteresis | True | 1958 | -256.921 | -229.868 | 234.388 | 1216 | 555 |
| m3|sync|c2|h1000|s9 | queue_greedy_sticky | True | 1846 | -196.457 | -106.381 | 419.964 | 1168 | 537 |
| m3|sync|c2|h1000|s9 | queue_softmin | True | 1647 | -176.994 | -96.967 | 464.161 | 968 | 615 |

## Response sensitivity (behavioural proxy)

| group | policy | sync | switch | run | flip rule | flip truth | entropy | near-tie 1ms | E2E mean | E2E p99 |
|---|---|---|---|---|---|---|---|---|---|---|
| m3|sync|b|geometry|h1000 | geometry | 1000 | 0.222 | 4.35 | 0.0000 | 0.0000 | n/a | 0.091 | 655.2 | 1039.7 |
| m3|sync|b|queue_greedy_hysteresis|h1000 | queue_greedy_hysteresis | 1000 | 0.148 | 6.43 | 0.2144 | 0.3052 | n/a | 0.055 | 428.4 | 942.7 |
| m3|sync|b|queue_greedy|h1000 | queue_greedy | 1000 | 0.240 | 4.03 | 0.2764 | 0.2764 | n/a | 0.067 | 374.3 | 691.1 |
| m3|sync|c2|geometry|h1000 | geometry | 1000 | 0.222 | 4.38 | 0.0000 | 0.0000 | n/a | 0.094 | 673.5 | 1175.2 |
| m3|sync|c2|queue_greedy_ewma|h1000 | queue_greedy_ewma | 1000 | 0.196 | 5.04 | 0.2684 | 0.2684 | n/a | 0.087 | 380.9 | 679.6 |
| m3|sync|c2|queue_greedy_hysteresis|h1000 | queue_greedy_hysteresis | 1000 | 0.144 | 6.81 | 0.1862 | 0.2860 | n/a | 0.064 | 437.8 | 838.2 |
| m3|sync|c2|queue_greedy_sticky|h1000 | queue_greedy_sticky | 1000 | 0.178 | 5.40 | 0.1925 | 0.4672 | n/a | 0.057 | 473.3 | 1126.7 |
| m3|sync|c2|queue_greedy|h1000 | queue_greedy | 1000 | 0.308 | 3.19 | 0.2777 | 0.2777 | n/a | 0.066 | 408.1 | 964.7 |
| m3|sync|c2|queue_softmin|h1000 | queue_softmin | 1000 | 0.627 | 1.58 | 0.2713 | 0.4015 | 0.890 | 0.042 | 493.0 | 1037.5 |

_BEHAVIOURAL PROXIES (switch rate, mean run length, flip, entropy, near-tie fraction); not a strict mathematical sensitivity of J_

## Metric gaps and known limitations

- cells without evaluable decisions: none
- cells without an action-entropy audit: 19 of 22
- cells without a sticky audit: 19 of 22
- cells with no Gamma samples: 0 of 22
- cells using the legacy scorer fallback: none
- cells whose id disagrees with the config: none
- cells whose id could not be parsed: none
- action entropy: only queue_softmin audits expose selection.probabilities/entropy
- sticky keep/override: only queue_greedy_sticky audits expose them
- path stretch: no shortest-path baseline in the evidence (hops only)
- window-summed control service time: link_service_windows carry no kind=='ctrl' marker, so occupied.ctrl_isl_s / stop_time is used
- control packet delay: control.instances[3] is an absolute received_at, so advertisement age is taken from the probe (state_received_at_s - state_measured_at_s)
- Gamma needs >=3 truth samples inside +-0.25 s on BOTH resources
- paired groups without the baseline policy: 1
