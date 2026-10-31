# Response dynamics - `m2-c2-greedy-s9`

- policy `queue_greedy` (eta=0.0, alpha=0.25); window [4.0, 14.0]
- decisions 24301 over 223 (sat,dst) streams; flips 6250 (0.2572)
- switch rate **0.2734** (6582/24078 adjacent pairs)
- action runs: 6805 runs, length p50 1.0 / p90 5.0 / max 455; duration p50 0.000 ms, max 9885.152 ms
- action entropy (**realised choices only**; policy softmin is absent): weighted mean 0.810 bit, p50 0.575 bit, modal-direction share p50 0.867

## Flip consequences (dJ = J_truth(chosen) - J_truth(fresh), ms)

- dJ p50 6.300 / p90 12.772 / p99 18.989 ms; exactly-zero share 0.000
- partition at tol=0.5 ms: fresh_better_other=369, persistent_bias=4779, proxy_gap_tiny=149, quick_remerge=953
- **P(consequential|flip) = 0.9762**, **P(harmful|flip) = 0.9762** (proxy definitions, not causal)
- proxy regret over flips: mean 6.794 ms, p90 12.772 ms

| tol (ms) | tiny | fresh better | stale better | P(consequential) | P(harmful) |
|---|---|---|---|---|---|
| 0.1 | 66 | 6184 | 0 | 0.9894 | 0.9894 |
| 0.5 | 149 | 6101 | 0 | 0.9762 | 0.9762 |
| 1 | 284 | 5966 | 0 | 0.9546 | 0.9546 |
| 5 | 2195 | 4055 | 0 | 0.6488 | 0.6488 |
