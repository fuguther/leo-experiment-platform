# Response dynamics - `m2-b-ewma-s7`

- policy `queue_greedy_ewma` (eta=0.0, alpha=0.25); window [4.0, 14.0]
- decisions 19789 over 196 (sat,dst) streams; flips 5186 (0.2621)
- switch rate **0.1383** (2709/19593 adjacent pairs)
- action runs: 2905 runs, length p50 1.0 / p90 13.0 / max 842; duration p50 0.000 ms, max 9919.687 ms
- action entropy (**realised choices only**; policy softmin is absent): weighted mean 0.445 bit, p50 0.297 bit, modal-direction share p50 0.958

## Flip consequences (dJ = J_truth(chosen) - J_truth(fresh), ms)

- dJ p50 5.943 / p90 10.801 / p99 18.426 ms; exactly-zero share 0.000
- partition at tol=0.5 ms: fresh_better_other=240, persistent_bias=4243, proxy_gap_tiny=255, quick_remerge=448
- **P(consequential|flip) = 0.9508**, **P(harmful|flip) = 0.9508** (proxy definitions, not causal)
- proxy regret over flips: mean 6.118 ms, p90 10.801 ms

| tol (ms) | tiny | fresh better | stale better | P(consequential) | P(harmful) |
|---|---|---|---|---|---|
| 0.1 | 155 | 5031 | 0 | 0.9701 | 0.9701 |
| 0.5 | 255 | 4931 | 0 | 0.9508 | 0.9508 |
| 1 | 391 | 4795 | 0 | 0.9246 | 0.9246 |
| 5 | 1958 | 3228 | 0 | 0.6224 | 0.6224 |
