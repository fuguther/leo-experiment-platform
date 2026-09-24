# PROCEDURE — EXP-20260924-PLATFORM-AUDIT-F2-R01

Work package: `WP-LEO-V2-PLATFORM-AUDIT-F2` revision 1.
This document is the executable procedure for the minimal end-to-end
platform-audit experiment. Every command below is reproducible from the
repository root at the reviewed revision.

## 1. Compile (already executed; `execution_authorized=false`)

```bash
PYTHONPATH=. python3 CODE/experiment_platform/compile_matrix_experiment.py \
  EXPERIMENTS/request-sources/EXP-20260924-PLATFORM-AUDIT-F2-R01.json \
  --out EXPERIMENTS/EXP-20260924-PLATFORM-AUDIT-F2-R01 --root .
```

Expected sentinel: `compile-report.json` `status=COMPILED_REVIEW_REQUIRED`,
`errors=[]`, `execution_authorized=false`.

## 2. Review -> decision -> finalization

Three independent review receipts are produced by three separate cold-start
review sessions (`cold_start`, `satellite_drl`, `adversarial`), each bound to
the artifact hashes below. Without all three PASS the decision cannot be ACCEPT
and `finalize_decision.py` refuses to emit a finalization.

```bash
python3 CODE/work/finalize_decision.py \
  --brief CODE/work/WP-LEO-V2-PLATFORM-AUDIT-F2/R01/brief.json \
  --decision CODE/work/WP-LEO-V2-PLATFORM-AUDIT-F2/R01/decision.json \
  --out CODE/work/WP-LEO-V2-PLATFORM-AUDIT-F2/R01/finalization.json
```

## 3. Authorize

```bash
python3 CODE/experiment_platform/authorize_experiment.py \
  --experiment EXPERIMENTS/EXP-20260924-PLATFORM-AUDIT-F2-R01 \
  --finalization CODE/work/WP-LEO-V2-PLATFORM-AUDIT-F2/R01/finalization.json \
  --out EXPERIMENTS/EXP-20260924-PLATFORM-AUDIT-F2-R01/authorization.json
```

Expected sentinel: `authorization.json` `status=AUTHORIZED` with exactly the two
compiled cells in `authorized_runs`.

## 4. Run

### 4a. Canonical remote runner (the formally recognized route)

```bash
CODE/scripts/remote/run-remote.sh --runtime-kind leo_sim_v2 \
  --config EXPERIMENTS/EXP-20260924-PLATFORM-AUDIT-F2-R01/resolved/EXP-20260924-PLATFORM-AUDIT-F2-R01-control-s7.leo-sim.yaml \
  --authorization EXPERIMENTS/EXP-20260924-PLATFORM-AUDIT-F2-R01/authorization.json \
  --session exp-20260924-platform-audit-f2-r01-control-s7
```

**DECLARED PLATFORM LIMITATION (blocking, not worked around here).** The
`f2` arm cannot be run through this route at all today. F2 hard-requires a
timeline sink (`CODE/leo_sim/kernel.py:1145-1152`), and the formal runner
never supplies one: `CODE/scripts/remote/remote_job.py:304-309` builds the
child command from exactly `--config --out --authorization --launch-nonce
--expect-run-id`, and `run-remote.sh:44` rejects `--` explicitly
("arbitrary commands are forbidden by the formal runner"), so there is no
passthrough. A formal F2 run therefore fails loud with
`RUN REFUSED (F2 attributable timing)`. Repairing that requires editing the
authorization chain, which needs its own independent cold review and cannot be
exercised in this environment.

### 4b. Local formal-flag execution (what was actually used here)

The local CLI accepts the same formal binding flags, so both arms are executed
locally with the authorization attached. This is a *degraded* substitute: it
exercises the authorization verifier and writes `formal_run.json`, but it is
NOT the canonical remote route and the resulting artifacts are not VM
provenance.

```bash
python3 -m CODE.leo_sim run \
  --config EXPERIMENTS/EXP-20260924-PLATFORM-AUDIT-F2-R01/resolved/EXP-20260924-PLATFORM-AUDIT-F2-R01-control-s7.leo-sim.yaml \
  --out CODE/Results/EXP-20260924-PLATFORM-AUDIT-F2-R01-control-s7 \
  --authorization EXPERIMENTS/EXP-20260924-PLATFORM-AUDIT-F2-R01/authorization.json \
  --launch-nonce <32 hex> \
  --expect-run-id EXP-20260924-PLATFORM-AUDIT-F2-R01-control-s7

python3 -m CODE.leo_sim run \
  --config EXPERIMENTS/EXP-20260924-PLATFORM-AUDIT-F2-R01/resolved/EXP-20260924-PLATFORM-AUDIT-F2-R01-f2-s7.leo-sim.yaml \
  --out CODE/Results/EXP-20260924-PLATFORM-AUDIT-F2-R01-f2-s7 \
  --authorization EXPERIMENTS/EXP-20260924-PLATFORM-AUDIT-F2-R01/authorization.json \
  --launch-nonce <32 hex> \
  --expect-run-id EXP-20260924-PLATFORM-AUDIT-F2-R01-f2-s7 \
  --timeline-log CODE/Results/EXP-20260924-PLATFORM-AUDIT-F2-R01-f2-s7/timeline.jsonl
```

## 5. Verify and analyse

```bash
python3 -m CODE.leo_sim receipt verify CODE/Results/EXP-20260924-PLATFORM-AUDIT-F2-R01-control-s7
python3 -m CODE.leo_sim receipt verify CODE/Results/EXP-20260924-PLATFORM-AUDIT-F2-R01-f2-s7
python3 -m CODE.experiment_platform.v2_analysis \
  --experiment EXPERIMENTS/EXP-20260924-PLATFORM-AUDIT-F2-R01 \
  --authorization EXPERIMENTS/EXP-20260924-PLATFORM-AUDIT-F2-R01/authorization.json \
  --out ANALYSIS/EXP-20260924-PLATFORM-AUDIT-F2-R01/v2-paired
```

## 6. Mandatory negative controls (each must FAIL loudly)

| # | Control | Required outcome |
| --- | --- | --- |
| N1 | `f2` config run with no `--timeline-log` | exit 3, `RUN REFUSED (F2 attributable timing)` |
| N2 | run with `--authorization` but a launch nonce of the wrong length | exit 3, `RUN REFUSED (formal authorization)` |
| N3 | run with `--expect-run-id` for the other cell | refusal from `verify_authorization_for_leo_sim_v2_config` |
| N4 | one byte of a compiled `resolved/*.leo-sim.yaml` mutated after authorization | authorization verification refuses |
| N5 | `authorize_experiment.py` with any review receipt downgraded to BLOCK | no `authorization.json` is written |

A control that does not trigger is reported as an UNPROVEN control and is not
counted as passed.
