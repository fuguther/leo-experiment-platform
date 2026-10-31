# Round-3 six-policy matrix — behavioural analysis

Schema `sq-round3/v1`. 33 cells.

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
| m3-b-queue_greedy_ewma-h1000-s7 | queue_greedy_ewma | 1000 | 7 | 20186 | 20124 | 495.9 | 899.3 | 10.97 | 0.2692 | 0.2692 | 1.64 | 6.92 | 0.138 | 6.84 | 365.6 | 504.9 | 666.9 | 0.99939 | 21.53 | 18647155 | 0.440 | 24.4 | 0.0116 |
| m3-b-queue_greedy_ewma-h1000-s8 | queue_greedy_ewma | 1000 | 8 | 19400 | 19304 | 496.8 | 907.4 | 11.46 | 0.2682 | 0.2682 | 1.83 | 7.56 | 0.175 | 5.45 | 339.9 | 504.7 | 638.5 | 0.99938 | 20.20 | 13651159 | 0.438 | 24.4 | 0.0116 |
| m3-b-queue_greedy_ewma-h1000-s9 | queue_greedy_ewma | 1000 | 9 | 19423 | 19361 | 504.5 | 900.1 | 10.98 | 0.2564 | 0.2564 | 1.57 | 6.96 | 0.155 | 6.12 | 368.5 | 509.2 | 582.6 | 1.00000 | 21.80 | 15396594 | 0.431 | 24.4 | 0.0116 |
| m3-b-queue_greedy_hysteresis-h1000-s7 | queue_greedy_hysteresis | 1000 | 7 | 22066 | 21566 | 495.9 | 901.4 | 11.08 | 0.2144 | 0.3052 | 2.27 | 8.40 | 0.148 | 6.43 | 428.4 | 775.5 | 942.7 | 0.97636 | 24.99 | 26342165 | 0.445 | 24.4 | 0.0116 |
| m3-b-queue_greedy_hysteresis-h1000-s8 | queue_greedy_hysteresis | 1000 | 8 | 19304 | 19211 | 497.5 | 905.5 | 11.67 | 0.1838 | 0.2769 | 1.95 | 8.15 | 0.078 | 11.47 | 364.3 | 579.3 | 648.3 | 0.99938 | 21.37 | 20105597 | 0.445 | 24.4 | 0.0116 |
| m3-b-queue_greedy_hysteresis-h1000-s9 | queue_greedy_hysteresis | 1000 | 9 | 20518 | 20281 | 511.0 | 901.3 | 10.94 | 0.2007 | 0.2942 | 2.10 | 8.22 | 0.117 | 7.93 | 431.9 | 730.4 | 897.0 | 0.99247 | 25.18 | 24713649 | 0.439 | 24.4 | 0.0116 |
| m3-b-queue_greedy_sticky-h1000-s7 | queue_greedy_sticky | 1000 | 7 | 19822 | 19512 | 500.0 | 900.2 | 12.04 | 0.2110 | 0.4333 | 7.66 | 25.46 | 0.150 | 6.28 | 424.4 | 799.6 | 1022.2 | 0.96788 | 22.60 | 78551122 | 0.325 | 24.4 | 0.0116 |
| m3-b-queue_greedy_sticky-h1000-s8 | queue_greedy_sticky | 1000 | 8 | 18865 | 18533 | 499.3 | 907.4 | 11.14 | 0.2163 | 0.4236 | 6.73 | 21.53 | 0.153 | 6.12 | 409.5 | 761.6 | 933.4 | 0.97442 | 22.35 | 60451414 | 0.323 | 24.4 | 0.0116 |
| m3-b-queue_greedy_sticky-h1000-s9 | queue_greedy_sticky | 1000 | 9 | 18879 | 18695 | 506.9 | 906.0 | 12.08 | 0.2229 | 0.4184 | 6.96 | 20.94 | 0.135 | 6.92 | 402.4 | 692.2 | 897.4 | 0.98306 | 21.69 | 66346197 | 0.320 | 24.4 | 0.0116 |
| m3-b-queue_softmin-h1000-s7 | queue_softmin | 1000 | 7 | 24518 | 23696 | 503.9 | 903.2 | 11.69 | 0.2637 | 0.4002 | 4.60 | 16.19 | 0.620 | 1.60 | 491.6 | 887.8 | 1030.4 | 0.82727 | 28.65 | 24416817 | 0.396 | 24.4 | 0.0116 |
| m3-b-queue_softmin-h1000-s8 | queue_softmin | 1000 | 8 | 23123 | 22378 | 502.5 | 906.9 | 11.41 | 0.2672 | 0.3975 | 4.54 | 16.04 | 0.621 | 1.60 | 480.9 | 903.7 | 1042.0 | 0.84279 | 28.14 | 18647859 | 0.390 | 24.4 | 0.0116 |
| m3-b-queue_softmin-h1000-s9 | queue_softmin | 1000 | 9 | 23095 | 22309 | 506.4 | 896.8 | 11.31 | 0.2685 | 0.4017 | 4.63 | 16.15 | 0.613 | 1.62 | 481.9 | 882.3 | 1012.4 | 0.84944 | 28.14 | 20677262 | 0.387 | 24.4 | 0.0116 |
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
| m3|sync|b|queue_greedy_ewma|h1000 | 3 | 0.2646 [0.2564..0.2692] | 0.2646 [0.2564..0.2692] | 1.68 [1.57..1.83] | 499.0 [495.9..504.5] | 902.3 [899.3..907.4] | 0.156 [0.138..0.175] | 6.14 [5.45..6.84] | 358.0 [339.9..368.5] | 629.3 [582.6..666.9] | 0.99959 [0.99938..1.00000] |
| m3|sync|b|queue_greedy_hysteresis|h1000 | 3 | 0.1996 [0.1838..0.2144] | 0.2921 [0.2769..0.3052] | 2.11 [1.95..2.27] | 501.4 [495.9..511.0] | 902.7 [901.3..905.5] | 0.115 [0.078..0.148] | 8.61 [6.43..11.47] | 408.2 [364.3..431.9] | 829.3 [648.3..942.7] | 0.98940 [0.97636..0.99938] |
| m3|sync|b|queue_greedy_sticky|h1000 | 3 | 0.2168 [0.2110..0.2229] | 0.4251 [0.4184..0.4333] | 7.12 [6.73..7.66] | 502.0 [499.3..506.9] | 904.5 [900.2..907.4] | 0.146 [0.135..0.153] | 6.44 [6.12..6.92] | 412.1 [402.4..424.4] | 951.0 [897.4..1022.2] | 0.97512 [0.96788..0.98306] |
| m3|sync|b|queue_greedy|h1000 | 3 | 0.2764 [0.2716..0.2824] | 0.2764 [0.2716..0.2824] | 1.83 [1.75..1.95] | 498.3 [493.3..507.3] | 901.1 [898.0..906.6] | 0.240 [0.230..0.252] | 4.03 [3.85..4.21] | 374.3 [364.3..379.6] | 691.1 [667.9..717.9] | 0.99815 [0.99749..0.99938] |
| m3|sync|b|queue_softmin|h1000 | 3 | 0.2665 [0.2637..0.2685] | 0.3998 [0.3975..0.4017] | 4.59 [4.54..4.63] | 504.3 [502.5..506.4] | 902.3 [896.8..906.9] | 0.618 [0.613..0.621] | 1.61 [1.60..1.62] | 484.8 [480.9..491.6] | 1028.2 [1012.4..1042.0] | 0.83983 [0.82727..0.84944] |
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
- cells whose committed action differed from the used-state argmin: `m3-b-queue_greedy_hysteresis-h1000-s7` (2123), `m3-b-queue_greedy_hysteresis-h1000-s8` (2032), `m3-b-queue_greedy_hysteresis-h1000-s9` (2075), `m3-b-queue_greedy_sticky-h1000-s7` (3235), `m3-b-queue_greedy_sticky-h1000-s8` (2893), `m3-b-queue_greedy_sticky-h1000-s9` (2724), `m3-b-queue_softmin-h1000-s7` (6552), `m3-b-queue_softmin-h1000-s8` (6223), `m3-b-queue_softmin-h1000-s9` (6278), `m3-c2-queue_greedy_hysteresis-h1000-s7` (2756), `m3-c2-queue_greedy_hysteresis-h1000-s8` (2546), `m3-c2-queue_greedy_hysteresis-h1000-s9` (2954), `m3-c2-queue_greedy_sticky-h1000-s7` (5694), `m3-c2-queue_greedy_sticky-h1000-s8` (4939), `m3-c2-queue_greedy_sticky-h1000-s9` (5448), `m3-c2-queue_softmin-h1000-s7` (8211), `m3-c2-queue_softmin-h1000-s8` (7816), `m3-c2-queue_softmin-h1000-s9` (7925)

## Gamma bins — flip rate (largest regime/sync group)

Cells: `m3-b-geometry-h1000-s8`, `m3-b-geometry-h1000-s9`, `m3-b-queue_greedy-h1000-s7`, `m3-b-queue_greedy-h1000-s8`, `m3-b-queue_greedy-h1000-s9`, `m3-b-queue_greedy_ewma-h1000-s7`, `m3-b-queue_greedy_ewma-h1000-s8`, `m3-b-queue_greedy_ewma-h1000-s9`, `m3-b-queue_greedy_hysteresis-h1000-s7`, `m3-b-queue_greedy_hysteresis-h1000-s8`, `m3-b-queue_greedy_hysteresis-h1000-s9`, `m3-b-queue_greedy_sticky-h1000-s7`, `m3-b-queue_greedy_sticky-h1000-s8`, `m3-b-queue_greedy_sticky-h1000-s9`, `m3-b-queue_softmin-h1000-s7`, `m3-b-queue_softmin-h1000-s8`, `m3-b-queue_softmin-h1000-s9` (full per-cell bins in the JSON).

| cell | lt_0.5 | 0.5_1 | 1_2 | 2_5 | gt_5 |
|---|---|---|---|---|---|
| m3-b-geometry-h1000-s8 | 0.000 (11847) | 0.000 (873) | 0.000 (903) | 0.000 (1154) | 0.000 (1594) |
| m3-b-geometry-h1000-s9 | 0.000 (12122) | 0.000 (893) | 0.000 (771) | 0.000 (904) | 0.000 (1728) |
| m3-b-queue_greedy-h1000-s7 | 0.151 (13842) | 0.484 (1521) | 0.602 (1252) | 0.719 (997) | 0.829 (906) |
| m3-b-queue_greedy-h1000-s8 | 0.166 (13540) | 0.488 (1583) | 0.604 (1237) | 0.660 (944) | 0.804 (813) |
| m3-b-queue_greedy-h1000-s9 | 0.160 (13606) | 0.551 (1699) | 0.601 (1186) | 0.689 (1003) | 0.844 (813) |
| m3-b-queue_greedy_ewma-h1000-s7 | 0.156 (14249) | 0.466 (1585) | 0.640 (1260) | 0.660 (1108) | 0.681 (1039) |
| m3-b-queue_greedy_ewma-h1000-s8 | 0.117 (13322) | 0.444 (1357) | 0.600 (1181) | 0.776 (988) | 0.938 (1427) |
| m3-b-queue_greedy_ewma-h1000-s9 | 0.138 (13838) | 0.500 (1336) | 0.598 (1138) | 0.660 (1074) | 0.752 (1028) |
| m3-b-queue_greedy_hysteresis-h1000-s7 | 0.124 (14473) | 0.306 (1851) | 0.412 (1547) | 0.518 (1500) | 0.607 (1136) |
| m3-b-queue_greedy_hysteresis-h1000-s8 | 0.090 (13378) | 0.325 (1564) | 0.430 (1281) | 0.508 (1015) | 0.590 (1040) |
| m3-b-queue_greedy_hysteresis-h1000-s9 | 0.107 (13607) | 0.306 (1843) | 0.409 (1435) | 0.544 (1227) | 0.639 (962) |
| m3-b-queue_greedy_sticky-h1000-s7 | 0.130 (11245) | 0.271 (2069) | 0.306 (1767) | 0.388 (1583) | 0.450 (1580) |
| m3-b-queue_greedy_sticky-h1000-s8 | 0.109 (10613) | 0.254 (1873) | 0.370 (1744) | 0.482 (1557) | 0.499 (1500) |
| m3-b-queue_greedy_sticky-h1000-s9 | 0.136 (11221) | 0.276 (1845) | 0.319 (1603) | 0.461 (1465) | 0.528 (1381) |
| m3-b-queue_softmin-h1000-s7 | 0.189 (15577) | 0.358 (2738) | 0.438 (2070) | 0.465 (1621) | 0.497 (798) |
| m3-b-queue_softmin-h1000-s8 | 0.193 (14791) | 0.372 (2549) | 0.426 (1904) | 0.457 (1463) | 0.522 (764) |
| m3-b-queue_softmin-h1000-s9 | 0.189 (14427) | 0.372 (2665) | 0.436 (2016) | 0.477 (1525) | 0.569 (721) |

## Paired E2E inside one trace (delta = policy - geometry)

| group | policy | same trace | paired | mean delta ms | p50 | p95 | faster | slower |
|---|---|---|---|---|---|---|---|---|
| m3|sync|b|h1000|s8 | queue_greedy | True | 1602 | -248.120 | -239.089 | 170.833 | 1166 | 292 |
| m3|sync|b|h1000|s8 | queue_greedy_ewma | True | 1602 | -287.265 | -321.993 | 176.275 | 1194 | 238 |
| m3|sync|b|h1000|s8 | queue_greedy_hysteresis | True | 1602 | -262.784 | -229.686 | 140.308 | 1227 | 221 |
| m3|sync|b|h1000|s8 | queue_greedy_sticky | True | 1562 | -213.259 | -189.157 | 208.647 | 1118 | 302 |
| m3|sync|b|h1000|s8 | queue_softmin | True | 1351 | -134.377 | -104.428 | 421.796 | 837 | 468 |
| m3|sync|b|h1000|s9 | queue_greedy | True | 1590 | -303.112 | -378.472 | 144.623 | 1209 | 232 |
| m3|sync|b|h1000|s9 | queue_greedy_ewma | True | 1594 | -314.495 | -432.248 | 137.944 | 1209 | 221 |
| m3|sync|b|h1000|s9 | queue_greedy_hysteresis | True | 1582 | -250.645 | -274.364 | 176.046 | 1166 | 288 |
| m3|sync|b|h1000|s9 | queue_greedy_sticky | True | 1567 | -280.171 | -320.552 | 168.452 | 1159 | 253 |
| m3|sync|b|h1000|s9 | queue_softmin | True | 1354 | -193.389 | -183.161 | 364.251 | 893 | 418 |
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
| m3|sync|b|queue_greedy_ewma|h1000 | queue_greedy_ewma | 1000 | 0.156 | 6.14 | 0.2646 | 0.2646 | n/a | 0.101 | 358.0 | 629.3 |
| m3|sync|b|queue_greedy_hysteresis|h1000 | queue_greedy_hysteresis | 1000 | 0.115 | 8.61 | 0.1996 | 0.2921 | n/a | 0.063 | 408.2 | 829.3 |
| m3|sync|b|queue_greedy_sticky|h1000 | queue_greedy_sticky | 1000 | 0.146 | 6.44 | 0.2168 | 0.4251 | n/a | 0.063 | 412.1 | 951.0 |
| m3|sync|b|queue_greedy|h1000 | queue_greedy | 1000 | 0.240 | 4.03 | 0.2764 | 0.2764 | n/a | 0.067 | 374.3 | 691.1 |
| m3|sync|b|queue_softmin|h1000 | queue_softmin | 1000 | 0.618 | 1.61 | 0.2665 | 0.3998 | 0.882 | 0.039 | 484.8 | 1028.2 |
| m3|sync|c2|geometry|h1000 | geometry | 1000 | 0.222 | 4.38 | 0.0000 | 0.0000 | n/a | 0.094 | 673.5 | 1175.2 |
| m3|sync|c2|queue_greedy_ewma|h1000 | queue_greedy_ewma | 1000 | 0.196 | 5.04 | 0.2684 | 0.2684 | n/a | 0.087 | 380.9 | 679.6 |
| m3|sync|c2|queue_greedy_hysteresis|h1000 | queue_greedy_hysteresis | 1000 | 0.144 | 6.81 | 0.1862 | 0.2860 | n/a | 0.064 | 437.8 | 838.2 |
| m3|sync|c2|queue_greedy_sticky|h1000 | queue_greedy_sticky | 1000 | 0.178 | 5.40 | 0.1925 | 0.4672 | n/a | 0.057 | 473.3 | 1126.7 |
| m3|sync|c2|queue_greedy|h1000 | queue_greedy | 1000 | 0.308 | 3.19 | 0.2777 | 0.2777 | n/a | 0.066 | 408.1 | 964.7 |
| m3|sync|c2|queue_softmin|h1000 | queue_softmin | 1000 | 0.627 | 1.58 | 0.2713 | 0.4015 | 0.890 | 0.042 | 493.0 | 1037.5 |

_BEHAVIOURAL PROXIES (switch rate, mean run length, flip, entropy, near-tie fraction); not a strict mathematical sensitivity of J_

## Metric gaps and known limitations

- cells without evaluable decisions: none
- cells without an action-entropy audit: 27 of 33
- cells without a sticky audit: 27 of 33
- cells with no Gamma samples: 0 of 33
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
