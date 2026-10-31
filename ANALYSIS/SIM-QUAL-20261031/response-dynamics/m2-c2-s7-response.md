# Response dynamics - `m2-c2-s7` (3 cells)

Flip semantics imported from `sq_persistence_analyze` (verified identical
to `sq_policy_analyze` by its selfcheck). dJ is a truth-substituted proxy,
not a causal measurement; policy softmin does not exist in the logs, so the
reported entropy is over realised choices only.

| cell | policy | flips | flip rate | switch rate | modal share | entropy (bit) | dJ p50 (ms) | dJ p90 | P(consequential) | P(harmful) |
|---|---|---|---|---|---|---|---|---|---|---|
| m2-c2-geom-s7 | geometry | 0 | 0.0000 | 0.2213 | 1.000 | 0.404 | n/a | n/a | n/a | n/a |
| m2-c2-greedy-s7 | queue_greedy | 6592 | 0.2638 | 0.2974 | 0.857 | 0.863 | 6.455 | 13.224 | 0.9765 | 0.9765 |
| m2-c2-ewma-s7 | queue_greedy_ewma | 6462 | 0.2604 | 0.1584 | 0.948 | 0.503 | 5.725 | 11.717 | 0.9494 | 0.9494 |

## Per-cell detail

- [m2-c2-geom-s7](./m2-c2-geom-s7.md)
- [m2-c2-greedy-s7](./m2-c2-greedy-s7.md)
- [m2-c2-ewma-s7](./m2-c2-ewma-s7.md)
