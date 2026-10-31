# Response dynamics - `m2-c2-ewma-s8`

- policy `queue_greedy_ewma` (eta=0.0, alpha=0.25); window [4.0, 14.0]
- decisions 23644 over 194 (sat,dst) streams; flips 6246 (0.2642)
- switch rate **0.2171** (5092/23450 adjacent pairs)
- action runs: 5286 runs, length p50 1.0 / p90 7.0 / max 716; duration p50 0.000 ms, max 9903.532 ms
- action entropy (**realised choices only**; policy softmin is absent): weighted mean 0.695 bit, p50 0.371 bit, modal-direction share p50 0.936

## Flip consequences (dJ = J_truth(chosen) - J_truth(fresh), ms)

- dJ p50 6.158 / p90 11.543 / p99 17.444 ms; exactly-zero share 0.000
- partition at tol=0.5 ms: fresh_better_other=314, persistent_bias=5112, proxy_gap_tiny=208, quick_remerge=612
- **P(consequential|flip) = 0.9667**, **P(harmful|flip) = 0.9667** (proxy definitions, not causal)
- proxy regret over flips: mean 6.476 ms, p90 11.543 ms

| tol (ms) | tiny | fresh better | stale better | P(consequential) | P(harmful) |
|---|---|---|---|---|---|
| 0.1 | 111 | 6135 | 0 | 0.9822 | 0.9822 |
| 0.5 | 208 | 6038 | 0 | 0.9667 | 0.9667 |
| 1 | 342 | 5904 | 0 | 0.9452 | 0.9452 |
| 5 | 2137 | 4109 | 0 | 0.6579 | 0.6579 |
