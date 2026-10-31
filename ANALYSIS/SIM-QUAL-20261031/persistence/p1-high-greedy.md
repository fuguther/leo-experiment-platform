# Persistence / directional-bias report - `p1-high-greedy`

**Caliber:** stream key = per-(sat,dst), with dst and bits joined from `trace.jsonl.gz` by pid (the decision projection drops dst); `tau_relax` is measured on the backlog **including in-service remaining bits**; `z` is reported both raw and restricted to `epsilon_r >= 1.00 Mbps`; `delta` is in bit/s.

- policy: `queue_greedy` (eta=0.0, alpha=0.25)
- flip rate (eval 4-14 s, frozen rule): **0.2779** (4037/14529); whole-run 0.2778 (4098/14752)
- queue-aware policy: 4037 flips in the eval window, 4098 over the whole run

## Directional bias (delta_r, bit/s)

- sum_r delta_r = **0.00 Mbps** (0 by construction; every flip is one + move and one - move)
- one-way moved load: **265.60 Mbps** (= sum|delta_r|/2, the number to quote); sum|delta_r| = 531.20 Mbps counts each moved bit twice
- positive excess total 265.60 Mbps, negative deficit total -265.60 Mbps, top-5 excess 148.80 Mbps
- resources touched 117 (|delta|>1 Mbps: 54); unpaired flips dropped 25
- caveat: delta is a per-decision attribution of bits moved between the link that should have been used and the one actually used; it is NOT end-to-end rerouted net traffic (later hops can correct it).

| side | link | delta (Mbps) | z |
|---|---|---|---|
| excess | isl:44:43 | +50.90 | 1.33 |
| excess | isl:6:26 | +30.00 | 0.62 |
| excess | isl:26:46 | +28.40 | 0.46 |
| excess | isl:46:45 | +21.20 | 0.35 |
| excess | isl:14:34 | +18.30 | 0.22 |
| deficit | isl:23:43 | -38.30 | - |
| deficit | isl:5:25 | -35.00 | - |
| deficit | isl:25:45 | -21.70 | - |
| deficit | isl:125:5 | -18.10 | - |
| deficit | isl:3:23 | -16.90 | - |

Longest same-sign delta run: +9.00 s / -9.00 s (0.5 s bins).

## Epsilon on corridor links

`rho=0.1` -> 24 links; saturated 0.5 s link-bins 0/2934.

| rho | n links | eps p50 (Mbps) | eps p90 | eps min | max util |
|---|---|---|---|---|---|
| rho=0.05 | 12 | 45.41 | 48.09 | 25.62 | 0.744 |
| rho=0.1 | 24 | 51.20 | 69.20 | 25.62 | 0.744 |
| rho=0.2 | 47 | 70.60 | 82.50 | 25.62 | 0.744 |

Sub-bin check at 0.1 s on the same corridor (0.5 s attribution bins hide saturation):

| window | link-subbins | saturated | frac | served util max | eps min (Mbps) | offered > mu | offered util max |
|---|---|---|---|---|---|---|---|
| eval | 2400 | 55 | 0.0229 | 1.0000 | 0.000 | 118 | 1.200 |
| burst | 120 | 12 | 0.1000 | 1.0000 | 0.000 | 18 | 1.100 |

## z = |delta| / epsilon (flip decisions, eval window)

`z` is a normalised bias magnitude, **not** an overload indicator: `epsilon` is residual capacity after the bits that actually flowed (misrouted ones included), while `delta` is a counterfactual bookkeeping swing.

- raw: p50 1.319, p90 3.163, share>=0.5 0.659, share>=1 0.538 (n=4037)
- valid (epsilon_r >= 1.00 Mbps): p50 1.319, p90 3.163, share>=0.5 0.663, share>=1 0.541 (n=4012; excluded: 0 below the epsilon floor, 25 with no endpoint resource)

### Raw z bins (all flips)

| z bin | n flips | frac | E2E p50 | E2E p95 | E2E p99 | backlog growth p50 (Mbit/s) | backlog@t p50 (Mbit) | relaxed@+2 s | top resource |
|---|---|---|---|---|---|---|---|---|---|
| [0,0.1) | 592 | 0.147 | 296.31 | 487.00 | 521.27 | 0.00 | 0.00 | 0.910 | isl:65:64 |
| [0.1,0.3) | 532 | 0.132 | 357.37 | 499.22 | 510.52 | 0.00 | 0.00 | 0.874 | isl:14:34 |
| [0.3,0.5) | 229 | 0.057 | 357.21 | 451.99 | 504.03 | 0.00 | 0.00 | 0.869 | isl:44:43 |
| [0.5,1) | 487 | 0.121 | 357.29 | 437.79 | 535.93 | 0.00 | 0.00 | 0.856 | isl:125:5 |
| [1,inf) | 2172 | 0.538 | 296.66 | 445.48 | 487.41 | -0.37 | 0.67 | 0.732 | isl:44:43 |

### z_valid bins (epsilon_r >= floor only)

| z bin | n flips | frac | E2E p50 | E2E p95 | E2E p99 | backlog growth p50 (Mbit/s) | backlog@t p50 (Mbit) | relaxed@+2 s | top resource |
|---|---|---|---|---|---|---|---|---|---|
| [0,0.1) | 592 | 0.148 | 296.31 | 487.00 | 521.27 | 0.00 | 0.00 | 0.910 | isl:65:64 |
| [0.1,0.3) | 532 | 0.133 | 357.37 | 499.22 | 510.52 | 0.00 | 0.00 | 0.874 | isl:14:34 |
| [0.3,0.5) | 229 | 0.057 | 357.21 | 451.99 | 504.03 | 0.00 | 0.00 | 0.869 | isl:44:43 |
| [0.5,1) | 487 | 0.121 | 357.29 | 437.79 | 535.93 | 0.00 | 0.00 | 0.856 | isl:125:5 |
| [1,inf) | 2172 | 0.541 | 296.66 | 445.48 | 487.41 | -0.37 | 0.67 | 0.732 | isl:44:43 |

Non-flip contrast row (no delta, hence no z): n=10492, E2E p50 299.10 ms, backlog growth p50 0.00 Mbit/s, relaxed@+2 s 0.899.
25 flips have no pair of endpoint links and are excluded from the z bins and from delta.

Burst window [8.0, 8.5]: 306 flips, z>=1 162, z>=0.5 244; 0.5 s-bin offered util max 0.980

## Persistence (tau_error on decision timestamps)

- (sat,dst) same-direction flip runs: 625 (>=2 flips: 309, >=3: 235); length p50 1, p90 16, max 163; duration p50 175.78 ms, p90 2139.80 ms, max 9735.29 ms (runs with >=2 flips; single-flip runs have zero duration)
- per-sat lens (**superseded** Round-1 lens, cross-check only): max length 163, max duration 9735.29 ms
- per-link persistent-excess runs (|delta_r| > 5.00 Mbps): 77 total, 77 with >=2 decisions, 19 lasting >=1 s, 20 censored
- **tau_error**: p50 0.93 s, p90 2.92 s, max 9.74 s; decisions per run p50 14

| link | t0 (s) | t1 (s) | tau_error (s) | decisions | |delta| mean (Mbps) | censored |
|---|---|---|---|---|---|---|
| isl:14:34 | 4.20 | 13.94 | 9.735 | 181 | 18.59 | True |
| isl:34:54 | 4.22 | 13.96 | 9.735 | 165 | 16.95 | True |
| isl:54:55 | 4.24 | 13.97 | 9.735 | 161 | 16.54 | True |
| isl:44:43 | 6.02 | 13.99 | 7.971 | 527 | 66.11 | True |
| isl:56:57 | 4.18 | 10.76 | 6.581 | 121 | 18.39 | True |
| isl:126:127 | 8.34 | 12.98 | 4.639 | 27 | 5.82 | False |
| isl:124:125 | 7.32 | 11.45 | 4.131 | 18 | 4.36 | False |
| isl:6:26 | 10.01 | 12.98 | 2.971 | 209 | 70.36 | False |

|delta| threshold sensitivity (0.5 s bins, **durations quantized - not tau_error**):

| threshold | runs | censored | in corridor | >=1 s | binned dur p50 (s) | p90 | max |
|---|---|---|---|---|---|---|---|
| 1.00 Mbps | 404 | 88 | 63 | 142 | 0.50 | 2.00 | 7.50 |
| 5.00 Mbps | 203 | 49 | 50 | 71 | 0.50 | 1.50 | 7.50 |
| 10.00 Mbps | 132 | 35 | 45 | 68 | 1.00 | 1.50 | 7.50 |

## Backlog relaxation (corridor links)

- backlog = queued + in-service remaining; service slot p50 10.00 ms
- excursions: 36 across 24 corridor links (peak floor 2.00 Mbit = 2 service slots)
- tau_50: p50 12.35 ms, p90 31.88 ms
- tau_band (back to preceding p10-p90): p50 5.26 ms, p90 17.00 ms

### Error segments vs relaxation / vs bias size

Split by tau_error / tau_relax - **degenerate in this cell** (no segment has ratio < 1):

| group | segments | tau_error p50 (s) | tau_relax p50 (s) | ratio p50 | \|delta\| p50 (Mbps) | backlog slope p50 (Mbit/s) | backlog p50 (Mbit) | E2E mean (ms) | E2E vs link baseline (ms) | packets |
|---|---|---|---|---|---|---|---|---|---|---|
| ratio >=1 | 7 | 0.981 | 0.0120 | 81.67 | 66.11 | 0.10 | 0.00 | 324.13 | -8.92 | 990 |
| ratio <1 | 0 | n/a | n/a | n/a | n/a | n/a | n/a | n/a | n/a | 0 |

Split by |delta| (median 66.11 Mbps):

| group | segments | tau_error p50 (s) | tau_relax p50 (s) | ratio p50 | \|delta\| p50 (Mbps) | backlog slope p50 (Mbit/s) | backlog p50 (Mbit) | E2E mean (ms) | E2E vs link baseline (ms) | packets |
|---|---|---|---|---|---|---|---|---|---|---|
| |delta| high | 4 | 0.981 | 0.0400 | 81.67 | 69.36 | 0.11 | 0.21 | 303.92 | -5.15 | 646 |
| |delta| low | 3 | 1.875 | 0.0107 | 175.89 | 20.81 | 0.10 | 0.00 | 351.08 | -13.95 | 344 |

