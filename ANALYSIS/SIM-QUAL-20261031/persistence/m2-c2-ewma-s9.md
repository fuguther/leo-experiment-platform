# Persistence / directional-bias report - `m2-c2-ewma-s9`

**Caliber:** stream key = per-(sat,dst), with dst and bits joined from `trace.jsonl.gz` by pid (the decision projection drops dst); `tau_relax` is measured on the backlog **including in-service remaining bits**; `z` is reported both raw and restricted to `epsilon_r >= 1.00 Mbps`; `delta` is in bit/s.

- policy: `queue_greedy_ewma` (eta=0.0, alpha=0.25)
- flip rate (eval 4-14 s, frozen rule): **0.2666** (6529/24487); whole-run 0.2657 (6614/24891)
- queue-aware policy: 6529 flips in the eval window, 6614 over the whole run

## Directional bias (delta_r, bit/s)

- sum_r delta_r = **0.00 Mbps** (0 by construction; every flip is one + move and one - move)
- one-way moved load: **409.90 Mbps** (= sum|delta_r|/2, the number to quote); sum|delta_r| = 819.80 Mbps counts each moved bit twice
- positive excess total 409.90 Mbps, negative deficit total -409.90 Mbps, top-5 excess 230.00 Mbps
- resources touched 117 (|delta|>1 Mbps: 70); unpaired flips dropped 37
- caveat: delta is a per-decision attribution of bits moved between the link that should have been used and the one actually used; it is NOT end-to-end rerouted net traffic (later hops can correct it).

| side | link | delta (Mbps) | z |
|---|---|---|---|
| excess | isl:14:34 | +53.20 | 1.14 |
| excess | isl:54:55 | +46.40 | 0.90 |
| excess | isl:34:54 | +46.20 | 1.02 |
| excess | isl:26:46 | +43.60 | 0.93 |
| excess | isl:46:45 | +40.60 | 0.88 |
| deficit | isl:35:55 | -53.70 | - |
| deficit | isl:25:45 | -46.50 | - |
| deficit | isl:135:15 | -44.00 | - |
| deficit | isl:15:35 | -43.80 | - |
| deficit | isl:5:25 | -28.30 | - |

Longest same-sign delta run: +10.00 s / -10.00 s (0.5 s bins).

## Epsilon on corridor links

`rho=0.1` -> 24 links; saturated 0.5 s link-bins 6/3605.

| rho | n links | eps p50 (Mbps) | eps p90 | eps min | max util |
|---|---|---|---|---|---|
| rho=0.05 | 12 | 32.33 | 34.73 | 6.19 | 0.938 |
| rho=0.1 | 24 | 40.39 | 44.40 | 6.19 | 0.938 |
| rho=0.2 | 49 | 44.50 | 65.30 | 6.19 | 0.938 |

Sub-bin check at 0.1 s on the same corridor (0.5 s attribution bins hide saturation):

| window | link-subbins | saturated | frac | served util max | eps min (Mbps) | offered > mu | offered util max |
|---|---|---|---|---|---|---|---|
| eval | 2400 | 360 | 0.1500 | 1.0000 | 0.000 | 563 | 1.300 |
| burst | 120 | 6 | 0.0500 | 1.0000 | 0.000 | 20 | 1.300 |

## z = |delta| / epsilon (flip decisions, eval window)

`z` is a normalised bias magnitude, **not** an overload indicator: `epsilon` is residual capacity after the bits that actually flowed (misrouted ones included), while `delta` is a counterfactual bookkeeping swing.

- raw: p50 1.973, p90 13.421, share>=0.5 0.780, share>=1 0.656 (n=6529)
- valid (epsilon_r >= 1.00 Mbps): p50 1.973, p90 13.421, share>=0.5 0.778, share>=1 0.649 (n=6310; excluded: 182 below the epsilon floor, 37 with no endpoint resource)

### Raw z bins (all flips)

| z bin | n flips | frac | E2E p50 | E2E p95 | E2E p99 | backlog growth p50 (Mbit/s) | backlog@t p50 (Mbit) | relaxed@+2 s | top resource |
|---|---|---|---|---|---|---|---|---|---|
| [0,0.1) | 708 | 0.108 | 312.03 | 552.60 | 674.32 | 0.00 | 0.00 | 0.919 | isl:124:125 |
| [0.1,0.3) | 408 | 0.062 | 440.19 | 624.05 | 705.63 | 0.00 | 0.00 | 0.885 | isl:4:3 |
| [0.3,0.5) | 284 | 0.043 | 462.21 | 606.36 | 652.57 | 0.00 | 0.20 | 0.870 | isl:54:55 |
| [0.5,1) | 812 | 0.124 | 494.97 | 667.01 | 696.83 | 0.00 | 0.28 | 0.718 | isl:54:55 |
| [1,inf) | 4280 | 0.656 | 430.27 | 541.80 | 686.74 | -0.54 | 0.69 | 0.759 | isl:6:26 |

### z_valid bins (epsilon_r >= floor only)

| z bin | n flips | frac | E2E p50 | E2E p95 | E2E p99 | backlog growth p50 (Mbit/s) | backlog@t p50 (Mbit) | relaxed@+2 s | top resource |
|---|---|---|---|---|---|---|---|---|---|
| [0,0.1) | 708 | 0.112 | 312.03 | 552.60 | 674.32 | 0.00 | 0.00 | 0.919 | isl:124:125 |
| [0.1,0.3) | 408 | 0.065 | 440.19 | 624.05 | 705.63 | 0.00 | 0.00 | 0.885 | isl:4:3 |
| [0.3,0.5) | 284 | 0.045 | 462.21 | 606.36 | 652.57 | 0.00 | 0.20 | 0.870 | isl:54:55 |
| [0.5,1) | 812 | 0.129 | 494.97 | 667.01 | 696.83 | 0.00 | 0.28 | 0.718 | isl:54:55 |
| [1,inf) | 4098 | 0.649 | 428.68 | 542.53 | 688.30 | -0.50 | 0.69 | 0.760 | isl:6:26 |

Non-flip contrast row (no delta, hence no z): n=17958, E2E p50 426.96 ms, backlog growth p50 0.00 Mbit/s, relaxed@+2 s 0.878.
37 flips have no pair of endpoint links and are excluded from the z bins and from delta.

Burst window [8.0, 8.5]: 291 flips, z>=1 174, z>=0.5 217; 0.5 s-bin offered util max 0.960

## Persistence (tau_error on decision timestamps)

- (sat,dst) same-direction flip runs: 1278 (>=2 flips: 539, >=3: 368); length p50 1, p90 10, max 508; duration p50 58.72 ms, p90 1125.50 ms, max 9781.09 ms (runs with >=2 flips; single-flip runs have zero duration)
- per-sat lens (**superseded** Round-1 lens, cross-check only): max length 508, max duration 9781.09 ms
- per-link persistent-excess runs (|delta_r| > 5.00 Mbps): 90 total, 90 with >=2 decisions, 28 lasting >=1 s, 26 censored
- **tau_error**: p50 0.95 s, p90 3.99 s, max 9.79 s; decisions per run p50 34

| link | t0 (s) | t1 (s) | tau_error (s) | decisions | |delta| mean (Mbps) | censored |
|---|---|---|---|---|---|---|
| isl:14:34 | 4.16 | 13.95 | 9.791 | 532 | 54.34 | True |
| isl:34:54 | 4.17 | 13.96 | 9.791 | 463 | 47.29 | True |
| isl:54:55 | 4.19 | 13.97 | 9.781 | 479 | 48.97 | True |
| isl:126:127 | 4.54 | 13.83 | 9.289 | 70 | 7.54 | True |
| isl:6:5 | 4.10 | 12.98 | 8.877 | 62 | 6.98 | True |

|delta| threshold sensitivity (0.5 s bins, **durations quantized - not tau_error**):

| threshold | runs | censored | in corridor | >=1 s | binned dur p50 (s) | p90 | max |
|---|---|---|---|---|---|---|---|
| 1.00 Mbps | 446 | 104 | 45 | 174 | 1.00 | 2.00 | 7.50 |
| 5.00 Mbps | 266 | 53 | 28 | 102 | 0.50 | 1.50 | 8.50 |
| 10.00 Mbps | 154 | 33 | 29 | 67 | 1.00 | 1.00 | 3.00 |

## Backlog relaxation (corridor links)

- backlog = queued + in-service remaining; service slot p50 10.00 ms
- excursions: 66 across 24 corridor links (peak floor 2.00 Mbit = 2 service slots)
- tau_50: p50 18.28 ms, p90 91.95 ms
- tau_band (back to preceding p10-p90): p50 7.50 ms, p90 62.41 ms

### Error segments vs relaxation / vs bias size

Split by tau_error / tau_relax - **degenerate in this cell** (no segment has ratio < 1):

| group | segments | tau_error p50 (s) | tau_relax p50 (s) | ratio p50 | \|delta\| p50 (Mbps) | backlog slope p50 (Mbit/s) | backlog p50 (Mbit) | E2E mean (ms) | E2E vs link baseline (ms) | packets |
|---|---|---|---|---|---|---|---|---|---|---|
| ratio >=1 | 6 | 0.971 | 0.0100 | 17.78 | 59.66 | 0.00 | 0.65 | 395.20 | -24.95 | 922 |
| ratio <1 | 0 | n/a | n/a | n/a | n/a | n/a | n/a | n/a | n/a | 0 |

Split by |delta| (median 63.33 Mbps):

| group | segments | tau_error p50 (s) | tau_relax p50 (s) | ratio p50 | \|delta\| p50 (Mbps) | backlog slope p50 (Mbit/s) | backlog p50 (Mbit) | E2E mean (ms) | E2E vs link baseline (ms) | packets |
|---|---|---|---|---|---|---|---|---|---|---|
| |delta| high | 3 | 0.966 | 0.0100 | 96.11 | 84.91 | 0.00 | 0.77 | 378.27 | -36.67 | 289 |
| |delta| low | 3 | 2.674 | 0.1505 | 17.78 | 59.45 | 0.30 | 0.29 | 412.14 | -13.23 | 633 |

