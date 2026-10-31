# Response dynamics - `m2-b-s7` (3 cells)

Flip semantics imported from `sq_persistence_analyze` (verified identical
to `sq_policy_analyze` by its selfcheck). dJ is a truth-substituted proxy,
not a causal measurement; policy softmin does not exist in the logs, so the
reported entropy is over realised choices only.

| cell | policy | flips | flip rate | switch rate | modal share | entropy (bit) | dJ p50 (ms) | dJ p90 | P(consequential) | P(harmful) |
|---|---|---|---|---|---|---|---|---|---|---|
| m2-b-geom-s7 | geometry | 0 | 0.0000 | 0.2131 | 1.000 | 0.404 | n/a | n/a | n/a | n/a |
| m2-b-greedy-s7 | queue_greedy | 5284 | 0.2686 | 0.2407 | 0.896 | 0.823 | 6.155 | 11.578 | 0.9818 | 0.9818 |
| m2-b-ewma-s7 | queue_greedy_ewma | 5186 | 0.2621 | 0.1383 | 0.958 | 0.445 | 5.943 | 10.801 | 0.9508 | 0.9508 |

## Per-cell detail

- [m2-b-geom-s7](./m2-b-geom-s7.md)
- [m2-b-greedy-s7](./m2-b-greedy-s7.md)
- [m2-b-ewma-s7](./m2-b-ewma-s7.md)
