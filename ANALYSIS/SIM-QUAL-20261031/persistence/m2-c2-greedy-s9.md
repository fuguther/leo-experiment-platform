# Persistence / directional-bias report - `m2-c2-greedy-s9`

**Caliber:** stream key = per-(sat,dst), with dst and bits joined from `trace.jsonl.gz` by pid (the decision projection drops dst); `tau_relax` is measured on the backlog **including in-service remaining bits**; `z` is reported both raw and restricted to `epsilon_r >= 1.00 Mbps`; `delta` is in bit/s.

- policy: `queue_greedy` (eta=0.0, alpha=0.25)
- flip rate (eval 4-14 s, frozen rule): **0.2589** (6291/24301); whole-run 0.2594 (6412/24721)
- queue-aware policy: 6291 flips in the eval window, 6412 over the whole run

## Directional bias (delta_r, bit/s)

- sum_r delta_r = **0.00 Mbps** (0 by construction; every flip is one + move and one - move)
- one-way moved load: **287.50 Mbps** (= sum|delta_r|/2, the number to quote); sum|delta_r| = 575.00 Mbps counts each moved bit twice
- positive excess total 287.50 Mbps, negative deficit total -287.50 Mbps, top-5 excess 143.60 Mbps
- resources touched 139 (|delta|>1 Mbps: 75); unpaired flips dropped 41
- caveat: delta is a per-decision attribution of bits moved between the link that should have been used and the one actually used; it is NOT end-to-end rerouted net traffic (later hops can correct it).

| side | link | delta (Mbps) | z |
|---|---|---|---|
| excess | isl:14:34 | +36.90 | 0.65 |
| excess | isl:46:45 | +34.10 | 0.76 |
| excess | isl:54:55 | +28.20 | 0.43 |
| excess | isl:26:46 | +22.30 | 0.42 |
| excess | isl:34:54 | +22.10 | 0.36 |
| deficit | isl:25:45 | -40.30 | - |
| deficit | isl:5:25 | -30.70 | - |
| deficit | isl:35:55 | -27.60 | - |
| deficit | isl:125:5 | -26.30 | - |
| deficit | isl:135:15 | -20.90 | - |

Longest same-sign delta run: +7.50 s / -5.00 s (0.5 s bins).

## Epsilon on corridor links

`rho=0.1` -> 29 links; saturated 0.5 s link-bins 4/3738.

| rho | n links | eps p50 (Mbps) | eps p90 | eps min | max util |
|---|---|---|---|---|---|
| rho=0.05 | 15 | 29.60 | 41.10 | 5.99 | 0.940 |
| rho=0.1 | 29 | 43.70 | 45.23 | 5.99 | 0.940 |
| rho=0.2 | 58 | 50.42 | 65.40 | 5.99 | 0.940 |

Sub-bin check at 0.1 s on the same corridor (0.5 s attribution bins hide saturation):

| window | link-subbins | saturated | frac | served util max | eps min (Mbps) | offered > mu | offered util max |
|---|---|---|---|---|---|---|---|
| eval | 2900 | 329 | 0.1134 | 1.0000 | 0.000 | 534 | 1.300 |
| burst | 145 | 19 | 0.1310 | 1.0000 | 0.000 | 35 | 1.300 |

## z = |delta| / epsilon (flip decisions, eval window)

`z` is a normalised bias magnitude, **not** an overload indicator: `epsilon` is residual capacity after the bits that actually flowed (misrouted ones included), while `delta` is a counterfactual bookkeeping swing.

- raw: p50 1.289, p90 12.497, share>=0.5 0.734, share>=1 0.558 (n=6291)
- valid (epsilon_r >= 1.00 Mbps): p50 1.289, p90 12.497, share>=0.5 0.736, share>=1 0.558 (n=6191; excluded: 59 below the epsilon floor, 41 with no endpoint resource)

### Raw z bins (all flips)

| z bin | n flips | frac | E2E p50 | E2E p95 | E2E p99 | backlog growth p50 (Mbit/s) | backlog@t p50 (Mbit) | relaxed@+2 s | top resource |
|---|---|---|---|---|---|---|---|---|---|
| [0,0.1) | 797 | 0.127 | 309.59 | 667.54 | 937.60 | 0.00 | 0.00 | 0.893 | isl:25:24 |
| [0.1,0.3) | 541 | 0.086 | 457.16 | 815.27 | 850.91 | 0.00 | 0.00 | 0.828 | isl:37:57 |
| [0.3,0.5) | 296 | 0.047 | 481.05 | 808.27 | 844.38 | 0.00 | 0.00 | 0.878 | isl:23:43 |
| [0.5,1) | 1104 | 0.175 | 458.20 | 687.95 | 840.16 | 0.00 | 0.00 | 0.861 | isl:106:105 |
| [1,inf) | 3512 | 0.558 | 442.35 | 657.29 | 808.27 | -0.80 | 0.70 | 0.767 | isl:46:45 |

### z_valid bins (epsilon_r >= floor only)

| z bin | n flips | frac | E2E p50 | E2E p95 | E2E p99 | backlog growth p50 (Mbit/s) | backlog@t p50 (Mbit) | relaxed@+2 s | top resource |
|---|---|---|---|---|---|---|---|---|---|
| [0,0.1) | 797 | 0.129 | 309.59 | 667.54 | 937.60 | 0.00 | 0.00 | 0.893 | isl:25:24 |
| [0.1,0.3) | 541 | 0.087 | 457.16 | 815.27 | 850.91 | 0.00 | 0.00 | 0.828 | isl:37:57 |
| [0.3,0.5) | 296 | 0.048 | 481.05 | 808.27 | 844.38 | 0.00 | 0.00 | 0.878 | isl:23:43 |
| [0.5,1) | 1104 | 0.178 | 458.20 | 687.95 | 840.16 | 0.00 | 0.00 | 0.861 | isl:106:105 |
| [1,inf) | 3453 | 0.558 | 443.47 | 658.11 | 808.27 | -0.80 | 0.70 | 0.771 | isl:46:45 |

Non-flip contrast row (no delta, hence no z): n=18010, E2E p50 443.27 ms, backlog growth p50 0.00 Mbit/s, relaxed@+2 s 0.873.
41 flips have no pair of endpoint links and are excluded from the z bins and from delta.

Burst window [8.0, 8.5]: 412 flips, z>=1 240, z>=0.5 287; 0.5 s-bin offered util max 0.960

## Persistence (tau_error on decision timestamps)

- (sat,dst) same-direction flip runs: 1575 (>=2 flips: 645, >=3: 413); length p50 1, p90 6, max 264; duration p50 40.00 ms, p90 941.51 ms, max 9259.99 ms (runs with >=2 flips; single-flip runs have zero duration)
- per-sat lens (**superseded** Round-1 lens, cross-check only): max length 264, max duration 9259.99 ms
- per-link persistent-excess runs (|delta_r| > 5.00 Mbps): 145 total, 145 with >=2 decisions, 30 lasting >=1 s, 31 censored
- **tau_error**: p50 0.90 s, p90 2.23 s, max 7.44 s; decisions per run p50 19

| link | t0 (s) | t1 (s) | tau_error (s) | decisions | |delta| mean (Mbps) | censored |
|---|---|---|---|---|---|---|
| isl:4:3 | 4.50 | 11.94 | 7.439 | 71 | 9.54 | False |
| isl:126:127 | 8.11 | 13.83 | 5.718 | 43 | 7.52 | True |
| isl:14:34 | 8.10 | 12.98 | 4.881 | 278 | 56.96 | False |
| isl:34:54 | 8.12 | 12.99 | 4.871 | 195 | 40.03 | False |
| isl:54:55 | 8.14 | 13.00 | 4.861 | 250 | 51.43 | False |

|delta| threshold sensitivity (0.5 s bins, **durations quantized - not tau_error**):

| threshold | runs | censored | in corridor | >=1 s | binned dur p50 (s) | p90 | max |
|---|---|---|---|---|---|---|---|
| 1.00 Mbps | 568 | 128 | 74 | 256 | 1.00 | 2.00 | 6.50 |
| 5.00 Mbps | 351 | 64 | 60 | 144 | 1.00 | 1.50 | 7.50 |
| 10.00 Mbps | 207 | 40 | 57 | 114 | 1.00 | 1.50 | 5.00 |

## Backlog relaxation (corridor links)

- backlog = queued + in-service remaining; service slot p50 10.00 ms
- excursions: 75 across 29 corridor links (peak floor 2.00 Mbit = 2 service slots)
- tau_50: p50 20.55 ms, p90 92.08 ms
- tau_band (back to preceding p10-p90): p50 7.40 ms, p90 50.55 ms

### Error segments vs relaxation / vs bias size

Split by tau_error / tau_relax - **degenerate in this cell** (no segment has ratio < 1):

| group | segments | tau_error p50 (s) | tau_relax p50 (s) | ratio p50 | \|delta\| p50 (Mbps) | backlog slope p50 (Mbit/s) | backlog p50 (Mbit) | E2E mean (ms) | E2E vs link baseline (ms) | packets |
|---|---|---|---|---|---|---|---|---|---|---|
| ratio >=1 | 20 | 0.969 | 0.0169 | 56.92 | 83.32 | 0.52 | 0.50 | 407.47 | -30.46 | 1824 |
| ratio <1 | 0 | n/a | n/a | n/a | n/a | n/a | n/a | n/a | n/a | 0 |

Split by |delta| (median 74.63 Mbps):

| group | segments | tau_error p50 (s) | tau_relax p50 (s) | ratio p50 | \|delta\| p50 (Mbps) | backlog slope p50 (Mbit/s) | backlog p50 (Mbit) | E2E mean (ms) | E2E vs link baseline (ms) | packets |
|---|---|---|---|---|---|---|---|---|---|---|
| |delta| high | 10 | 0.969 | 0.0169 | 47.89 | 89.22 | 0.58 | 0.51 | 389.33 | -29.73 | 903 |
| |delta| low | 10 | 0.966 | 0.0104 | 57.24 | 20.55 | 0.08 | 0.29 | 425.60 | -31.20 | 921 |

