# Persistence / directional-bias report - `m2-c2-greedy-s8`

**Caliber:** stream key = per-(sat,dst), with dst and bits joined from `trace.jsonl.gz` by pid (the decision projection drops dst); `tau_relax` is measured on the backlog **including in-service remaining bits**; `z` is reported both raw and restricted to `epsilon_r >= 1.00 Mbps`; `delta` is in bit/s.

- policy: `queue_greedy` (eta=0.0, alpha=0.25)
- flip rate (eval 4-14 s, frozen rule): **0.2583** (6286/24338); whole-run 0.2581 (6414/24850)
- queue-aware policy: 6286 flips in the eval window, 6414 over the whole run

## Directional bias (delta_r, bit/s)

- sum_r delta_r = **0.00 Mbps** (0 by construction; every flip is one + move and one - move)
- one-way moved load: **264.10 Mbps** (= sum|delta_r|/2, the number to quote); sum|delta_r| = 528.20 Mbps counts each moved bit twice
- positive excess total 264.10 Mbps, negative deficit total -264.10 Mbps, top-5 excess 133.70 Mbps
- resources touched 147 (|delta|>1 Mbps: 66); unpaired flips dropped 41
- caveat: delta is a per-decision attribution of bits moved between the link that should have been used and the one actually used; it is NOT end-to-end rerouted net traffic (later hops can correct it).

| side | link | delta (Mbps) | z |
|---|---|---|---|
| excess | isl:6:26 | +29.70 | 0.79 |
| excess | isl:34:54 | +29.00 | 0.45 |
| excess | isl:14:34 | +28.00 | 0.46 |
| excess | isl:54:55 | +27.70 | 0.40 |
| excess | isl:26:46 | +19.30 | 0.27 |
| deficit | isl:35:55 | -31.70 | - |
| deficit | isl:15:35 | -28.00 | - |
| deficit | isl:125:5 | -25.40 | - |
| deficit | isl:5:25 | -21.90 | - |
| deficit | isl:135:15 | -20.10 | - |

Longest same-sign delta run: +5.00 s / -5.00 s (0.5 s bins).

## Epsilon on corridor links

`rho=0.1` -> 30 links; saturated 0.5 s link-bins 0/3752.

| rho | n links | eps p50 (Mbps) | eps p90 | eps min | max util |
|---|---|---|---|---|---|
| rho=0.05 | 15 | 30.02 | 32.70 | 5.91 | 0.941 |
| rho=0.1 | 30 | 32.70 | 56.30 | 5.91 | 0.941 |
| rho=0.2 | 60 | 59.30 | 69.89 | 5.91 | 0.941 |

Sub-bin check at 0.1 s on the same corridor (0.5 s attribution bins hide saturation):

| window | link-subbins | saturated | frac | served util max | eps min (Mbps) | offered > mu | offered util max |
|---|---|---|---|---|---|---|---|
| eval | 3000 | 347 | 0.1157 | 1.0000 | 0.000 | 540 | 1.300 |
| burst | 150 | 15 | 0.1000 | 1.0000 | 0.000 | 18 | 1.200 |

## z = |delta| / epsilon (flip decisions, eval window)

`z` is a normalised bias magnitude, **not** an overload indicator: `epsilon` is residual capacity after the bits that actually flowed (misrouted ones included), while `delta` is a counterfactual bookkeeping swing.

- raw: p50 0.960, p90 12.097, share>=0.5 0.689, share>=1 0.473 (n=6286)
- valid (epsilon_r >= 1.00 Mbps): p50 0.960, p90 12.097, share>=0.5 0.693, share>=1 0.476 (n=6245; excluded: 0 below the epsilon floor, 41 with no endpoint resource)

### Raw z bins (all flips)

| z bin | n flips | frac | E2E p50 | E2E p95 | E2E p99 | backlog growth p50 (Mbit/s) | backlog@t p50 (Mbit) | relaxed@+2 s | top resource |
|---|---|---|---|---|---|---|---|---|---|
| [0,0.1) | 815 | 0.130 | 319.97 | 638.68 | 705.80 | 0.00 | 0.00 | 0.928 | isl:46:47 |
| [0.1,0.3) | 631 | 0.100 | 445.46 | 673.42 | 779.23 | 0.00 | 0.00 | 0.846 | isl:126:127 |
| [0.3,0.5) | 471 | 0.075 | 495.14 | 698.05 | 784.35 | 0.00 | 0.00 | 0.892 | isl:3:23 |
| [0.5,1) | 1353 | 0.215 | 468.26 | 669.08 | 769.28 | 0.00 | 0.00 | 0.845 | isl:106:105 |
| [1,inf) | 2975 | 0.473 | 450.67 | 623.91 | 699.26 | -0.84 | 0.70 | 0.788 | isl:6:26 |

### z_valid bins (epsilon_r >= floor only)

| z bin | n flips | frac | E2E p50 | E2E p95 | E2E p99 | backlog growth p50 (Mbit/s) | backlog@t p50 (Mbit) | relaxed@+2 s | top resource |
|---|---|---|---|---|---|---|---|---|---|
| [0,0.1) | 815 | 0.131 | 319.97 | 638.68 | 705.80 | 0.00 | 0.00 | 0.928 | isl:46:47 |
| [0.1,0.3) | 631 | 0.101 | 445.46 | 673.42 | 779.23 | 0.00 | 0.00 | 0.846 | isl:126:127 |
| [0.3,0.5) | 471 | 0.075 | 495.14 | 698.05 | 784.35 | 0.00 | 0.00 | 0.892 | isl:3:23 |
| [0.5,1) | 1353 | 0.217 | 468.26 | 669.08 | 769.28 | 0.00 | 0.00 | 0.845 | isl:106:105 |
| [1,inf) | 2975 | 0.476 | 450.67 | 623.91 | 699.26 | -0.84 | 0.70 | 0.788 | isl:6:26 |

Non-flip contrast row (no delta, hence no z): n=18052, E2E p50 448.95 ms, backlog growth p50 0.00 Mbit/s, relaxed@+2 s 0.867.
41 flips have no pair of endpoint links and are excluded from the z bins and from delta.

Burst window [8.0, 8.5]: 307 flips, z>=1 107, z>=0.5 184; 0.5 s-bin offered util max 0.940

## Persistence (tau_error on decision timestamps)

- (sat,dst) same-direction flip runs: 1579 (>=2 flips: 631, >=3: 394); length p50 1, p90 6, max 235; duration p50 50.00 ms, p90 964.54 ms, max 9440.63 ms (runs with >=2 flips; single-flip runs have zero duration)
- per-sat lens (**superseded** Round-1 lens, cross-check only): max length 235, max duration 9440.63 ms
- per-link persistent-excess runs (|delta_r| > 5.00 Mbps): 143 total, 143 with >=2 decisions, 38 lasting >=1 s, 27 censored
- **tau_error**: p50 0.88 s, p90 1.92 s, max 6.94 s; decisions per run p50 15

| link | t0 (s) | t1 (s) | tau_error (s) | decisions | |delta| mean (Mbps) | censored |
|---|---|---|---|---|---|---|
| isl:126:127 | 7.02 | 13.96 | 6.940 | 65 | 9.37 | True |
| isl:14:34 | 9.03 | 13.99 | 4.960 | 245 | 49.39 | True |
| isl:46:47 | 9.02 | 13.92 | 4.891 | 30 | 6.13 | True |
| isl:34:54 | 9.04 | 13.91 | 4.865 | 171 | 35.15 | True |
| isl:57:58 | 6.41 | 10.98 | 4.571 | 122 | 26.69 | False |

|delta| threshold sensitivity (0.5 s bins, **durations quantized - not tau_error**):

| threshold | runs | censored | in corridor | >=1 s | binned dur p50 (s) | p90 | max |
|---|---|---|---|---|---|---|---|
| 1.00 Mbps | 613 | 118 | 85 | 258 | 1.00 | 2.00 | 5.00 |
| 5.00 Mbps | 335 | 58 | 70 | 146 | 1.00 | 2.00 | 5.00 |
| 10.00 Mbps | 194 | 38 | 60 | 98 | 1.00 | 2.00 | 5.00 |

## Backlog relaxation (corridor links)

- backlog = queued + in-service remaining; service slot p50 10.00 ms
- excursions: 70 across 30 corridor links (peak floor 2.00 Mbit = 2 service slots)
- tau_50: p50 17.44 ms, p90 101.30 ms
- tau_band (back to preceding p10-p90): p50 8.75 ms, p90 31.21 ms

### Error segments vs relaxation / vs bias size

Split by tau_error / tau_relax - **degenerate in this cell** (no segment has ratio < 1):

| group | segments | tau_error p50 (s) | tau_relax p50 (s) | ratio p50 | \|delta\| p50 (Mbps) | backlog slope p50 (Mbit/s) | backlog p50 (Mbit) | E2E mean (ms) | E2E vs link baseline (ms) | packets |
|---|---|---|---|---|---|---|---|---|---|---|
| ratio >=1 | 15 | 0.976 | 0.0110 | 89.10 | 75.56 | 0.31 | 0.45 | 413.19 | -27.55 | 1442 |
| ratio <1 | 0 | n/a | n/a | n/a | n/a | n/a | n/a | n/a | n/a | 0 |

Split by |delta| (median 75.56 Mbps):

| group | segments | tau_error p50 (s) | tau_relax p50 (s) | ratio p50 | \|delta\| p50 (Mbps) | backlog slope p50 (Mbit/s) | backlog p50 (Mbit) | E2E mean (ms) | E2E vs link baseline (ms) | packets |
|---|---|---|---|---|---|---|---|---|---|---|
| |delta| high | 8 | 0.976 | 0.0110 | 89.10 | 89.28 | 0.73 | 0.70 | 403.15 | -20.62 | 688 |
| |delta| low | 7 | 0.983 | 0.0100 | 92.13 | 40.01 | 0.06 | 0.45 | 424.67 | -35.47 | 754 |

