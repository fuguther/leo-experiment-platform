# Persistence / directional-bias report - `m2-c2-ewma-s8`

**Caliber:** stream key = per-(sat,dst), with dst and bits joined from `trace.jsonl.gz` by pid (the decision projection drops dst); `tau_relax` is measured on the backlog **including in-service remaining bits**; `z` is reported both raw and restricted to `epsilon_r >= 1.00 Mbps`; `delta` is in bit/s.

- policy: `queue_greedy_ewma` (eta=0.0, alpha=0.25)
- flip rate (eval 4-14 s, frozen rule): **0.2658** (6285/23644); whole-run 0.2650 (6400/24147)
- queue-aware policy: 6285 flips in the eval window, 6400 over the whole run

## Directional bias (delta_r, bit/s)

- sum_r delta_r = **0.00 Mbps** (0 by construction; every flip is one + move and one - move)
- one-way moved load: **413.70 Mbps** (= sum|delta_r|/2, the number to quote); sum|delta_r| = 827.40 Mbps counts each moved bit twice
- positive excess total 413.70 Mbps, negative deficit total -413.70 Mbps, top-5 excess 227.60 Mbps
- resources touched 123 (|delta|>1 Mbps: 68); unpaired flips dropped 39
- caveat: delta is a per-decision attribution of bits moved between the link that should have been used and the one actually used; it is NOT end-to-end rerouted net traffic (later hops can correct it).

| side | link | delta (Mbps) | z |
|---|---|---|---|
| excess | isl:6:26 | +49.20 | 1.73 |
| excess | isl:14:34 | +48.00 | 0.93 |
| excess | isl:46:45 | +44.20 | 1.14 |
| excess | isl:54:55 | +43.30 | 0.77 |
| excess | isl:26:46 | +42.90 | 1.10 |
| deficit | isl:25:45 | -49.20 | - |
| deficit | isl:5:25 | -47.70 | - |
| deficit | isl:35:55 | -45.90 | - |
| deficit | isl:15:35 | -35.50 | - |
| deficit | isl:135:15 | -32.70 | - |

Longest same-sign delta run: +10.00 s / -10.00 s (0.5 s bins).

## Epsilon on corridor links

`rho=0.1` -> 24 links; saturated 0.5 s link-bins 2/3592.

| rho | n links | eps p50 (Mbps) | eps p90 | eps min | max util |
|---|---|---|---|---|---|
| rho=0.05 | 12 | 30.51 | 31.47 | 6.01 | 0.940 |
| rho=0.1 | 24 | 38.61 | 44.73 | 6.01 | 0.940 |
| rho=0.2 | 48 | 47.90 | 61.28 | 6.01 | 0.940 |

Sub-bin check at 0.1 s on the same corridor (0.5 s attribution bins hide saturation):

| window | link-subbins | saturated | frac | served util max | eps min (Mbps) | offered > mu | offered util max |
|---|---|---|---|---|---|---|---|
| eval | 2400 | 313 | 0.1304 | 1.0000 | 0.000 | 524 | 1.300 |
| burst | 120 | 13 | 0.1083 | 1.0000 | 0.000 | 24 | 1.200 |

## z = |delta| / epsilon (flip decisions, eval window)

`z` is a normalised bias magnitude, **not** an overload indicator: `epsilon` is residual capacity after the bits that actually flowed (misrouted ones included), while `delta` is a counterfactual bookkeeping swing.

- raw: p50 1.156, p90 10.067, share>=0.5 0.740, share>=1 0.545 (n=6285)
- valid (epsilon_r >= 1.00 Mbps): p50 1.156, p90 10.067, share>=0.5 0.743, share>=1 0.546 (n=6207; excluded: 39 below the epsilon floor, 39 with no endpoint resource)

### Raw z bins (all flips)

| z bin | n flips | frac | E2E p50 | E2E p95 | E2E p99 | backlog growth p50 (Mbit/s) | backlog@t p50 (Mbit) | relaxed@+2 s | top resource |
|---|---|---|---|---|---|---|---|---|---|
| [0,0.1) | 812 | 0.129 | 310.68 | 524.30 | 671.40 | 0.00 | 0.00 | 0.884 | isl:44:45 |
| [0.1,0.3) | 556 | 0.088 | 398.64 | 620.77 | 684.91 | 0.00 | 0.00 | 0.842 | isl:126:127 |
| [0.3,0.5) | 226 | 0.036 | 436.58 | 666.96 | 685.42 | 0.00 | 0.03 | 0.699 | isl:54:55 |
| [0.5,1) | 1224 | 0.195 | 398.25 | 564.88 | 692.97 | 0.00 | 0.27 | 0.733 | isl:34:54 |
| [1,inf) | 3428 | 0.545 | 395.24 | 518.16 | 645.70 | -0.50 | 0.70 | 0.772 | isl:6:26 |

### z_valid bins (epsilon_r >= floor only)

| z bin | n flips | frac | E2E p50 | E2E p95 | E2E p99 | backlog growth p50 (Mbit/s) | backlog@t p50 (Mbit) | relaxed@+2 s | top resource |
|---|---|---|---|---|---|---|---|---|---|
| [0,0.1) | 812 | 0.131 | 310.68 | 524.30 | 671.40 | 0.00 | 0.00 | 0.884 | isl:44:45 |
| [0.1,0.3) | 556 | 0.090 | 398.64 | 620.77 | 684.91 | 0.00 | 0.00 | 0.842 | isl:126:127 |
| [0.3,0.5) | 226 | 0.036 | 436.58 | 666.96 | 685.42 | 0.00 | 0.03 | 0.699 | isl:54:55 |
| [0.5,1) | 1224 | 0.197 | 398.25 | 564.88 | 692.97 | 0.00 | 0.27 | 0.733 | isl:34:54 |
| [1,inf) | 3389 | 0.546 | 395.23 | 518.64 | 645.70 | -0.50 | 0.70 | 0.772 | isl:6:26 |

Non-flip contrast row (no delta, hence no z): n=17359, E2E p50 377.18 ms, backlog growth p50 0.00 Mbit/s, relaxed@+2 s 0.865.
39 flips have no pair of endpoint links and are excluded from the z bins and from delta.

Burst window [8.0, 8.5]: 355 flips, z>=1 218, z>=0.5 266; 0.5 s-bin offered util max 0.920

## Persistence (tau_error on decision timestamps)

- (sat,dst) same-direction flip runs: 1294 (>=2 flips: 576, >=3: 408); length p50 1, p90 9, max 454; duration p50 65.78 ms, p90 1181.82 ms, max 9824.36 ms (runs with >=2 flips; single-flip runs have zero duration)
- per-sat lens (**superseded** Round-1 lens, cross-check only): max length 454, max duration 9824.36 ms
- per-link persistent-excess runs (|delta_r| > 5.00 Mbps): 82 total, 82 with >=2 decisions, 34 lasting >=1 s, 24 censored
- **tau_error**: p50 0.96 s, p90 5.13 s, max 9.82 s; decisions per run p50 15

| link | t0 (s) | t1 (s) | tau_error (s) | decisions | |delta| mean (Mbps) | censored |
|---|---|---|---|---|---|---|
| isl:14:34 | 4.16 | 13.99 | 9.824 | 480 | 48.86 | True |
| isl:54:55 | 4.20 | 14.00 | 9.799 | 438 | 44.70 | True |
| isl:34:54 | 4.18 | 13.91 | 9.729 | 382 | 39.26 | True |
| isl:126:127 | 7.02 | 13.96 | 6.941 | 64 | 9.22 | True |
| isl:24:44 | 7.16 | 13.95 | 6.793 | 87 | 12.81 | True |

|delta| threshold sensitivity (0.5 s bins, **durations quantized - not tau_error**):

| threshold | runs | censored | in corridor | >=1 s | binned dur p50 (s) | p90 | max |
|---|---|---|---|---|---|---|---|
| 1.00 Mbps | 429 | 111 | 47 | 146 | 0.50 | 2.00 | 6.50 |
| 5.00 Mbps | 229 | 51 | 47 | 89 | 0.50 | 1.50 | 4.50 |
| 10.00 Mbps | 127 | 36 | 41 | 51 | 1.00 | 1.50 | 3.00 |

## Backlog relaxation (corridor links)

- backlog = queued + in-service remaining; service slot p50 10.00 ms
- excursions: 69 across 24 corridor links (peak floor 2.00 Mbit = 2 service slots)
- tau_50: p50 22.47 ms, p90 80.32 ms
- tau_band (back to preceding p10-p90): p50 6.28 ms, p90 38.39 ms

### Error segments vs relaxation / vs bias size

Split by tau_error / tau_relax - **degenerate in this cell** (no segment has ratio < 1):

| group | segments | tau_error p50 (s) | tau_relax p50 (s) | ratio p50 | \|delta\| p50 (Mbps) | backlog slope p50 (Mbit/s) | backlog p50 (Mbit) | E2E mean (ms) | E2E vs link baseline (ms) | packets |
|---|---|---|---|---|---|---|---|---|---|---|
| ratio >=1 | 13 | 0.988 | 0.0301 | 93.08 | 75.69 | 0.37 | 0.52 | 383.39 | -6.96 | 2055 |
| ratio <1 | 0 | n/a | n/a | n/a | n/a | n/a | n/a | n/a | n/a | 0 |

Split by |delta| (median 75.69 Mbps):

| group | segments | tau_error p50 (s) | tau_relax p50 (s) | ratio p50 | \|delta\| p50 (Mbps) | backlog slope p50 (Mbit/s) | backlog p50 (Mbit) | E2E mean (ms) | E2E vs link baseline (ms) | packets |
|---|---|---|---|---|---|---|---|---|---|---|
| |delta| high | 7 | 0.979 | 0.0104 | 96.13 | 85.35 | 0.72 | 0.62 | 369.59 | -4.65 | 1059 |
| |delta| low | 6 | 0.988 | 0.0308 | 32.78 | 35.00 | 0.25 | 0.16 | 399.49 | -9.66 | 996 |

