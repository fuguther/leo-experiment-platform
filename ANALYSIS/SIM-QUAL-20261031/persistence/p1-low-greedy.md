# Persistence / directional-bias report - `p1-low-greedy`

**Caliber:** stream key = per-(sat,dst), with dst and bits joined from `trace.jsonl.gz` by pid (the decision projection drops dst); `tau_relax` is measured on the backlog **including in-service remaining bits**; `z` is reported both raw and restricted to `epsilon_r >= 1.00 Mbps`; `delta` is in bit/s.

- policy: `queue_greedy` (eta=0.0, alpha=0.25)
- flip rate (eval 4-14 s, frozen rule): **0.2774** (1371/4942); whole-run 0.2764 (1382/5000)
- queue-aware policy: 1371 flips in the eval window, 1382 over the whole run

## Directional bias (delta_r, bit/s)

- sum_r delta_r = **0.00 Mbps** (0 by construction; every flip is one + move and one - move)
- one-way moved load: **107.90 Mbps** (= sum|delta_r|/2, the number to quote); sum|delta_r| = 215.80 Mbps counts each moved bit twice
- positive excess total 107.90 Mbps, negative deficit total -107.90 Mbps, top-5 excess 87.20 Mbps
- resources touched 71 (|delta|>1 Mbps: 25); unpaired flips dropped 8
- caveat: delta is a per-decision attribution of bits moved between the link that should have been used and the one actually used; it is NOT end-to-end rerouted net traffic (later hops can correct it).

| side | link | delta (Mbps) | z |
|---|---|---|---|
| excess | isl:6:26 | +23.40 | 0.32 |
| excess | isl:26:46 | +21.80 | 0.29 |
| excess | isl:46:45 | +20.40 | 0.27 |
| excess | isl:44:43 | +17.90 | 0.23 |
| excess | isl:24:44 | +3.70 | 0.04 |
| deficit | isl:125:5 | -21.30 | - |
| deficit | isl:25:45 | -21.30 | - |
| deficit | isl:5:25 | -20.40 | - |
| deficit | isl:23:43 | -11.80 | - |
| deficit | isl:63:62 | -6.10 | - |

Longest same-sign delta run: +6.00 s / -6.00 s (0.5 s bins).

## Epsilon on corridor links

`rho=0.1` -> 18 links; saturated 0.5 s link-bins 0/1681.

| rho | n links | eps p50 (Mbps) | eps p90 | eps min | max util |
|---|---|---|---|---|---|
| rho=0.05 | 9 | 76.00 | 76.72 | 70.00 | 0.300 |
| rho=0.1 | 18 | 76.83 | 81.10 | 70.00 | 0.300 |
| rho=0.2 | 37 | 88.58 | 95.70 | 70.00 | 0.300 |

Sub-bin check at 0.1 s on the same corridor (0.5 s attribution bins hide saturation):

| window | link-subbins | saturated | frac | served util max | eps min (Mbps) | offered > mu | offered util max |
|---|---|---|---|---|---|---|---|
| eval | 1800 | 0 | 0.0000 | 0.7893 | 21.071 | 0 | 0.800 |
| burst | 90 | 0 | 0.0000 | 0.4000 | 60.000 | 0 | 0.400 |

## z = |delta| / epsilon (flip decisions, eval window)

`z` is a normalised bias magnitude, **not** an overload indicator: `epsilon` is residual capacity after the bits that actually flowed (misrouted ones included), while `delta` is a counterfactual bookkeeping swing.

- raw: p50 0.361, p90 0.777, share>=0.5 0.236, share>=1 0.019 (n=1371)
- valid (epsilon_r >= 1.00 Mbps): p50 0.361, p90 0.777, share>=0.5 0.237, share>=1 0.019 (n=1363; excluded: 0 below the epsilon floor, 8 with no endpoint resource)

### Raw z bins (all flips)

| z bin | n flips | frac | E2E p50 | E2E p95 | E2E p99 | backlog growth p50 (Mbit/s) | backlog@t p50 (Mbit) | relaxed@+2 s | top resource |
|---|---|---|---|---|---|---|---|---|---|
| [0,0.1) | 260 | 0.190 | 288.48 | 427.70 | 481.54 | 0.00 | 0.00 | 0.942 | isl:4:3 |
| [0.1,0.3) | 299 | 0.218 | 291.81 | 427.73 | 435.57 | 0.00 | 0.00 | 0.883 | isl:46:45 |
| [0.3,0.5) | 481 | 0.351 | 289.08 | 427.83 | 434.13 | 0.00 | 0.00 | 0.884 | isl:44:43 |
| [0.5,1) | 297 | 0.217 | 289.59 | 427.27 | 427.30 | 0.00 | 0.00 | 0.848 | isl:6:26 |
| [1,inf) | 26 | 0.019 | 289.34 | 303.25 | 308.81 | 0.00 | 0.00 | 0.769 | isl:44:43 |

### z_valid bins (epsilon_r >= floor only)

| z bin | n flips | frac | E2E p50 | E2E p95 | E2E p99 | backlog growth p50 (Mbit/s) | backlog@t p50 (Mbit) | relaxed@+2 s | top resource |
|---|---|---|---|---|---|---|---|---|---|
| [0,0.1) | 260 | 0.191 | 288.48 | 427.70 | 481.54 | 0.00 | 0.00 | 0.942 | isl:4:3 |
| [0.1,0.3) | 299 | 0.219 | 291.81 | 427.73 | 435.57 | 0.00 | 0.00 | 0.883 | isl:46:45 |
| [0.3,0.5) | 481 | 0.353 | 289.08 | 427.83 | 434.13 | 0.00 | 0.00 | 0.884 | isl:44:43 |
| [0.5,1) | 297 | 0.218 | 289.59 | 427.27 | 427.30 | 0.00 | 0.00 | 0.848 | isl:6:26 |
| [1,inf) | 26 | 0.019 | 289.34 | 303.25 | 308.81 | 0.00 | 0.00 | 0.769 | isl:44:43 |

Non-flip contrast row (no delta, hence no z): n=3571, E2E p50 288.98 ms, backlog growth p50 0.00 Mbit/s, relaxed@+2 s 0.949.
8 flips have no pair of endpoint links and are excluded from the z bins and from delta.

Burst window [8.0, 8.5]: 55 flips, z>=1 0, z>=0.5 0; 0.5 s-bin offered util max 0.240

## Persistence (tau_error on decision timestamps)

- (sat,dst) same-direction flip runs: 153 (>=2 flips: 94, >=3: 72); length p50 2, p90 24, max 139; duration p50 942.55 ms, p90 7224.23 ms, max 9656.25 ms (runs with >=2 flips; single-flip runs have zero duration)
- per-sat lens (**superseded** Round-1 lens, cross-check only): max length 139, max duration 9375.01 ms
- per-link persistent-excess runs (|delta_r| > 5.00 Mbps): 28 total, 28 with >=2 decisions, 11 lasting >=1 s, 9 censored
- **tau_error**: p50 0.96 s, p90 2.92 s, max 5.90 s; decisions per run p50 25

| link | t0 (s) | t1 (s) | tau_error (s) | decisions | |delta| mean (Mbps) | censored |
|---|---|---|---|---|---|---|
| isl:6:26 | 4.09 | 10.00 | 5.901 | 178 | 30.16 | True |
| isl:44:43 | 4.26 | 7.98 | 3.723 | 124 | 33.30 | True |
| isl:6:26 | 11.04 | 13.97 | 2.934 | 81 | 27.61 | True |
| isl:26:46 | 7.07 | 9.99 | 2.923 | 76 | 26.00 | False |
| isl:46:45 | 11.08 | 13.98 | 2.901 | 78 | 26.88 | True |
| isl:26:46 | 11.06 | 13.96 | 2.901 | 78 | 26.88 | True |
| isl:46:45 | 7.01 | 9.86 | 2.855 | 79 | 27.67 | False |
| isl:44:43 | 9.13 | 11.98 | 2.846 | 81 | 28.46 | False |

|delta| threshold sensitivity (0.5 s bins, **durations quantized - not tau_error**):

| threshold | runs | censored | in corridor | >=1 s | binned dur p50 (s) | p90 | max |
|---|---|---|---|---|---|---|---|
| 1.00 Mbps | 263 | 46 | 36 | 75 | 0.50 | 1.50 | 3.50 |
| 5.00 Mbps | 57 | 18 | 26 | 24 | 1.00 | 3.00 | 3.50 |
| 10.00 Mbps | 42 | 18 | 25 | 19 | 1.00 | 3.00 | 3.00 |

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

