# Directional persistence - `p1-high` (4 cells)

**Caliber:** per-(sat,dst) streams (dst/bits joined from `trace.jsonl.gz`); `tau_relax` includes in-service remaining bits; `z` is reported raw and with an epsilon floor; `delta` is bit/s per-decision attribution.

Diagnostic only. flip = truth-substituted same-decision re-ranking (`sq_policy_analyze.py` rule); geometry flip = 0 by construction. Windows: eval 4-14 s, bins 0.5 s, burst 8-8.5 s.

## Headline

| cell | policy | flip rate (eval) | one-way moved Mbps | eps p50 corridor Mbps | z>=0.5 raw | z>=1 raw | z>=0.5 valid | z>=1 valid | max flip run | tau_error p50 s | tau_relax p50 ms |
|---|---|---|---|---|---|---|---|---|---|---|---|
| p1-high-geom | geometry | 0.0000 | 0.00 | 11.21 | n/a | n/a | n/a | n/a | n/a | n/a | 151.16 |
| p1-high-greedy | queue_greedy | 0.2779 | 265.60 | 51.20 | 0.659 | 0.538 | 0.663 | 0.541 | 163 | 0.93 | 12.35 |
| p1-high-hyst | queue_greedy_hysteresis | 0.1816 | 112.90 | 48.00 | 0.583 | 0.462 | 0.588 | 0.467 | 138 | 0.80 | 13.85 |
| p1-high-ewma | queue_greedy_ewma | 0.2850 | 327.50 | 30.40 | 0.710 | 0.658 | 0.715 | 0.663 | 211 | 0.93 | 12.67 |

## z bin share of flips (raw | valid)

| cell | set | [0,0.1) | [0.1,0.3) | [0.3,0.5) | [0.5,1) | [1,inf) | n |
|---|---|---|---|---|---|---|---|
| p1-high-geom | raw | n/a | n/a | n/a | n/a | n/a | 0 |
| p1-high-geom | valid | n/a | n/a | n/a | n/a | n/a | 0 |
| p1-high-greedy | raw | 0.147 | 0.132 | 0.057 | 0.121 | 0.538 | 4012 |
| p1-high-greedy | valid | 0.148 | 0.133 | 0.057 | 0.121 | 0.541 | 4012 |
| p1-high-hyst | raw | 0.173 | 0.139 | 0.095 | 0.121 | 0.462 | 2668 |
| p1-high-hyst | valid | 0.175 | 0.141 | 0.096 | 0.122 | 0.467 | 2668 |
| p1-high-ewma | raw | 0.134 | 0.122 | 0.026 | 0.052 | 0.658 | 4049 |
| p1-high-ewma | valid | 0.135 | 0.123 | 0.026 | 0.052 | 0.663 | 4049 |

## Directional bias and persistence

| cell | sum_r \|delta_r\| Mbps | positive excess Mbps | top excess link | longest +run s | error runs >=1 s | tau_error max s | tau_relax p90 ms |
|---|---|---|---|---|---|---|---|
| p1-high-geom | 0.00 | 0.00 | n/a | n/a | 0 | n/a | 2120.59 |
| p1-high-greedy | 531.20 | 265.60 | isl:44:43 +50.9 | 9.00 | 19 | 9.74 | 31.88 |
| p1-high-hyst | 225.80 | 112.90 | isl:54:55 +13.8 | 7.00 | 23 | 9.66 | 40.22 |
| p1-high-ewma | 655.00 | 327.50 | isl:6:26 +68.1 | 10.00 | 17 | 9.91 | 31.88 |

## Per-cell detail

- [p1-high-geom](./p1-high-geom.md)
- [p1-high-greedy](./p1-high-greedy.md)
- [p1-high-hyst](./p1-high-hyst.md)
- [p1-high-ewma](./p1-high-ewma.md)

