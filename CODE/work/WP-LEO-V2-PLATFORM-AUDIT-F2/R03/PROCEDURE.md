# PROCEDURE — WP-LEO-V2-PLATFORM-AUDIT-F2 revision 3

Experiment: `EXP-20260924-PLATFORM-AUDIT-F2-R03` (leo_sim_v2, strict 2-cell paired design).

FROZEN PRIOR REVISIONS. Both earlier revisions are preserved byte-for-byte as evidence and must
NOT be edited: `R01/` (revision 1: 1 PASS / 2 BLOCK) and `R02/` (revision 2: 0 PASS / 3 BLOCK).
Revision 3 initially violated this - its own fix commit rewrote revision-2 `PROCEDURE.md`,
`attribute_f2.py`, `brief.json` and request source AFTER the revision-2 receipts were issued -
and those four files have since been RESTORED to the reviewed bytes, so that
`R02/artifact-set.json` verifies again (11/11). One residual divergence is unavoidable and is
declared here: the revision-2 receipts also hash-bound `ANALYSIS/CURRENT-EVENT-TIMELINE.md`, and
the revision-2 adversarial review *required* that document to be corrected (section 5 was
stale), so restoring it would undo a required fix.
Revision 1 of this work package was **BLOCKed** by the satellite_drl and adversarial roles
(and PASSed by cold_start). This revision is the response; the revision-1 artifacts
(`...-R01`) are preserved unchanged as revision-1 evidence.

## 0. Declared scope — what this batch does and does NOT establish

DELIVERED (verifiable by the commands below):
- the compiled matrix (`errors=[]`, `execution_authorized=false`);
- `authorization.json` with `status=AUTHORIZED` over exactly the two compiled cells;
- for each arm a natural-end receipt + `formal_run.json`, plus the f2 arm timeline stream
  and its sha256-bound sidecar manifest;
- an explicit independent delay decomposition (step 5b) showing `node_process_s` and
  `decision_compute_s` as separate named terms, with the discriminating unlabelled-vs-labelled check.

NOT DELIVERED — declared limitations, not omissions:
- **The canonical remote execution.** F2 requires a timeline sink (`kernel.py:1145-1152`);
  `CODE/scripts/remote/remote_job.py:304-309` builds the child command from exactly
  `--config --out --authorization --launch-nonce --expect-run-id` and never passes one, and
  `run-remote.sh:44` rejects `--` outright. Both the satellite_drl and adversarial reviewers
  independently tried to find a passthrough and found none. Runs here therefore use the LOCAL
  CLI with the same formal binding flags (step 4b): a degraded substitute that exercises the
  authorization verifier and writes `formal_run.json`, but carries NO VM provenance.
- **The analysis leg.** `CODE/experiment_platform/v2_analysis.py:410-415` requires
  `governance_receipt.json`, which only `remote_job.py` writes (`:132`, `:678`, `:700`), and v5
  receipts force a VM external launch witness (`:440-442`, `:494-497`, `:922`). A locally
  executed cell therefore CANNOT be verified by `v2_analysis`. This batch does NOT run it and
  does NOT claim the chain closes through analysis.
- **The F2 separation is not in `v2_analysis`.** `v2_analysis` does not import
  `metrics_independent` at all. The non-test importers in this repository are this work
  package own `R03/attribute_f2.py` and its frozen revision-2 twin `R02/attribute_f2.py`
  (both added precisely because the analyzer does not do it), plus another work package
  `step5_recompute.py`, which takes no timeline argument.
  The separation is produced by step 5b below, on purpose and visibly, not by the analyzer.

## 1. Compile

```bash
PYTHONPATH=. python3 CODE/experiment_platform/compile_matrix_experiment.py \
  EXPERIMENTS/request-sources/EXP-20260924-PLATFORM-AUDIT-F2-R03.json \
  --out EXPERIMENTS/EXP-20260924-PLATFORM-AUDIT-F2-R03 --root .
```

Sentinel: `compile-report.json` `status=COMPILED_REVIEW_REQUIRED`, `errors=[]`,
`execution_authorized=false`. The two resolved configs must differ in EXACTLY one leaf,
`execution.node_process_delay_s` (0 vs 0.05); `execution.compute_delay_s` is 0.05 in BOTH
arms and is deliberately shared so the decision stage is active and the
`decision_compute_s` separation is discriminating rather than `0 == 0`.

## 2. Review -> decision -> finalization

Three separate cold-start sessions (independence = distinct session ids only; see the brief
independence disclosure) produce `cold_start`, `satellite_drl` and `adversarial`
receipts (`agent-review-receipt/v2`), each binding the 11 hashes in `artifact-set.json`.
Receipts are copied into `CODE/work/WP-LEO-V2-PLATFORM-AUDIT-F2/R03/` and bound there as
repo-relative paths in `decision.json.applied_review_receipts`;
`decision.json` is authored by the producer as `agent-work-decision/v1` (schema
`CODE/work/decision.schema.json`, example `CODE/work/decision.example.json`). For an ACCEPT it
must carry `applied_review_receipts` (all PASS), empty `blocking_findings`, empty
`revision_instructions`, `next_revision: null`, and an `artifact_hashes` map covering the
reviewed set. `finalize_decision.py:43-51` rejects receipt paths outside the repository, and
`finalize_decision.py:175-183` requires all three roles PASS with distinct reviewer sessions
and no duplicate `receipt_id`/`reviewer_id`.

```bash
python3 CODE/work/finalize_decision.py \
  --brief  CODE/work/WP-LEO-V2-PLATFORM-AUDIT-F2/R03/brief.json \
  --decision CODE/work/WP-LEO-V2-PLATFORM-AUDIT-F2/R03/decision.json \
  --out CODE/work/WP-LEO-V2-PLATFORM-AUDIT-F2/R03/finalization.json
```

## 3. Authorize

```bash
python3 CODE/experiment_platform/authorize_experiment.py \
  --experiment EXPERIMENTS/EXP-20260924-PLATFORM-AUDIT-F2-R03 \
  --finalization CODE/work/WP-LEO-V2-PLATFORM-AUDIT-F2/R03/finalization.json \
  --out EXPERIMENTS/EXP-20260924-PLATFORM-AUDIT-F2-R03/authorization.json
```

Sentinel: `status=AUTHORIZED`, `authorized_runs` = exactly the two compiled run ids,
`execution_authorized` in the compile report staying `false` (it describes the DESIGN, not the
run permission — see the audit report limitation L19).

## 4. Run

### 4a. Canonical remote runner (NOT usable here; declared limitation)

```bash
CODE/scripts/remote/run-remote.sh --runtime-kind leo_sim_v2 \
  --config EXPERIMENTS/EXP-20260924-PLATFORM-AUDIT-F2-R03/resolved/EXP-20260924-PLATFORM-AUDIT-F2-R03-f2-s7.leo-sim.yaml \
  --authorization EXPERIMENTS/EXP-20260924-PLATFORM-AUDIT-F2-R03/authorization.json \
  --session exp-20260924-platform-audit-f2-r02-f2-s7
```

This fails loud for the f2 arm (`RUN REFUSED (F2 attributable timing)`) because no timeline
stream can be supplied through the formal runner.

### 4b. Local formal-flag execution (the route actually used)

The `--timeline-log` parent directory must ALREADY EXIST and must not sit under a
symlinked path (`__main__.py:33-49` refuses otherwise; `/tmp` is a symlink on macOS and is
therefore always refused). Create it first, and create it for BOTH arms so the two arms are
observed identically.

```bash
for arm in control f2; do
  mkdir -p CODE/Results/EXP-20260924-PLATFORM-AUDIT-F2-R03-$arm-s7
  python3 -m CODE.leo_sim run \
    --config EXPERIMENTS/EXP-20260924-PLATFORM-AUDIT-F2-R03/resolved/EXP-20260924-PLATFORM-AUDIT-F2-R03-$arm-s7.leo-sim.yaml \
    --out CODE/Results/EXP-20260924-PLATFORM-AUDIT-F2-R03-$arm-s7 \
    --authorization EXPERIMENTS/EXP-20260924-PLATFORM-AUDIT-F2-R03/authorization.json \
    --launch-nonce <32 lowercase hex> \
    --expect-run-id EXP-20260924-PLATFORM-AUDIT-F2-R03-$arm-s7 \
    --timeline-log CODE/Results/EXP-20260924-PLATFORM-AUDIT-F2-R03-$arm-s7/timeline.jsonl
done
```

`--timeline-log` is passed for BOTH arms so the two arms are observed identically (the control
arm is expected to contain zero `node_process_*` milestones; that is the negative control).

## 5. Verify and decompose

```bash
python3 -m CODE.leo_sim receipt verify CODE/Results/EXP-20260924-PLATFORM-AUDIT-F2-R03-control-s7
python3 -m CODE.leo_sim receipt verify CODE/Results/EXP-20260924-PLATFORM-AUDIT-F2-R03-f2-s7
```

### 5b. F2 attribution step (REQUIRED — this is the step revision 1 was missing)

```bash
python3 CODE/work/WP-LEO-V2-PLATFORM-AUDIT-F2/R03/attribute_f2.py \
  --results-root CODE/Results \
  --experiment EXP-20260924-PLATFORM-AUDIT-F2-R03 \
  --compute-delay-s 0.05 \
  --out ANALYSIS/EXP-20260924-PLATFORM-AUDIT-F2-R03/f2-attribution.json
```

The tool reads each arm ledger, folds the arm timeline into per-packet F2 occupancies with
`metrics_independent.node_process_spans`, and runs `verify_delay_decomposition` TWICE per arm:
WITH the spans (labelled) and WITHOUT them (unlabelled).

Before it judges anything, the tool refuses damaged inputs: it reads the delays from EACH ARM
`resolved_config.json` (a `--compute-delay-s` flag is cross-checked against it, never
substituted for it), verifies the timeline sidecar (`row_count`, `log_sha256`) and the receipt
binding, requires the f2 arm to carry a positive occupancy count while the control carries
none, and refuses a run with no delivered packet. `attribute_f2.py --self-test` exercises all
seven refusal controls and prints each result; an untriggered control is an UNPROVEN control.

Sentinel (all must hold):
1. `node_process_spans_supplied` is `true` for both arms;
2. `ok` is `true` and `max_abs_residual_s <= 1e-9` for the labelled run of both arms;
3. per arm, labelled `total_node_process_s` equals the occupancy folded from that arm timeline;
4. per arm, labelled `total_decision_compute_s` is an exact multiple of `compute_delay_s`;
5. the discriminating identity
   `unlabelled_decision_compute_s - labelled_decision_compute_s == total_node_process_s`
   holds to 1e-9 (it is `0` for the control arm by construction);
6. per-link `available_capacity_bits` / `capacity_bits` BETWEEN arms is **reported, not
   asserted equal** — a 0.05 s shift can legitimately move an availability window across the
   0.1 s sampling boundary, and the honest statement is the measured comparison, not equality.

## 6. Mandatory negative controls

| # | Control | Required outcome |
| --- | --- | --- |
| N1 | f2 config with no `--timeline-log` | exit 3, `RUN REFUSED (F2 attributable timing)` |
| N2 | partial formal flag set | exit 3, `formal run requires authorization, launch_nonce and expect_run_id together` |
| N3 | `--expect-run-id` naming the OTHER cell | refusal from `verify_authorization_for_leo_sim_v2_config` |
| N4 | one byte of a `resolved/*.leo-sim.yaml` mutated after authorization | authorization verification refuses |
| N5 | a review receipt downgraded to BLOCK | `finalize_decision.py` emits no finalization |
| N6 | control config + `--dry-run`, no `--timeline-log` | exit 0 `DRY RUN` — proves N1 is not vacuous |

A control that does not trigger is reported as UNPROVEN and is NOT counted as passed.

## 7. What may be claimed from this batch

MAY: the F2 occupancy is independently recorded on the timeline and is NOT counted as decision
computation time; supplied with the spans, the labelled `decision_compute_s` closes to
`decisions x compute_delay_s` per arm; without the spans the uncovered reading exceeds the
labelled one by exactly the node total.

MAY NOT: that the end-to-end delay increase equals the node total. MEASURED ON THIS EXACT R03
DESIGN (both arms `execution.compute_delay_s = 0.05`): the total gain is **0.849200279 s**
against a node total of 0.900 s, because one whole node occupancy is absorbed by a
pre-existing holding/ISL-queue wait — packet 99 carries a 0.15 s occupancy but its e2e rises
only 0.099999964 s while its `holding_wait_s` FALLS by 0.050000000 s, and packet 2 diverts
8.000e-04 s into `queue_wait`. The figure 0.850000245 s belongs to the REVISION-1 design
(`compute_delay_s = 0`) and must never be quoted as this design's measurement.
Additivity holds only on the contention-free unit fixture. Nor may it claim per-link phase
identity between arms, any effect size or causal statement, VM provenance, a completed analysis
leg, or that the `min_delivered_packets` / `min_multisat_deliveries` acceptance values are
enforced on route 4b (they live only in the remote runner).
