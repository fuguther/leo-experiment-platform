# Response dynamics - `m2-c2-ewma-s9`

- policy `queue_greedy_ewma` (eta=0.0, alpha=0.25); window [4.0, 14.0]
- decisions 24487 over 195 (sat,dst) streams; flips 6492 (0.2651)
- switch rate **0.2073** (5036/24292 adjacent pairs)
- action runs: 5231 runs, length p50 1.0 / p90 7.0 / max 622; duration p50 0.000 ms, max 9908.133 ms
- action entropy (**realised choices only**; policy softmin is absent): weighted mean 0.731 bit, p50 0.362 bit, modal-direction share p50 0.931

## Flip consequences (dJ = J_truth(chosen) - J_truth(fresh), ms)

- dJ p50 6.517 / p90 11.887 / p99 17.543 ms; exactly-zero share 0.000
- partition at tol=0.5 ms: fresh_better_other=328, persistent_bias=5297, proxy_gap_tiny=265, quick_remerge=602
- **P(consequential|flip) = 0.9592**, **P(harmful|flip) = 0.9592** (proxy definitions, not causal)
- proxy regret over flips: mean 6.673 ms, p90 11.887 ms

| tol (ms) | tiny | fresh better | stale better | P(consequential) | P(harmful) |
|---|---|---|---|---|---|
| 0.1 | 146 | 6346 | 0 | 0.9775 | 0.9775 |
| 0.5 | 265 | 6227 | 0 | 0.9592 | 0.9592 |
| 1 | 395 | 6097 | 0 | 0.9392 | 0.9392 |
| 5 | 1929 | 4563 | 0 | 0.7029 | 0.7029 |
