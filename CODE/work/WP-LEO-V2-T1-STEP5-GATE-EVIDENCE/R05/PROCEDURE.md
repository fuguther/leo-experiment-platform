# R05 PROCEDURE — reproducible execution and evidence contract

This file is part of the reviewed artifact set of revision R05. Every command is literal and every
experiment id is this revision's own id. This document deliberately contains **no** experiment id from
an earlier revision anywhere, in or out of a command block, because the gate scans the whole document
text for that (see section 3, G1a).

## 0. Revision history (referenced by revision label only, never by full experiment id)

- **revision R01** (earlier main): BLOCKed by an independent cold-start review. Its auto-generated
  RUNBOOK mandated a post-run analysis step whose metric whitelist unconditionally rejects the metric
  the request declared, so the documented flow could not complete; the recomputation harness had no
  entry point and lay outside the reviewed hash set; its reports were truncated.
- **revision R02**: fixed those three, but was BLOCKed again: this procedure named superseded
  experiment directories, and because the launcher derives the run id from the config filename,
  following it would have executed the wrong revision; and it documented no
  finalization/authorization step although the launcher refuses to start without an authorization.
- **revision R03**: reviewed and authorised on an earlier main, then invalidated when F2 merged and
  added the opt-in key `execution.node_process_delay_s`, which changes **every** `config_sha256`. Measured on
  byte-unchanged resolved configs, all four config hashes changed (micro control
  `e6b90446…afd2ce` to `63aab057…22d347e`; micro cd05 `2d39a7f2…537d7030` to `869fc3d6…63677119`;
  corridor control `d45c6a50…277fd27f` to `52dfe3d9…4c47a3`; corridor cd05 `49439fe7…45cee45` to
  `1391843c…8136e82`). `verify_compiled_matrix` then failed for all six earlier experiments and
  `verify_authorization` failed on both earlier authorization files, so that PR was closed unmerged as
  superseded with every artifact, receipt and measurement preserved.
- **revision R04** (new cycle on the F2 main): the mechanical procedure gate was introduced. It passed
  pre-review with every negative control triggered, and two of three reviewers returned PASS, but the
  adversarial review BLOCKed it with three findings, all re-verified as real: (1) the gate's G1 scanned
  only backtick-fenced bash blocks, so a complete run command placed in unfenced prose was ACCEPTED;
  (2) `brief.json` still asserted the pre-F2 corridor equivalence value; (3) the F2 disclosure claimed
  gate reporting that did not exist.
- **revision R05** (this revision): fixes all three. G1a now scans the **whole document text** with no
  fence exemption and its control injects a foreign id into unfenced text; G1b fails loud on any
  command-like run-remote line outside a fence and its control injects exactly that; a new check G5
  reports each cell's **actual resolved** `execution.node_process_delay_s` so the F2 disclosure is
  mechanically backed, with a control proving a non-zero value is flagged; G4 no longer crashes when the
  gate is invoked from outside the repository root.

## 0b. F2 disclosure (mechanically backed by gate check G5)

The compiled configs contain F2's key and it is **OFF**. Gate check **G5** reads each cell's resolved
config and reports the actual `execution.node_process_delay_s` value in its detail block; the current
report shows `0.0` for all four cells, and G5's negative control proves the same predicate flags a
non-zero value. **This deployment contains F2 code with F2 disabled.** No request sets that key.

## 1. Deployment

Run from a **main-branch checkout** (the push script requires a real `.git` directory, so a linked
worktree cannot be the deployment source, and it needs the gitignored
`CODE/scripts/remote/remote.env`):

```bash
cd <main checkout>
git fetch origin
git merge --ff-only origin/main              # forward-only; never reset/checkout
CODE/scripts/remote/push-remote.sh           # takes no arguments
```

Record `source_git_commit`, `source_git_branch`, the local `source_tree_sha256` / `file_count`, and the
remote `receipt_sha256`. Verify the deployed commit equals the current `origin/main`.

## 2. Deployment verification (VM side, per-file recompute)

```bash
ssh <vm> 'cat "/data/论文/leo-direct-sim/.deployment_commit"'
ssh <vm> 'cat "/data/论文/leo-direct-sim/.remote_runtime/deployment.json"' > /tmp/deployment.json
```

Recompute sha256 for **every** record in `deployed_files` on the VM and classify each OK / MISSING /
CHANGED, then recompute the same manifest against the local deployment source and report the mismatch
count. The `.deployment_commit` witness exists only on the VM and is excluded from the local comparison.
A deployment conclusion from an earlier main SHA must never be reused.

## 3. MANDATORY mechanical executable-procedure gate

This is the positive fix for the defect class that four review rounds kept finding. **Run it BEFORE
requesting review and again AFTER the authorisation exists. Any failure means the revision is REVISEd
and must not reach a formal run.**

```bash
cd <repo root>
PYTHONPATH=. python3 CODE/work/WP-LEO-V2-T1-STEP5-GATE-EVIDENCE/tools/check_procedure.py \
  --root . \
  --procedure CODE/work/WP-LEO-V2-T1-STEP5-GATE-EVIDENCE/R05/PROCEDURE.md \
  --artifact-set CODE/work/WP-LEO-V2-T1-STEP5-GATE-EVIDENCE/R05/artifact-set.json \
  --experiment-id EXP-20260923-T1-STEP5-MICRO-R05 \
  --experiment-id EXP-20260923-T1-STEP5-PRESSURE-R05 \
  --phase pre-review \
  --out CODE/Results/_step5_diagnostic/procedure-gate-pre-review.json
```

Expected sentinel: `{"all_checks_ok": true, ...}` and exit code 0. Checks:

- **G1a** NO line of this document, fenced or not, may contain an experiment id from another revision.
  The whole text is scanned with no fence exemption. Control: a foreign id injected into **unfenced**
  text must be detected.
- **G1b** every command-like `run-remote.sh` invocation anywhere in the document must sit inside a fenced
  block that parses to a compiled cell of this revision, and the documented invocations must equal the
  compiled cells (config path, authorization path, session). Control: an unfenced complete invocation
  must be reported.
- **G2** the declared `primary_metric` must actually be accepted by the analyzer dispatch
  (`v2_analysis._metric_from_result`), not by reading code. Control: an unsupported metric name must be
  REJECTED.
- **G4** the gate and this procedure must themselves be in the reviewed artifact set. Control: a
  synthetic set with a zeroed gate hash must report a problem.
- **G5** every cell's resolved config must have F2 disabled, and the actual value must be reported.
  Control: a synthetic config with `node_process_delay_s = 0.5` must be flagged.

**Negative controls are mandatory in every check and are printed on every run.** A check whose control
does not trigger is reported as an **UNPROVEN GATE**, listed in `unproven_gates`, and is **not counted
as passed**: `all_checks_ok` is true only when no executed check failed AND none is unproven. Checks that
cannot run yet appear in `skipped_checks` and are likewise not counted as passed.

After the authorisation exists, re-run with `--phase all` to add:

- **G3** the documented chain reaches the launcher accept/reject decision point: for every cell the exact
  module `run-remote.sh` invokes (`v2_serial_gate`) plus the exact authorization-binding check the launcher
  performs must both pass. Control: a wrong run id must be REJECTED.

```bash
PYTHONPATH=. python3 CODE/work/WP-LEO-V2-T1-STEP5-GATE-EVIDENCE/tools/check_procedure.py \
  --root . \
  --procedure CODE/work/WP-LEO-V2-T1-STEP5-GATE-EVIDENCE/R05/PROCEDURE.md \
  --artifact-set CODE/work/WP-LEO-V2-T1-STEP5-GATE-EVIDENCE/R05/artifact-set.json \
  --experiment-id EXP-20260923-T1-STEP5-MICRO-R05 \
  --experiment-id EXP-20260923-T1-STEP5-PRESSURE-R05 \
  --phase all \
  --out CODE/Results/_step5_diagnostic/procedure-gate-all.json
```

## 4. Reviews -> decision -> finalization -> authorization

```bash
# (a) three independent cold-start review sessions write, under CODE/work/WP-LEO-V2-T1-STEP5-GATE-EVIDENCE/R05/:
#     review-cold-start.json, review-satellite-drl.json, review-adversarial.json
#     Each receipt must bind EVERY key of CODE/work/WP-LEO-V2-T1-STEP5-GATE-EVIDENCE/R05/artifact-set.json.

# (b) the decision maker writes CODE/work/WP-LEO-V2-T1-STEP5-GATE-EVIDENCE/R05/decision.json
#     (schema agent-work-decision/v1, decision = ACCEPT, applying the three PASS receipts).

# (c) materialise the ACCEPTED finalization:
python3 CODE/work/finalize_decision.py \
  --brief CODE/work/WP-LEO-V2-T1-STEP5-GATE-EVIDENCE/R05/brief.json \
  --decision CODE/work/WP-LEO-V2-T1-STEP5-GATE-EVIDENCE/R05/decision.json \
  --out CODE/work/WP-LEO-V2-T1-STEP5-GATE-EVIDENCE/R05/finalization.json

# (d) derive the execution credential for BOTH experiments from that one finalization:
python3 CODE/experiment_platform/authorize_experiment.py \
  --experiment EXPERIMENTS/EXP-20260923-T1-STEP5-MICRO-R05 \
  --finalization CODE/work/WP-LEO-V2-T1-STEP5-GATE-EVIDENCE/R05/finalization.json \
  --out EXPERIMENTS/EXP-20260923-T1-STEP5-MICRO-R05/authorization.json

python3 CODE/experiment_platform/authorize_experiment.py \
  --experiment EXPERIMENTS/EXP-20260923-T1-STEP5-PRESSURE-R05 \
  --finalization CODE/work/WP-LEO-V2-T1-STEP5-GATE-EVIDENCE/R05/finalization.json \
  --out EXPERIMENTS/EXP-20260923-T1-STEP5-PRESSURE-R05/authorization.json
```

Expected sentinels: `finalize_decision.py` prints `ACCEPTED`; `authorize_experiment.py` prints
`AUTHORIZED: <experiment_id> (2 runs)`. Both exit 2 with a `BLOCK:` line and write nothing on failure.
`authorization.json` must be a direct child of its experiment directory, and each `request.json`'s
`work_finalization` must equal `CODE/work/WP-LEO-V2-T1-STEP5-GATE-EVIDENCE/R05/finalization.json` exactly. Neither request declares
`execution_policy`, so the serial predecessor gate is a documented no-op and a pair's two cells may be
launched in any order.

## 5. Formal runs

One command per cell. A cell is usable only if the VM run directory `CODE/Results/<run_id>/` contains
`formal_run.json` with `natural_end = true` and `governance_receipt.json` with `research_eligible = true` and
`verification_errors = []`. File presence alone is not admission. The external launch witness is
`CODE/Results/_external_launch_witness/<run_id>.json`.

```bash
CODE/scripts/remote/run-remote.sh --runtime-kind leo_sim_v2 \
  --config EXPERIMENTS/EXP-20260923-T1-STEP5-MICRO-R05/resolved/EXP-20260923-T1-STEP5-MICRO-R05-control-s7.leo-sim.yaml \
  --authorization EXPERIMENTS/EXP-20260923-T1-STEP5-MICRO-R05/authorization.json \
  --session exp-20260923-t1-step5-micro-r05-control-s7
```

```bash
CODE/scripts/remote/run-remote.sh --runtime-kind leo_sim_v2 \
  --config EXPERIMENTS/EXP-20260923-T1-STEP5-MICRO-R05/resolved/EXP-20260923-T1-STEP5-MICRO-R05-cd05-s7.leo-sim.yaml \
  --authorization EXPERIMENTS/EXP-20260923-T1-STEP5-MICRO-R05/authorization.json \
  --session exp-20260923-t1-step5-micro-r05-cd05-s7
```

```bash
CODE/scripts/remote/run-remote.sh --runtime-kind leo_sim_v2 \
  --config EXPERIMENTS/EXP-20260923-T1-STEP5-PRESSURE-R05/resolved/EXP-20260923-T1-STEP5-PRESSURE-R05-control-s7.leo-sim.yaml \
  --authorization EXPERIMENTS/EXP-20260923-T1-STEP5-PRESSURE-R05/authorization.json \
  --session exp-20260923-t1-step5-pressure-r05-control-s7
```

```bash
CODE/scripts/remote/run-remote.sh --runtime-kind leo_sim_v2 \
  --config EXPERIMENTS/EXP-20260923-T1-STEP5-PRESSURE-R05/resolved/EXP-20260923-T1-STEP5-PRESSURE-R05-cd05-s7.leo-sim.yaml \
  --authorization EXPERIMENTS/EXP-20260923-T1-STEP5-PRESSURE-R05/authorization.json \
  --session exp-20260923-t1-step5-pressure-r05-cd05-s7
```

## 6. Independent recomputation (chain 1 + chain 2)

Run **on the VM** from the deployed workspace root. `<run_id>` is one of the four ids above; `<cd>` is `0.0`
for the `control` cells and `0.05` for the `cd05` cells.

```bash
cd "/data/论文/leo-direct-sim"
PYTHONPATH=. python3 CODE/work/WP-LEO-V2-T1-STEP5-GATE-EVIDENCE/tools/step5_recompute.py analyze \
  --run-id <run_id> --cd <cd> \
  --ledgers  CODE/Results/<run_id>/ledgers.json \
  --receipt  CODE/Results/<run_id>/receipt.json \
  --out      CODE/Results/<run_id>/step5-recompute.json
```

The report is exhaustive: every link (including zero-service links) and every delivered packet appears,
and the mismatch lists contain every disagreement with both values.

### 6a. Harness self-test (before trusting any analyze output)

```bash
PYTHONPATH=. python3 CODE/work/WP-LEO-V2-T1-STEP5-GATE-EVIDENCE/tools/step5_recompute.py \
  selftest --out CODE/Results/_step5_diagnostic/selftest.json
```

Exits 0 only if all nine synthetic fail-loud properties hold; the decisive one is that a **merged
interval of 2 x compute_delay_s is REPORTED as a violation** rather than summed into a passing total,
which is why a bare `residual < 1e-9` is not sufficient on its own.

### 6b. MANDATORY backstops for the registered chain-2 blind spot

The independent module does **not** enforce `end-start == served/rate` for an `ok` service window, so time
can hide *inside* a covered transmission span while every uncovered-interval check still passes
(measured: a 1.5 s window for 1000 bit at 1000 bps, needing only 1.0 s, yielded 0 gap violations, 0
closure errors and residual 0.0). That finding is registered and deliberately **not repaired**, because
it is bound into reviewed bytes. Consumers must apply all three of:

1. require `witness.delivered_gap_reconciliation_all_agree == true` **and**
   `witness.delivered_packets_checked` equal to the run's delivered-packet count;
2. read `witness.identity_checks` values, not merely `admissible`;
3. cross-check every `--cd` against the cell's **bound** resolved config before accepting any verdict:
   the config's `execution.compute_delay_s` must equal the `--cd` used. The reported verdict depends on
   `--cd`: the same data yields a violation at `--cd=0.05` and none at `--cd=0.5`.

The per-gap claim stays scoped to **delivered** packets; packets never delivered have no delivery
interval and therefore no uncovered interval at all.

## 7. Substituted decision-count witness (admissible only if proven identical)

A formal authorized run cannot carry `--decision-log` (the run entry point refuses it for formal runs and
the remote launcher never passes it), so no formal artifact contains a decision row. The substitute is a
deterministic **non-formal** re-execution on the SAME VM, same commit, same resolved config, same seed:

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
`link_service_windows` and `link_available_windows` are identical between the formal and the diagnostic run.
Otherwise the decision-count witness is **unavailable for that run** and nothing weaker may be
substituted. A missing or empty decision log is a hard error.

## 8. Post-run cohort verification

```bash
PYTHONPATH=. python3 -m CODE.experiment_platform.v2_analysis \
  --experiment EXPERIMENTS/<EXP> \
  --authorization EXPERIMENTS/<EXP>/authorization.json \
  --out ANALYSIS/<EXP>/v2-paired
```

Cohort/identity re-verification plus the descriptive paired value of `e2e_delay_mean_s`. It is **not** the
acceptance basis for either evidence chain; the independent recomputation is.

## 9. Artifact persistence

Raw events, config, trace, seed, code SHA, run receipts and the raw JSON recompute outputs stay in
`CODE/Results/<run_id>/` and are **never** committed (`CODE/Results/` is gitignored). The receipt reports a
path + sha256 inventory. No receipt or ledger key set is modified, no prior evidence file is deleted or
moved, and nothing in this batch restores or claims to restore lost raw data.
