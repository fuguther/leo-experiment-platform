# Response dynamics - `m2-b-geom-s7`

- policy `geometry` (eta=0.0, alpha=0.25); window [4.0, 14.0]
- decisions 18050 over 182 (sat,dst) streams; flips 0 (0.0000)
- switch rate **0.2131** (3807/17868 adjacent pairs)
- action runs: 3989 runs, length p50 1.0 / p90 5.0 / max 1009; duration p50 0.000 ms, max 9957.373 ms
- action entropy (**realised choices only**; policy softmin is absent): weighted mean 0.404 bit, p50 0.000 bit, modal-direction share p50 1.000

## Flip consequences (dJ = J_truth(chosen) - J_truth(fresh), ms)

- dJ p50 n/a / p90 n/a / p99 n/a ms; exactly-zero share n/a
- partition at tol=0.5 ms: 
- **P(consequential|flip) = n/a**, **P(harmful|flip) = n/a** (proxy definitions, not causal)
- proxy regret over flips: mean n/a ms, p90 n/a ms

| tol (ms) | tiny | fresh better | stale better | P(consequential) | P(harmful) |
|---|---|---|---|---|---|
| 0.1 | 0 | 0 | 0 | n/a | n/a |
| 0.5 | 0 | 0 | 0 | n/a | n/a |
| 1 | 0 | 0 | 0 | n/a | n/a |
| 5 | 0 | 0 | 0 | n/a | n/a |
