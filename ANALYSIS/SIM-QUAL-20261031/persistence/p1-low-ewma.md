# Persistence / directional-bias report - `p1-low-ewma`

**Caliber:** stream key = per-(sat,dst), with dst and bits joined from `trace.jsonl.gz` by pid (the decision projection drops dst); `tau_relax` is measured on the backlog **including in-service remaining bits**; `z` is reported both raw and restricted to `epsilon_r >= 1.00 Mbps`; `delta` is in bit/s.

- policy: `queue_greedy_ewma` (eta=0.0, alpha=0.25)
- flip rate (eval 4-14 s, frozen rule): **0.2816** (1385/4919); whole-run 0.2807 (1396/4973)
- queue-aware policy: 1385 flips in the eval window, 1396 over the whole run

## Directional bias (delta_r, bit/s)

- sum_r delta_r = **0.00 Mbps** (0 by construction; every flip is one + move and one - move)
- one-way moved load: **126.70 Mbps** (= sum|delta_r|/2, the number to quote); sum|delta_r| = 253.40 Mbps counts each moved bit twice
- positive excess total 126.70 Mbps, negative deficit total -126.70 Mbps, top-5 excess 105.10 Mbps
- resources touched 67 (|delta|>1 Mbps: 24); unpaired flips dropped 8
- caveat: delta is a per-decision attribution of bits moved between the link that should have been used and the one actually used; it is NOT end-to-end rerouted net traffic (later hops can correct it).

| side | link | delta (Mbps) | z |
|---|---|---|---|
| excess | isl:6:26 | +28.40 | 0.40 |
| excess | isl:44:43 | +27.00 | 0.37 |
| excess | isl:26:46 | +23.60 | 0.32 |
| excess | isl:46:45 | +21.70 | 0.29 |
| excess | isl:24:44 | +4.40 | 0.05 |
| deficit | isl:125:5 | -25.80 | - |
| deficit | isl:5:25 | -24.50 | - |
| deficit | isl:25:45 | -22.60 | - |
| deficit | isl:23:43 | -17.40 | - |
| deficit | isl:63:62 | -9.60 | - |

Longest same-sign delta run: +10.00 s / -10.00 s (0.5 s bins).

## Epsilon on corridor links

`rho=0.1` -> 18 links; saturated 0.5 s link-bins 0/1624.

| rho | n links | eps p50 (Mbps) | eps p90 | eps min | max util |
|---|---|---|---|---|---|
| rho=0.05 | 9 | 72.15 | 72.43 | 70.00 | 0.300 |
| rho=0.1 | 18 | 73.90 | 81.50 | 70.00 | 0.300 |
| rho=0.2 | 36 | 86.48 | 96.80 | 70.00 | 0.300 |

Sub-bin check at 0.1 s on the same corridor (0.5 s attribution bins hide saturation):

| window | link-subbins | saturated | frac | served util max | eps min (Mbps) | offered > mu | offered util max |
|---|---|---|---|---|---|---|---|
| eval | 1800 | 0 | 0.0000 | 0.7630 | 23.697 | 0 | 0.800 |
| burst | 90 | 0 | 0.0000 | 0.4000 | 60.000 | 0 | 0.400 |

## z = |delta| / epsilon (flip decisions, eval window)

`z` is a normalised bias magnitude, **not** an overload indicator: `epsilon` is residual capacity after the bits that actually flowed (misrouted ones included), while `delta` is a counterfactual bookkeeping swing.

- raw: p50 0.367, p90 0.777, share>=0.5 0.248, share>=1 0.019 (n=1385)
- valid (epsilon_r >= 1.00 Mbps): p50 0.367, p90 0.777, share>=0.5 0.250, share>=1 0.019 (n=1377; excluded: 0 below the epsilon floor, 8 with no endpoint resource)

### Raw z bins (all flips)

| z bin | n flips | frac | E2E p50 | E2E p95 | E2E p99 | backlog growth p50 (Mbit/s) | backlog@t p50 (Mbit) | relaxed@+2 s | top resource |
|---|---|---|---|---|---|---|---|---|---|
| [0,0.1) | 241 | 0.174 | 273.63 | 431.01 | 514.13 | 0.00 | 0.00 | 0.963 | isl:4:3 |
| [0.1,0.3) | 287 | 0.207 | 288.94 | 301.77 | 427.61 | 0.00 | 0.00 | 0.875 | isl:6:26 |
| [0.3,0.5) | 505 | 0.365 | 289.01 | 373.98 | 434.78 | 0.00 | 0.00 | 0.840 | isl:44:43 |
| [0.5,1) | 318 | 0.230 | 288.85 | 300.21 | 303.25 | 0.00 | 0.00 | 0.836 | isl:6:26 |
| [1,inf) | 26 | 0.019 | 289.34 | 303.25 | 308.81 | 0.00 | 0.00 | 0.731 | isl:44:43 |

### z_valid bins (epsilon_r >= floor only)

| z bin | n flips | frac | E2E p50 | E2E p95 | E2E p99 | backlog growth p50 (Mbit/s) | backlog@t p50 (Mbit) | relaxed@+2 s | top resource |
|---|---|---|---|---|---|---|---|---|---|
| [0,0.1) | 241 | 0.175 | 273.63 | 431.01 | 514.13 | 0.00 | 0.00 | 0.963 | isl:4:3 |
| [0.1,0.3) | 287 | 0.208 | 288.94 | 301.77 | 427.61 | 0.00 | 0.00 | 0.875 | isl:6:26 |
| [0.3,0.5) | 505 | 0.367 | 289.01 | 373.98 | 434.78 | 0.00 | 0.00 | 0.840 | isl:44:43 |
| [0.5,1) | 318 | 0.231 | 288.85 | 300.21 | 303.25 | 0.00 | 0.00 | 0.836 | isl:6:26 |
| [1,inf) | 26 | 0.019 | 289.34 | 303.25 | 308.81 | 0.00 | 0.00 | 0.731 | isl:44:43 |

Non-flip contrast row (no delta, hence no z): n=3534, E2E p50 288.93 ms, backlog growth p50 0.00 Mbit/s, relaxed@+2 s 0.951.
8 flips have no pair of endpoint links and are excluded from the z bins and from delta.

Burst window [8.0, 8.5]: 57 flips, z>=1 0, z>=0.5 0; 0.5 s-bin offered util max 0.280

## Persistence (tau_error on decision timestamps)

- (sat,dst) same-direction flip runs: 133 (>=2 flips: 81, >=3: 65); length p50 2, p90 32, max 139; duration p50 1751.30 ms, p90 7415.28 ms, max 9656.25 ms (runs with >=2 flips; single-flip runs have zero duration)
- per-sat lens (**superseded** Round-1 lens, cross-check only): max length 139, max duration 9375.01 ms
- per-link persistent-excess runs (|delta_r| > 5.00 Mbps): 18 total, 18 with >=2 decisions, 8 lasting >=1 s, 7 censored
- **tau_error**: p50 0.76 s, p90 5.95 s, max 9.88 s; decisions per run p50 8

| link | t0 (s) | t1 (s) | tau_error (s) | decisions | |delta| mean (Mbps) | censored |
|---|---|---|---|---|---|---|
| isl:6:26 | 4.09 | 13.97 | 9.880 | 284 | 28.74 | True |
| isl:44:43 | 4.26 | 13.98 | 9.721 | 279 | 28.70 | True |
| isl:46:45 | 8.03 | 13.98 | 5.950 | 155 | 26.05 | True |
| isl:26:46 | 8.01 | 13.96 | 5.950 | 150 | 25.21 | True |
| isl:46:45 | 4.13 | 7.00 | 2.870 | 92 | 32.05 | True |
| isl:26:46 | 4.11 | 6.98 | 2.870 | 86 | 29.96 | True |
| isl:25:24 | 12.02 | 14.00 | 1.980 | 23 | 11.62 | True |
| isl:24:44 | 7.12 | 8.43 | 1.300 | 31 | 23.84 | False |

|delta| threshold sensitivity (0.5 s bins, **durations quantized - not tau_error**):

| threshold | runs | censored | in corridor | >=1 s | binned dur p50 (s) | p90 | max |
|---|---|---|---|---|---|---|---|
| 1.00 Mbps | 236 | 43 | 24 | 62 | 0.50 | 1.50 | 4.00 |
| 5.00 Mbps | 38 | 15 | 13 | 9 | 0.50 | 1.50 | 2.50 |
| 10.00 Mbps | 25 | 16 | 14 | 5 | 1.00 | 1.00 | 1.00 |

## Backlog relaxation (corridor links)

- backlog = queued + in-service remaining; service slot p50 10.00 ms
- excursions: 6 across 18 corridor links (peak floor 2.00 Mbit = 2 service slots)
- tau_50: p50 10.71 ms, p90 10.77 ms
- tau_band (back to preceding p10-p90): p50 11.36 ms, p90 13.19 ms

### Error segments vs relaxation / vs bias size

Split by tau_error / tau_relax:

| group | segments | tau_error p50 (s) | tau_relax p50 (s) | ratio p50 | \|delta\| p50 (Mbps) | backlog slope p50 (Mbit/s) | backlog p50 (Mbit) | E2E mean (ms) | E2E vs link baseline (ms) | packets |
|---|---|---|---|---|---|---|---|---|---|---|
| ratio >=1 | 0 | n/a | n/a | n/a | n/a | n/a | n/a | n/a | n/a | 0 |
| ratio <1 | 0 | n/a | n/a | n/a | n/a | n/a | n/a | n/a | n/a | 0 |

Split by |delta| (median n/a Mbps):

| group | segments | tau_error p50 (s) | tau_relax p50 (s) | ratio p50 | \|delta\| p50 (Mbps) | backlog slope p50 (Mbit/s) | backlog p50 (Mbit) | E2E mean (ms) | E2E vs link baseline (ms) | packets |
|---|---|---|---|---|---|---|---|---|---|---|
| |delta| high | 0 | n/a | n/a | n/a | n/a | n/a | n/a | n/a | n/a | 0 |
| |delta| low | 0 | n/a | n/a | n/a | n/a | n/a | n/a | n/a | n/a | 0 |

