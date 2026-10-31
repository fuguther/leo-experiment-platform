# Directional persistence - `p1-low` (4 cells)

**Caliber:** per-(sat,dst) streams (dst/bits joined from `trace.jsonl.gz`); `tau_relax` includes in-service remaining bits; `z` is reported raw and with an epsilon floor; `delta` is bit/s per-decision attribution.

Diagnostic only. flip = truth-substituted same-decision re-ranking (`sq_policy_analyze.py` rule); geometry flip = 0 by construction. Windows: eval 4-14 s, bins 0.5 s, burst 8-8.5 s.

## Headline

| cell | policy | flip rate (eval) | one-way moved Mbps | eps p50 corridor Mbps | z>=0.5 raw | z>=1 raw | z>=0.5 valid | z>=1 valid | max flip run | tau_error p50 s | tau_relax p50 ms |
|---|---|---|---|---|---|---|---|---|---|---|---|
| p1-low-geom | geometry | 0.0000 | 0.00 | 70.13 | n/a | n/a | n/a | n/a | n/a | n/a | 14.36 |
| p1-low-greedy | queue_greedy | 0.2774 | 107.90 | 76.83 | 0.236 | 0.019 | 0.237 | 0.019 | 139 | 0.96 | 10.71 |
| p1-low-hyst | queue_greedy_hysteresis | 0.1453 | 47.60 | 71.00 | 0.255 | 0.074 | 0.256 | 0.074 | 110 | 0.74 | 13.48 |
| p1-low-ewma | queue_greedy_ewma | 0.2816 | 126.70 | 73.90 | 0.248 | 0.019 | 0.250 | 0.019 | 139 | 0.76 | 10.71 |

## z bin share of flips (raw | valid)

| cell | set | [0,0.1) | [0.1,0.3) | [0.3,0.5) | [0.5,1) | [1,inf) | n |
|---|---|---|---|---|---|---|---|
| p1-low-geom | raw | n/a | n/a | n/a | n/a | n/a | 0 |
| p1-low-geom | valid | n/a | n/a | n/a | n/a | n/a | 0 |
| p1-low-greedy | raw | 0.190 | 0.218 | 0.351 | 0.217 | 0.019 | 1363 |
| p1-low-greedy | valid | 0.191 | 0.219 | 0.353 | 0.218 | 0.019 | 1363 |
| p1-low-hyst | raw | 0.203 | 0.191 | 0.344 | 0.181 | 0.074 | 698 |
| p1-low-hyst | valid | 0.205 | 0.192 | 0.347 | 0.182 | 0.074 | 698 |
| p1-low-ewma | raw | 0.174 | 0.207 | 0.365 | 0.230 | 0.019 | 1377 |
| p1-low-ewma | valid | 0.175 | 0.208 | 0.367 | 0.231 | 0.019 | 1377 |

## Directional bias and persistence

| cell | sum_r \|delta_r\| Mbps | positive excess Mbps | top excess link | longest +run s | error runs >=1 s | tau_error max s | tau_relax p90 ms |
|---|---|---|---|---|---|---|---|
| p1-low-geom | 0.00 | 0.00 | n/a | n/a | 0 | n/a | 30.71 |
| p1-low-greedy | 215.80 | 107.90 | isl:6:26 +23.4 | 6.00 | 11 | 5.90 | 10.77 |
| p1-low-hyst | 95.20 | 47.60 | isl:5:25 +11.8 | 7.00 | 5 | 6.92 | 23.67 |
| p1-low-ewma | 253.40 | 126.70 | isl:6:26 +28.4 | 10.00 | 8 | 9.88 | 10.77 |

## Per-cell detail

- [p1-low-geom](./p1-low-geom.md)
- [p1-low-greedy](./p1-low-greedy.md)
- [p1-low-hyst](./p1-low-hyst.md)
- [p1-low-ewma](./p1-low-ewma.md)

