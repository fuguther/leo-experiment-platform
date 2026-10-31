# Response dynamics - `m2-b-greedy-s7`

- policy `queue_greedy` (eta=0.0, alpha=0.25); window [4.0, 14.0]
- decisions 19673 over 206 (sat,dst) streams; flips 5284 (0.2686)
- switch rate **0.2407** (4685/19467 adjacent pairs)
- action runs: 4891 runs, length p50 1.0 / p90 7.0 / max 417; duration p50 0.000 ms, max 9777.667 ms
- action entropy (**realised choices only**; policy softmin is absent): weighted mean 0.823 bit, p50 0.462 bit, modal-direction share p50 0.896

## Flip consequences (dJ = J_truth(chosen) - J_truth(fresh), ms)

- dJ p50 6.155 / p90 11.578 / p99 18.125 ms; exactly-zero share 0.000
- partition at tol=0.5 ms: fresh_better_other=314, persistent_bias=4264, proxy_gap_tiny=96, quick_remerge=610
- **P(consequential|flip) = 0.9818**, **P(harmful|flip) = 0.9818** (proxy definitions, not causal)
- proxy regret over flips: mean 6.368 ms, p90 11.578 ms

| tol (ms) | tiny | fresh better | stale better | P(consequential) | P(harmful) |
|---|---|---|---|---|---|
| 0.1 | 38 | 5246 | 0 | 0.9928 | 0.9928 |
| 0.5 | 96 | 5188 | 0 | 0.9818 | 0.9818 |
| 1 | 216 | 5068 | 0 | 0.9591 | 0.9591 |
| 5 | 1932 | 3352 | 0 | 0.6344 | 0.6344 |
