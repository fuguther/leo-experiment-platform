# Persistence / directional-bias report - `m2-c2-ewma-s7`

**Caliber:** stream key = per-(sat,dst), with dst and bits joined from `trace.jsonl.gz` by pid (the decision projection drops dst); `tau_relax` is measured on the backlog **including in-service remaining bits**; `z` is reported both raw and restricted to `epsilon_r >= 1.00 Mbps`; `delta` is in bit/s.

- policy: `queue_greedy_ewma` (eta=0.0, alpha=0.25)
- flip rate (eval 4-14 s, frozen rule): **0.2621** (6505/24819); whole-run 0.2627 (6647/25305)
- queue-aware policy: 6505 flips in the eval window, 6647 over the whole run

## Directional bias (delta_r, bit/s)

- sum_r delta_r = **0.00 Mbps** (0 by construction; every flip is one + move and one - move)
- one-way moved load: **398.20 Mbps** (= sum|delta_r|/2, the number to quote); sum|delta_r| = 796.40 Mbps counts each moved bit twice
- positive excess total 398.20 Mbps, negative deficit total -398.20 Mbps, top-5 excess 251.80 Mbps
- resources touched 125 (|delta|>1 Mbps: 66); unpaired flips dropped 43
- caveat: delta is a per-decision attribution of bits moved between the link that should have been used and the one actually used; it is NOT end-to-end rerouted net traffic (later hops can correct it).

| side | link | delta (Mbps) | z |
|---|---|---|---|
| excess | isl:6:26 | +56.10 | 2.17 |
| excess | isl:14:34 | +55.50 | 1.25 |
| excess | isl:34:54 | +52.40 | 1.18 |
| excess | isl:54:55 | +51.30 | 1.05 |
| excess | isl:77:78 | +36.50 | 0.66 |
| deficit | isl:35:55 | -58.60 | - |
| deficit | isl:135:15 | -50.90 | - |
| deficit | isl:15:35 | -48.50 | - |
| deficit | isl:106:105 | -31.10 | - |
| deficit | isl:125:5 | -27.80 | - |

Longest same-sign delta run: +10.00 s / -10.00 s (0.5 s bins).

## Epsilon on corridor links

`rho=0.1` -> 28 links; saturated 0.5 s link-bins 1/3656.

| rho | n links | eps p50 (Mbps) | eps p90 | eps min | max util |
|---|---|---|---|---|---|
| rho=0.05 | 14 | 23.21 | 27.40 | 5.67 | 0.943 |
| rho=0.1 | 28 | 32.40 | 42.50 | 5.67 | 0.943 |
| rho=0.2 | 56 | 43.08 | 55.73 | 5.67 | 0.943 |

Sub-bin check at 0.1 s on the same corridor (0.5 s attribution bins hide saturation):

| window | link-subbins | saturated | frac | served util max | eps min (Mbps) | offered > mu | offered util max |
|---|---|---|---|---|---|---|---|
| eval | 2800 | 485 | 0.1732 | 1.0000 | 0.000 | 725 | 1.700 |
| burst | 140 | 13 | 0.0929 | 1.0000 | 0.000 | 28 | 1.100 |

## z = |delta| / epsilon (flip decisions, eval window)

`z` is a normalised bias magnitude, **not** an overload indicator: `epsilon` is residual capacity after the bits that actually flowed (misrouted ones included), while `delta` is a counterfactual bookkeeping swing.

- raw: p50 1.500, p90 18.942, share>=0.5 0.751, share>=1 0.592 (n=6505)
- valid (epsilon_r >= 1.00 Mbps): p50 1.400, p90 14.916, share>=0.5 0.749, share>=1 0.584 (n=6284; excluded: 178 below the epsilon floor, 43 with no endpoint resource)

### Raw z bins (all flips)

| z bin | n flips | frac | E2E p50 | E2E p95 | E2E p99 | backlog growth p50 (Mbit/s) | backlog@t p50 (Mbit) | relaxed@+2 s | top resource |
|---|---|---|---|---|---|---|---|---|---|
| [0,0.1) | 781 | 0.120 | 309.42 | 572.09 | 686.37 | 0.00 | 0.00 | 0.887 | isl:6:5 |
| [0.1,0.3) | 554 | 0.085 | 449.96 | 587.46 | 688.30 | 0.00 | 0.00 | 0.884 | isl:35:36 |
| [0.3,0.5) | 241 | 0.037 | 455.92 | 521.90 | 673.27 | 0.00 | 0.00 | 0.963 | isl:23:43 |
| [0.5,1) | 1038 | 0.160 | 495.35 | 525.81 | 682.39 | 0.00 | 0.24 | 0.712 | isl:77:78 |
| [1,inf) | 3848 | 0.592 | 447.36 | 568.57 | 683.93 | -0.56 | 0.70 | 0.830 | isl:6:26 |

### z_valid bins (epsilon_r >= floor only)

| z bin | n flips | frac | E2E p50 | E2E p95 | E2E p99 | backlog growth p50 (Mbit/s) | backlog@t p50 (Mbit) | relaxed@+2 s | top resource |
|---|---|---|---|---|---|---|---|---|---|
| [0,0.1) | 781 | 0.124 | 309.42 | 572.09 | 686.37 | 0.00 | 0.00 | 0.887 | isl:6:5 |
| [0.1,0.3) | 554 | 0.088 | 449.96 | 587.46 | 688.30 | 0.00 | 0.00 | 0.884 | isl:35:36 |
| [0.3,0.5) | 241 | 0.038 | 455.92 | 521.90 | 673.27 | 0.00 | 0.00 | 0.963 | isl:23:43 |
| [0.5,1) | 1038 | 0.165 | 495.35 | 525.81 | 682.39 | 0.00 | 0.24 | 0.712 | isl:77:78 |
| [1,inf) | 3670 | 0.584 | 446.63 | 572.09 | 685.70 | -0.52 | 0.69 | 0.822 | isl:6:26 |

Non-flip contrast row (no delta, hence no z): n=18314, E2E p50 444.82 ms, backlog growth p50 0.00 Mbit/s, relaxed@+2 s 0.859.
43 flips have no pair of endpoint links and are excluded from the z bins and from delta.

Burst window [8.0, 8.5]: 323 flips, z>=1 172, z>=0.5 236; 0.5 s-bin offered util max 0.980

## Persistence (tau_error on decision timestamps)

- (sat,dst) same-direction flip runs: 1199 (>=2 flips: 523, >=3: 341); length p50 1, p90 7, max 432; duration p50 50.00 ms, p90 1416.01 ms, max 9243.37 ms (runs with >=2 flips; single-flip runs have zero duration)
- per-sat lens (**superseded** Round-1 lens, cross-check only): max length 432, max duration 9221.63 ms
- per-link persistent-excess runs (|delta_r| > 5.00 Mbps): 97 total, 97 with >=2 decisions, 37 lasting >=1 s, 31 censored
- **tau_error**: p50 0.94 s, p90 4.69 s, max 9.82 s; decisions per run p50 14

| link | t0 (s) | t1 (s) | tau_error (s) | decisions | |delta| mean (Mbps) | censored |
|---|---|---|---|---|---|---|
| isl:14:34 | 4.17 | 13.98 | 9.819 | 555 | 56.53 | True |
| isl:34:54 | 4.18 | 13.99 | 9.808 | 525 | 53.53 | True |
| isl:54:55 | 4.20 | 13.99 | 9.794 | 513 | 52.38 | True |
| isl:77:78 | 5.01 | 13.99 | 8.982 | 385 | 42.86 | True |
| isl:35:36 | 5.03 | 13.99 | 8.962 | 112 | 12.50 | True |

|delta| threshold sensitivity (0.5 s bins, **durations quantized - not tau_error**):

| threshold | runs | censored | in corridor | >=1 s | binned dur p50 (s) | p90 | max |
|---|---|---|---|---|---|---|---|
| 1.00 Mbps | 441 | 128 | 52 | 157 | 1.00 | 2.00 | 8.50 |
| 5.00 Mbps | 270 | 66 | 40 | 86 | 0.50 | 2.00 | 5.50 |
| 10.00 Mbps | 151 | 45 | 33 | 54 | 1.00 | 2.00 | 5.00 |

## Backlog relaxation (corridor links)

- backlog = queued + in-service remaining; service slot p50 10.00 ms
- excursions: 65 across 28 corridor links (peak floor 2.00 Mbit = 2 service slots)
- tau_50: p50 20.75 ms, p90 92.11 ms
- tau_band (back to preceding p10-p90): p50 5.68 ms, p90 21.72 ms

### Error segments vs relaxation / vs bias size

Split by tau_error / tau_relax - **degenerate in this cell** (no segment has ratio < 1):

| group | segments | tau_error p50 (s) | tau_relax p50 (s) | ratio p50 | \|delta\| p50 (Mbps) | backlog slope p50 (Mbit/s) | backlog p50 (Mbit) | E2E mean (ms) | E2E vs link baseline (ms) | packets |
|---|---|---|---|---|---|---|---|---|---|---|
| ratio >=1 | 8 | 0.810 | 0.0103 | 65.35 | 92.58 | 1.23 | 0.85 | 399.66 | -39.52 | 507 |
| ratio <1 | 0 | n/a | n/a | n/a | n/a | n/a | n/a | n/a | n/a | 0 |

Split by |delta| (median 90.13 Mbps):

| group | segments | tau_error p50 (s) | tau_relax p50 (s) | ratio p50 | \|delta\| p50 (Mbps) | backlog slope p50 (Mbit/s) | backlog p50 (Mbit) | E2E mean (ms) | E2E vs link baseline (ms) | packets |
|---|---|---|---|---|---|---|---|---|---|---|
| |delta| high | 4 | 0.981 | 0.0150 | 65.35 | 100.88 | 1.57 | 0.95 | 400.79 | -36.30 | 284 |
| |delta| low | 4 | 0.810 | 0.0103 | 65.60 | 82.22 | 1.23 | 0.85 | 398.52 | -42.74 | 223 |

