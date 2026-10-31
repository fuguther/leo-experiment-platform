# Response dynamics - `m2-c2-s9` (3 cells)

Flip semantics imported from `sq_persistence_analyze` (verified identical
to `sq_policy_analyze` by its selfcheck). dJ is a truth-substituted proxy,
not a causal measurement; policy softmin does not exist in the logs, so the
reported entropy is over realised choices only.

| cell | policy | flips | flip rate | switch rate | modal share | entropy (bit) | dJ p50 (ms) | dJ p90 | P(consequential) | P(harmful) |
|---|---|---|---|---|---|---|---|---|---|---|
| m2-c2-geom-s9 | geometry | 0 | 0.0000 | 0.2105 | 1.000 | 0.397 | n/a | n/a | n/a | n/a |
| m2-c2-greedy-s9 | queue_greedy | 6250 | 0.2572 | 0.2734 | 0.867 | 0.810 | 6.300 | 12.772 | 0.9762 | 0.9762 |
| m2-c2-ewma-s9 | queue_greedy_ewma | 6492 | 0.2651 | 0.2073 | 0.931 | 0.731 | 6.517 | 11.887 | 0.9592 | 0.9592 |

## Per-cell detail

- [m2-c2-geom-s9](./m2-c2-geom-s9.md)
- [m2-c2-greedy-s9](./m2-c2-greedy-s9.md)
- [m2-c2-ewma-s9](./m2-c2-ewma-s9.md)
