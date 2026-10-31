# Response dynamics - `m2-c2-hyst-s7` (1 cells)

Flip semantics imported from `sq_persistence_analyze` (verified identical
to `sq_policy_analyze` by its selfcheck). dJ is a truth-substituted proxy,
not a causal measurement; policy softmin does not exist in the logs, so the
reported entropy is over realised choices only.

| cell | policy | flips | flip rate | switch rate | modal share | entropy (bit) | dJ p50 (ms) | dJ p90 | P(consequential) | P(harmful) |
|---|---|---|---|---|---|---|---|---|---|---|
| m2-c2-hyst-s7 | queue_greedy_hysteresis | 4915 | 0.1886 | 0.1912 | 0.857 | 0.805 | 8.072 | 17.541 | 0.9898 | 0.9536 |

## Per-cell detail

- [m2-c2-hyst-s7](./m2-c2-hyst-s7.md)
