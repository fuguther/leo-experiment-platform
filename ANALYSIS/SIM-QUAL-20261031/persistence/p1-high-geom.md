# Persistence / directional-bias report - `p1-high-geom`

**Caliber:** stream key = per-(sat,dst), with dst and bits joined from `trace.jsonl.gz` by pid (the decision projection drops dst); `tau_relax` is measured on the backlog **including in-service remaining bits**; `z` is reported both raw and restricted to `epsilon_r >= 1.00 Mbps`; `delta` is in bit/s.

- policy: `geometry` (eta=0.0, alpha=0.25)
- flip rate (eval 4-14 s, frozen rule): **0.0000** (0/14477); whole-run 0.0000 (0/14725)
- geometry negative control: flip = 0 by construction (check_ok=True)

## Directional bias (delta_r, bit/s)

- sum_r delta_r = **0.00 Mbps** (0 by construction; every flip is one + move and one - move)
- one-way moved load: **0.00 Mbps** (= sum|delta_r|/2, the number to quote); sum|delta_r| = 0.00 Mbps counts each moved bit twice
- positive excess total 0.00 Mbps, negative deficit total 0.00 Mbps, top-5 excess 0.00 Mbps
- resources touched 0 (|delta|>1 Mbps: 0); unpaired flips dropped 0
- caveat: delta is a per-decision attribution of bits moved between the link that should have been used and the one actually used; it is NOT end-to-end rerouted net traffic (later hops can correct it).

| side | link | delta (Mbps) | z |
|---|---|---|---|

Longest same-sign delta run: +n/a s / -n/a s (0.5 s bins).

## Epsilon on corridor links

`rho=0.1` -> 14 links; saturated 0.5 s link-bins 16/1955.

| rho | n links | eps p50 (Mbps) | eps p90 | eps min | max util |
|---|---|---|---|---|---|
| rho=0.05 | 7 | 9.40 | 11.17 | 8.02 | 0.920 |
| rho=0.1 | 14 | 11.21 | 27.73 | 8.02 | 0.920 |
| rho=0.2 | 28 | 31.09 | 73.16 | 8.02 | 0.920 |

Sub-bin check at 0.1 s on the same corridor (0.5 s attribution bins hide saturation):

| window | link-subbins | saturated | frac | served util max | eps min (Mbps) | offered > mu | offered util max |
|---|---|---|---|---|---|---|---|
| eval | 1400 | 497 | 0.3550 | 1.0000 | 0.000 | 584 | 1.900 |
| burst | 70 | 22 | 0.3143 | 1.0000 | 0.000 | 26 | 1.700 |

## z = |delta| / epsilon (flip decisions, eval window)

`z` is a normalised bias magnitude, **not** an overload indicator: `epsilon` is residual capacity after the bits that actually flowed (misrouted ones included), while `delta` is a counterfactual bookkeeping swing.

- raw: p50 n/a, p90 n/a, share>=0.5 n/a, share>=1 n/a (n=0)
- valid (epsilon_r >= 1.00 Mbps): p50 n/a, p90 n/a, share>=0.5 n/a, share>=1 n/a (n=0; excluded: 0 below the epsilon floor, 0 with no endpoint resource)

### Raw z bins (all flips)

| z bin | n flips | frac | E2E p50 | E2E p95 | E2E p99 | backlog growth p50 (Mbit/s) | backlog@t p50 (Mbit) | relaxed@+2 s | top resource |
|---|---|---|---|---|---|---|---|---|---|
| [0,0.1) | 0 | n/a | n/a | n/a | n/a | n/a | n/a | n/a | n/a |
| [0.1,0.3) | 0 | n/a | n/a | n/a | n/a | n/a | n/a | n/a | n/a |
| [0.3,0.5) | 0 | n/a | n/a | n/a | n/a | n/a | n/a | n/a | n/a |
| [0.5,1) | 0 | n/a | n/a | n/a | n/a | n/a | n/a | n/a | n/a |
| [1,inf) | 0 | n/a | n/a | n/a | n/a | n/a | n/a | n/a | n/a |

### z_valid bins (epsilon_r >= floor only)

| z bin | n flips | frac | E2E p50 | E2E p95 | E2E p99 | backlog growth p50 (Mbit/s) | backlog@t p50 (Mbit) | relaxed@+2 s | top resource |
|---|---|---|---|---|---|---|---|---|---|
| [0,0.1) | 0 | n/a | n/a | n/a | n/a | n/a | n/a | n/a | n/a |
| [0.1,0.3) | 0 | n/a | n/a | n/a | n/a | n/a | n/a | n/a | n/a |
| [0.3,0.5) | 0 | n/a | n/a | n/a | n/a | n/a | n/a | n/a | n/a |
| [0.5,1) | 0 | n/a | n/a | n/a | n/a | n/a | n/a | n/a | n/a |
| [1,inf) | 0 | n/a | n/a | n/a | n/a | n/a | n/a | n/a | n/a |

Non-flip contrast row (no delta, hence no z): n=14477, E2E p50 345.18 ms, backlog growth p50 0.00 Mbit/s, relaxed@+2 s 0.824.

Burst window [8.0, 8.5]: 0 flips, z>=1 0, z>=0.5 0; 0.5 s-bin offered util max 1.500

## Persistence (tau_error on decision timestamps)

- (sat,dst) same-direction flip runs: 0 (>=2 flips: 0, >=3: 0); length p50 n/a, p90 n/a, max n/a; duration p50 n/a ms, p90 n/a ms, max n/a ms (runs with >=2 flips; single-flip runs have zero duration)
- per-sat lens (**superseded** Round-1 lens, cross-check only): max length n/a, max duration n/a ms
- per-link persistent-excess runs (|delta_r| > 5.00 Mbps): 0 total, 0 with >=2 decisions, 0 lasting >=1 s, 0 censored
- **tau_error**: p50 n/a s, p90 n/a s, max n/a s; decisions per run p50 n/a

|delta| threshold sensitivity (0.5 s bins, **durations quantized - not tau_error**):

| threshold | runs | censored | in corridor | >=1 s | binned dur p50 (s) | p90 | max |
|---|---|---|---|---|---|---|---|
| 1.00 Mbps | 0 | 0 | 0 | 0 | n/a | n/a | n/a |
| 5.00 Mbps | 0 | 0 | 0 | 0 | n/a | n/a | n/a |
| 10.00 Mbps | 0 | 0 | 0 | 0 | n/a | n/a | n/a |

## Backlog relaxation (corridor links)

- backlog = queued + in-service remaining; service slot p50 10.00 ms
- excursions: 52 across 14 corridor links (peak floor 2.00 Mbit = 2 service slots)
- tau_50: p50 151.16 ms, p90 2120.59 ms
- tau_band (back to preceding p10-p90): p50 14.39 ms, p90 1140.08 ms

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

