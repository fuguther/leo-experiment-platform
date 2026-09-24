# R06 PROCEDURE — reproducible execution and evidence contract

This file is part of the reviewed artifact set of revision R06. Section 5 is **generated**, not
written: it is produced from the compiled run-manifests by
`CODE/work/WP-LEO-V2-T1-STEP5-GATE-EVIDENCE/tools/procedure_commands.py` and must appear here byte-for-byte.

## 0. Revision history (by revision label; no earlier experiment id appears in this document)

- **revision R01**: BLOCKed. The auto-generated RUNBOOK mandated a post-run analysis step whose metric
  whitelist unconditionally rejects the metric the request declared; the recomputation harness had no
  entry point and lay outside the reviewed hash set; its reports were truncated.
- **revision R02**: fixed those, then BLOCKed because this procedure named superseded experiment
  directories — and the launcher derives the run id from the config filename — and because it
  documented no finalization/authorization step although the launcher refuses to start without one.
- **revision R03**: authorised, then invalidated when F2 merged and added the opt-in key
  `execution.node_process_delay_s`, which changes every `config_sha256` (measured: micro control
  `e6b90446…afd2ce` to `63aab057…22d347e`; micro cd05 `2d39a7f2…537d7030` to `869fc3d6…63677119`; corridor
  control `d45c6a50…277fd27f` to `52dfe3d9…4c47a3`; corridor cd05 `49439fe7…45cee45` to `1391843c…8136e82`).
- **revision R04**: introduced the mechanical procedure gate. It BLOCKed on: the gate scanned only
  fenced blocks so an unfenced command passed; a stale pre-F2 corridor value in the brief; and an F2
  disclosure that claimed gate reporting which did not exist.
- **revision R05**: fixed all three. G1 scanned the whole document, yet was still evaded: an executable
  backslash-continued unfenced command whose id was split across lines passed, and an id split across
  prose lines or written in lower case was not detected at all.
- **revision R06** (this revision): abandons scanning. The run commands are **derived** from the
  run-manifests and the procedure must contain the derived text byte-for-byte (section 3, check G0).
  G1a/G1b remain only as additional hygiene checks and are explicitly NOT the structural guarantee.

## 0b. F2 disclosure (mechanically backed by gate check G5)

The compiled configs contain F2's key and it is **OFF**: gate check **G5** reads each cell's resolved
config and reports the actual `execution.node_process_delay_s` value (currently `0.0` for all four
cells), with a control proving a non-zero value is flagged. **This deployment contains F2 code with F2
disabled.** No request sets that key.

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

Recompute sha256 for **every** record in `deployed_files` on the VM, classify each OK / MISSING / CHANGED,
then recompute the same manifest against the local deployment source and report the mismatch count. The
`.deployment_commit` witness exists only on the VM and is excluded from the local comparison. A
deployment conclusion from an earlier main SHA must never be reused.

## 3. MANDATORY mechanical gate

```bash
cd <repo root>
PYTHONPATH=. python3 CODE/work/WP-LEO-V2-T1-STEP5-GATE-EVIDENCE/tools/check_procedure.py \
  --root . \
  --procedure CODE/work/WP-LEO-V2-T1-STEP5-GATE-EVIDENCE/R06/PROCEDURE.md \
  --artifact-set CODE/work/WP-LEO-V2-T1-STEP5-GATE-EVIDENCE/R06/artifact-set.json \
  --experiment-id EXP-20260923-T1-STEP5-MICRO-R06 \
  --experiment-id EXP-20260923-T1-STEP5-PRESSURE-R06 \
  --phase pre-review \
  --out CODE/Results/_step5_diagnostic/procedure-gate-pre-review.json
```

Expected sentinel: `{"all_checks_ok": true, ...}` and exit code 0.

**G0 is the structural guarantee of this revision.** It does not scan prose at all. The gate calls the
reviewed generator `tools/procedure_commands.py` to derive the four run commands from the compiled
run-manifests, and requires this document to **contain the derived marked block byte-for-byte**. A
command is thus reproduced rather than checked: fenced, unfenced, line-split, re-cased, variable-built
or one-character-edited variants simply are not the generated text and fail. G0's negative control
edits **one character** inside the generated block and requires detection, completely removes the
markers and requires detection, and the control result is printed on every run. A control that does not
trigger makes G0 an UNPROVEN GATE, which cannot be counted as passed.

### G0 GUARANTEE BOUNDARY (verbatim; do not widen)

- G0 GUARANTEES that this PROCEDURE contains, byte-for-byte, the four commands derived from the
  compiled run-manifests, with no deviation anywhere inside that derived block.
- G0 does NOT guarantee and does NOT claim that any other command text elsewhere in this document is
  covered. The initiator's boundary sentence, quoted verbatim: "派生块之外的命令文本不受本保证约束"
  (command text outside the derived block is NOT covered by this guarantee).

Any statement presenting G0 as "no wrong command can appear in the document" is an over-claim and
is forbidden. This boundary is stated together with the demotion of G1a/G1b: those are HYGIENE
(additional, cheap sanity checks), they are explicitly NOT the structural guarantee, they catch only
realistic accidental drift, and they are known to be evadable by line-splitting and re-casing.

### FINDINGS SELF-DISCOVERED BY THE PRODUCER (both fixed; recorded as findings)

1. **End-to-end single-byte mutation, measured rather than simulated.** Editing exactly one byte of the
   generated block in the real procedure file (offset 9719, character "6" changed to "7") made the gate
   exit 1 with G0 failed. The file was then restored byte-identically
   (sha256 e936b2db32d836982f68507536cdb7aa49ea88ff7005095aabe6376ee2fca317) and the gate returned green.
2. **A G0 control that could never trigger.** The first marker-removal control stripped the markers off
   the generated BLOCK; the remaining body is exactly what legitimately appears inside the marked
   region, so the control could never fire - and G0 was reported as an UNPROVEN GATE instead of passing.
   It was corrected to strip the marker LINES FROM THE DOCUMENT and require the containment check to
   fail. The unproven-gate rule is what surfaced this second defect. Both were found and fixed by the
   producer.

The remaining checks are supporting, not structural:

- **G1a / G1b (HYGIENE ONLY, explicitly NOT the structural guarantee)** — G1a scans the whole document
  for a foreign experiment id and G1b flags command-like run-remote lines outside a fence. Both are
  known to be evadable by line-splitting and re-casing; they are retained because they catch realistic
  accidental drift cheaply, and their negative controls are still required to trigger.
- **G2** the declared `primary_metric` must actually be accepted by the analyzer dispatch, not by
  reading code. Control: an unsupported metric name must be REJECTED.
- **G3** (`--phase all`) the chain must reach the launcher accept/reject decision point **using the
  derived commands**: for every cell the gate parses the generated block, then runs the exact module
  `run-remote.sh` invokes (`v2_serial_gate`) and the exact authorization-binding check the launcher
  performs, with the config path, authorization path and run id taken from the generated command.
  Control: a wrong run id must be REJECTED. This proves the derived commands are themselves acceptable,
  not merely that the document matches the generator.
- **G4** the gate, the generator and this procedure must be inside the reviewed artifact set. Control: a
  synthetic set with a zeroed gate hash must report a problem.
- **G5** every cell's resolved config must have F2 disabled, with the actual value reported. Control: a
  synthetic config with `node_process_delay_s = 0.5` must be flagged.

**Negative controls are mandatory in every check and are printed on every run.** A check whose control
does not trigger is reported as an **UNPROVEN GATE**, listed in `unproven_gates`, and is not counted as
passed: `all_checks_ok` is true only when no executed check failed AND none is unproven. Checks that
cannot run yet appear in `skipped_checks` and are likewise not counted as passed.

## 4. Reviews -> decision -> finalization -> authorization

```bash
# (a) three independent cold-start review sessions write, under CODE/work/WP-LEO-V2-T1-STEP5-GATE-EVIDENCE/R06/:
#     review-cold-start.json, review-satellite-drl.json, review-adversarial.json
#     Each receipt must bind EVERY key of CODE/work/WP-LEO-V2-T1-STEP5-GATE-EVIDENCE/R06/artifact-set.json.

# (b) the decision maker writes CODE/work/WP-LEO-V2-T1-STEP5-GATE-EVIDENCE/R06/decision.json
#     (schema agent-work-decision/v1, decision = ACCEPT, applying the three PASS receipts).

# (c) materialise the ACCEPTED finalization:
python3 CODE/work/finalize_decision.py \
  --brief CODE/work/WP-LEO-V2-T1-STEP5-GATE-EVIDENCE/R06/brief.json \
  --decision CODE/work/WP-LEO-V2-T1-STEP5-GATE-EVIDENCE/R06/decision.json \
  --out CODE/work/WP-LEO-V2-T1-STEP5-GATE-EVIDENCE/R06/finalization.json

# (d) derive the execution credential for BOTH experiments from that one finalization:
python3 CODE/experiment_platform/authorize_experiment.py \
  --experiment EXPERIMENTS/EXP-20260923-T1-STEP5-MICRO-R06 \
  --finalization CODE/work/WP-LEO-V2-T1-STEP5-GATE-EVIDENCE/R06/finalization.json \
  --out EXPERIMENTS/EXP-20260923-T1-STEP5-MICRO-R06/authorization.json

python3 CODE/experiment_platform/authorize_experiment.py \
  --experiment EXPERIMENTS/EXP-20260923-T1-STEP5-PRESSURE-R06 \
  --finalization CODE/work/WP-LEO-V2-T1-STEP5-GATE-EVIDENCE/R06/finalization.json \
  --out EXPERIMENTS/EXP-20260923-T1-STEP5-PRESSURE-R06/authorization.json
```

Expected sentinels: `finalize_decision.py` prints `ACCEPTED`; `authorize_experiment.py` prints
`AUTHORIZED: <experiment_id> (2 runs)`. Both exit 2 with a `BLOCK:` line and write nothing on failure.
`authorization.json` must be a direct child of its experiment directory, and each `request.json`'s
`work_finalization` must equal `CODE/work/WP-LEO-V2-T1-STEP5-GATE-EVIDENCE/R06/finalization.json` exactly. Neither request declares
`execution_policy`, so the serial predecessor gate is a documented no-op.

## 5. Formal runs (GENERATED - reproduced, not checked)

These commands are generated from the compiled run-manifests by
CODE/work/WP-LEO-V2-T1-STEP5-GATE-EVIDENCE/tools/procedure_commands.py and must appear
here byte-for-byte between the markers. Editing them by even one character fails gate
check G0, which is the structural guarantee of this revision.

<!-- GENERATED-RUN-COMMANDS:BEGIN (do not edit by hand) -->
```bash
CODE/scripts/remote/run-remote.sh --runtime-kind leo_sim_v2 \
  --config EXPERIMENTS/EXP-20260923-T1-STEP5-MICRO-R06/resolved/EXP-20260923-T1-STEP5-MICRO-R06-control-s7.leo-sim.yaml \
  --authorization EXPERIMENTS/EXP-20260923-T1-STEP5-MICRO-R06/authorization.json \
  --session exp-20260923-t1-step5-micro-r06-control-s7
```

```bash
CODE/scripts/remote/run-remote.sh --runtime-kind leo_sim_v2 \
  --config EXPERIMENTS/EXP-20260923-T1-STEP5-MICRO-R06/resolved/EXP-20260923-T1-STEP5-MICRO-R06-cd05-s7.leo-sim.yaml \
  --authorization EXPERIMENTS/EXP-20260923-T1-STEP5-MICRO-R06/authorization.json \
  --session exp-20260923-t1-step5-micro-r06-cd05-s7
```

```bash
CODE/scripts/remote/run-remote.sh --runtime-kind leo_sim_v2 \
  --config EXPERIMENTS/EXP-20260923-T1-STEP5-PRESSURE-R06/resolved/EXP-20260923-T1-STEP5-PRESSURE-R06-control-s7.leo-sim.yaml \
  --authorization EXPERIMENTS/EXP-20260923-T1-STEP5-PRESSURE-R06/authorization.json \
  --session exp-20260923-t1-step5-pressure-r06-control-s7
```

```bash
CODE/scripts/remote/run-remote.sh --runtime-kind leo_sim_v2 \
  --config EXPERIMENTS/EXP-20260923-T1-STEP5-PRESSURE-R06/resolved/EXP-20260923-T1-STEP5-PRESSURE-R06-cd05-s7.leo-sim.yaml \
  --authorization EXPERIMENTS/EXP-20260923-T1-STEP5-PRESSURE-R06/authorization.json \
  --session exp-20260923-t1-step5-pressure-r06-cd05-s7
```
<!-- GENERATED-RUN-COMMANDS:END -->

## 6. Independent recomputation (chain 1 + chain 2)

Run **on the VM** from the deployed workspace root. `<run_id>` is one of the four ids in section 5; `<cd>`
is `0.0` for the `control` cells and `0.05` for the `cd05` cells.

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

The independent module does not enforce `end-start == served/rate` for an `ok` service window, so time
can hide inside a covered transmission span while every uncovered-interval check still passes (measured:
a 1.5 s window for 1000 bit at 1000 bps, needing only 1.0 s, yielded 0 gap violations, 0 closure errors
and residual 0.0). That finding is registered and deliberately **not repaired**, because it is bound
into reviewed bytes. Consumers must apply all three of:

1. require `witness.delivered_gap_reconciliation_all_agree == true` **and**
   `witness.delivered_packets_checked` equal to the run's delivered-packet count;
2. read `witness.identity_checks` values, not merely `admissible`;
3. cross-check every `--cd` against the cell's **bound** resolved config before accepting any verdict:
   the config's `execution.compute_delay_s` must equal the `--cd` used. The reported verdict depends on
   `--cd`: the same data yields a violation at `--cd=0.05` and none at `--cd=0.5`.

The per-gap claim stays scoped to **delivered** packets; packets never delivered have no delivery
interval and therefore no uncovered interval at all.

## 7. Substituted decision-count witness (admissible only if proven identical)

A formal authorized run cannot carry `--decision-log` (the run entry point refuses it for formal runs
and the remote launcher never passes it), so no formal artifact contains a decision row. The substitute
is a deterministic **non-formal** re-execution on the SAME VM, same commit, same resolved config, same
seed:

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
