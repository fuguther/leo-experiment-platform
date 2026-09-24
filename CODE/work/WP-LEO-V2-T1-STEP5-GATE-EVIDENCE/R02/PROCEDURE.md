# R02 PROCEDURE — reproducible execution and evidence contract

This file is part of the reviewed artifact set of revision 2. It exists because
revision 1's review (COLD-B1/B2/B3) found that the load-bearing analysis workflow
was not reproducible from the artifacts and that the prescribed post-run analysis
could not accept the declared primary metric. Every command below is literal.

## 0. What changed in revision 2 (responses to R01 review)

| R01 finding | Fix |
|---|---|
| COLD-B1: the RUNBOOK's post-run analysis (`v2_analysis`) rejects `primary_metric = independent_recompute_closure` (`CODE/experiment_platform/v2_analysis.py:276`). | `primary_metric` is now `e2e_delay_mean_s`, which `v2_analysis._metric_from_result` supports (`v2_analysis.py:249-261`) and which is computed from the SAME delayed-packet `e2e_s` that the independent decomposition closes against. The batch's gate evidence remains the independent recomputation; `v2_analysis` supplies cohort/identity re-verification and one descriptive paired value. |
| COLD-B2: the harness had no entry point, no production comparator, no witness verifier, and was outside the reviewed hash set. | `tools/step5_recompute.py` is now a CLI with `analyze` and `witness` subcommands, diffs every per-link and per-packet quantity against the production reading, and is bound into `decision.artifact_hashes` together with this file and the demand CSV, so all three review receipts must hash-bind it. |
| COLD-B3: report lists were truncated (`[:20]`, `[:50]`) and the tolerance was undeclared. | No list is truncated anywhere. An explicit `tolerance_contract_s` is emitted in every report; full per-gap tables are emitted for every delivered packet. |

## 1. Deployment

Run from a **main-branch checkout** (push-remote.sh requires a real `.git`
directory, so a linked worktree cannot be the deployment source; it also
requires `CODE/scripts/remote/remote.env`, which is gitignored and therefore
absent from worktrees):

```bash
cd <main checkout>
git fetch origin
git merge --ff-only origin/main              # forward-only; never reset/checkout
CODE/scripts/remote/push-remote.sh           # takes no arguments
```

Record `source_git_commit`, `source_git_branch`, the local
`source_tree_sha256`/`file_count`, and the remote `receipt_sha256`.

## 2. Deployment verification (VM side, per-file recompute)

```bash
ssh <vm> 'cat "/data/论文/leo-direct-sim/.deployment_commit"'
ssh <vm> 'cat "/data/论文/leo-direct-sim/.remote_runtime/deployment.json"' > /tmp/deployment.json
```

Recompute sha256 for **every** record in `deployed_files` on the VM and classify
each as OK / MISSING / CHANGED; then recompute the same manifest against the
local deployment source and report the mismatch count. The `.deployment_commit`
witness exists only on the VM and is excluded from the local comparison.

## 3. Formal runs

One command per cell, in RUNBOOK order. `--authorization` must stay a direct
child of the experiment directory.

```bash
CODE/scripts/remote/run-remote.sh --runtime-kind leo_sim_v2 \
  --config EXPERIMENTS/EXP-20260923-T1-STEP5-MICRO-R01/resolved/EXP-20260923-T1-STEP5-MICRO-R01-control-s7.leo-sim.yaml \
  --authorization EXPERIMENTS/EXP-20260923-T1-STEP5-MICRO-R01/authorization.json \
  --session step5-micro-control
# ... and the same three more times:
#   MICRO  cd05-s7, PRESSURE control-s7, PRESSURE cd05-s7
```

A cell is only usable if the run directory on the VM
(`CODE/Results/<run_id>/`) contains `formal_run.json` with
`natural_end = true`, and `governance_receipt.json` with
`research_eligible = true` and `verification_errors = []`. File presence alone
is not admission. The external launch witness is
`CODE/Results/_external_launch_witness/<run_id>.json`.

## 4. Independent recomputation (chain 1 + chain 2)

Run **on the VM**, from the deployed workspace root, so the raw outputs stay in
the canonical VM results location:

```bash
cd "/data/论文/leo-direct-sim"
PYTHONPATH=. python3 CODE/work/WP-LEO-V2-T1-STEP5-GATE-EVIDENCE/tools/step5_recompute.py analyze \
  --run-id <run_id> --cd <0.0|0.05> \
  --ledgers  CODE/Results/<run_id>/ledgers.json \
  --receipt  CODE/Results/<run_id>/receipt.json \
  --out      CODE/Results/<run_id>/step5-recompute.json
```

The report is exhaustive: every link (including zero-service links) and every
delivered packet appears, and `chain1_link_utilization.mismatches` /
`chain2_delay_decomposition.production_phase_mismatches` list every
disagreement against the production reading with both values.

## 4b. Harness self-test (run BEFORE trusting any analyze/witness output)

```bash
PYTHONPATH=. python3 CODE/work/WP-LEO-V2-T1-STEP5-GATE-EVIDENCE/tools/step5_recompute.py \
  selftest --out CODE/Results/_step5_diagnostic/selftest.json
```

Exits 0 only if every fail-loud property below actually holds on synthetic
fixtures, so a green `analyze` result cannot be vacuous:

1. an uncovered interval exactly equal to `compute_delay_s` is accepted;
2. a **merged / lumped interval of 2 x compute_delay_s is REPORTED as a
   violation**, not summed into a passing total — this is the property that
   makes "residual < 1e-9" insufficient on its own;
3. an interval of `compute_delay_s` plus unexplained extra time is reported
   together with the excess;
4. a tampered `capacity_bits != rate*(end-start)` is rejected;
5. an overlapping available-capacity window on one link is rejected;
6. chain 1 reports every link and agrees with an identical production reading;
7. a tampered production utilisation is reported as a mismatch (the comparator
   is two-sided, so "0 mismatches" is not a vacuous pass);
8. an undelivered packet receives truncated accounting and never the delivered
   closed form;
9. the harness's own independence guard holds.

## 5. Substituted decision-count witness (admissible only if proven identical)

A formal authorized run cannot carry `--decision-log`
(`CODE/leo_sim/__main__.py:332-335` refuses it; `remote_job.formal_command` never
passes it), so no formal artifact contains a decision row. The substitute is a
deterministic **non-formal** re-execution on the SAME VM, same commit, same
resolved config, same seed:

```bash
cd "/data/论文/leo-direct-sim"
mkdir -p CODE/Results/_step5_diagnostic/<run_id>
PYTHONPATH=. python3 -m CODE.leo_sim run \
  --config EXPERIMENTS/<EXP>/resolved/<run_id>.leo-sim.yaml \
  --out CODE/Results/_step5_diagnostic/<run_id> \
  --decision-log CODE/Results/_step5_diagnostic/<run_id>/decisions.jsonl

PYTHONPATH=. python3 CODE/work/WP-LEO-V2-T1-STEP5-GATE-EVIDENCE/tools/step5_recompute.py witness \
  --run-id <run_id> --cd 0.05 \
  --ledgers            CODE/Results/<run_id>/ledgers.json \
  --formal-receipt     CODE/Results/<run_id>/receipt.json \
  --diagnostic-ledgers CODE/Results/_step5_diagnostic/<run_id>/ledgers.json \
  --diagnostic-receipt CODE/Results/_step5_diagnostic/<run_id>/receipt.json \
  --decision-log       CODE/Results/_step5_diagnostic/<run_id>/decisions.jsonl \
  --out                CODE/Results/<run_id>/step5-witness.json
```

**Admissibility rule (fail-loud).** `witness.admissible` is true only when
`trace_sha256` AND the sha256 digests of `packet_events`,
`link_service_windows` and `link_available_windows` are all identical between
the formal and the diagnostic run. If any of the four differs,
`admissible = false`, `inadmissible_reason` is populated, the decision-count
witness is **unavailable for that run**, and it must NOT be substituted by
anything weaker. The report still lists the formal per-gap evidence, which is
independent of the witness.

## 6. Post-run cohort verification

```bash
PYTHONPATH=. python3 -m CODE.experiment_platform.v2_analysis \
  --experiment EXPERIMENTS/<EXP> \
  --authorization EXPERIMENTS/<EXP>/authorization.json \
  --out ANALYSIS/<EXP>/v2-paired
```

This is cohort/identity re-verification plus the descriptive paired value of
`e2e_delay_mean_s`. It is **not** the acceptance basis for either evidence
chain; `metrics_independent` is (`CODE/leo_sim/metrics.summarize` is never used
as an acceptance criterion in this batch).

## 7. Artifact persistence

Raw events, config, trace, seed, code SHA, run receipts and the raw JSON
recompute outputs stay in the canonical VM results location
`CODE/Results/<run_id>/` and are **never** committed (`CODE/Results/` is
gitignored). The receipt reports a path + sha256 inventory. No receipt or ledger
key set is modified, no prior evidence file is deleted or moved, and nothing in
this batch restores or claims to restore the lost R02 raw data.
