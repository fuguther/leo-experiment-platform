# EXP-20260824-ISL-BANDWIDTH-PILOT-R03

Runtime: `leo_sim_v2`; compilation only, no run is launched.

Cells are listed in mandatory order; every later command is blocked until its predecessors pass the serial evidence gate:

Execution policy: `serial_fail_closed`. The canonical runner applies a machine-enforced serial predecessor gate before every cell after the first; missing or ineligible pulled predecessor evidence blocks the next launch.

## EXP-20260824-ISL-BANDWIDTH-PILOT-R03-b5-s7

```bash
CODE/scripts/remote/run-remote.sh \
  --runtime-kind leo_sim_v2 \
  --config EXPERIMENTS/EXP-20260824-ISL-BANDWIDTH-PILOT-R03/resolved/EXP-20260824-ISL-BANDWIDTH-PILOT-R03-b5-s7.leo-sim.yaml \
  --authorization EXPERIMENTS/EXP-20260824-ISL-BANDWIDTH-PILOT-R03/authorization.json \
  --session exp-20260824-isl-bandwidth-pilot-r03-b5-s7
```

## EXP-20260824-ISL-BANDWIDTH-PILOT-R03-b2-s7

```bash
CODE/scripts/remote/run-remote.sh \
  --runtime-kind leo_sim_v2 \
  --config EXPERIMENTS/EXP-20260824-ISL-BANDWIDTH-PILOT-R03/resolved/EXP-20260824-ISL-BANDWIDTH-PILOT-R03-b2-s7.leo-sim.yaml \
  --authorization EXPERIMENTS/EXP-20260824-ISL-BANDWIDTH-PILOT-R03/authorization.json \
  --session exp-20260824-isl-bandwidth-pilot-r03-b2-s7
```

## V2 analysis after every authorized cell has a natural-end result

```bash
python3 -m CODE.experiment_platform.v2_analysis \
  --experiment EXPERIMENTS/EXP-20260824-ISL-BANDWIDTH-PILOT-R03 \
  --authorization EXPERIMENTS/EXP-20260824-ISL-BANDWIDTH-PILOT-R03/authorization.json \
  --out ANALYSIS/EXP-20260824-ISL-BANDWIDTH-PILOT-R03/v2-paired
```

The output is evidence-bound analysis only; claim-support and value-gate review remain required.

## Apply the frozen post-analysis decision

Run this persisted classifier only after the V2 analysis above produces a verified manifest. Any verification or classification error is a stop, never a no-pressure result.

```bash
python3 -m CODE.experiment_platform.isl_pressure_decision \
  --root . \
  --manifest ANALYSIS/EXP-20260824-ISL-BANDWIDTH-PILOT-R03/v2-paired/analysis-manifest.json \
  --control-arm b5 \
  --candidate-arm b2 \
  --out ANALYSIS/EXP-20260824-ISL-BANDWIDTH-PILOT-R03/pressure-classification.json
```

The command and subsequent action are frozen in `CODE/work/WP-LEO-V2-ISL-BANDWIDTH-PILOT/R03/pressure-decision.json`. Do not substitute an in-memory classification or change thresholds after observing results.