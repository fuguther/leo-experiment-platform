# R04 PROCEDURE — reproducible execution and evidence contract

This file is part of the reviewed artifact set of revision R04. Every command is literal and every
experiment id is this revision's own id.

## 0. Why R04 exists (new cycle, not a fourth review round of R03)

Revision R03 was reviewed and authorised on main `7c49788`. PR #215 (F2) then merged and added the
opt-in key `execution.node_process_delay_s`, which changes **every** `config_sha256`. Measured on the
identical, byte-unchanged resolved configs:

| run | `config_sha256` under `7c49788` | under `8a25a07` |
|---|---|---|
| MICRO-R03-control-s7 | `e6b90446…afd2ce` | `63aab057…22d347e` |
| MICRO-R03-cd05-s7 | `2d39a7f2…537d7030` | `869fc3d6…63677119` |
| PRESSURE-R03-control-s7 | `d45c6a50…277fd27f` | `52dfe3d9…4c47a3` |
| PRESSURE-R03-cd05-s7 | `49439fe7…45cee45` | `1391843c…8136e82` |

Under F2 code `matrix.verify_compiled_matrix` failed for all six earlier experiments and
`authorize_experiment.verify_authorization` failed on both R03 `authorization.json` files, so those
authorisations are unverifiable on main and were closed unmerged (PR #216, superseded).

R04 therefore recompiles the same authorised batch under the new main with new `experiment_id`s.
`trace_identity_sha256` is unchanged (`a101e0d5…` micro, `4e28833c…` corridor), because trace identity
carries only `execution.max_packets` from the execution group. R01/R02/R03 artifacts and receipts are
preserved untouched; their failure is recorded, not deleted.

## 0b. F2 disclosure

The compiled configs contain F2's key and it is **OFF**: every R04 cell resolves with
`execution.node_process_delay_s = 0.0`. Evidence is printed by the mechanical gate (section 3, check G2
detail) and was additionally recorded at compile time. **This deployment contains F2 code with F2
disabled.** No R04 request sets that key.

## 1. Deployment

Run from a **main-branch checkout** (`push-remote.sh:27` requires a real `.git` directory, so a linked
worktree cannot be the deployment source, and it needs the gitignored
`CODE/scripts/remote/remote.env`):

```bash
cd <main checkout>
git fetch origin
git merge --ff-only origin/main              # forward-only; never reset/checkout
CODE/scripts/remote/push-remote.sh           # takes no arguments
```

Record `source_git_commit`, `source_git_branch`, the local `source_tree_sha256` / `file_count`, and the
remote `receipt_sha256`. Record the deployed commit and verify it equals the current `origin/main`.

## 2. Deployment verification (VM side, per-file recompute)

```bash
ssh <vm> 'cat "/data/论文/leo-direct-sim/.deployment_commit"'
ssh <vm> 'cat "/data/论文/leo-direct-sim/.remote_runtime/deployment.json"' > /tmp/deployment.json
```

Recompute sha256 for **every** record in `deployed_files` on the VM and classify each OK / MISSING /
CHANGED, then recompute the same manifest against the local deployment source and report the
mismatch count. The `.deployment_commit` witness exists only on the VM and is excluded from the local
comparison. A deployment conclusion from an earlier main SHA must never be reused.

## 3. MANDATORY mechanical executable-procedure gate

This is the positive fix for the defect class that three review rounds kept finding: a process
document treated as executable while nothing mechanically compared it to the tools it invokes.
**Run it BEFORE requesting review and again AFTER the authorisation exists. Any failure means the
revision is REVISEd and must not reach a formal run.**

```bash
cd <repo root>
PYTHONPATH=. python3 CODE/work/WP-LEO-V2-T1-STEP5-GATE-EVIDENCE/tools/check_procedure.py \
  --root . \
  --procedure CODE/work/WP-LEO-V2-T1-STEP5-GATE-EVIDENCE/R04/PROCEDURE.md \
  --artifact-set CODE/work/WP-LEO-V2-T1-STEP5-GATE-EVIDENCE/R04/artifact-set.json \
  --experiment-id EXP-20260923-T1-STEP5-MICRO-R04 \
  --experiment-id EXP-20260923-T1-STEP5-PRESSURE-R04 \
  --phase pre-review \
  --out CODE/Results/_step5_diagnostic/procedure-gate-pre-review.json
```

Expected sentinel: `{"all_checks_ok": true, "failed": []}` and exit code 0. It enforces:

- **G1** no run command references any revision other than this one, and the run commands in this
  file are exactly the cells of this revision's compiled run-manifests (config path, authorization
  path and session name all consistent with the compiled `run_id`s);
- **G2** the declared `primary_metric` is actually accepted by the toolchain, proven by invoking
  `v2_analysis._metric_from_result` - the same dispatch the RUNBOOK-mandated analyzer uses - rather
  than by reading code, and it reports which metric names the whitelist accepts and rejects;
- **G4** the gate itself and this procedure are inside the revision's reviewed artifact set, so the
  revision cannot claim compliance it does not have.

After the authorisation exists, re-run with `--phase all` to add:

- **G3** the documented chain reaches the launcher accept/reject decision point: for every cell the
  exact module `run-remote.sh` invokes (`v2_serial_gate`) plus the exact authorization-binding check
  the launcher performs must both pass.

```bash
PYTHONPATH=. python3 CODE/work/WP-LEO-V2-T1-STEP5-GATE-EVIDENCE/tools/check_procedure.py \
  --root . \
  --procedure CODE/work/WP-LEO-V2-T1-STEP5-GATE-EVIDENCE/R04/PROCEDURE.md \
  --artifact-set CODE/work/WP-LEO-V2-T1-STEP5-GATE-EVIDENCE/R04/artifact-set.json \
  --experiment-id EXP-20260923-T1-STEP5-MICRO-R04 \
  --experiment-id EXP-20260923-T1-STEP5-PRESSURE-R04 \
  --phase all \
  --out CODE/Results/_step5_diagnostic/procedure-gate-all.json
```

## 4. Reviews -> decision -> finalization -> authorization

Ordering is fixed and comes before any deployment or run.

```bash
# (a) three independent cold-start review sessions write, under CODE/work/WP-LEO-V2-T1-STEP5-GATE-EVIDENCE/R04/:
#     review-cold-start.json, review-satellite-drl.json, review-adversarial.json
#     Each receipt must bind EVERY key of CODE/work/WP-LEO-V2-T1-STEP5-GATE-EVIDENCE/R04/artifact-set.json.

# (b) the decision maker writes CODE/work/WP-LEO-V2-T1-STEP5-GATE-EVIDENCE/R04/decision.json
#     (schema agent-work-decision/v1, decision = ACCEPT, applying the three PASS receipts).

# (c) materialise the ACCEPTED finalization:
python3 CODE/work/finalize_decision.py \
  --brief CODE/work/WP-LEO-V2-T1-STEP5-GATE-EVIDENCE/R04/brief.json \
  --decision CODE/work/WP-LEO-V2-T1-STEP5-GATE-EVIDENCE/R04/decision.json \
  --out CODE/work/WP-LEO-V2-T1-STEP5-GATE-EVIDENCE/R04/finalization.json

# (d) derive the execution credential for BOTH experiments from that one finalization:
python3 CODE/experiment_platform/authorize_experiment.py \
  --experiment EXPERIMENTS/EXP-20260923-T1-STEP5-MICRO-R04 \
  --finalization CODE/work/WP-LEO-V2-T1-STEP5-GATE-EVIDENCE/R04/finalization.json \
  --out EXPERIMENTS/EXP-20260923-T1-STEP5-MICRO-R04/authorization.json

python3 CODE/experiment_platform/authorize_experiment.py \
  --experiment EXPERIMENTS/EXP-20260923-T1-STEP5-PRESSURE-R04 \
  --finalization CODE/work/WP-LEO-V2-T1-STEP5-GATE-EVIDENCE/R04/finalization.json \
  --out EXPERIMENTS/EXP-20260923-T1-STEP5-PRESSURE-R04/authorization.json
```

Expected sentinels: `finalize_decision.py` prints `ACCEPTED`; `authorize_experiment.py` prints
`AUTHORIZED: <experiment_id> (2 runs)`. Both exit 2 with a `BLOCK:` line and write nothing on failure.
`authorization.json` must be a direct child of its experiment directory (`v2_serial_gate.py:48-51`),
and each `request.json`'s `work_finalization` must equal
`CODE/work/WP-LEO-V2-T1-STEP5-GATE-EVIDENCE/R04/finalization.json` exactly (`authorize_experiment.py:411-415`). Neither request
declares `execution_policy`, so the serial predecessor gate is a documented no-op
(`v2_serial_gate.py:52-55`) and a pair's two cells may be launched in any order.

## 5. Formal runs

One command per cell. A cell is usable only if the VM run directory `CODE/Results/<run_id>/` contains
`formal_run.json` with `natural_end = true` and `governance_receipt.json` with `research_eligible = true` and
`verification_errors = []`. File presence alone is not admission. The external launch witness is
`CODE/Results/_external_launch_witness/<run_id>.json`.

```bash
CODE/scripts/remote/run-remote.sh --runtime-kind leo_sim_v2 \
  --config EXPERIMENTS/EXP-20260923-T1-STEP5-MICRO-R04/resolved/EXP-20260923-T1-STEP5-MICRO-R04-control-s7.leo-sim.yaml \
  --authorization EXPERIMENTS/EXP-20260923-T1-STEP5-MICRO-R04/authorization.json \
  --session exp-20260923-t1-step5-micro-r04-control-s7
```

```bash
CODE/scripts/remote/run-remote.sh --runtime-kind leo_sim_v2 \
  --config EXPERIMENTS/EXP-20260923-T1-STEP5-MICRO-R04/resolved/EXP-20260923-T1-STEP5-MICRO-R04-cd05-s7.leo-sim.yaml \
  --authorization EXPERIMENTS/EXP-20260923-T1-STEP5-MICRO-R04/authorization.json \
  --session exp-20260923-t1-step5-micro-r04-cd05-s7
```

```bash
CODE/scripts/remote/run-remote.sh --runtime-kind leo_sim_v2 \
  --config EXPERIMENTS/EXP-20260923-T1-STEP5-PRESSURE-R04/resolved/EXP-20260923-T1-STEP5-PRESSURE-R04-control-s7.leo-sim.yaml \
  --authorization EXPERIMENTS/EXP-20260923-T1-STEP5-PRESSURE-R04/authorization.json \
  --session exp-20260923-t1-step5-pressure-r04-control-s7
```

```bash
CODE/scripts/remote/run-remote.sh --runtime-kind leo_sim_v2 \
  --config EXPERIMENTS/EXP-20260923-T1-STEP5-PRESSURE-R04/resolved/EXP-20260923-T1-STEP5-PRESSURE-R04-cd05-s7.leo-sim.yaml \
  --authorization EXPERIMENTS/EXP-20260923-T1-STEP5-PRESSURE-R04/authorization.json \
  --session exp-20260923-t1-step5-pressure-r04-cd05-s7
```

## 6. Independent recomputation (chain 1 + chain 2)

Run **on the VM** from the deployed workspace root so raw outputs stay in the canonical results
location. `<run_id>` is one of the four ids above; `<cd>` is `0.0` for the `control` cells and `0.05` for
the `cd05` cells.

```bash
cd "/data/论文/leo-direct-sim"
PYTHONPATH=. python3 CODE/work/WP-LEO-V2-T1-STEP5-GATE-EVIDENCE/tools/step5_recompute.py analyze \
  --run-id <run_id> --cd <cd> \
  --ledgers  CODE/Results/<run_id>/ledgers.json \
  --receipt  CODE/Results/<run_id>/receipt.json \
  --out      CODE/Results/<run_id>/step5-recompute.json
```

The report is exhaustive: every link (including zero-service links) and every delivered packet
appears, and `chain1_link_utilization.mismatches` /
`chain2_delay_decomposition.production_phase_mismatches` list every disagreement with both values.

### 6a. Harness self-test (before trusting any analyze output)

```bash
PYTHONPATH=. python3 CODE/work/WP-LEO-V2-T1-STEP5-GATE-EVIDENCE/tools/step5_recompute.py \
  selftest --out CODE/Results/_step5_diagnostic/selftest.json
```

Exits 0 only if all nine synthetic fail-loud properties hold; the decisive one is that a **merged
interval of 2 x compute_delay_s is REPORTED as a violation** rather than summed into a passing
total, which is why a bare `residual < 1e-9` is not sufficient on its own.

### 6b. MANDATORY backstops for the registered chain-2 blind spot

`metrics_independent.py:297-363` does **not** enforce `end-start == served/rate` for an `ok` service
window, so time can hide *inside* a covered transmission span while every uncovered-interval check
still passes (measured: a 1.5 s window for 1000 bit at 1000 bps, needing only 1.0 s, yielded 0 gap
violations, 0 closure errors and residual 0.0). That finding is registered and deliberately **not
repaired**, because it is bound into reviewed bytes. Consumers must therefore apply all three of:

1. require `witness.delivered_gap_reconciliation_all_agree == true` **and**
   `witness.delivered_packets_checked` equal to the run's delivered-packet count (the witness is the
   only backstop against a padded service window);
2. read `witness.identity_checks` values, not merely `admissible`, since digest identity can be
   vacuous if a caller supplies explicit trace arguments;
3. cross-check every `--cd` against the cell's **bound** resolved config before accepting any
   verdict: `python3 -c "import yaml,sys; print(yaml.safe_load(open(sys.argv[1]))['execution']['compute_delay_s'])" <config>`
   must equal the `--cd` used. The reported verdict depends on `--cd`: the same data yields a
   violation at `--cd=0.05` and none at `--cd=0.5`.

The per-gap claim stays scoped to **delivered** packets; packets that are never delivered have no
delivery interval and therefore no uncovered interval at all.

## 7. Substituted decision-count witness (admissible only if proven identical)

A formal authorized run cannot carry `--decision-log` (`CODE/leo_sim/__main__.py:332-335` refuses it;
`remote_job.formal_command` at `remote_job.py:300-310` never passes it), so no formal artifact contains a
decision row. The substitute is a deterministic **non-formal** re-execution on the SAME VM, same
commit, same resolved config, same seed:

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

`admissible` is true only when `trace_sha256` and the sha256 digests of `packet_events`,
`link_service_windows` and `link_available_windows` are all identical between the formal and the
diagnostic run. Otherwise the decision-count witness is **unavailable for that run** and nothing
weaker may be substituted. A missing or empty decision log is a hard error.

## 8. Post-run cohort verification

```bash
PYTHONPATH=. python3 -m CODE.experiment_platform.v2_analysis \
  --experiment EXPERIMENTS/<EXP> \
  --authorization EXPERIMENTS/<EXP>/authorization.json \
  --out ANALYSIS/<EXP>/v2-paired
```

Cohort/identity re-verification plus the descriptive paired value of `e2e_delay_mean_s`. It is **not**
the acceptance basis for either evidence chain; `metrics_independent` is, and
`CODE/leo_sim/metrics.summarize` is never used as an acceptance criterion in this batch.

## 9. Artifact persistence

Raw events, config, trace, seed, code SHA, run receipts and the raw JSON recompute outputs stay in
`CODE/Results/<run_id>/` and are **never** committed (`CODE/Results/` is gitignored). The receipt reports a
path + sha256 inventory. No receipt or ledger key set is modified, no prior evidence file is deleted
or moved, and nothing in this batch restores or claims to restore the lost R02 raw data.
