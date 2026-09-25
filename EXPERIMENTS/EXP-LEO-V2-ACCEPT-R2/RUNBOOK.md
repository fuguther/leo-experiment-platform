# EXP-LEO-V2-ACCEPT-R2

Runtime: `leo_sim_v2`; compilation only, no run is launched.

Each cell is an independent controlled command after review, authorization, and clean deployment:

## Design accounting

2 planned cells; 2 unique resolved configurations; 0 exact re-execution cells.

Exact re-executions are repeatability evidence only and do not increase the independent-condition count.

## One-change policy

Declared policy: `strict`; declared factor(s): routing.policy.

- contrast `treatment_minus_control`: changed paths routing.policy

Changed paths are counted from the RESOLVED configurations of the planned cells, excluding stochastic identity paths.

## EXP-LEO-V2-ACCEPT-R2-control-s7

```bash
CODE/scripts/remote/run-remote.sh \
  --runtime-kind leo_sim_v2 \
  --config EXPERIMENTS/EXP-LEO-V2-ACCEPT-R2/resolved/EXP-LEO-V2-ACCEPT-R2-control-s7.leo-sim.yaml \
  --authorization EXPERIMENTS/EXP-LEO-V2-ACCEPT-R2/authorization.json \
  --session exp-leo-v2-accept-r2-control-s7
```

## EXP-LEO-V2-ACCEPT-R2-treatment-s7

```bash
CODE/scripts/remote/run-remote.sh \
  --runtime-kind leo_sim_v2 \
  --config EXPERIMENTS/EXP-LEO-V2-ACCEPT-R2/resolved/EXP-LEO-V2-ACCEPT-R2-treatment-s7.leo-sim.yaml \
  --authorization EXPERIMENTS/EXP-LEO-V2-ACCEPT-R2/authorization.json \
  --session exp-leo-v2-accept-r2-treatment-s7
```

## V2 analysis after every authorized cell has a natural-end result

```bash
python3 -m CODE.experiment_platform.v2_analysis \
  --experiment EXPERIMENTS/EXP-LEO-V2-ACCEPT-R2 \
  --authorization EXPERIMENTS/EXP-LEO-V2-ACCEPT-R2/authorization.json \
  --out ANALYSIS/EXP-LEO-V2-ACCEPT-R2/v2-paired
```

The output is evidence-bound analysis only; claim-support and value-gate review remain required.