# Persistence / directional-bias report - `p2-sync-greedy`

**Caliber:** stream key = per-(sat,dst), with dst and bits joined from `trace.jsonl.gz` by pid (the decision projection drops dst); `tau_relax` is measured on the backlog **including in-service remaining bits**; `z` is reported both raw and restricted to `epsilon_r >= 1.00 Mbps`; `delta` is in bit/s.

- policy: `queue_greedy` (eta=0.0, alpha=0.25)
- flip rate (eval 4-14 s, frozen rule): **0.2243** (3307/14746); whole-run 0.2233 (3345/14977)
- queue-aware policy: 3307 flips in the eval window, 3345 over the whole run

## Directional bias (delta_r, bit/s)

- sum_r delta_r = **0.00 Mbps** (0 by construction; every flip is one + move and one - move)
- one-way moved load: **145.20 Mbps** (= sum|delta_r|/2, the number to quote); sum|delta_r| = 290.40 Mbps counts each moved bit twice
- positive excess total 145.20 Mbps, negative deficit total -145.20 Mbps, top-5 excess 67.10 Mbps
- resources touched 110 (|delta|>1 Mbps: 49); unpaired flips dropped 36
- caveat: delta is a per-decision attribution of bits moved between the link that should have been used and the one actually used; it is NOT end-to-end rerouted net traffic (later hops can correct it).

| side | link | delta (Mbps) | z |
|---|---|---|---|
| excess | isl:46:45 | +15.80 | 0.23 |
| excess | isl:14:34 | +14.50 | 0.17 |
| excess | isl:26:46 | +13.50 | 0.19 |
| excess | isl:54:55 | +12.20 | 0.14 |
| excess | isl:34:54 | +11.10 | 0.13 |
| deficit | isl:25:45 | -13.20 | - |
| deficit | isl:135:15 | -13.00 | - |
| deficit | isl:35:55 | -11.60 | - |
| deficit | isl:15:35 | -10.60 | - |
| deficit | isl:5:25 | -9.70 | - |

Longest same-sign delta run: +8.00 s / -8.00 s (0.5 s bins).

## Epsilon on corridor links

`rho=0.1` -> 27 links; saturated 0.5 s link-bins 0/3214.

| rho | n links | eps p50 (Mbps) | eps p90 | eps min | max util |
|---|---|---|---|---|---|
| rho=0.05 | 13 | 47.81 | 56.70 | 25.62 | 0.744 |
| rho=0.1 | 27 | 61.60 | 68.00 | 25.62 | 0.744 |
| rho=0.2 | 53 | 70.80 | 86.30 | 25.62 | 0.744 |

Sub-bin check at 0.1 s on the same corridor (0.5 s attribution bins hide saturation):

| window | link-subbins | saturated | frac | served util max | eps min (Mbps) | offered > mu | offered util max |
|---|---|---|---|---|---|---|---|
| eval | 2700 | 13 | 0.0048 | 0.9990 | 0.098 | 51 | 1.200 |
| burst | 135 | 6 | 0.0444 | 0.9990 | 0.098 | 8 | 1.100 |

## z = |delta| / epsilon (flip decisions, eval window)

`z` is a normalised bias magnitude, **not** an overload indicator: `epsilon` is residual capacity after the bits that actually flowed (misrouted ones included), while `delta` is a counterfactual bookkeeping swing.

- raw: p50 0.136, p90 0.401, share>=0.5 0.036, share>=1 0.000 (n=3307)
- valid (epsilon_r >= 1.00 Mbps): p50 0.136, p90 0.401, share>=0.5 0.036, share>=1 0.000 (n=3271; excluded: 0 below the epsilon floor, 36 with no endpoint resource)

### Raw z bins (all flips)

| z bin | n flips | frac | E2E p50 | E2E p95 | E2E p99 | backlog growth p50 (Mbit/s) | backlog@t p50 (Mbit) | relaxed@+2 s | top resource |
|---|---|---|---|---|---|---|---|---|---|
| [0,0.1) | 1329 | 0.402 | 317.92 | 576.48 | 788.90 | 0.00 | 0.00 | 0.880 | isl:125:5 |
| [0.1,0.3) | 1320 | 0.399 | 318.50 | 514.60 | 671.95 | 0.00 | 0.00 | 0.855 | isl:6:26 |
| [0.3,0.5) | 504 | 0.152 | 308.75 | 504.45 | 734.79 | -0.09 | 0.34 | 0.792 | isl:26:46 |
| [0.5,1) | 118 | 0.036 | 310.08 | 561.74 | 703.13 | -0.09 | 0.41 | 0.695 | isl:44:43 |
| [1,inf) | 0 | 0.000 | n/a | n/a | n/a | n/a | n/a | n/a | n/a |

### z_valid bins (epsilon_r >= floor only)

| z bin | n flips | frac | E2E p50 | E2E p95 | E2E p99 | backlog growth p50 (Mbit/s) | backlog@t p50 (Mbit) | relaxed@+2 s | top resource |
|---|---|---|---|---|---|---|---|---|---|
| [0,0.1) | 1329 | 0.406 | 317.92 | 576.48 | 788.90 | 0.00 | 0.00 | 0.880 | isl:125:5 |
| [0.1,0.3) | 1320 | 0.404 | 318.50 | 514.60 | 671.95 | 0.00 | 0.00 | 0.855 | isl:6:26 |
| [0.3,0.5) | 504 | 0.154 | 308.75 | 504.45 | 734.79 | -0.09 | 0.34 | 0.792 | isl:26:46 |
| [0.5,1) | 118 | 0.036 | 310.08 | 561.74 | 703.13 | -0.09 | 0.41 | 0.695 | isl:44:43 |
| [1,inf) | 0 | 0.000 | n/a | n/a | n/a | n/a | n/a | n/a | n/a |

Non-flip contrast row (no delta, hence no z): n=11439, E2E p50 304.89 ms, backlog growth p50 0.00 Mbit/s, relaxed@+2 s 0.894.
36 flips have no pair of endpoint links and are excluded from the z bins and from delta.

Burst window [8.0, 8.5]: 242 flips, z>=1 0, z>=0.5 18; 0.5 s-bin offered util max 0.980

## Persistence (tau_error on decision timestamps)

- (sat,dst) same-direction flip runs: 1171 (>=2 flips: 582, >=3: 384); length p50 1, p90 6, max 64; duration p50 52.53 ms, p90 530.00 ms, max 9247.89 ms (runs with >=2 flips; single-flip runs have zero duration)
- per-sat lens (**superseded** Round-1 lens, cross-check only): max length 64, max duration 9247.89 ms
- per-link persistent-excess runs (|delta_r| > 5.00 Mbps): 64 total, 64 with >=2 decisions, 27 lasting >=1 s, 19 censored
- **tau_error**: p50 0.47 s, p90 2.98 s, max 9.91 s; decisions per run p50 10

| link | t0 (s) | t1 (s) | tau_error (s) | decisions | |delta| mean (Mbps) | censored |
|---|---|---|---|---|---|---|
| isl:26:46 | 4.08 | 14.00 | 9.914 | 203 | 20.48 | True |
| isl:46:45 | 4.10 | 13.97 | 9.870 | 247 | 25.03 | True |
| isl:14:34 | 4.20 | 13.94 | 9.735 | 147 | 15.10 | True |
| isl:34:54 | 4.22 | 13.96 | 9.735 | 123 | 12.63 | True |
| isl:54:55 | 4.24 | 13.97 | 9.735 | 124 | 12.74 | True |

|delta| threshold sensitivity (0.5 s bins, **durations quantized - not tau_error**):

| threshold | runs | censored | in corridor | >=1 s | binned dur p50 (s) | p90 | max |
|---|---|---|---|---|---|---|---|
| 1.00 Mbps | 471 | 95 | 74 | 137 | 0.50 | 1.50 | 5.50 |
| 5.00 Mbps | 212 | 50 | 67 | 48 | 0.50 | 1.50 | 8.00 |
| 10.00 Mbps | 125 | 29 | 58 | 36 | 0.50 | 1.50 | 2.00 |

## Backlog relaxation (corridor links)

- backlog = queued + in-service remaining; service slot p50 10.00 ms
- excursions: 70 across 27 corridor links (peak floor 2.00 Mbit = 2 service slots)
- tau_50: p50 11.41 ms, p90 30.29 ms
- tau_band (back to preceding p10-p90): p50 10.19 ms, p90 31.39 ms

### Error segments vs relaxation / vs bias size

Split by tau_error / tau_relax - **degenerate in this cell** (no segment has ratio < 1):

| group | segments | tau_error p50 (s) | tau_relax p50 (s) | ratio p50 | \|delta\| p50 (Mbps) | backlog slope p50 (Mbit/s) | backlog p50 (Mbit) | E2E mean (ms) | E2E vs link baseline (ms) | packets |
|---|---|---|---|---|---|---|---|---|---|---|
| ratio >=1 | 15 | 1.291 | 0.0105 | 95.89 | 18.59 | 0.09 | 0.37 | 338.61 | 2.55 | 996 |
| ratio <1 | 0 | n/a | n/a | n/a | n/a | n/a | n/a | n/a | n/a | 0 |

Split by |delta| (median 18.59 Mbps):

| group | segments | tau_error p50 (s) | tau_relax p50 (s) | ratio p50 | \|delta\| p50 (Mbps) | backlog slope p50 (Mbit/s) | backlog p50 (Mbit) | E2E mean (ms) | E2E vs link baseline (ms) | packets |
|---|---|---|---|---|---|---|---|---|---|---|
| |delta| high | 8 | 1.291 | 0.0106 | 122.42 | 36.13 | 0.83 | 0.62 | 338.32 | 5.68 | 505 |
| |delta| low | 7 | 1.419 | 0.0105 | 95.89 | 11.76 | 0.02 | 0.00 | 338.94 | -1.03 | 491 |

