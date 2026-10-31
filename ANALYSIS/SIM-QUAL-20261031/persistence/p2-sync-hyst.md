# Persistence / directional-bias report - `p2-sync-hyst`

**Caliber:** stream key = per-(sat,dst), with dst and bits joined from `trace.jsonl.gz` by pid (the decision projection drops dst); `tau_relax` is measured on the backlog **including in-service remaining bits**; `z` is reported both raw and restricted to `epsilon_r >= 1.00 Mbps`; `delta` is in bit/s.

- policy: `queue_greedy_hysteresis` (eta=0.005, alpha=0.25)
- flip rate (eval 4-14 s, frozen rule): **0.1645** (2419/14704); whole-run 0.1642 (2456/14956)
- queue-aware policy: 2419 flips in the eval window, 2456 over the whole run

## Directional bias (delta_r, bit/s)

- sum_r delta_r = **0.00 Mbps** (0 by construction; every flip is one + move and one - move)
- one-way moved load: **77.10 Mbps** (= sum|delta_r|/2, the number to quote); sum|delta_r| = 154.20 Mbps counts each moved bit twice
- positive excess total 77.10 Mbps, negative deficit total -77.10 Mbps, top-5 excess 38.90 Mbps
- resources touched 86 (|delta|>1 Mbps: 35); unpaired flips dropped 34
- caveat: delta is a per-decision attribution of bits moved between the link that should have been used and the one actually used; it is NOT end-to-end rerouted net traffic (later hops can correct it).

| side | link | delta (Mbps) | z |
|---|---|---|---|
| excess | isl:26:46 | +12.50 | 0.18 |
| excess | isl:54:55 | +9.30 | 0.11 |
| excess | isl:34:54 | +6.80 | 0.08 |
| excess | isl:56:57 | +5.40 | 0.07 |
| excess | isl:4:3 | +4.90 | 0.05 |
| deficit | isl:35:55 | -9.30 | - |
| deficit | isl:5:25 | -8.30 | - |
| deficit | isl:15:35 | -6.80 | - |
| deficit | isl:125:5 | -5.40 | - |
| deficit | isl:77:78 | -5.30 | - |

Longest same-sign delta run: +3.50 s / -3.50 s (0.5 s bins).

## Epsilon on corridor links

`rho=0.1` -> 24 links; saturated 0.5 s link-bins 0/2971.

| rho | n links | eps p50 (Mbps) | eps p90 | eps min | max util |
|---|---|---|---|---|---|
| rho=0.05 | 12 | 38.00 | 43.56 | 23.32 | 0.767 |
| rho=0.1 | 24 | 51.20 | 71.80 | 23.32 | 0.767 |
| rho=0.2 | 48 | 72.36 | 84.42 | 23.32 | 0.767 |

Sub-bin check at 0.1 s on the same corridor (0.5 s attribution bins hide saturation):

| window | link-subbins | saturated | frac | served util max | eps min (Mbps) | offered > mu | offered util max |
|---|---|---|---|---|---|---|---|
| eval | 2400 | 21 | 0.0088 | 0.9990 | 0.098 | 127 | 1.200 |
| burst | 120 | 8 | 0.0667 | 0.9990 | 0.098 | 10 | 1.100 |

## z = |delta| / epsilon (flip decisions, eval window)

`z` is a normalised bias magnitude, **not** an overload indicator: `epsilon` is residual capacity after the bits that actually flowed (misrouted ones included), while `delta` is a counterfactual bookkeeping swing.

- raw: p50 0.123, p90 0.615, share>=0.5 0.136, share>=1 0.039 (n=2419)
- valid (epsilon_r >= 1.00 Mbps): p50 0.123, p90 0.615, share>=0.5 0.138, share>=1 0.040 (n=2385; excluded: 0 below the epsilon floor, 34 with no endpoint resource)

### Raw z bins (all flips)

| z bin | n flips | frac | E2E p50 | E2E p95 | E2E p99 | backlog growth p50 (Mbit/s) | backlog@t p50 (Mbit) | relaxed@+2 s | top resource |
|---|---|---|---|---|---|---|---|---|---|
| [0,0.1) | 975 | 0.403 | 317.28 | 627.52 | 802.33 | 0.00 | 0.00 | 0.917 | isl:46:45 |
| [0.1,0.3) | 730 | 0.302 | 357.41 | 574.79 | 818.73 | 0.00 | 0.19 | 0.877 | isl:25:45 |
| [0.3,0.5) | 352 | 0.146 | 314.65 | 568.68 | 801.74 | -0.27 | 0.61 | 0.858 | isl:26:46 |
| [0.5,1) | 233 | 0.096 | 318.14 | 574.41 | 802.33 | -0.41 | 0.65 | 0.820 | isl:6:26 |
| [1,inf) | 95 | 0.039 | 308.73 | 546.19 | 574.00 | -0.68 | 0.70 | 0.800 | isl:6:26 |

### z_valid bins (epsilon_r >= floor only)

| z bin | n flips | frac | E2E p50 | E2E p95 | E2E p99 | backlog growth p50 (Mbit/s) | backlog@t p50 (Mbit) | relaxed@+2 s | top resource |
|---|---|---|---|---|---|---|---|---|---|
| [0,0.1) | 975 | 0.409 | 317.28 | 627.52 | 802.33 | 0.00 | 0.00 | 0.917 | isl:46:45 |
| [0.1,0.3) | 730 | 0.306 | 357.41 | 574.79 | 818.73 | 0.00 | 0.19 | 0.877 | isl:25:45 |
| [0.3,0.5) | 352 | 0.148 | 314.65 | 568.68 | 801.74 | -0.27 | 0.61 | 0.858 | isl:26:46 |
| [0.5,1) | 233 | 0.098 | 318.14 | 574.41 | 802.33 | -0.41 | 0.65 | 0.820 | isl:6:26 |
| [1,inf) | 95 | 0.040 | 308.73 | 546.19 | 574.00 | -0.68 | 0.70 | 0.800 | isl:6:26 |

Non-flip contrast row (no delta, hence no z): n=12285, E2E p50 305.94 ms, backlog growth p50 0.00 Mbit/s, relaxed@+2 s 0.890.
34 flips have no pair of endpoint links and are excluded from the z bins and from delta.

Burst window [8.0, 8.5]: 208 flips, z>=1 0, z>=0.5 18; 0.5 s-bin offered util max 1.020

## Persistence (tau_error on decision timestamps)

- (sat,dst) same-direction flip runs: 689 (>=2 flips: 370, >=3: 267); length p50 2, p90 8, max 52; duration p50 52.84 ms, p90 665.94 ms, max 9603.98 ms (runs with >=2 flips; single-flip runs have zero duration)
- per-sat lens (**superseded** Round-1 lens, cross-check only): max length 52, max duration 9603.98 ms
- per-link persistent-excess runs (|delta_r| > 5.00 Mbps): 71 total, 71 with >=2 decisions, 21 lasting >=1 s, 16 censored
- **tau_error**: p50 0.39 s, p90 3.21 s, max 9.66 s; decisions per run p50 7

| link | t0 (s) | t1 (s) | tau_error (s) | decisions | |delta| mean (Mbps) | censored |
|---|---|---|---|---|---|---|
| isl:54:55 | 4.24 | 13.90 | 9.661 | 103 | 10.66 | True |
| isl:6:26 | 4.07 | 11.49 | 7.424 | 148 | 19.94 | True |
| isl:34:54 | 4.22 | 10.94 | 6.719 | 63 | 9.38 | True |
| isl:26:46 | 7.52 | 13.34 | 5.811 | 103 | 17.73 | False |
| isl:4:3 | 5.03 | 9.44 | 4.408 | 37 | 8.39 | False |

|delta| threshold sensitivity (0.5 s bins, **durations quantized - not tau_error**):

| threshold | runs | censored | in corridor | >=1 s | binned dur p50 (s) | p90 | max |
|---|---|---|---|---|---|---|---|
| 1.00 Mbps | 388 | 73 | 108 | 97 | 0.50 | 1.50 | 4.50 |
| 5.00 Mbps | 206 | 36 | 74 | 47 | 0.50 | 1.50 | 3.00 |
| 10.00 Mbps | 88 | 16 | 48 | 18 | 0.50 | 1.50 | 2.00 |

## Backlog relaxation (corridor links)

- backlog = queued + in-service remaining; service slot p50 10.00 ms
- excursions: 89 across 24 corridor links (peak floor 2.00 Mbit = 2 service slots)
- tau_50: p50 13.92 ms, p90 40.22 ms
- tau_band (back to preceding p10-p90): p50 11.12 ms, p90 61.07 ms

### Error segments vs relaxation / vs bias size

Split by tau_error / tau_relax - **degenerate in this cell** (no segment has ratio < 1):

| group | segments | tau_error p50 (s) | tau_relax p50 (s) | ratio p50 | \|delta\| p50 (Mbps) | backlog slope p50 (Mbit/s) | backlog p50 (Mbit) | E2E mean (ms) | E2E vs link baseline (ms) | packets |
|---|---|---|---|---|---|---|---|---|---|---|
| ratio >=1 | 18 | 0.380 | 0.0228 | 11.34 | 26.74 | 0.86 | 0.19 | 367.74 | 39.08 | 1028 |
| ratio <1 | 0 | n/a | n/a | n/a | n/a | n/a | n/a | n/a | n/a | 0 |

Split by |delta| (median 27.03 Mbps):

| group | segments | tau_error p50 (s) | tau_relax p50 (s) | ratio p50 | \|delta\| p50 (Mbps) | backlog slope p50 (Mbit/s) | backlog p50 (Mbit) | E2E mean (ms) | E2E vs link baseline (ms) | packets |
|---|---|---|---|---|---|---|---|---|---|---|
| |delta| high | 9 | 0.179 | 0.0262 | 5.61 | 51.49 | 3.06 | 0.80 | 385.71 | 54.94 | 197 |
| |delta| low | 9 | 1.919 | 0.0210 | 88.57 | 17.12 | 0.28 | 0.12 | 349.78 | 23.22 | 831 |

