# Response dynamics - `m2-c2-s8` (3 cells)

Flip semantics imported from `sq_persistence_analyze` (verified identical
to `sq_policy_analyze` by its selfcheck). dJ is a truth-substituted proxy,
not a causal measurement; policy softmin does not exist in the logs, so the
reported entropy is over realised choices only.

| cell | policy | flips | flip rate | switch rate | modal share | entropy (bit) | dJ p50 (ms) | dJ p90 | P(consequential) | P(harmful) |
|---|---|---|---|---|---|---|---|---|---|---|
| m2-c2-geom-s8 | geometry | 0 | 0.0000 | 0.2123 | 1.000 | 0.405 | n/a | n/a | n/a | n/a |
| m2-c2-greedy-s8 | queue_greedy | 6245 | 0.2566 | 0.2549 | 0.833 | 0.797 | 6.129 | 11.843 | 0.9709 | 0.9709 |
| m2-c2-ewma-s8 | queue_greedy_ewma | 6246 | 0.2642 | 0.2171 | 0.936 | 0.695 | 6.158 | 11.543 | 0.9667 | 0.9667 |

## Per-cell detail

- [m2-c2-geom-s8](./m2-c2-geom-s8.md)
- [m2-c2-greedy-s8](./m2-c2-greedy-s8.md)
- [m2-c2-ewma-s8](./m2-c2-ewma-s8.md)
