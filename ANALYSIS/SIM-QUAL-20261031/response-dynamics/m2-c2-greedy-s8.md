# Response dynamics - `m2-c2-greedy-s8`

- policy `queue_greedy` (eta=0.0, alpha=0.25); window [4.0, 14.0]
- decisions 24338 over 214 (sat,dst) streams; flips 6245 (0.2566)
- switch rate **0.2549** (6149/24124 adjacent pairs)
- action runs: 6363 runs, length p50 1.0 / p90 6.0 / max 623; duration p50 0.000 ms, max 9903.532 ms
- action entropy (**realised choices only**; policy softmin is absent): weighted mean 0.797 bit, p50 0.650 bit, modal-direction share p50 0.833

## Flip consequences (dJ = J_truth(chosen) - J_truth(fresh), ms)

- dJ p50 6.129 / p90 11.843 / p99 18.204 ms; exactly-zero share 0.000
- partition at tol=0.5 ms: fresh_better_other=429, persistent_bias=4734, proxy_gap_tiny=182, quick_remerge=900
- **P(consequential|flip) = 0.9709**, **P(harmful|flip) = 0.9709** (proxy definitions, not causal)
- proxy regret over flips: mean 6.486 ms, p90 11.843 ms

| tol (ms) | tiny | fresh better | stale better | P(consequential) | P(harmful) |
|---|---|---|---|---|---|
| 0.1 | 64 | 6181 | 0 | 0.9898 | 0.9898 |
| 0.5 | 182 | 6063 | 0 | 0.9709 | 0.9709 |
| 1 | 307 | 5938 | 0 | 0.9508 | 0.9508 |
| 5 | 2275 | 3970 | 0 | 0.6357 | 0.6357 |
