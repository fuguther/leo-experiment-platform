# Directional persistence - `m2-c2-s7` (3 cells)

**Caliber:** per-(sat,dst) streams (dst/bits joined from `trace.jsonl.gz`); `tau_relax` includes in-service remaining bits; `z` is reported raw and with an epsilon floor; `delta` is bit/s per-decision attribution.

Diagnostic only. flip = truth-substituted same-decision re-ranking (`sq_policy_analyze.py` rule); geometry flip = 0 by construction. Windows: eval 4-14 s, bins 0.5 s, burst 8-8.5 s.

## Headline

| cell | policy | flip rate (eval) | one-way moved Mbps | eps p50 corridor Mbps | z>=0.5 raw | z>=1 raw | z>=0.5 valid | z>=1 valid | max flip run | tau_error p50 s | tau_relax p50 ms |
|---|---|---|---|---|---|---|---|---|---|---|---|
| m2-c2-geom-s7 | geometry | 0.0000 | 0.00 | 4.75 | n/a | n/a | n/a | n/a | n/a | n/a | 420.14 |
| m2-c2-greedy-s7 | queue_greedy | 0.2654 | 267.00 | 34.51 | 0.708 | 0.528 | 0.708 | 0.524 | 309 | 0.92 | 21.35 |
| m2-c2-ewma-s7 | queue_greedy_ewma | 0.2621 | 398.20 | 32.40 | 0.751 | 0.592 | 0.749 | 0.584 | 432 | 0.94 | 20.75 |

## z bin share of flips (raw | valid)

| cell | set | [0,0.1) | [0.1,0.3) | [0.3,0.5) | [0.5,1) | [1,inf) | n |
|---|---|---|---|---|---|---|---|
| m2-c2-geom-s7 | raw | n/a | n/a | n/a | n/a | n/a | 0 |
| m2-c2-geom-s7 | valid | n/a | n/a | n/a | n/a | n/a | 0 |
| m2-c2-greedy-s7 | raw | 0.129 | 0.104 | 0.053 | 0.179 | 0.528 | 6592 |
| m2-c2-greedy-s7 | valid | 0.132 | 0.106 | 0.054 | 0.183 | 0.524 | 6494 |
| m2-c2-ewma-s7 | raw | 0.120 | 0.085 | 0.037 | 0.160 | 0.592 | 6462 |
| m2-c2-ewma-s7 | valid | 0.124 | 0.088 | 0.038 | 0.165 | 0.584 | 6284 |

## Directional bias and persistence

| cell | sum_r \|delta_r\| Mbps | positive excess Mbps | top excess link | longest +run s | error runs >=1 s | tau_error max s | tau_relax p90 ms |
|---|---|---|---|---|---|---|---|
| m2-c2-geom-s7 | 0.00 | 0.00 | n/a | n/a | 0 | n/a | 7974.00 |
| m2-c2-greedy-s7 | 534.00 | 267.00 | isl:46:45 +41.1 | 4.50 | 30 | 6.38 | 86.68 |
| m2-c2-ewma-s7 | 796.40 | 398.20 | isl:6:26 +56.1 | 10.00 | 37 | 9.82 | 92.11 |

## Per-cell detail

- [m2-c2-geom-s7](./m2-c2-geom-s7.md)
- [m2-c2-greedy-s7](./m2-c2-greedy-s7.md)
- [m2-c2-ewma-s7](./m2-c2-ewma-s7.md)

