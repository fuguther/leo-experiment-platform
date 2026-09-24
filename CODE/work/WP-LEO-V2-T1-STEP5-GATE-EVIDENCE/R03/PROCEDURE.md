# R03 PROCEDURE — reproducible execution and evidence contract

This file is part of the reviewed artifact set of revision 3. Every command below
is literal, and every experiment id is this revision's own id, so no command can
silently point at a superseded revision.

## 0. Revision history and what revision 3 fixes

| Finding | Raised by | Status in this revision |
|---|---|---|
| COLD-B1: the auto-generated RUNBOOK mandates a `v2_analysis` step whose metric whitelist unconditionally rejects the declared `primary_metric` (`CODE/experiment_platform/v2_analysis.py:276`), so following the RUNBOOK literally could not complete. | R01 cold_start (Codex) | Worked around: `primary_metric` is `e2e_delay_mean_s`, which `v2_analysis._metric_from_result` supports (`v2_analysis.py:249-261`) and which is the same delivered-packet `e2e_s` that the independent decomposition closes against. **The underlying conflict is NOT repaired and is registered as an unfixed process defect**: editing `v2_analysis` or widening its whitelist would be a production-contract change outside this batch's authorisation. |
| COLD-B2: the recomputation workflow was not reproducible from the artifacts and lay outside the reviewed hash set. | R01 cold_start | Fixed: `tools/step5_recompute.py` is a CLI with `analyze` / `witness` / `selftest`, bound into `decision.artifact_hashes` together with this file and the demand CSV. |
| COLD-B3: report lists were truncated and the tolerance was undeclared. | R01 cold_start | Fixed: no report path truncates, and `tolerance_contract_s` is emitted by the `analyze`, `witness` and `selftest` reports alike. |
| COLD-R02-B1: this procedure named the **superseded R01** experiment directories, so following it would have run revision-1 configs and never executed the reviewed artifacts; because `run-remote.sh:97` derives the run id from the config **filename**, those runs would also have been attributed to the wrong revision. | R02 cold_start (GLM) | Fixed: all run commands below use `EXP-20260923-T1-STEP5-MICRO-R03` and `EXP-20260923-T1-STEP5-PRESSURE-R03`, and a self-check asserts that no command line in this file references an earlier revision. |
| COLD-R02-B2: the procedure never documented how `finalization.json` and `authorization.json` are produced, so the mandated ordering was not achievable from this file alone (`run-remote.sh:82` refuses to start without an authorization). | R02 cold_start (GLM) | Fixed: section 3 documents reviews -> decision -> finalization -> authorization with exact commands. |
| Non-blocking: `witness` silently treated a missing decision log as zero deferred rows, which could look like an admissible witness. | R02 cold_start (GLM) | Fixed: a missing or empty decision log is now a hard error. |

## 1. Deployment

Run from a **main-branch checkout**. `push-remote.sh:27` requires `.git` to be a real
directory, so a linked worktree cannot be the deployment source; it also needs
`CODE/scripts/remote/remote.env`, which is gitignored and therefore absent from
worktrees.

```bash
cd <main checkout>
git fetch origin
git merge --ff-only origin/main              # forward-only; never reset/checkout
CODE/scripts/remote/push-remote.sh           # takes no arguments
```

Record `source_git_commit`, `source_git_branch`, the local `source_tree_sha256` /
`file_count`, and the remote `receipt_sha256`.

## 2. Deployment verification (VM side, per-file recompute)

```bash
ssh <vm> 'cat "/data/论文/leo-direct-sim/.deployment_commit"'
ssh <vm> 'cat "/data/论文/leo-direct-sim/.remote_runtime/deployment.json"' > /tmp/deployment.json
```

Recompute sha256 for **every** record in `deployed_files` on the VM and classify each
as OK / MISSING / CHANGED; then recompute the same manifest against the local
deployment source and report the mismatch count. The `.deployment_commit` witness
exists only on the VM and is excluded from the local comparison.

## 3. Reviews -> decision -> finalization -> authorization

Mandatory, and it comes **before** any deployment or run. The ordering is fixed:
compile -> three independent reviews -> decision -> finalization -> authorization ->
clean deployment -> formal run.

```bash
# (a) three independent cold-start review sessions write, under CODE/work/WP-LEO-V2-T1-STEP5-GATE-EVIDENCE/R03/:
#     review-cold-start.json, review-satellite-drl.json, review-adversarial.json
#     Each receipt must bind EVERY key of CODE/work/WP-LEO-V2-T1-STEP5-GATE-EVIDENCE/R03/artifact-set.json.

# (b) the decision maker writes CODE/work/WP-LEO-V2-T1-STEP5-GATE-EVIDENCE/R03/decision.json
#     (schema agent-work-decision/v1, decision = ACCEPT, applying the three PASS receipts).

# (c) materialise the ACCEPTED finalization:
python3 CODE/work/finalize_decision.py \
  --brief CODE/work/WP-LEO-V2-T1-STEP5-GATE-EVIDENCE/R03/brief.json \
  --decision CODE/work/WP-LEO-V2-T1-STEP5-GATE-EVIDENCE/R03/decision.json \
  --out CODE/work/WP-LEO-V2-T1-STEP5-GATE-EVIDENCE/R03/finalization.json

# (d) derive the execution credential for BOTH experiments from that one finalization:
python3 CODE/experiment_platform/authorize_experiment.py \
  --experiment EXPERIMENTS/EXP-20260923-T1-STEP5-MICRO-R03 \
  --finalization CODE/work/WP-LEO-V2-T1-STEP5-GATE-EVIDENCE/R03/finalization.json \
  --out EXPERIMENTS/EXP-20260923-T1-STEP5-MICRO-R03/authorization.json

python3 CODE/experiment_platform/authorize_experiment.py \
  --experiment EXPERIMENTS/EXP-20260923-T1-STEP5-PRESSURE-R03 \
  --finalization CODE/work/WP-LEO-V2-T1-STEP5-GATE-EVIDENCE/R03/finalization.json \
  --out EXPERIMENTS/EXP-20260923-T1-STEP5-PRESSURE-R03/authorization.json
```

Expected sentinels: `finalize_decision.py` prints `ACCEPTED`; `authorize_experiment.py`
prints `AUTHORIZED: <experiment_id> (2 runs)`. Both fail with exit code 2, print a
`BLOCK:` line and write nothing on failure. `authorization.json` must be a direct
child of its experiment directory (`v2_serial_gate.py:48-51`), and `request.json`'s
`work_finalization` must equal `CODE/work/WP-LEO-V2-T1-STEP5-GATE-EVIDENCE/R03/finalization.json` exactly
(`authorize_experiment.py:411-415`). Neither request declares `execution_policy`, so
the serial predecessor gate is a documented no-op (`v2_serial_gate.py:52-55`) and a
pair's two cells may be launched in any order.

## 4. Formal runs

One command per cell. A cell is usable only if the VM run directory
`CODE/Results/<run_id>/` contains `formal_run.json` with `natural_end = true` and
`governance_receipt.json` with `research_eligible = true` and
`verification_errors = []`. File presence alone is not admission. The external launch
witness is `CODE/Results/_external_launch_witness/<run_id>.json`.

```bash
CODE/scripts/remote/run-remote.sh --runtime-kind leo_sim_v2 \
  --config EXPERIMENTS/EXP-20260923-T1-STEP5-MICRO-R03/resolved/EXP-20260923-T1-STEP5-MICRO-R03-control-s7.leo-sim.yaml \
  --authorization EXPERIMENTS/EXP-20260923-T1-STEP5-MICRO-R03/authorization.json \
  --session exp-20260923-t1-step5-micro-r03-control-s7
```

```bash
CODE/scripts/remote/run-remote.sh --runtime-kind leo_sim_v2 \
  --config EXPERIMENTS/EXP-20260923-T1-STEP5-MICRO-R03/resolved/EXP-20260923-T1-STEP5-MICRO-R03-cd05-s7.leo-sim.yaml \
  --authorization EXPERIMENTS/EXP-20260923-T1-STEP5-MICRO-R03/authorization.json \
  --session exp-20260923-t1-step5-micro-r03-cd05-s7
```

```bash
CODE/scripts/remote/run-remote.sh --runtime-kind leo_sim_v2 \
  --config EXPERIMENTS/EXP-20260923-T1-STEP5-PRESSURE-R03/resolved/EXP-20260923-T1-STEP5-PRESSURE-R03-control-s7.leo-sim.yaml \
  --authorization EXPERIMENTS/EXP-20260923-T1-STEP5-PRESSURE-R03/authorization.json \
  --session exp-20260923-t1-step5-pressure-r03-control-s7
```

```bash
CODE/scripts/remote/run-remote.sh --runtime-kind leo_sim_v2 \
  --config EXPERIMENTS/EXP-20260923-T1-STEP5-PRESSURE-R03/resolved/EXP-20260923-T1-STEP5-PRESSURE-R03-cd05-s7.leo-sim.yaml \
  --authorization EXPERIMENTS/EXP-20260923-T1-STEP5-PRESSURE-R03/authorization.json \
  --session exp-20260923-t1-step5-pressure-r03-cd05-s7
```

## 5. Independent recomputation (chain 1 + chain 2)

Run **on the VM**, from the deployed workspace root, so the raw outputs stay in the
canonical VM results location. `<run_id>` is one of the four ids above; `<cd>` is
`0.0` for the `control` cells and `0.05` for the `cd05` cells.

```bash
cd "/data/论文/leo-direct-sim"
PYTHONPATH=. python3 CODE/work/WP-LEO-V2-T1-STEP5-GATE-EVIDENCE/tools/step5_recompute.py analyze \
  --run-id <run_id> --cd <cd> \
  --ledgers  CODE/Results/<run_id>/ledgers.json \
  --receipt  CODE/Results/<run_id>/receipt.json \
  --out      CODE/Results/<run_id>/step5-recompute.json
```

The report is exhaustive: every link (including zero-service links) and every delivered
packet appears, and `chain1_link_utilization.mismatches` /
`chain2_delay_decomposition.production_phase_mismatches` list every disagreement
against the production reading with both values.

## 5b. Harness self-test (run BEFORE trusting any analyze/witness output)

```bash
PYTHONPATH=. python3 CODE/work/WP-LEO-V2-T1-STEP5-GATE-EVIDENCE/tools/step5_recompute.py \
  selftest --out CODE/Results/_step5_diagnostic/selftest.json
```

Exits 0 only if every fail-loud property below actually holds on synthetic fixtures,
so a green `analyze` result cannot be vacuous:

1. an uncovered interval exactly equal to `compute_delay_s` is accepted;
2. a **merged / lumped interval of 2 x compute_delay_s is REPORTED as a violation**,
   not summed into a passing total — the property that makes "residual < 1e-9"
   insufficient on its own;
3. an interval of `compute_delay_s` plus unexplained extra time is reported together
   with the excess;
4. a tampered `capacity_bits != rate*(end-start)` is rejected;
5. an overlapping available-capacity window on one link is rejected;
6. chain 1 reports every link and agrees with an identical production reading;
7. a tampered production utilisation is reported as a mismatch (the comparator is
   two-sided, so "0 mismatches" is not a vacuous pass);
8. an undelivered packet receives truncated accounting and never the delivered closed
   form;
9. the harness's own independence guard holds.

## 6. Substituted decision-count witness (admissible only if proven identical)

A formal authorized run cannot carry `--decision-log` (`CODE/leo_sim/__main__.py:332-335`
refuses it; `remote_job.formal_command` at `remote_job.py:300-310` never passes it), so
no formal artifact contains a decision row. The substitute is a deterministic
**non-formal** re-execution on the SAME VM, same commit, same resolved config, same seed:

```bash
cd "/data/论文/leo-direct-sim"
mkdir -p CODE/Results/_step5_diagnostic/<run_id>
PYTHONPATH=. python3 -m CODE.leo_sim run \
  --config EXPERIMENTS/<EXP>/resolved/<run_id>.leo-sim.yaml \
  --out CODE/Results/_step5_diagnostic/<run_id> \
  --decision-log CODE/Results/_step5_diagnostic/<run_id>/decisions.jsonl

PYTHONPATH=. python3 CODE/work/WP-LEO-V2-T1-STEP5-GATE-EVIDENCE/tools/step5_recompute.py witness \
  --run-id <run_id> --cd <cd> \
  --ledgers            CODE/Results/<run_id>/ledgers.json \
  --formal-receipt     CODE/Results/<run_id>/receipt.json \
  --diagnostic-ledgers CODE/Results/_step5_diagnostic/<run_id>/ledgers.json \
  --diagnostic-receipt CODE/Results/_step5_diagnostic/<run_id>/receipt.json \
  --decision-log       CODE/Results/_step5_diagnostic/<run_id>/decisions.jsonl \
  --out                CODE/Results/<run_id>/step5-witness.json
```

**Admissibility rule (fail-loud).** `witness.admissible` is true only when
`trace_sha256` AND the sha256 digests of `packet_events`, `link_service_windows` and
`link_available_windows` are all identical between the formal and the diagnostic run.
If any of the four differs, `admissible = false`, `inadmissible_reason` is populated,
the decision-count witness is **unavailable for that run**, and nothing weaker may be
substituted. A missing or empty decision log is a hard error, not a zero-row witness.
The formal per-gap evidence is independent of the witness and is still reported.

## 7. Post-run cohort verification

```bash
PYTHONPATH=. python3 -m CODE.experiment_platform.v2_analysis \
  --experiment EXPERIMENTS/<EXP> \
  --authorization EXPERIMENTS/<EXP>/authorization.json \
  --out ANALYSIS/<EXP>/v2-paired
```

Cohort/identity re-verification plus the descriptive paired value of
`e2e_delay_mean_s`. It is **not** the acceptance basis for either evidence chain;
`metrics_independent` is, and `CODE/leo_sim/metrics.summarize` is never used as an
acceptance criterion in this batch.

## 8. Artifact persistence

Raw events, config, trace, seed, code SHA, run receipts and the raw JSON recompute
outputs stay in the canonical VM results location `CODE/Results/<run_id>/` and are
**never** committed (`CODE/Results/` is gitignored). The receipt reports a path +
sha256 inventory. No receipt or ledger key set is modified, no prior evidence file is
deleted or moved, and nothing in this batch restores or claims to restore the lost R02
raw data.
