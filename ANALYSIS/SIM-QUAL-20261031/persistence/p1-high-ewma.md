# Persistence / directional-bias report - `p1-high-ewma`

**Caliber:** stream key = per-(sat,dst), with dst and bits joined from `trace.jsonl.gz` by pid (the decision projection drops dst); `tau_relax` is measured on the backlog **including in-service remaining bits**; `z` is reported both raw and restricted to `epsilon_r >= 1.00 Mbps`; `delta` is in bit/s.

- policy: `queue_greedy_ewma` (eta=0.0, alpha=0.25)
- flip rate (eval 4-14 s, frozen rule): **0.2850** (4078/14309); whole-run 0.2849 (4144/14544)
- queue-aware policy: 4078 flips in the eval window, 4144 over the whole run

## Directional bias (delta_r, bit/s)

- sum_r delta_r = **0.00 Mbps** (0 by construction; every flip is one + move and one - move)
- one-way moved load: **327.50 Mbps** (= sum|delta_r|/2, the number to quote); sum|delta_r| = 655.00 Mbps counts each moved bit twice
- positive excess total 327.50 Mbps, negative deficit total -327.50 Mbps, top-5 excess 210.90 Mbps
- resources touched 104 (|delta|>1 Mbps: 53); unpaired flips dropped 29
- caveat: delta is a per-decision attribution of bits moved between the link that should have been used and the one actually used; it is NOT end-to-end rerouted net traffic (later hops can correct it).

| side | link | delta (Mbps) | z |
|---|---|---|---|
| excess | isl:6:26 | +68.10 | 2.33 |
| excess | isl:44:43 | +64.10 | 2.12 |
| excess | isl:24:44 | +36.90 | 1.17 |
| excess | isl:26:46 | +22.80 | 0.32 |
| excess | isl:14:34 | +19.00 | 0.24 |
| deficit | isl:23:43 | -63.60 | - |
| deficit | isl:125:5 | -43.20 | - |
| deficit | isl:3:23 | -36.90 | - |
| deficit | isl:106:105 | -27.40 | - |
| deficit | isl:5:25 | -23.30 | - |

Longest same-sign delta run: +10.00 s / -10.00 s (0.5 s bins).

## Epsilon on corridor links

`rho=0.1` -> 20 links; saturated 0.5 s link-bins 0/2688.

| rho | n links | eps p50 (Mbps) | eps p90 | eps min | max util |
|---|---|---|---|---|---|
| rho=0.05 | 10 | 27.90 | 30.22 | 25.22 | 0.748 |
| rho=0.1 | 20 | 30.40 | 57.05 | 25.22 | 0.748 |
| rho=0.2 | 41 | 70.50 | 83.20 | 25.22 | 0.748 |

Sub-bin check at 0.1 s on the same corridor (0.5 s attribution bins hide saturation):

| window | link-subbins | saturated | frac | served util max | eps min (Mbps) | offered > mu | offered util max |
|---|---|---|---|---|---|---|---|
| eval | 2000 | 59 | 0.0295 | 1.0000 | 0.000 | 138 | 1.200 |
| burst | 100 | 8 | 0.0800 | 1.0000 | 0.000 | 18 | 1.100 |

## z = |delta| / epsilon (flip decisions, eval window)

`z` is a normalised bias magnitude, **not** an overload indicator: `epsilon` is residual capacity after the bits that actually flowed (misrouted ones included), while `delta` is a counterfactual bookkeeping swing.

- raw: p50 1.786, p90 3.163, share>=0.5 0.710, share>=1 0.658 (n=4078)
- valid (epsilon_r >= 1.00 Mbps): p50 1.786, p90 3.163, share>=0.5 0.715, share>=1 0.663 (n=4049; excluded: 0 below the epsilon floor, 29 with no endpoint resource)

### Raw z bins (all flips)

| z bin | n flips | frac | E2E p50 | E2E p95 | E2E p99 | backlog growth p50 (Mbit/s) | backlog@t p50 (Mbit) | relaxed@+2 s | top resource |
|---|---|---|---|---|---|---|---|---|---|
| [0,0.1) | 548 | 0.134 | 290.51 | 514.06 | 535.33 | 0.00 | 0.00 | 0.938 | isl:126:127 |
| [0.1,0.3) | 499 | 0.122 | 357.29 | 462.90 | 515.84 | 0.00 | 0.00 | 0.840 | isl:14:34 |
| [0.3,0.5) | 107 | 0.026 | 358.83 | 451.99 | 504.03 | 0.00 | 0.00 | 0.935 | isl:14:34 |
| [0.5,1) | 211 | 0.052 | 358.70 | 503.88 | 535.33 | 0.00 | 0.36 | 0.905 | isl:14:34 |
| [1,inf) | 2684 | 0.658 | 295.89 | 322.23 | 477.48 | -0.24 | 0.66 | 0.751 | isl:6:26 |

### z_valid bins (epsilon_r >= floor only)

| z bin | n flips | frac | E2E p50 | E2E p95 | E2E p99 | backlog growth p50 (Mbit/s) | backlog@t p50 (Mbit) | relaxed@+2 s | top resource |
|---|---|---|---|---|---|---|---|---|---|
| [0,0.1) | 548 | 0.135 | 290.51 | 514.06 | 535.33 | 0.00 | 0.00 | 0.938 | isl:126:127 |
| [0.1,0.3) | 499 | 0.123 | 357.29 | 462.90 | 515.84 | 0.00 | 0.00 | 0.840 | isl:14:34 |
| [0.3,0.5) | 107 | 0.026 | 358.83 | 451.99 | 504.03 | 0.00 | 0.00 | 0.935 | isl:14:34 |
| [0.5,1) | 211 | 0.052 | 358.70 | 503.88 | 535.33 | 0.00 | 0.36 | 0.905 | isl:14:34 |
| [1,inf) | 2684 | 0.663 | 295.89 | 322.23 | 477.48 | -0.24 | 0.66 | 0.751 | isl:6:26 |

Non-flip contrast row (no delta, hence no z): n=10231, E2E p50 297.12 ms, backlog growth p50 0.00 Mbit/s, relaxed@+2 s 0.897.
29 flips have no pair of endpoint links and are excluded from the z bins and from delta.

Burst window [8.0, 8.5]: 274 flips, z>=1 150, z>=0.5 229; 0.5 s-bin offered util max 0.980

## Persistence (tau_error on decision timestamps)

- (sat,dst) same-direction flip runs: 445 (>=2 flips: 237, >=3: 188); length p50 2, p90 21, max 211; duration p50 261.83 ms, p90 3002.95 ms, max 9735.29 ms (runs with >=2 flips; single-flip runs have zero duration)
- per-sat lens (**superseded** Round-1 lens, cross-check only): max length 211, max duration 9735.29 ms
- per-link persistent-excess runs (|delta_r| > 5.00 Mbps): 51 total, 51 with >=2 decisions, 17 lasting >=1 s, 18 censored
- **tau_error**: p50 0.93 s, p90 9.73 s, max 9.91 s; decisions per run p50 10

| link | t0 (s) | t1 (s) | tau_error (s) | decisions | |delta| mean (Mbps) | censored |
|---|---|---|---|---|---|---|
| isl:6:26 | 4.07 | 13.98 | 9.914 | 694 | 70.00 | True |
| isl:44:43 | 4.22 | 13.98 | 9.766 | 666 | 68.19 | True |
| isl:14:34 | 4.20 | 13.94 | 9.735 | 190 | 19.52 | True |
| isl:34:54 | 4.22 | 13.96 | 9.735 | 159 | 16.33 | True |
| isl:54:55 | 4.24 | 13.97 | 9.735 | 160 | 16.44 | True |

|delta| threshold sensitivity (0.5 s bins, **durations quantized - not tau_error**):

| threshold | runs | censored | in corridor | >=1 s | binned dur p50 (s) | p90 | max |
|---|---|---|---|---|---|---|---|
| 1.00 Mbps | 310 | 82 | 44 | 108 | 0.50 | 2.00 | 8.50 |
| 5.00 Mbps | 146 | 45 | 27 | 38 | 0.50 | 1.00 | 5.00 |
| 10.00 Mbps | 86 | 30 | 19 | 37 | 1.00 | 1.50 | 6.00 |

## Backlog relaxation (corridor links)

- backlog = queued + in-service remaining; service slot p50 10.00 ms
- excursions: 35 across 20 corridor links (peak floor 2.00 Mbit = 2 service slots)
- tau_50: p50 12.67 ms, p90 31.88 ms
- tau_band (back to preceding p10-p90): p50 4.50 ms, p90 16.57 ms

### Error segments vs relaxation / vs bias size

Split by tau_error / tau_relax - **degenerate in this cell** (no segment has ratio < 1):

| group | segments | tau_error p50 (s) | tau_relax p50 (s) | ratio p50 | \|delta\| p50 (Mbps) | backlog slope p50 (Mbit/s) | backlog p50 (Mbit) | E2E mean (ms) | E2E vs link baseline (ms) | packets |
|---|---|---|---|---|---|---|---|---|---|---|
| ratio >=1 | 4 | 0.984 | 0.0223 | 44.03 | 74.20 | 0.74 | 0.73 | 311.33 | 2.87 | 456 |
| ratio <1 | 0 | n/a | n/a | n/a | n/a | n/a | n/a | n/a | n/a | 0 |

Split by |delta| (median 73.13 Mbps):

| group | segments | tau_error p50 (s) | tau_relax p50 (s) | ratio p50 | \|delta\| p50 (Mbps) | backlog slope p50 (Mbit/s) | backlog p50 (Mbit) | E2E mean (ms) | E2E vs link baseline (ms) | packets |
|---|---|---|---|---|---|---|---|---|---|---|
| |delta| high | 2 | 0.979 | 0.0223 | 43.80 | 74.20 | 0.74 | 0.00 | 319.83 | 9.23 | 154 |
| |delta| low | 2 | 0.258 | 0.0223 | 4.06 | 27.10 | -2.79 | 0.23 | 302.83 | -3.49 | 302 |

