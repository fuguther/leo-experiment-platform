# Persistence / directional-bias report - `m2-c2-greedy-s7`

**Caliber:** stream key = per-(sat,dst), with dst and bits joined from `trace.jsonl.gz` by pid (the decision projection drops dst); `tau_relax` is measured on the backlog **including in-service remaining bits**; `z` is reported both raw and restricted to `epsilon_r >= 1.00 Mbps`; `delta` is in bit/s.

- policy: `queue_greedy` (eta=0.0, alpha=0.25)
- flip rate (eval 4-14 s, frozen rule): **0.2654** (6633/24990); whole-run 0.2661 (6795/25531)
- queue-aware policy: 6633 flips in the eval window, 6795 over the whole run

## Directional bias (delta_r, bit/s)

- sum_r delta_r = **0.00 Mbps** (0 by construction; every flip is one + move and one - move)
- one-way moved load: **267.00 Mbps** (= sum|delta_r|/2, the number to quote); sum|delta_r| = 534.00 Mbps counts each moved bit twice
- positive excess total 267.00 Mbps, negative deficit total -267.00 Mbps, top-5 excess 133.00 Mbps
- resources touched 132 (|delta|>1 Mbps: 71); unpaired flips dropped 41
- caveat: delta is a per-decision attribution of bits moved between the link that should have been used and the one actually used; it is NOT end-to-end rerouted net traffic (later hops can correct it).

| side | link | delta (Mbps) | z |
|---|---|---|---|
| excess | isl:46:45 | +41.10 | 0.75 |
| excess | isl:54:55 | +30.90 | 0.45 |
| excess | isl:14:34 | +25.40 | 0.39 |
| excess | isl:63:62 | +17.80 | 0.25 |
| excess | isl:6:26 | +17.80 | 0.33 |
| deficit | isl:25:45 | -39.80 | - |
| deficit | isl:35:55 | -29.30 | - |
| deficit | isl:44:43 | -21.60 | - |
| deficit | isl:83:82 | -16.50 | - |
| deficit | isl:135:15 | -15.90 | - |

Longest same-sign delta run: +4.50 s / -5.00 s (0.5 s bins).

## Epsilon on corridor links

`rho=0.1` -> 28 links; saturated 0.5 s link-bins 0/3834.

| rho | n links | eps p50 (Mbps) | eps p90 | eps min | max util |
|---|---|---|---|---|---|
| rho=0.05 | 14 | 25.41 | 34.20 | 5.67 | 0.943 |
| rho=0.1 | 28 | 34.51 | 48.74 | 5.67 | 0.943 |
| rho=0.2 | 56 | 54.70 | 66.40 | 5.67 | 0.943 |

Sub-bin check at 0.1 s on the same corridor (0.5 s attribution bins hide saturation):

| window | link-subbins | saturated | frac | served util max | eps min (Mbps) | offered > mu | offered util max |
|---|---|---|---|---|---|---|---|
| eval | 2800 | 431 | 0.1539 | 1.0000 | 0.000 | 600 | 1.700 |
| burst | 140 | 12 | 0.0857 | 1.0000 | 0.000 | 15 | 1.100 |

## z = |delta| / epsilon (flip decisions, eval window)

`z` is a normalised bias magnitude, **not** an overload indicator: `epsilon` is residual capacity after the bits that actually flowed (misrouted ones included), while `delta` is a counterfactual bookkeeping swing.

- raw: p50 1.184, p90 13.310, share>=0.5 0.708, share>=1 0.528 (n=6633)
- valid (epsilon_r >= 1.00 Mbps): p50 1.134, p90 13.051, share>=0.5 0.708, share>=1 0.524 (n=6494; excluded: 98 below the epsilon floor, 41 with no endpoint resource)

### Raw z bins (all flips)

| z bin | n flips | frac | E2E p50 | E2E p95 | E2E p99 | backlog growth p50 (Mbit/s) | backlog@t p50 (Mbit) | relaxed@+2 s | top resource |
|---|---|---|---|---|---|---|---|---|---|
| [0,0.1) | 854 | 0.129 | 360.77 | 756.54 | 969.79 | 0.00 | 0.00 | 0.871 | isl:46:47 |
| [0.1,0.3) | 691 | 0.104 | 453.88 | 963.10 | 977.83 | 0.00 | 0.00 | 0.877 | isl:23:43 |
| [0.3,0.5) | 353 | 0.053 | 495.84 | 752.06 | 929.39 | 0.00 | 0.00 | 0.873 | isl:15:35 |
| [0.5,1) | 1190 | 0.179 | 499.58 | 968.47 | 980.37 | 0.00 | 0.08 | 0.759 | isl:34:54 |
| [1,inf) | 3504 | 0.528 | 441.71 | 679.92 | 922.02 | -0.76 | 0.70 | 0.764 | isl:6:26 |

### z_valid bins (epsilon_r >= floor only)

| z bin | n flips | frac | E2E p50 | E2E p95 | E2E p99 | backlog growth p50 (Mbit/s) | backlog@t p50 (Mbit) | relaxed@+2 s | top resource |
|---|---|---|---|---|---|---|---|---|---|
| [0,0.1) | 854 | 0.132 | 360.77 | 756.54 | 969.79 | 0.00 | 0.00 | 0.871 | isl:46:47 |
| [0.1,0.3) | 691 | 0.106 | 453.88 | 963.10 | 977.83 | 0.00 | 0.00 | 0.877 | isl:23:43 |
| [0.3,0.5) | 353 | 0.054 | 495.84 | 752.06 | 929.39 | 0.00 | 0.00 | 0.873 | isl:15:35 |
| [0.5,1) | 1190 | 0.183 | 499.58 | 968.47 | 980.37 | 0.00 | 0.08 | 0.759 | isl:34:54 |
| [1,inf) | 3406 | 0.524 | 440.93 | 680.32 | 922.02 | -0.72 | 0.70 | 0.760 | isl:46:45 |

Non-flip contrast row (no delta, hence no z): n=18357, E2E p50 446.28 ms, backlog growth p50 0.00 Mbit/s, relaxed@+2 s 0.876.
41 flips have no pair of endpoint links and are excluded from the z bins and from delta.

Burst window [8.0, 8.5]: 309 flips, z>=1 145, z>=0.5 188; 0.5 s-bin offered util max 0.980

## Persistence (tau_error on decision timestamps)

- (sat,dst) same-direction flip runs: 1839 (>=2 flips: 720, >=3: 436); length p50 1, p90 6, max 309; duration p50 42.85 ms, p90 937.32 ms, max 9784.24 ms (runs with >=2 flips; single-flip runs have zero duration)
- per-sat lens (**superseded** Round-1 lens, cross-check only): max length 309, max duration 9784.24 ms
- per-link persistent-excess runs (|delta_r| > 5.00 Mbps): 149 total, 149 with >=2 decisions, 30 lasting >=1 s, 32 censored
- **tau_error**: p50 0.92 s, p90 1.94 s, max 6.38 s; decisions per run p50 31

| link | t0 (s) | t1 (s) | tau_error (s) | decisions | |delta| mean (Mbps) | censored |
|---|---|---|---|---|---|---|
| isl:6:5 | 7.55 | 13.93 | 6.377 | 48 | 7.53 | True |
| isl:124:125 | 8.03 | 13.90 | 5.866 | 39 | 6.65 | True |
| isl:126:127 | 4.16 | 9.99 | 5.828 | 53 | 9.09 | True |
| isl:23:43 | 7.05 | 11.99 | 4.947 | 124 | 25.07 | False |
| isl:4:3 | 4.51 | 8.89 | 4.372 | 40 | 9.15 | False |

|delta| threshold sensitivity (0.5 s bins, **durations quantized - not tau_error**):

| threshold | runs | censored | in corridor | >=1 s | binned dur p50 (s) | p90 | max |
|---|---|---|---|---|---|---|---|
| 1.00 Mbps | 593 | 136 | 93 | 257 | 1.00 | 2.00 | 5.00 |
| 5.00 Mbps | 370 | 69 | 66 | 176 | 1.00 | 1.50 | 5.00 |
| 10.00 Mbps | 241 | 42 | 62 | 129 | 1.00 | 1.00 | 4.00 |

## Backlog relaxation (corridor links)

- backlog = queued + in-service remaining; service slot p50 10.00 ms
- excursions: 77 across 28 corridor links (peak floor 2.00 Mbit = 2 service slots)
- tau_50: p50 21.35 ms, p90 86.68 ms
- tau_band (back to preceding p10-p90): p50 7.42 ms, p90 51.93 ms

### Error segments vs relaxation / vs bias size

Split by tau_error / tau_relax - **degenerate in this cell** (no segment has ratio < 1):

| group | segments | tau_error p50 (s) | tau_relax p50 (s) | ratio p50 | \|delta\| p50 (Mbps) | backlog slope p50 (Mbit/s) | backlog p50 (Mbit) | E2E mean (ms) | E2E vs link baseline (ms) | packets |
|---|---|---|---|---|---|---|---|---|---|---|
| ratio >=1 | 18 | 0.971 | 0.0159 | 35.56 | 69.11 | 0.09 | 0.54 | 450.45 | -13.51 | 1549 |
| ratio <1 | 0 | n/a | n/a | n/a | n/a | n/a | n/a | n/a | n/a | 0 |

Split by |delta| (median 71.64 Mbps):

| group | segments | tau_error p50 (s) | tau_relax p50 (s) | ratio p50 | \|delta\| p50 (Mbps) | backlog slope p50 (Mbit/s) | backlog p50 (Mbit) | E2E mean (ms) | E2E vs link baseline (ms) | packets |
|---|---|---|---|---|---|---|---|---|---|---|
| |delta| high | 9 | 0.983 | 0.0580 | 16.96 | 91.50 | 0.52 | 0.55 | 452.72 | 5.89 | 747 |
| |delta| low | 9 | 0.843 | 0.0159 | 57.68 | 34.97 | 0.09 | 0.54 | 448.18 | -32.90 | 802 |

