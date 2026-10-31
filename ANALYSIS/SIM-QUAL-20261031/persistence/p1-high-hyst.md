# Persistence / directional-bias report - `p1-high-hyst`

**Caliber:** stream key = per-(sat,dst), with dst and bits joined from `trace.jsonl.gz` by pid (the decision projection drops dst); `tau_relax` is measured on the backlog **including in-service remaining bits**; `z` is reported both raw and restricted to `epsilon_r >= 1.00 Mbps`; `delta` is in bit/s.

- policy: `queue_greedy_hysteresis` (eta=0.005, alpha=0.25)
- flip rate (eval 4-14 s, frozen rule): **0.1816** (2695/14843); whole-run 0.1815 (2740/15097)
- queue-aware policy: 2695 flips in the eval window, 2740 over the whole run

## Directional bias (delta_r, bit/s)

- sum_r delta_r = **0.00 Mbps** (0 by construction; every flip is one + move and one - move)
- one-way moved load: **112.90 Mbps** (= sum|delta_r|/2, the number to quote); sum|delta_r| = 225.80 Mbps counts each moved bit twice
- positive excess total 112.90 Mbps, negative deficit total -112.90 Mbps, top-5 excess 47.60 Mbps
- resources touched 90 (|delta|>1 Mbps: 48); unpaired flips dropped 27
- caveat: delta is a per-decision attribution of bits moved between the link that should have been used and the one actually used; it is NOT end-to-end rerouted net traffic (later hops can correct it).

| side | link | delta (Mbps) | z |
|---|---|---|---|
| excess | isl:54:55 | +13.80 | 0.16 |
| excess | isl:34:54 | +10.40 | 0.12 |
| excess | isl:26:46 | +10.00 | 0.14 |
| excess | isl:46:45 | +6.70 | 0.10 |
| excess | isl:84:83 | +6.70 | 0.07 |
| deficit | isl:35:55 | -14.70 | - |
| deficit | isl:15:35 | -10.30 | - |
| deficit | isl:125:5 | -8.90 | - |
| deficit | isl:25:45 | -8.40 | - |
| deficit | isl:5:25 | -7.00 | - |

Longest same-sign delta run: +7.00 s / -7.00 s (0.5 s bins).

## Epsilon on corridor links

`rho=0.1` -> 21 links; saturated 0.5 s link-bins 0/2673.

| rho | n links | eps p50 (Mbps) | eps p90 | eps min | max util |
|---|---|---|---|---|---|
| rho=0.05 | 11 | 39.64 | 47.90 | 23.52 | 0.765 |
| rho=0.1 | 21 | 48.00 | 62.86 | 23.52 | 0.765 |
| rho=0.2 | 42 | 69.30 | 83.30 | 23.52 | 0.765 |

Sub-bin check at 0.1 s on the same corridor (0.5 s attribution bins hide saturation):

| window | link-subbins | saturated | frac | served util max | eps min (Mbps) | offered > mu | offered util max |
|---|---|---|---|---|---|---|---|
| eval | 2100 | 77 | 0.0367 | 1.0000 | 0.000 | 200 | 1.200 |
| burst | 105 | 9 | 0.0857 | 1.0000 | 0.000 | 14 | 1.100 |

## z = |delta| / epsilon (flip decisions, eval window)

`z` is a normalised bias magnitude, **not** an overload indicator: `epsilon` is residual capacity after the bits that actually flowed (misrouted ones included), while `delta` is a counterfactual bookkeeping swing.

- raw: p50 0.790, p90 2.637, share>=0.5 0.583, share>=1 0.462 (n=2695)
- valid (epsilon_r >= 1.00 Mbps): p50 0.790, p90 2.637, share>=0.5 0.588, share>=1 0.467 (n=2668; excluded: 0 below the epsilon floor, 27 with no endpoint resource)

### Raw z bins (all flips)

| z bin | n flips | frac | E2E p50 | E2E p95 | E2E p99 | backlog growth p50 (Mbit/s) | backlog@t p50 (Mbit) | relaxed@+2 s | top resource |
|---|---|---|---|---|---|---|---|---|---|
| [0,0.1) | 466 | 0.173 | 307.21 | 550.81 | 679.11 | 0.00 | 0.00 | 0.929 | isl:126:127 |
| [0.1,0.3) | 375 | 0.139 | 369.37 | 555.77 | 694.21 | 0.00 | 0.00 | 0.859 | isl:54:55 |
| [0.3,0.5) | 257 | 0.095 | 427.54 | 597.54 | 745.13 | 0.00 | 0.00 | 0.864 | isl:23:43 |
| [0.5,1) | 325 | 0.121 | 430.40 | 557.43 | 694.21 | -0.33 | 0.59 | 0.858 | isl:6:26 |
| [1,inf) | 1245 | 0.462 | 326.57 | 553.72 | 604.50 | -0.47 | 0.69 | 0.866 | isl:46:45 |

### z_valid bins (epsilon_r >= floor only)

| z bin | n flips | frac | E2E p50 | E2E p95 | E2E p99 | backlog growth p50 (Mbit/s) | backlog@t p50 (Mbit) | relaxed@+2 s | top resource |
|---|---|---|---|---|---|---|---|---|---|
| [0,0.1) | 466 | 0.175 | 307.21 | 550.81 | 679.11 | 0.00 | 0.00 | 0.929 | isl:126:127 |
| [0.1,0.3) | 375 | 0.141 | 369.37 | 555.77 | 694.21 | 0.00 | 0.00 | 0.859 | isl:54:55 |
| [0.3,0.5) | 257 | 0.096 | 427.54 | 597.54 | 745.13 | 0.00 | 0.00 | 0.864 | isl:23:43 |
| [0.5,1) | 325 | 0.122 | 430.40 | 557.43 | 694.21 | -0.33 | 0.59 | 0.858 | isl:6:26 |
| [1,inf) | 1245 | 0.467 | 326.57 | 553.72 | 604.50 | -0.47 | 0.69 | 0.866 | isl:46:45 |

Non-flip contrast row (no delta, hence no z): n=12148, E2E p50 317.55 ms, backlog growth p50 0.00 Mbit/s, relaxed@+2 s 0.901.
27 flips have no pair of endpoint links and are excluded from the z bins and from delta.

Burst window [8.0, 8.5]: 177 flips, z>=1 30, z>=0.5 68; 0.5 s-bin offered util max 1.020

## Persistence (tau_error on decision timestamps)

- (sat,dst) same-direction flip runs: 465 (>=2 flips: 249, >=3: 182); length p50 2, p90 11, max 138; duration p50 62.81 ms, p90 1305.78 ms, max 9883.94 ms (runs with >=2 flips; single-flip runs have zero duration)
- per-sat lens (**superseded** Round-1 lens, cross-check only): max length 138, max duration 9883.94 ms
- per-link persistent-excess runs (|delta_r| > 5.00 Mbps): 75 total, 75 with >=2 decisions, 23 lasting >=1 s, 16 censored
- **tau_error**: p50 0.80 s, p90 2.44 s, max 9.66 s; decisions per run p50 17

| link | t0 (s) | t1 (s) | tau_error (s) | decisions | |delta| mean (Mbps) | censored |
|---|---|---|---|---|---|---|
| isl:54:55 | 4.24 | 13.90 | 9.661 | 136 | 14.08 | True |
| isl:34:54 | 4.22 | 10.94 | 6.719 | 103 | 15.33 | True |
| isl:56:57 | 4.18 | 8.98 | 4.793 | 88 | 18.36 | True |
| isl:126:127 | 9.50 | 13.87 | 4.367 | 26 | 5.95 | True |
| isl:124:125 | 7.32 | 11.45 | 4.131 | 18 | 4.36 | False |

|delta| threshold sensitivity (0.5 s bins, **durations quantized - not tau_error**):

| threshold | runs | censored | in corridor | >=1 s | binned dur p50 (s) | p90 | max |
|---|---|---|---|---|---|---|---|
| 1.00 Mbps | 299 | 69 | 66 | 109 | 0.50 | 1.50 | 4.50 |
| 5.00 Mbps | 181 | 36 | 58 | 55 | 0.50 | 1.00 | 3.50 |
| 10.00 Mbps | 118 | 31 | 48 | 34 | 0.50 | 1.00 | 2.00 |

## Backlog relaxation (corridor links)

- backlog = queued + in-service remaining; service slot p50 10.00 ms
- excursions: 49 across 21 corridor links (peak floor 2.00 Mbit = 2 service slots)
- tau_50: p50 13.85 ms, p90 40.22 ms
- tau_band (back to preceding p10-p90): p50 5.01 ms, p90 28.19 ms

### Error segments vs relaxation / vs bias size

Split by tau_error / tau_relax - **degenerate in this cell** (no segment has ratio < 1):

| group | segments | tau_error p50 (s) | tau_relax p50 (s) | ratio p50 | \|delta\| p50 (Mbps) | backlog slope p50 (Mbit/s) | backlog p50 (Mbit) | E2E mean (ms) | E2E vs link baseline (ms) | packets |
|---|---|---|---|---|---|---|---|---|---|---|
| ratio >=1 | 13 | 0.571 | 0.0200 | 19.21 | 29.39 | 0.57 | 0.21 | 381.64 | 9.12 | 707 |
| ratio <1 | 0 | n/a | n/a | n/a | n/a | n/a | n/a | n/a | n/a | 0 |

Split by |delta| (median 29.39 Mbps):

| group | segments | tau_error p50 (s) | tau_relax p50 (s) | ratio p50 | \|delta\| p50 (Mbps) | backlog slope p50 (Mbit/s) | backlog p50 (Mbit) | E2E mean (ms) | E2E vs link baseline (ms) | packets |
|---|---|---|---|---|---|---|---|---|---|---|
| |delta| high | 7 | 0.766 | 0.0200 | 23.10 | 53.06 | 0.88 | 0.28 | 381.15 | 13.06 | 366 |
| |delta| low | 6 | 0.270 | 0.0322 | 8.37 | 18.36 | 0.00 | 0.00 | 382.22 | 4.53 | 341 |

