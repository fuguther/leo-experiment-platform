# Four-policy stale-state matrix (diagnostic)

| cell | policy | multi | flip | R mean | R p90 | AoI p50 | m p50 | switch | W p50 | E2E mean | E2E p95 | isl_data area |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| p2-sync-geom | geometry | 14727 | 0.0000 | 0.00 | 0.00 | 56.3 | 12.95 | 0.332 | n/a | 359.7 | 670.6 | 117524640 |
| p2-sync-greedy | queue_greedy | 14977 | 0.2233 | 1.65 | 7.66 | 56.3 | 12.76 | 0.438 | 30.1 | 313.1 | 495.0 | 11771260 |
| p2-sync-hyst | queue_greedy_hysteresis | 14956 | 0.1642 | 1.68 | 7.56 | 56.0 | 12.73 | 0.317 | 25.3 | 322.0 | 514.8 | 14593786 |
| p2-sync-ewma | queue_greedy_ewma | 14702 | 0.1961 | 1.24 | 6.28 | 55.7 | 11.42 | 0.306 | 29.3 | 297.3 | 463.0 | 9321826 |

## Gamma bins vs flip rate

**p2-sync-geom** (median Gamma 0.007, n=12882)

| bin | n | flip rate |
|---|---|---|
| [0,0.5) | 11580 | 0.000 |
| [0.5,1) | 772 | 0.000 |
| [1,2) | 417 | 0.000 |
| [2,5) | 103 | 0.000 |
| [5,inf) | 10 | 0.000 |

**p2-sync-greedy** (median Gamma 0.014, n=13840)

| bin | n | flip rate |
|---|---|---|
| [0,0.5) | 13215 | 0.204 |
| [0.5,1) | 330 | 0.688 |
| [1,2) | 164 | 0.646 |
| [2,5) | 78 | 0.731 |
| [5,inf) | 53 | 0.679 |

**p2-sync-hyst** (median Gamma 0.017, n=13866)

| bin | n | flip rate |
|---|---|---|
| [0,0.5) | 12963 | 0.137 |
| [0.5,1) | 473 | 0.588 |
| [1,2) | 234 | 0.628 |
| [2,5) | 130 | 0.508 |
| [5,inf) | 66 | 0.470 |

**p2-sync-ewma** (median Gamma 0.011, n=13809)

| bin | n | flip rate |
|---|---|---|
| [0,0.5) | 12696 | 0.151 |
| [0.5,1) | 459 | 0.601 |
| [1,2) | 297 | 0.663 |
| [2,5) | 190 | 0.795 |
| [5,inf) | 167 | 0.778 |

## Paired E2E vs baseline

| cell | baseline | same trace | paired | mean delta ms | faster | slower |
|---|---|---|---|---|---|---|
| p2-sync-geom | p2-sync-geom | True | 1255 | 0.00 | 0 | 0 |
| p2-sync-greedy | p2-sync-geom | True | 1253 | -46.19 | 763 | 332 |
| p2-sync-hyst | p2-sync-geom | True | 1248 | -37.08 | 716 | 373 |
| p2-sync-ewma | p2-sync-geom | True | 1255 | -62.32 | 848 | 220 |
