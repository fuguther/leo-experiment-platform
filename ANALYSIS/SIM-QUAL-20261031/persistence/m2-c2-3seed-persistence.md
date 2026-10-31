# Directional persistence - `m2-c2-3seed` (9 cells)

**Caliber:** per-(sat,dst) streams (dst/bits joined from `trace.jsonl.gz`); `tau_relax` includes in-service remaining bits; `z` is reported raw and with an epsilon floor; `delta` is bit/s per-decision attribution.

Diagnostic only. flip = truth-substituted same-decision re-ranking (`sq_policy_analyze.py` rule); geometry flip = 0 by construction. Windows: eval 4-14 s, bins 0.5 s, burst 8-8.5 s.

## Headline

| cell | policy | flip rate (eval) | one-way moved Mbps | eps p50 corridor Mbps | z>=0.5 raw | z>=1 raw | z>=0.5 valid | z>=1 valid | max flip run | tau_error p50 s | tau_relax p50 ms |
|---|---|---|---|---|---|---|---|---|---|---|---|
| m2-c2-geom-s7 | geometry | 0.0000 | 0.00 | 4.75 | n/a | n/a | n/a | n/a | n/a | n/a | 420.14 |
| m2-c2-geom-s8 | geometry | 0.0000 | 0.00 | 5.66 | n/a | n/a | n/a | n/a | n/a | n/a | 383.32 |
| m2-c2-geom-s9 | geometry | 0.0000 | 0.00 | 4.74 | n/a | n/a | n/a | n/a | n/a | n/a | 971.91 |
| m2-c2-greedy-s7 | queue_greedy | 0.2654 | 267.00 | 34.51 | 0.708 | 0.528 | 0.708 | 0.524 | 309 | 0.92 | 21.35 |
| m2-c2-greedy-s8 | queue_greedy | 0.2583 | 264.10 | 32.70 | 0.689 | 0.473 | 0.693 | 0.476 | 235 | 0.88 | 17.44 |
| m2-c2-greedy-s9 | queue_greedy | 0.2589 | 287.50 | 43.70 | 0.734 | 0.558 | 0.736 | 0.558 | 264 | 0.90 | 20.55 |
| m2-c2-ewma-s7 | queue_greedy_ewma | 0.2621 | 398.20 | 32.40 | 0.751 | 0.592 | 0.749 | 0.584 | 432 | 0.94 | 20.75 |
| m2-c2-ewma-s8 | queue_greedy_ewma | 0.2658 | 413.70 | 38.61 | 0.740 | 0.545 | 0.743 | 0.546 | 454 | 0.96 | 22.47 |
| m2-c2-ewma-s9 | queue_greedy_ewma | 0.2666 | 409.90 | 40.39 | 0.780 | 0.656 | 0.778 | 0.649 | 508 | 0.95 | 18.28 |

## z bin share of flips (raw | valid)

| cell | set | [0,0.1) | [0.1,0.3) | [0.3,0.5) | [0.5,1) | [1,inf) | n |
|---|---|---|---|---|---|---|---|
| m2-c2-geom-s7 | raw | n/a | n/a | n/a | n/a | n/a | 0 |
| m2-c2-geom-s7 | valid | n/a | n/a | n/a | n/a | n/a | 0 |
| m2-c2-geom-s8 | raw | n/a | n/a | n/a | n/a | n/a | 0 |
| m2-c2-geom-s8 | valid | n/a | n/a | n/a | n/a | n/a | 0 |
| m2-c2-geom-s9 | raw | n/a | n/a | n/a | n/a | n/a | 0 |
| m2-c2-geom-s9 | valid | n/a | n/a | n/a | n/a | n/a | 0 |
| m2-c2-greedy-s7 | raw | 0.129 | 0.104 | 0.053 | 0.179 | 0.528 | 6592 |
| m2-c2-greedy-s7 | valid | 0.132 | 0.106 | 0.054 | 0.183 | 0.524 | 6494 |
| m2-c2-greedy-s8 | raw | 0.130 | 0.100 | 0.075 | 0.215 | 0.473 | 6245 |
| m2-c2-greedy-s8 | valid | 0.131 | 0.101 | 0.075 | 0.217 | 0.476 | 6245 |
| m2-c2-greedy-s9 | raw | 0.127 | 0.086 | 0.047 | 0.175 | 0.558 | 6250 |
| m2-c2-greedy-s9 | valid | 0.129 | 0.087 | 0.048 | 0.178 | 0.558 | 6191 |
| m2-c2-ewma-s7 | raw | 0.120 | 0.085 | 0.037 | 0.160 | 0.592 | 6462 |
| m2-c2-ewma-s7 | valid | 0.124 | 0.088 | 0.038 | 0.165 | 0.584 | 6284 |
| m2-c2-ewma-s8 | raw | 0.129 | 0.088 | 0.036 | 0.195 | 0.545 | 6246 |
| m2-c2-ewma-s8 | valid | 0.131 | 0.090 | 0.036 | 0.197 | 0.546 | 6207 |
| m2-c2-ewma-s9 | raw | 0.108 | 0.062 | 0.043 | 0.124 | 0.656 | 6492 |
| m2-c2-ewma-s9 | valid | 0.112 | 0.065 | 0.045 | 0.129 | 0.649 | 6310 |

## Directional bias and persistence

| cell | sum_r \|delta_r\| Mbps | positive excess Mbps | top excess link | longest +run s | error runs >=1 s | tau_error max s | tau_relax p90 ms |
|---|---|---|---|---|---|---|---|
| m2-c2-geom-s7 | 0.00 | 0.00 | n/a | n/a | 0 | n/a | 7974.00 |
| m2-c2-geom-s8 | 0.00 | 0.00 | n/a | n/a | 0 | n/a | 6715.91 |
| m2-c2-geom-s9 | 0.00 | 0.00 | n/a | n/a | 0 | n/a | 8314.15 |
| m2-c2-greedy-s7 | 534.00 | 267.00 | isl:46:45 +41.1 | 4.50 | 30 | 6.38 | 86.68 |
| m2-c2-greedy-s8 | 528.20 | 264.10 | isl:6:26 +29.7 | 5.00 | 38 | 6.94 | 101.30 |
| m2-c2-greedy-s9 | 575.00 | 287.50 | isl:14:34 +36.9 | 7.50 | 30 | 7.44 | 92.08 |
| m2-c2-ewma-s7 | 796.40 | 398.20 | isl:6:26 +56.1 | 10.00 | 37 | 9.82 | 92.11 |
| m2-c2-ewma-s8 | 827.40 | 413.70 | isl:6:26 +49.2 | 10.00 | 34 | 9.82 | 80.32 |
| m2-c2-ewma-s9 | 819.80 | 409.90 | isl:14:34 +53.2 | 10.00 | 28 | 9.79 | 91.95 |

## Per-cell detail

- [m2-c2-geom-s7](./m2-c2-geom-s7.md)
- [m2-c2-geom-s8](./m2-c2-geom-s8.md)
- [m2-c2-geom-s9](./m2-c2-geom-s9.md)
- [m2-c2-greedy-s7](./m2-c2-greedy-s7.md)
- [m2-c2-greedy-s8](./m2-c2-greedy-s8.md)
- [m2-c2-greedy-s9](./m2-c2-greedy-s9.md)
- [m2-c2-ewma-s7](./m2-c2-ewma-s7.md)
- [m2-c2-ewma-s8](./m2-c2-ewma-s8.md)
- [m2-c2-ewma-s9](./m2-c2-ewma-s9.md)

