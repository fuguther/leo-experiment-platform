# Four-policy stale-state matrix (diagnostic)

| cell | policy | multi | flip | R mean | R p90 | AoI p50 | m p50 | switch | W p50 | E2E mean | E2E p95 | isl_data area |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| p1-high-geom | geometry | 14725 | 0.0000 | 0.00 | 0.00 | 493.3 | 12.94 | 0.217 | n/a | 361.4 | 660.7 | 118891497 |
| p1-high-greedy | queue_greedy | 14752 | 0.2778 | 1.66 | 7.10 | 487.7 | 11.49 | 0.187 | 71.9 | 305.6 | 461.7 | 8251693 |
| p1-high-hyst | queue_greedy_hysteresis | 15097 | 0.1815 | 1.87 | 7.52 | 486.8 | 11.47 | 0.071 | 49.1 | 339.6 | 547.9 | 11975574 |
| p1-high-ewma | queue_greedy_ewma | 14544 | 0.2849 | 1.55 | 6.69 | 489.1 | 10.56 | 0.105 | 112.7 | 295.0 | 431.1 | 7767511 |

## Gamma bins vs flip rate

**p1-high-geom** (median Gamma 0.048, n=12872)

| bin | n | flip rate |
|---|---|---|
| [0,0.5) | 9487 | 0.000 |
| [0.5,1) | 646 | 0.000 |
| [1,2) | 689 | 0.000 |
| [2,5) | 943 | 0.000 |
| [5,inf) | 1107 | 0.000 |

**p1-high-greedy** (median Gamma 0.074, n=13497)

| bin | n | flip rate |
|---|---|---|
| [0,0.5) | 10464 | 0.158 |
| [0.5,1) | 1036 | 0.544 |
| [1,2) | 736 | 0.702 |
| [2,5) | 675 | 0.821 |
| [5,inf) | 586 | 0.828 |

**p1-high-hyst** (median Gamma 0.093, n=14003)

| bin | n | flip rate |
|---|---|---|
| [0,0.5) | 10261 | 0.096 |
| [0.5,1) | 1265 | 0.308 |
| [1,2) | 943 | 0.420 |
| [2,5) | 880 | 0.480 |
| [5,inf) | 654 | 0.613 |

**p1-high-ewma** (median Gamma 0.074, n=13621)

| bin | n | flip rate |
|---|---|---|
| [0,0.5) | 10282 | 0.152 |
| [0.5,1) | 1045 | 0.592 |
| [1,2) | 934 | 0.714 |
| [2,5) | 680 | 0.712 |
| [5,inf) | 680 | 0.834 |

## Paired E2E vs baseline

| cell | baseline | same trace | paired | mean delta ms | faster | slower |
|---|---|---|---|---|---|---|
| p1-high-geom | p1-high-geom | True | 1255 | 0.00 | 0 | 0 |
| p1-high-greedy | p1-high-geom | True | 1254 | -55.83 | 809 | 282 |
| p1-high-hyst | p1-high-geom | True | 1254 | -21.47 | 689 | 390 |
| p1-high-ewma | p1-high-geom | True | 1255 | -66.41 | 900 | 168 |
