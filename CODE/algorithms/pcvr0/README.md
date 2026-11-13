# PCVR-0 — Policy-Conditioned Cohort-Value Router

**Status:** executable research prototype; model training and network-performance validation are still pending.

PCVR-0 is not a queue predictor and does not claim that a better local ETA is equivalent to a better network outcome. It learns an action-conditioned mapping from the information available at one routing decision to the whole offered cohort's outcome under a controlled one-action intervention.

## 1. Algorithm

At decision i, satellite s receives a frozen local observation o_i, including its legal next-hop set A_i. The existing time-alignment scorer supplies the baseline action:

    a0_i = argmin over a in A_i of estimated_TA(o_i, a)

The scorer's deterministic tie-break and fallback semantics remain unchanged.

For each legal action a, PCVR-0 builds an action-specific feature vector:

    x_i,a = Phi(o_i, a)

The current feature contract contains 37 fields: local compute/query delay; first-hop queue wait, serialization and propagation; peer processing; predicted downstream resource work and packet serialization; remaining-path propagation; age, queue level, service rate and sample count of the actually received resource history; local egress work; action identity; candidate rank/count; common horizon; and explicit missing/fallback indicators.

The feature extractor is shared by data collection and inference. It accepts only ObservationSnapshot, calls the existing causal time-alignment planner, and rejects future-truth objects.

### Counterfactual training label

For a selected decision and every legal action a, the collector replays the same immutable trace/config/seed twice from the beginning, verifies the exact pre-branch fingerprint, and forces only that one action. All later decisions—including decisions by other satellites—are re-evaluated by the same baseline policy against the state that actually results.

The label is the fixed offered cohort's outcome vector:

    y_i,a = (1 - PDR_cohort, p99_cohort, mean_E2E_cohort, control_packets)

- PDR denominator is the offered trace cohort (packet IDs from the immutable trace), not only packets admitted to satellite ingress. Background packets remain in the simulated world and affect service, but are excluded from the evaluated offered cohort.
- p99 is nearest-rank p99 over delivered packets; delivery loss is a higher-priority objective, so a lower p99 cannot compensate for lower delivery.
- Mean E2E is over delivered packets.
- Control overhead is the number of control packets that actually entered a queue.
- An interrupted run, incomplete fate ledger, mismatched branch prefix, uncommitted forced action, or missing delivered E2E reconstruction is rejected rather than turned into a training label.

This is a one-step intervention under a fixed policy profile. It captures the downstream response that unfolds after the forced action, but does not identify the outcome of changing many decisions simultaneously or iterating to a new multi-agent equilibrium.

### Value model

For each objective m, PCVR-0 fits a ridge-linear head:

    predicted_y_i,a(m) = beta_m^T [1 ; (x_i,a - mu) / sigma]

Validation is leave-one-world-out: every action fork and decision from one world stays in the same held-out fold. At least three distinct world_id values and one common policy_id are required. The model records the p90 absolute out-of-world prediction error for each head.

### Online action rule

Let a* be the lexicographically best predicted legal action under the order:

    1 - PDR  <  p99  <  mean E2E  <  control packets

PCVR-0 switches from the baseline a0 only if the first practically non-tied objective satisfies:

    predicted_y(a*, m) - predicted_y(a0, m) < -(2 * e90(m) + tolerance(m))

If the predictions are tied within the declared practical tolerance, the next objective is considered. If the difference is larger than a tie tolerance but does not clear the empirical error margin, the policy returns to a0. Thus uncertain predictions do not cause a route change.

The e90 term is an empirical leave-one-world-out error statistic, not a confidence interval, worst-case bound, or safety certificate.

## 2. Code and commands

- policy.py: online feature extraction, frozen-model inference and guarded lexicographic action selection.
- collect.py: same-world, same-cohort single-action fork collection.
- train.py: paired-data validation, leave-one-world-out ridge fitting, and frozen plugin-bundle generation.
- manifest.json: generated with exact SHA-256 bindings for the policy module and model checkpoint.

From the platform repository root:

    python -m CODE.algorithms.pcvr0.collect --config path/to/world_seed_1.yaml --out out/pcvr0_seed_1.jsonl --max-decisions 8
    python -m CODE.experiment_platform.pcvr0_collect --config path/to/world_seed_2.yaml --out out/pcvr0_seed_2.jsonl --max-decisions 8
    python -m CODE.experiment_platform.pcvr0_collect --config path/to/world_seed_3.yaml --out out/pcvr0_seed_3.jsonl --max-decisions 8
    cat out/pcvr0_seed_*.jsonl > out/pcvr0_paired.jsonl
    python -m CODE.algorithms.pcvr0.train --data out/pcvr0_paired.jsonl --out-dir out/pcvr0_plugin_bundle

Collection requires:
- learning.algorithm=none;
- time_alignment.enabled=true;
- routing.policy_plugin=null;
- execution.decision_observation_mode=frozen;
- execution.compute_delay_s=0 for this first exact snapshot/commit collector.

Use the generated bundle's manifest.json as routing.policy_plugin (or use the platform's declared env:NAME path handoff). The plugin loader verifies both module and model bytes and requires inference-only behavior.

Targeted tests:

    pytest -q CODE/experiment_platform/tests/test_pcvr0.py

## 3. What this prototype does and does not establish

**Implemented:** the action-specific local feature contract; frozen inference policy; conservative baseline fallback; paired counterfactual dataset collector; fixed-cohort outcome extraction; grouped out-of-world training/validation; SHA-bound deployment bundle.

**Not yet established:** a trained model on the real multi-world LEO cohort; lower E2E/p99 or higher PDR than the baseline; benefit after accounting for actual on-board inference compute; repeated multi-node policy-improvement stability; dynamic rematch correctness; a formal bound on model error or policy regret.

The first scientific test is not a large constellation run. It is a small, paired, multi-world cohort where every legal first-hop action is forked from an identical branch point. We must verify that PCVR-0 predicts held-out action outcomes better than the baseline ETA ranking, and that its guarded decisions improve the same fixed cohort in full-kernel replay. Only then should it enter a larger network experiment.
