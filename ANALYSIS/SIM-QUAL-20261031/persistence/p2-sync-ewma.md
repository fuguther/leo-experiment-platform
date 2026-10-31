# Persistence / directional-bias report - `p2-sync-ewma`

**Caliber:** stream key = per-(sat,dst), with dst and bits joined from `trace.jsonl.gz` by pid (the decision projection drops dst); `tau_relax` is measured on the backlog **including in-service remaining bits**; `z` is reported both raw and restricted to `epsilon_r >= 1.00 Mbps`; `delta` is in bit/s.

- policy: `queue_greedy_ewma` (eta=0.0, alpha=0.25)
- flip rate (eval 4-14 s, frozen rule): **0.1957** (2830/14464); whole-run 0.1961 (2883/14702)
- queue-aware policy: 2830 flips in the eval window, 2883 over the whole run

## Directional bias (delta_r, bit/s)

- sum_r delta_r = **0.00 Mbps** (0 by construction; every flip is one + move and one - move)
- one-way moved load: **180.40 Mbps** (= sum|delta_r|/2, the number to quote); sum|delta_r| = 360.80 Mbps counts each moved bit twice
- positive excess total 180.40 Mbps, negative deficit total -180.40 Mbps, top-5 excess 112.70 Mbps
- resources touched 102 (|delta|>1 Mbps: 44); unpaired flips dropped 33
- caveat: delta is a per-decision attribution of bits moved between the link that should have been used and the one actually used; it is NOT end-to-end rerouted net traffic (later hops can correct it).

| side | link | delta (Mbps) | z |
|---|---|---|---|
| excess | isl:6:26 | +39.00 | 1.15 |
| excess | isl:26:46 | +24.80 | 0.45 |
| excess | isl:14:34 | +18.20 | 0.22 |
| excess | isl:54:55 | +15.80 | 0.19 |
| excess | isl:34:54 | +14.90 | 0.18 |
| deficit | isl:5:25 | -25.10 | - |
| deficit | isl:125:5 | -24.30 | - |
| deficit | isl:106:105 | -16.60 | - |
| deficit | isl:35:55 | -15.80 | - |
| deficit | isl:15:35 | -15.00 | - |

Longest same-sign delta run: +9.00 s / -10.00 s (0.5 s bins).

## Epsilon on corridor links

`rho=0.1` -> 23 links; saturated 0.5 s link-bins 0/2818.

| rho | n links | eps p50 (Mbps) | eps p90 | eps min | max util |
|---|---|---|---|---|---|
| rho=0.05 | 12 | 30.83 | 32.01 | 25.32 | 0.747 |
| rho=0.1 | 23 | 33.10 | 75.05 | 25.32 | 0.747 |
| rho=0.2 | 47 | 78.40 | 86.91 | 25.32 | 0.747 |

Sub-bin check at 0.1 s on the same corridor (0.5 s attribution bins hide saturation):

| window | link-subbins | saturated | frac | served util max | eps min (Mbps) | offered > mu | offered util max |
|---|---|---|---|---|---|---|---|
| eval | 2300 | 13 | 0.0057 | 0.9990 | 0.098 | 138 | 1.200 |
| burst | 115 | 6 | 0.0522 | 0.9990 | 0.098 | 21 | 1.200 |

## z = |delta| / epsilon (flip decisions, eval window)

`z` is a normalised bias magnitude, **not** an overload indicator: `epsilon` is residual capacity after the bits that actually flowed (misrouted ones included), while `delta` is a counterfactual bookkeeping swing.

- raw: p50 0.220, p90 1.096, share>=0.5 0.284, share>=1 0.116 (n=2830)
- valid (epsilon_r >= 1.00 Mbps): p50 0.220, p90 1.096, share>=0.5 0.287, share>=1 0.118 (n=2797; excluded: 0 below the epsilon floor, 33 with no endpoint resource)

### Raw z bins (all flips)

| z bin | n flips | frac | E2E p50 | E2E p95 | E2E p99 | backlog growth p50 (Mbit/s) | backlog@t p50 (Mbit) | relaxed@+2 s | top resource |
|---|---|---|---|---|---|---|---|---|---|
| [0,0.1) | 785 | 0.277 | 299.98 | 522.50 | 734.53 | 0.00 | 0.00 | 0.915 | isl:25:45 |
| [0.1,0.3) | 786 | 0.278 | 316.98 | 514.09 | 734.45 | 0.00 | 0.00 | 0.870 | isl:46:45 |
| [0.3,0.5) | 422 | 0.149 | 301.78 | 489.60 | 535.75 | 0.00 | 0.24 | 0.844 | isl:26:46 |
| [0.5,1) | 475 | 0.168 | 298.78 | 450.00 | 535.75 | -0.00 | 0.38 | 0.825 | isl:26:46 |
| [1,inf) | 329 | 0.116 | 294.85 | 427.12 | 477.96 | 0.00 | 0.51 | 0.690 | isl:6:26 |

### z_valid bins (epsilon_r >= floor only)

| z bin | n flips | frac | E2E p50 | E2E p95 | E2E p99 | backlog growth p50 (Mbit/s) | backlog@t p50 (Mbit) | relaxed@+2 s | top resource |
|---|---|---|---|---|---|---|---|---|---|
| [0,0.1) | 785 | 0.281 | 299.98 | 522.50 | 734.53 | 0.00 | 0.00 | 0.915 | isl:25:45 |
| [0.1,0.3) | 786 | 0.281 | 316.98 | 514.09 | 734.45 | 0.00 | 0.00 | 0.870 | isl:46:45 |
| [0.3,0.5) | 422 | 0.151 | 301.78 | 489.60 | 535.75 | 0.00 | 0.24 | 0.844 | isl:26:46 |
| [0.5,1) | 475 | 0.170 | 298.78 | 450.00 | 535.75 | -0.00 | 0.38 | 0.825 | isl:26:46 |
| [1,inf) | 329 | 0.118 | 294.85 | 427.12 | 477.96 | 0.00 | 0.51 | 0.690 | isl:6:26 |

Non-flip contrast row (no delta, hence no z): n=11634, E2E p50 298.55 ms, backlog growth p50 0.00 Mbit/s, relaxed@+2 s 0.874.
33 flips have no pair of endpoint links and are excluded from the z bins and from delta.

Burst window [8.0, 8.5]: 187 flips, z>=1 20, z>=0.5 121; 0.5 s-bin offered util max 1.000

## Persistence (tau_error on decision timestamps)

- (sat,dst) same-direction flip runs: 735 (>=2 flips: 381, >=3: 281); length p50 2, p90 8, max 162; duration p50 74.86 ms, p90 885.03 ms, max 9735.26 ms (runs with >=2 flips; single-flip runs have zero duration)
- per-sat lens (**superseded** Round-1 lens, cross-check only): max length 162, max duration 9735.26 ms
- per-link persistent-excess runs (|delta_r| > 5.00 Mbps): 58 total, 58 with >=2 decisions, 21 lasting >=1 s, 20 censored
- **tau_error**: p50 0.61 s, p90 6.89 s, max 9.91 s; decisions per run p50 9

| link | t0 (s) | t1 (s) | tau_error (s) | decisions | |delta| mean (Mbps) | censored |
|---|---|---|---|---|---|---|
| isl:6:26 | 4.07 | 13.98 | 9.914 | 415 | 41.86 | True |
| isl:26:46 | 4.08 | 13.94 | 9.857 | 268 | 27.19 | True |
| isl:14:34 | 4.20 | 13.94 | 9.735 | 180 | 18.49 | True |
| isl:34:54 | 4.22 | 13.96 | 9.735 | 148 | 15.20 | True |
| isl:54:55 | 4.24 | 13.97 | 9.735 | 157 | 16.13 | True |
| isl:65:64 | 4.22 | 13.85 | 9.631 | 90 | 9.34 | True |
| isl:46:45 | 4.10 | 10.99 | 6.893 | 171 | 24.81 | True |
| isl:124:125 | 7.32 | 11.45 | 4.131 | 17 | 4.11 | False |

|delta| threshold sensitivity (0.5 s bins, **durations quantized - not tau_error**):

| threshold | runs | censored | in corridor | >=1 s | binned dur p50 (s) | p90 | max |
|---|---|---|---|---|---|---|---|
| 1.00 Mbps | 356 | 94 | 72 | 93 | 0.50 | 1.50 | 4.50 |
| 5.00 Mbps | 173 | 49 | 58 | 32 | 0.50 | 1.00 | 5.00 |
| 10.00 Mbps | 101 | 30 | 40 | 35 | 0.50 | 1.50 | 5.00 |

## Backlog relaxation (corridor links)

- backlog = queued + in-service remaining; service slot p50 10.00 ms
- excursions: 49 across 23 corridor links (peak floor 2.00 Mbit = 2 service slots)
- tau_50: p50 12.26 ms, p90 30.27 ms
- tau_band (back to preceding p10-p90): p50 5.61 ms, p90 32.39 ms

### Error segments vs relaxation / vs bias size

Split by tau_error / tau_relax - **degenerate in this cell** (no segment has ratio < 1):

| group | segments | tau_error p50 (s) | tau_relax p50 (s) | ratio p50 | \|delta\| p50 (Mbps) | backlog slope p50 (Mbit/s) | backlog p50 (Mbit) | E2E mean (ms) | E2E vs link baseline (ms) | packets |
|---|---|---|---|---|---|---|---|---|---|---|
| ratio >=1 | 11 | 0.729 | 0.0200 | 38.23 | 21.22 | 0.00 | 0.00 | 335.59 | 2.77 | 706 |
| ratio <1 | 0 | n/a | n/a | n/a | n/a | n/a | n/a | n/a | n/a | 0 |

Split by |delta| (median 21.22 Mbps):

| group | segments | tau_error p50 (s) | tau_relax p50 (s) | ratio p50 | \|delta\| p50 (Mbps) | backlog slope p50 (Mbit/s) | backlog p50 (Mbit) | E2E mean (ms) | E2E vs link baseline (ms) | packets |
|---|---|---|---|---|---|---|---|---|---|---|
| |delta| high | 6 | 0.613 | 0.0105 | 36.25 | 30.20 | -0.00 | 0.00 | 323.59 | 4.74 | 506 |
| |delta| low | 5 | 0.765 | 0.0201 | 38.23 | 15.54 | 0.00 | 0.00 | 349.99 | 0.41 | 200 |

