# Response dynamics - `m2-c2-greedy-s7`

- policy `queue_greedy` (eta=0.0, alpha=0.25); window [4.0, 14.0]
- decisions 24990 over 208 (sat,dst) streams; flips 6592 (0.2638)
- switch rate **0.2974** (7370/24782 adjacent pairs)
- action runs: 7578 runs, length p50 1.0 / p90 5.0 / max 363; duration p50 0.000 ms, max 9936.133 ms
- action entropy (**realised choices only**; policy softmin is absent): weighted mean 0.863 bit, p50 0.592 bit, modal-direction share p50 0.857

## Flip consequences (dJ = J_truth(chosen) - J_truth(fresh), ms)

- dJ p50 6.455 / p90 13.224 / p99 21.646 ms; exactly-zero share 0.000
- partition at tol=0.5 ms: fresh_better_other=585, persistent_bias=4824, proxy_gap_tiny=155, quick_remerge=1028
- **P(consequential|flip) = 0.9765**, **P(harmful|flip) = 0.9765** (proxy definitions, not causal)
- proxy regret over flips: mean 7.221 ms, p90 13.224 ms

| tol (ms) | tiny | fresh better | stale better | P(consequential) | P(harmful) |
|---|---|---|---|---|---|
| 0.1 | 48 | 6544 | 0 | 0.9927 | 0.9927 |
| 0.5 | 155 | 6437 | 0 | 0.9765 | 0.9765 |
| 1 | 270 | 6322 | 0 | 0.9590 | 0.9590 |
| 5 | 2211 | 4381 | 0 | 0.6646 | 0.6646 |
