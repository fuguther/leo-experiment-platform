# Round-3 six-policy matrix — behavioural analysis

Schema `sq-round3/v1`. 16 cells.

- **flip semantics**: `truth_substituted_rerank_not_resimulated_counterfactual` — a truth-substituted re-ranking of the same decision row, **not** a re-simulated counterfactual.
- geometry is the negative control: its J carries no state term, so `flip` and `regret` must be 0 on the evaluable subset.
- `W` is a consecutive-wrong-decision duration; it is not packet queueing time.
- response-sensitivity columns are **behavioural proxies**, not a strict mathematical sensitivity.
- paired deltas only compare cells sharing `trace-manifest:source_trace_sha256`.

## Per-cell metrics

| cell | policy | sync ms | seed | multi | eval | AoI p50 | AoI p90 | m p50 | flip rule | flip truth | R mean | R p90 | switch | run | E2E mean | E2E p95 | E2E p99 | PDR | hops | isl_data | util max | ctrl Mbit | ctrl share |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| m2-b-geom-s7 | geometry | 1000 | 7 | 19174 | 19107 | 501.1 | 899.1 | 13.03 | 0.0000 | 0.0000 | 0.00 | 0.00 | 0.215 | 4.49 | 685.9 | 1045.8 | 1066.0 | 1.00000 | 16.86 | 680099839 | 0.530 | 295.1 | 0.1459 |
| m2-b-greedy-s7 | queue_greedy | 1000 | 7 | 20037 | 19921 | 493.3 | 897.8 | 11.32 | 0.2691 | 0.2691 | 1.72 | 7.08 | 0.241 | 4.03 | 365.4 | 583.0 | 668.0 | 0.99939 | 21.52 | 18464272 | 0.439 | 295.2 | 0.1459 |
| m2-b-ewma-s7 | queue_greedy_ewma | 1000 | 7 | 20174 | 20109 | 496.0 | 899.3 | 10.98 | 0.2634 | 0.2634 | 1.61 | 6.86 | 0.138 | 6.82 | 365.7 | 506.2 | 668.5 | 1.00000 | 21.53 | 18673275 | 0.440 | 295.2 | 0.1459 |
| m2-c2-geom-s7 | geometry | 1000 | 7 | 23472 | 23397 | 504.3 | 907.8 | 12.99 | 0.0000 | 0.0000 | 0.00 | 0.00 | 0.222 | 4.38 | 687.3 | 1192.6 | 1210.8 | 1.00000 | 17.32 | 830641906 | 0.538 | 295.0 | 0.1458 |
| m2-c2-greedy-s7 | queue_greedy | 1000 | 7 | 25809 | 25469 | 507.6 | 909.0 | 10.67 | 0.2651 | 0.2651 | 1.90 | 7.73 | 0.298 | 3.29 | 422.3 | 723.4 | 963.1 | 0.98925 | 24.47 | 36128429 | 0.472 | 295.2 | 0.1459 |
| m2-c2-ewma-s7 | queue_greedy_ewma | 1000 | 7 | 25378 | 25240 | 506.1 | 908.0 | 10.66 | 0.2616 | 0.2616 | 1.66 | 6.58 | 0.159 | 6.04 | 395.6 | 526.1 | 680.8 | 0.99707 | 23.00 | 32679470 | 0.472 | 295.2 | 0.1459 |
| m2-c2-hyst-s7 | queue_greedy_hysteresis | 1000 | 7 | 26951 | 26467 | 509.2 | 910.6 | 11.03 | 0.1874 | 0.2852 | 2.08 | 7.84 | 0.191 | 5.07 | 456.8 | 851.5 | 998.9 | 0.98876 | 26.14 | 50036185 | 0.472 | 295.2 | 0.1459 |
| m2-sync0020-c2-geom-s7 | geometry | 20 | 7 | 23472 | 23392 | 16.3 | 24.7 | 12.99 | 0.0000 | 0.0000 | 0.00 | 0.00 | 0.223 | 4.37 | 676.1 | 1177.3 | 1200.2 | 1.00000 | 17.28 | 809179045 | 0.539 | 1162.0 | 0.5802 |
| m2-sync0100-c2-geom-s7 | geometry | 100 | 7 | 23485 | 23407 | 56.5 | 96.3 | 13.01 | 0.0000 | 0.0000 | 0.00 | 0.00 | 0.224 | 4.34 | 675.2 | 1173.8 | 1198.2 | 1.00000 | 17.28 | 807292566 | 0.540 | 232.2 | 0.1160 |
| m2-sync1000-c2-geom-s7 | geometry | 1000 | 7 | 23497 | 23420 | 506.0 | 908.9 | 13.00 | 0.0000 | 0.0000 | 0.00 | 0.00 | 0.223 | 4.37 | 687.2 | 1188.8 | 1216.8 | 1.00000 | 17.32 | 830691492 | 0.539 | 24.4 | 0.0116 |
| m2-sync0020-c2-greedy-s7 | queue_greedy | 20 | 7 | 26255 | 25851 | 16.6 | 25.1 | 11.06 | 0.2602 | 0.2602 | 1.90 | 7.80 | 0.426 | 2.32 | 424.8 | 715.0 | 886.7 | 0.98437 | 24.55 | 39118242 | 0.470 | 1162.0 | 0.5804 |
| m2-sync0100-c2-greedy-s7 | queue_greedy | 100 | 7 | 26861 | 26298 | 56.9 | 96.9 | 11.26 | 0.2668 | 0.2668 | 2.12 | 8.15 | 0.411 | 2.40 | 439.3 | 775.2 | 912.5 | 0.96873 | 25.21 | 46742707 | 0.471 | 232.2 | 0.1161 |
| m2-sync1000-c2-greedy-s7 | queue_greedy | 1000 | 7 | 25757 | 25415 | 510.0 | 909.1 | 10.56 | 0.2777 | 0.2777 | 2.03 | 8.01 | 0.308 | 3.19 | 408.1 | 664.4 | 964.7 | 0.98876 | 23.65 | 35862704 | 0.472 | 24.4 | 0.0116 |
| m2-sync0020-c2-ewma-s7 | queue_greedy_ewma | 20 | 7 | 25019 | 24884 | 16.4 | 24.9 | 10.42 | 0.2271 | 0.2271 | 1.29 | 5.89 | 0.251 | 3.89 | 415.7 | 619.3 | 729.2 | 1.00000 | 24.12 | 34036480 | 0.470 | 1162.0 | 0.5804 |
| m2-sync0100-c2-ewma-s7 | queue_greedy_ewma | 100 | 7 | 24942 | 24834 | 56.1 | 96.2 | 10.36 | 0.2262 | 0.2262 | 1.28 | 5.73 | 0.208 | 4.65 | 420.2 | 603.3 | 721.7 | 0.99853 | 24.39 | 33577446 | 0.472 | 232.2 | 0.1161 |
| m2-sync1000-c2-ewma-s7 | queue_greedy_ewma | 1000 | 7 | 25384 | 25252 | 505.8 | 907.7 | 10.68 | 0.2635 | 0.2635 | 1.67 | 6.58 | 0.158 | 6.07 | 396.1 | 526.6 | 680.2 | 0.99658 | 23.04 | 32439279 | 0.472 | 24.4 | 0.0116 |

## Policy x regime x sync aggregates (mean [min..max] over seeds)

| group | cells | flip rule | flip truth | R mean ms | AoI p50 | AoI p90 | switch | run | E2E mean | E2E p99 | PDR |
|---|---|---|---|---|---|---|---|---|---|---|---|
| m2|default|b|geometry|h1000 | 1 | 0.0000 [0.0000..0.0000] | 0.0000 [0.0000..0.0000] | 0.00 [0.00..0.00] | 501.1 [501.1..501.1] | 899.1 [899.1..899.1] | 0.215 [0.215..0.215] | 4.49 [4.49..4.49] | 685.9 [685.9..685.9] | 1066.0 [1066.0..1066.0] | 1.00000 [1.00000..1.00000] |
| m2|default|b|queue_greedy_ewma|h1000 | 1 | 0.2634 [0.2634..0.2634] | 0.2634 [0.2634..0.2634] | 1.61 [1.61..1.61] | 496.0 [496.0..496.0] | 899.3 [899.3..899.3] | 0.138 [0.138..0.138] | 6.82 [6.82..6.82] | 365.7 [365.7..365.7] | 668.5 [668.5..668.5] | 1.00000 [1.00000..1.00000] |
| m2|default|b|queue_greedy|h1000 | 1 | 0.2691 [0.2691..0.2691] | 0.2691 [0.2691..0.2691] | 1.72 [1.72..1.72] | 493.3 [493.3..493.3] | 897.8 [897.8..897.8] | 0.241 [0.241..0.241] | 4.03 [4.03..4.03] | 365.4 [365.4..365.4] | 668.0 [668.0..668.0] | 0.99939 [0.99939..0.99939] |
| m2|default|c2|geometry|h1000 | 1 | 0.0000 [0.0000..0.0000] | 0.0000 [0.0000..0.0000] | 0.00 [0.00..0.00] | 504.3 [504.3..504.3] | 907.8 [907.8..907.8] | 0.222 [0.222..0.222] | 4.38 [4.38..4.38] | 687.3 [687.3..687.3] | 1210.8 [1210.8..1210.8] | 1.00000 [1.00000..1.00000] |
| m2|default|c2|queue_greedy_ewma|h1000 | 1 | 0.2616 [0.2616..0.2616] | 0.2616 [0.2616..0.2616] | 1.66 [1.66..1.66] | 506.1 [506.1..506.1] | 908.0 [908.0..908.0] | 0.159 [0.159..0.159] | 6.04 [6.04..6.04] | 395.6 [395.6..395.6] | 680.8 [680.8..680.8] | 0.99707 [0.99707..0.99707] |
| m2|default|c2|queue_greedy_hysteresis|h1000 | 1 | 0.1874 [0.1874..0.1874] | 0.2852 [0.2852..0.2852] | 2.08 [2.08..2.08] | 509.2 [509.2..509.2] | 910.6 [910.6..910.6] | 0.191 [0.191..0.191] | 5.07 [5.07..5.07] | 456.8 [456.8..456.8] | 998.9 [998.9..998.9] | 0.98876 [0.98876..0.98876] |
| m2|default|c2|queue_greedy|h1000 | 1 | 0.2651 [0.2651..0.2651] | 0.2651 [0.2651..0.2651] | 1.90 [1.90..1.90] | 507.6 [507.6..507.6] | 909.0 [909.0..909.0] | 0.298 [0.298..0.298] | 3.29 [3.29..3.29] | 422.3 [422.3..422.3] | 963.1 [963.1..963.1] | 0.98925 [0.98925..0.98925] |
| m2|sync|c2|geometry|h100 | 1 | 0.0000 [0.0000..0.0000] | 0.0000 [0.0000..0.0000] | 0.00 [0.00..0.00] | 56.5 [56.5..56.5] | 96.3 [96.3..96.3] | 0.224 [0.224..0.224] | 4.34 [4.34..4.34] | 675.2 [675.2..675.2] | 1198.2 [1198.2..1198.2] | 1.00000 [1.00000..1.00000] |
| m2|sync|c2|geometry|h1000 | 1 | 0.0000 [0.0000..0.0000] | 0.0000 [0.0000..0.0000] | 0.00 [0.00..0.00] | 506.0 [506.0..506.0] | 908.9 [908.9..908.9] | 0.223 [0.223..0.223] | 4.37 [4.37..4.37] | 687.2 [687.2..687.2] | 1216.8 [1216.8..1216.8] | 1.00000 [1.00000..1.00000] |
| m2|sync|c2|geometry|h20 | 1 | 0.0000 [0.0000..0.0000] | 0.0000 [0.0000..0.0000] | 0.00 [0.00..0.00] | 16.3 [16.3..16.3] | 24.7 [24.7..24.7] | 0.223 [0.223..0.223] | 4.37 [4.37..4.37] | 676.1 [676.1..676.1] | 1200.2 [1200.2..1200.2] | 1.00000 [1.00000..1.00000] |
| m2|sync|c2|queue_greedy_ewma|h100 | 1 | 0.2262 [0.2262..0.2262] | 0.2262 [0.2262..0.2262] | 1.28 [1.28..1.28] | 56.1 [56.1..56.1] | 96.2 [96.2..96.2] | 0.208 [0.208..0.208] | 4.65 [4.65..4.65] | 420.2 [420.2..420.2] | 721.7 [721.7..721.7] | 0.99853 [0.99853..0.99853] |
| m2|sync|c2|queue_greedy_ewma|h1000 | 1 | 0.2635 [0.2635..0.2635] | 0.2635 [0.2635..0.2635] | 1.67 [1.67..1.67] | 505.8 [505.8..505.8] | 907.7 [907.7..907.7] | 0.158 [0.158..0.158] | 6.07 [6.07..6.07] | 396.1 [396.1..396.1] | 680.2 [680.2..680.2] | 0.99658 [0.99658..0.99658] |
| m2|sync|c2|queue_greedy_ewma|h20 | 1 | 0.2271 [0.2271..0.2271] | 0.2271 [0.2271..0.2271] | 1.29 [1.29..1.29] | 16.4 [16.4..16.4] | 24.9 [24.9..24.9] | 0.251 [0.251..0.251] | 3.89 [3.89..3.89] | 415.7 [415.7..415.7] | 729.2 [729.2..729.2] | 1.00000 [1.00000..1.00000] |
| m2|sync|c2|queue_greedy|h100 | 1 | 0.2668 [0.2668..0.2668] | 0.2668 [0.2668..0.2668] | 2.12 [2.12..2.12] | 56.9 [56.9..56.9] | 96.9 [96.9..96.9] | 0.411 [0.411..0.411] | 2.40 [2.40..2.40] | 439.3 [439.3..439.3] | 912.5 [912.5..912.5] | 0.96873 [0.96873..0.96873] |
| m2|sync|c2|queue_greedy|h1000 | 1 | 0.2777 [0.2777..0.2777] | 0.2777 [0.2777..0.2777] | 2.03 [2.03..2.03] | 510.0 [510.0..510.0] | 909.1 [909.1..909.1] | 0.308 [0.308..0.308] | 3.19 [3.19..3.19] | 408.1 [408.1..408.1] | 964.7 [964.7..964.7] | 0.98876 [0.98876..0.98876] |
| m2|sync|c2|queue_greedy|h20 | 1 | 0.2602 [0.2602..0.2602] | 0.2602 [0.2602..0.2602] | 1.90 [1.90..1.90] | 16.6 [16.6..16.6] | 25.1 [25.1..25.1] | 0.426 [0.426..0.426] | 2.32 [2.32..2.32] | 424.8 [424.8..424.8] | 886.7 [886.7..886.7] | 0.98437 [0.98437..0.98437] |

## Consistency cross-checks

- geometry `chosen != recorded policy top` (must be 0): 0 in every cell
- audit identity `resource_work == q_used_bits / rate` violations (must be 0): 0 in every cell
- `chosen != used-state argmin` is expected only for hysteresis/sticky/softmin; per cell values are in the JSON.
- cells whose committed action differed from the used-state argmin: `m2-c2-hyst-s7` (2513)

## Gamma bins — flip rate (largest regime/sync group)

Cells: `m2-c2-ewma-s7`, `m2-c2-geom-s7`, `m2-c2-greedy-s7`, `m2-c2-hyst-s7` (full per-cell bins in the JSON).

| cell | lt_0.5 | 0.5_1 | 1_2 | 2_5 | gt_5 |
|---|---|---|---|---|---|
| m2-c2-geom-s7 | 0.000 (15451) | 0.000 (1171) | 0.000 (1073) | 0.000 (1249) | 0.000 (1900) |
| m2-c2-greedy-s7 | 0.150 (17613) | 0.456 (2277) | 0.586 (1736) | 0.625 (1344) | 0.715 (1201) |
| m2-c2-ewma-s7 | 0.146 (18043) | 0.405 (2002) | 0.563 (1509) | 0.700 (1293) | 0.785 (1464) |
| m2-c2-hyst-s7 | 0.105 (17975) | 0.265 (2501) | 0.359 (1959) | 0.469 (1686) | 0.573 (1353) |

## Paired E2E inside one trace (delta = policy - queue_greedy)

| group | policy | same trace | paired | mean delta ms | p50 | p95 | faster | slower |
|---|---|---|---|---|---|---|---|---|
| m2|default|b|h1000|s7 | geometry | True | 1649 | 320.749 | 405.788 | 713.060 | 224 | 1256 |
| m2|default|b|h1000|s7 | queue_greedy_ewma | True | 1649 | 0.211 | 0.000 | 170.445 | 666 | 589 |
| m2|default|c2|h1000|s7 | geometry | True | 2025 | 265.945 | 189.678 | 761.670 | 556 | 1283 |
| m2|default|c2|h1000|s7 | queue_greedy_ewma | True | 2019 | -26.877 | 0.000 | 172.639 | 843 | 760 |
| m2|default|c2|h1000|s7 | queue_greedy_hysteresis | True | 2002 | 34.217 | 5.621 | 361.965 | 738 | 1086 |
| m2|sync|c2|h1000|s7 | geometry | True | 2024 | 277.684 | 156.687 | 815.536 | 528 | 1303 |
| m2|sync|c2|h1000|s7 | queue_greedy_ewma | True | 2017 | -12.664 | 0.000 | 173.796 | 801 | 770 |
| m2|sync|c2|h100|s7 | geometry | True | 1983 | 231.099 | 128.701 | 787.094 | 569 | 1257 |
| m2|sync|c2|h100|s7 | queue_greedy_ewma | True | 1981 | -21.758 | -0.195 | 213.988 | 1000 | 816 |
| m2|sync|c2|h20|s7 | geometry | True | 2015 | 249.559 | 122.069 | 785.335 | 551 | 1290 |
| m2|sync|c2|h20|s7 | queue_greedy_ewma | True | 2015 | -9.878 | 0.000 | 219.725 | 958 | 839 |

## Freshness vs stability: delta(fast sync - slow sync)

| family|regime|policy | pairs | seeds | dAoI p50 | dAoI p90 | dflip rule | dswitch | dE2E mean | dE2E p99 | same trace |
|---|---|---|---|---|---|---|---|---|---|
| m2|sync|c2|geometry | 1 | [7] | 489.7 | 884.2 | 0.0000 | -0.000 | 11.12 | 16.69 | True |
| m2|sync|c2|queue_greedy | 1 | [7] | 493.4 | 884.0 | 0.0175 | -0.118 | -16.71 | 77.99 | True |
| m2|sync|c2|queue_greedy_ewma | 1 | [7] | 489.4 | 882.8 | 0.0364 | -0.093 | -19.53 | -48.97 | True |

## Response sensitivity (behavioural proxy)

| group | policy | sync | switch | run | flip rule | flip truth | entropy | near-tie 1ms | E2E mean | E2E p99 |
|---|---|---|---|---|---|---|---|---|---|---|
| m2|default|b|geometry|h1000 | geometry | 1000 | 0.215 | 4.49 | 0.0000 | 0.0000 | n/a | 0.092 | 685.9 | 1066.0 |
| m2|default|b|queue_greedy_ewma|h1000 | queue_greedy_ewma | 1000 | 0.138 | 6.82 | 0.2634 | 0.2634 | n/a | 0.080 | 365.7 | 668.5 |
| m2|default|b|queue_greedy|h1000 | queue_greedy | 1000 | 0.241 | 4.03 | 0.2691 | 0.2691 | n/a | 0.072 | 365.4 | 668.0 |
| m2|default|c2|geometry|h1000 | geometry | 1000 | 0.222 | 4.38 | 0.0000 | 0.0000 | n/a | 0.095 | 687.3 | 1210.8 |
| m2|default|c2|queue_greedy_ewma|h1000 | queue_greedy_ewma | 1000 | 0.159 | 6.04 | 0.2616 | 0.2616 | n/a | 0.073 | 395.6 | 680.8 |
| m2|default|c2|queue_greedy_hysteresis|h1000 | queue_greedy_hysteresis | 1000 | 0.191 | 5.07 | 0.1874 | 0.2852 | n/a | 0.050 | 456.8 | 998.9 |
| m2|default|c2|queue_greedy|h1000 | queue_greedy | 1000 | 0.298 | 3.29 | 0.2651 | 0.2651 | n/a | 0.059 | 422.3 | 963.1 |
| m2|sync|c2|geometry|h100 | geometry | 100 | 0.224 | 4.34 | 0.0000 | 0.0000 | n/a | 0.095 | 675.2 | 1198.2 |
| m2|sync|c2|geometry|h1000 | geometry | 1000 | 0.223 | 4.37 | 0.0000 | 0.0000 | n/a | 0.095 | 687.2 | 1216.8 |
| m2|sync|c2|geometry|h20 | geometry | 20 | 0.223 | 4.37 | 0.0000 | 0.0000 | n/a | 0.095 | 676.1 | 1200.2 |
| m2|sync|c2|queue_greedy_ewma|h100 | queue_greedy_ewma | 100 | 0.208 | 4.65 | 0.2262 | 0.2262 | n/a | 0.070 | 420.2 | 721.7 |
| m2|sync|c2|queue_greedy_ewma|h1000 | queue_greedy_ewma | 1000 | 0.158 | 6.07 | 0.2635 | 0.2635 | n/a | 0.072 | 396.1 | 680.2 |
| m2|sync|c2|queue_greedy_ewma|h20 | queue_greedy_ewma | 20 | 0.251 | 3.89 | 0.2271 | 0.2271 | n/a | 0.068 | 415.7 | 729.2 |
| m2|sync|c2|queue_greedy|h100 | queue_greedy | 100 | 0.411 | 2.40 | 0.2668 | 0.2668 | n/a | 0.057 | 439.3 | 912.5 |
| m2|sync|c2|queue_greedy|h1000 | queue_greedy | 1000 | 0.308 | 3.19 | 0.2777 | 0.2777 | n/a | 0.066 | 408.1 | 964.7 |
| m2|sync|c2|queue_greedy|h20 | queue_greedy | 20 | 0.426 | 2.32 | 0.2602 | 0.2602 | n/a | 0.055 | 424.8 | 886.7 |

_BEHAVIOURAL PROXIES (switch rate, mean run length, flip, entropy, near-tie fraction); not a strict mathematical sensitivity of J_

## Metric gaps and known limitations

- cells without evaluable decisions: none
- cells without an action-entropy audit: 16 of 16
- cells without a sticky audit: 16 of 16
- cells with no Gamma samples: 0 of 16
- cells using the legacy scorer fallback: none
- cells whose id disagrees with the config: none
- cells whose id could not be parsed: none
- action entropy: only queue_softmin audits expose selection.probabilities/entropy
- sticky keep/override: only queue_greedy_sticky audits expose them
- path stretch: no shortest-path baseline in the evidence (hops only)
- window-summed control service time: link_service_windows carry no kind=='ctrl' marker, so occupied.ctrl_isl_s / stop_time is used
- control packet delay: control.instances[3] is an absolute received_at, so advertisement age is taken from the probe (state_received_at_s - state_measured_at_s)
- Gamma needs >=3 truth samples inside +-0.25 s on BOTH resources
