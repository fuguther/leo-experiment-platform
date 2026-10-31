# Response dynamics - `m2-c2-hyst-s7`

- policy `queue_greedy_hysteresis` (eta=0.005, alpha=0.25); window [4.0, 14.0]
- decisions 26064 over 199 (sat,dst) streams; flips 4915 (0.1886)
- switch rate **0.1912** (4945/25865 adjacent pairs)
- action runs: 5144 runs, length p50 1.0 / p90 8.0 / max 720; duration p50 0.000 ms, max 9936.133 ms
- action entropy (**realised choices only**; policy softmin is absent): weighted mean 0.805 bit, p50 0.592 bit, modal-direction share p50 0.857

## Flip consequences (dJ = J_truth(chosen) - J_truth(fresh), ms)

- dJ p50 8.072 / p90 17.541 / p99 31.674 ms; exactly-zero share 0.000
- partition at tol=0.5 ms: fresh_better_other=464, persistent_bias=3470, proxy_gap_tiny=50, quick_remerge=753, stale_better=178
- **P(consequential|flip) = 0.9898**, **P(harmful|flip) = 0.9536** (proxy definitions, not causal)
- proxy regret over flips: mean 9.580 ms, p90 17.541 ms

| tol (ms) | tiny | fresh better | stale better | P(consequential) | P(harmful) |
|---|---|---|---|---|---|
| 0.1 | 22 | 4708 | 185 | 0.9955 | 0.9579 |
| 0.5 | 50 | 4687 | 178 | 0.9898 | 0.9536 |
| 1 | 100 | 4650 | 165 | 0.9797 | 0.9461 |
| 5 | 514 | 4401 | 0 | 0.8954 | 0.8954 |
