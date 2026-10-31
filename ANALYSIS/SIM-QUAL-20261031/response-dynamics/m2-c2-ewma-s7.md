# Response dynamics - `m2-c2-ewma-s7`

- policy `queue_greedy_ewma` (eta=0.0, alpha=0.25); window [4.0, 14.0]
- decisions 24819 over 203 (sat,dst) streams; flips 6462 (0.2604)
- switch rate **0.1584** (3900/24616 adjacent pairs)
- action runs: 4103 runs, length p50 1.0 / p90 9.0 / max 704; duration p50 0.000 ms, max 9936.133 ms
- action entropy (**realised choices only**; policy softmin is absent): weighted mean 0.503 bit, p50 0.296 bit, modal-direction share p50 0.948

## Flip consequences (dJ = J_truth(chosen) - J_truth(fresh), ms)

- dJ p50 5.725 / p90 11.717 / p99 18.497 ms; exactly-zero share 0.000
- partition at tol=0.5 ms: fresh_better_other=271, persistent_bias=5263, proxy_gap_tiny=327, quick_remerge=601
- **P(consequential|flip) = 0.9494**, **P(harmful|flip) = 0.9494** (proxy definitions, not causal)
- proxy regret over flips: mean 6.393 ms, p90 11.717 ms

| tol (ms) | tiny | fresh better | stale better | P(consequential) | P(harmful) |
|---|---|---|---|---|---|
| 0.1 | 167 | 6295 | 0 | 0.9742 | 0.9742 |
| 0.5 | 327 | 6135 | 0 | 0.9494 | 0.9494 |
| 1 | 470 | 5992 | 0 | 0.9273 | 0.9273 |
| 5 | 2404 | 4058 | 0 | 0.6280 | 0.6280 |
