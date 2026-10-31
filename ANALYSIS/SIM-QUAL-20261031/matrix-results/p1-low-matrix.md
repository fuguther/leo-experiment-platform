# Four-policy stale-state matrix (diagnostic)

| cell | policy | multi | flip | R mean | R p90 | AoI p50 | m p50 | switch | W p50 | E2E mean | E2E p95 | isl_data area |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| p1-low-geom | geometry | 4933 | 0.0000 | 0.00 | 0.00 | 521.9 | 12.88 | 0.286 | n/a | 270.3 | 308.1 | 1395252 |
| p1-low-greedy | queue_greedy | 5000 | 0.2764 | 1.86 | 7.87 | 519.7 | 12.95 | 0.267 | 159.3 | 291.0 | 427.7 | 803920 |
| p1-low-hyst | queue_greedy_hysteresis | 4885 | 0.1445 | 1.67 | 7.01 | 521.5 | 11.56 | 0.206 | 61.1 | 287.2 | 427.7 | 883366 |
| p1-low-ewma | queue_greedy_ewma | 4973 | 0.2807 | 1.82 | 7.89 | 519.7 | 12.64 | 0.289 | 156.8 | 275.3 | 357.3 | 781731 |

## Gamma bins vs flip rate

**p1-low-geom** (median Gamma 0.029, n=3996)

| bin | n | flip rate |
|---|---|---|
| [0,0.5) | 3140 | 0.000 |
| [0.5,1) | 287 | 0.000 |
| [1,2) | 249 | 0.000 |
| [2,5) | 202 | 0.000 |
| [5,inf) | 118 | 0.000 |

**p1-low-greedy** (median Gamma 0.040, n=4101)

| bin | n | flip rate |
|---|---|---|
| [0,0.5) | 3240 | 0.155 |
| [0.5,1) | 270 | 0.704 |
| [1,2) | 236 | 0.742 |
| [2,5) | 182 | 0.868 |
| [5,inf) | 173 | 0.896 |

**p1-low-hyst** (median Gamma 0.023, n=4150)

| bin | n | flip rate |
|---|---|---|
| [0,0.5) | 3419 | 0.079 |
| [0.5,1) | 301 | 0.362 |
| [1,2) | 200 | 0.440 |
| [2,5) | 152 | 0.513 |
| [5,inf) | 78 | 0.590 |

**p1-low-ewma** (median Gamma 0.041, n=4046)

| bin | n | flip rate |
|---|---|---|
| [0,0.5) | 3141 | 0.149 |
| [0.5,1) | 260 | 0.719 |
| [1,2) | 242 | 0.806 |
| [2,5) | 199 | 0.824 |
| [5,inf) | 204 | 0.877 |

## Paired E2E vs baseline

| cell | baseline | same trace | paired | mean delta ms | faster | slower |
|---|---|---|---|---|---|---|
| p1-low-geom | p1-low-geom | True | 427 | 0.00 | 0 | 0 |
| p1-low-greedy | p1-low-geom | True | 427 | 20.66 | 98 | 86 |
| p1-low-hyst | p1-low-geom | True | 427 | 16.91 | 199 | 75 |
| p1-low-ewma | p1-low-geom | True | 427 | 4.94 | 88 | 32 |
