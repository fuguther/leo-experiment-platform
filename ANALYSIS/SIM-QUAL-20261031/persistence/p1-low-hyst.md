# Persistence / directional-bias report - `p1-low-hyst`

**Caliber:** stream key = per-(sat,dst), with dst and bits joined from `trace.jsonl.gz` by pid (the decision projection drops dst); `tau_relax` is measured on the backlog **including in-service remaining bits**; `z` is reported both raw and restricted to `epsilon_r >= 1.00 Mbps`; `delta` is in bit/s.

- policy: `queue_greedy_hysteresis` (eta=0.005, alpha=0.25)
- flip rate (eval 4-14 s, frozen rule): **0.1453** (703/4839); whole-run 0.1445 (706/4885)
- queue-aware policy: 703 flips in the eval window, 706 over the whole run

## Directional bias (delta_r, bit/s)

- sum_r delta_r = **0.00 Mbps** (0 by construction; every flip is one + move and one - move)
- one-way moved load: **47.60 Mbps** (= sum|delta_r|/2, the number to quote); sum|delta_r| = 95.20 Mbps counts each moved bit twice
- positive excess total 47.60 Mbps, negative deficit total -47.60 Mbps, top-5 excess 35.10 Mbps
- resources touched 59 (|delta|>1 Mbps: 19); unpaired flips dropped 5
- caveat: delta is a per-decision attribution of bits moved between the link that should have been used and the one actually used; it is NOT end-to-end rerouted net traffic (later hops can correct it).

| side | link | delta (Mbps) | z |
|---|---|---|---|
| excess | isl:5:25 | +11.80 | 0.17 |
| excess | isl:46:45 | +10.00 | 0.11 |
| excess | isl:6:26 | +6.20 | 0.07 |
| excess | isl:4:24 | +5.10 | 0.06 |
| excess | isl:126:127 | +2.00 | 0.02 |
| deficit | isl:26:46 | -12.40 | - |
| deficit | isl:25:45 | -10.60 | - |
| deficit | isl:125:5 | -7.30 | - |
| deficit | isl:123:3 | -4.70 | - |
| deficit | isl:7:8 | -2.00 | - |

Longest same-sign delta run: +7.00 s / -7.00 s (0.5 s bins).

## Epsilon on corridor links

`rho=0.1` -> 15 links; saturated 0.5 s link-bins 0/1531.

| rho | n links | eps p50 (Mbps) | eps p90 | eps min | max util |
|---|---|---|---|---|---|
| rho=0.05 | 8 | 70.10 | 70.20 | 69.40 | 0.306 |
| rho=0.1 | 15 | 71.00 | 79.52 | 69.40 | 0.306 |
| rho=0.2 | 30 | 80.50 | 94.80 | 69.40 | 0.306 |

Sub-bin check at 0.1 s on the same corridor (0.5 s attribution bins hide saturation):

| window | link-subbins | saturated | frac | served util max | eps min (Mbps) | offered > mu | offered util max |
|---|---|---|---|---|---|---|---|
| eval | 1500 | 0 | 0.0000 | 0.8000 | 20.000 | 0 | 0.900 |
| burst | 75 | 0 | 0.0000 | 0.4856 | 51.441 | 0 | 0.500 |

## z = |delta| / epsilon (flip decisions, eval window)

`z` is a normalised bias magnitude, **not** an overload indicator: `epsilon` is residual capacity after the bits that actually flowed (misrouted ones included), while `delta` is a counterfactual bookkeeping swing.

- raw: p50 0.380, p90 0.789, share>=0.5 0.255, share>=1 0.074 (n=703)
- valid (epsilon_r >= 1.00 Mbps): p50 0.380, p90 0.789, share>=0.5 0.256, share>=1 0.074 (n=698; excluded: 0 below the epsilon floor, 5 with no endpoint resource)

### Raw z bins (all flips)

| z bin | n flips | frac | E2E p50 | E2E p95 | E2E p99 | backlog growth p50 (Mbit/s) | backlog@t p50 (Mbit) | relaxed@+2 s | top resource |
|---|---|---|---|---|---|---|---|---|---|
| [0,0.1) | 143 | 0.203 | 273.36 | 447.98 | 464.07 | 0.00 | 0.00 | 0.937 | isl:126:127 |
| [0.1,0.3) | 134 | 0.191 | 288.72 | 427.74 | 435.76 | 0.00 | 0.00 | 0.918 | isl:5:25 |
| [0.3,0.5) | 242 | 0.344 | 288.64 | 426.71 | 432.12 | 0.00 | 0.00 | 0.868 | isl:5:25 |
| [0.5,1) | 127 | 0.181 | 295.48 | 426.74 | 436.54 | 0.00 | 0.00 | 0.850 | isl:46:45 |
| [1,inf) | 52 | 0.074 | 294.58 | 307.21 | 308.24 | 0.00 | 0.31 | 0.769 | isl:44:43 |

### z_valid bins (epsilon_r >= floor only)

| z bin | n flips | frac | E2E p50 | E2E p95 | E2E p99 | backlog growth p50 (Mbit/s) | backlog@t p50 (Mbit) | relaxed@+2 s | top resource |
|---|---|---|---|---|---|---|---|---|---|
| [0,0.1) | 143 | 0.205 | 273.36 | 447.98 | 464.07 | 0.00 | 0.00 | 0.937 | isl:126:127 |
| [0.1,0.3) | 134 | 0.192 | 288.72 | 427.74 | 435.76 | 0.00 | 0.00 | 0.918 | isl:5:25 |
| [0.3,0.5) | 242 | 0.347 | 288.64 | 426.71 | 432.12 | 0.00 | 0.00 | 0.868 | isl:5:25 |
| [0.5,1) | 127 | 0.182 | 295.48 | 426.74 | 436.54 | 0.00 | 0.00 | 0.850 | isl:46:45 |
| [1,inf) | 52 | 0.074 | 294.58 | 307.21 | 308.24 | 0.00 | 0.31 | 0.769 | isl:44:43 |

Non-flip contrast row (no delta, hence no z): n=4136, E2E p50 288.67 ms, backlog growth p50 0.00 Mbit/s, relaxed@+2 s 0.934.
5 flips have no pair of endpoint links and are excluded from the z bins and from delta.

Burst window [8.0, 8.5]: 27 flips, z>=1 0, z>=0.5 0; 0.5 s-bin offered util max 0.300

## Persistence (tau_error on decision timestamps)

- (sat,dst) same-direction flip runs: 101 (>=2 flips: 64, >=3: 44); length p50 2, p90 14, max 110; duration p50 314.53 ms, p90 5811.44 ms, max 9427.22 ms (runs with >=2 flips; single-flip runs have zero duration)
- per-sat lens (**superseded** Round-1 lens, cross-check only): max length 110, max duration 8857.43 ms
- per-link persistent-excess runs (|delta_r| > 5.00 Mbps): 13 total, 13 with >=2 decisions, 5 lasting >=1 s, 5 censored
- **tau_error**: p50 0.74 s, p90 2.88 s, max 6.92 s; decisions per run p50 24

| link | t0 (s) | t1 (s) | tau_error (s) | decisions | |delta| mean (Mbps) | censored |
|---|---|---|---|---|---|---|
| isl:5:25 | 7.07 | 13.99 | 6.922 | 189 | 27.30 | True |
| isl:6:26 | 4.09 | 6.97 | 2.880 | 62 | 21.53 | True |
| isl:46:45 | 4.13 | 7.00 | 2.870 | 100 | 34.84 | True |
| isl:26:46 | 4.11 | 6.98 | 2.870 | 64 | 22.30 | True |
| isl:4:24 | 7.19 | 9.31 | 2.121 | 59 | 27.82 | False |

|delta| threshold sensitivity (0.5 s bins, **durations quantized - not tau_error**):

| threshold | runs | censored | in corridor | >=1 s | binned dur p50 (s) | p90 | max |
|---|---|---|---|---|---|---|---|
| 1.00 Mbps | 159 | 27 | 17 | 36 | 0.50 | 1.50 | 3.00 |
| 5.00 Mbps | 25 | 10 | 6 | 6 | 0.50 | 2.50 | 2.50 |
| 10.00 Mbps | 17 | 10 | 5 | 3 | 0.50 | 2.00 | 2.00 |

## Backlog relaxation (corridor links)

- backlog = queued + in-service remaining; service slot p50 10.00 ms
- excursions: 10 across 15 corridor links (peak floor 2.00 Mbit = 2 service slots)
- tau_50: p50 13.48 ms, p90 23.67 ms
- tau_band (back to preceding p10-p90): p50 18.11 ms, p90 27.42 ms

### Error segments vs relaxation / vs bias size

Split by tau_error / tau_relax - **degenerate in this cell** (no segment has ratio < 1):

| group | segments | tau_error p50 (s) | tau_relax p50 (s) | ratio p50 | \|delta\| p50 (Mbps) | backlog slope p50 (Mbit/s) | backlog p50 (Mbit) | E2E mean (ms) | E2E vs link baseline (ms) | packets |
|---|---|---|---|---|---|---|---|---|---|---|
| ratio >=1 | 1 | 6.922 | 0.0210 | 329.76 | 27.30 | -0.00 | 0.00 | 316.07 | 7.87 | 199 |
| ratio <1 | 0 | n/a | n/a | n/a | n/a | n/a | n/a | n/a | n/a | 0 |

Split by |delta| (median 27.30 Mbps):

| group | segments | tau_error p50 (s) | tau_relax p50 (s) | ratio p50 | \|delta\| p50 (Mbps) | backlog slope p50 (Mbit/s) | backlog p50 (Mbit) | E2E mean (ms) | E2E vs link baseline (ms) | packets |
|---|---|---|---|---|---|---|---|---|---|---|
| |delta| high | 1 | 6.922 | 0.0210 | 329.76 | 27.30 | -0.00 | 0.00 | 316.07 | 7.87 | 199 |
| |delta| low | 0 | n/a | n/a | n/a | n/a | n/a | n/a | n/a | n/a | 0 |

