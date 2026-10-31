# Directional persistence - `p2-sync` (4 cells)

**Caliber:** per-(sat,dst) streams (dst/bits joined from `trace.jsonl.gz`); `tau_relax` includes in-service remaining bits; `z` is reported raw and with an epsilon floor; `delta` is bit/s per-decision attribution.

Diagnostic only. flip = truth-substituted same-decision re-ranking (`sq_policy_analyze.py` rule); geometry flip = 0 by construction. Windows: eval 4-14 s, bins 0.5 s, burst 8-8.5 s.

## Headline

| cell | policy | flip rate (eval) | one-way moved Mbps | eps p50 corridor Mbps | z>=0.5 raw | z>=1 raw | z>=0.5 valid | z>=1 valid | max flip run | tau_error p50 s | tau_relax p50 ms |
|---|---|---|---|---|---|---|---|---|---|---|---|
| p2-sync-geom | geometry | 0.0000 | 0.00 | 11.31 | n/a | n/a | n/a | n/a | n/a | n/a | 40.11 |
| p2-sync-greedy | queue_greedy | 0.2243 | 145.20 | 61.60 | 0.036 | 0.000 | 0.036 | 0.000 | 64 | 0.47 | 11.41 |
| p2-sync-hyst | queue_greedy_hysteresis | 0.1645 | 77.10 | 51.20 | 0.136 | 0.039 | 0.138 | 0.040 | 52 | 0.39 | 13.92 |
| p2-sync-ewma | queue_greedy_ewma | 0.1957 | 180.40 | 33.10 | 0.284 | 0.116 | 0.287 | 0.118 | 162 | 0.61 | 12.26 |

## z bin share of flips (raw | valid)

| cell | set | [0,0.1) | [0.1,0.3) | [0.3,0.5) | [0.5,1) | [1,inf) | n |
|---|---|---|---|---|---|---|---|
| p2-sync-geom | raw | n/a | n/a | n/a | n/a | n/a | 0 |
| p2-sync-geom | valid | n/a | n/a | n/a | n/a | n/a | 0 |
| p2-sync-greedy | raw | 0.402 | 0.399 | 0.152 | 0.036 | 0.000 | 3271 |
| p2-sync-greedy | valid | 0.406 | 0.404 | 0.154 | 0.036 | 0.000 | 3271 |
| p2-sync-hyst | raw | 0.403 | 0.302 | 0.146 | 0.096 | 0.039 | 2385 |
| p2-sync-hyst | valid | 0.409 | 0.306 | 0.148 | 0.098 | 0.040 | 2385 |
| p2-sync-ewma | raw | 0.277 | 0.278 | 0.149 | 0.168 | 0.116 | 2797 |
| p2-sync-ewma | valid | 0.281 | 0.281 | 0.151 | 0.170 | 0.118 | 2797 |

## Directional bias and persistence

| cell | sum_r \|delta_r\| Mbps | positive excess Mbps | top excess link | longest +run s | error runs >=1 s | tau_error max s | tau_relax p90 ms |
|---|---|---|---|---|---|---|---|
| p2-sync-geom | 0.00 | 0.00 | n/a | n/a | 0 | n/a | 1831.03 |
| p2-sync-greedy | 290.40 | 145.20 | isl:46:45 +15.8 | 8.00 | 27 | 9.91 | 30.29 |
| p2-sync-hyst | 154.20 | 77.10 | isl:26:46 +12.5 | 3.50 | 21 | 9.66 | 40.22 |
| p2-sync-ewma | 360.80 | 180.40 | isl:6:26 +39.0 | 9.00 | 21 | 9.91 | 30.27 |

## Per-cell detail

- [p2-sync-geom](./p2-sync-geom.md)
- [p2-sync-greedy](./p2-sync-greedy.md)
- [p2-sync-hyst](./p2-sync-hyst.md)
- [p2-sync-ewma](./p2-sync-ewma.md)

