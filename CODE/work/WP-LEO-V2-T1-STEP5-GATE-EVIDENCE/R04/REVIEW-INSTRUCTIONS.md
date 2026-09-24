# Independent review instructions — WP-LEO-V2-T1-STEP5-GATE-EVIDENCE revision R04 (new cycle)

You are an INDEPENDENT REVIEWER, cold-started in a fresh session. You did not produce these
artifacts and must not trust the producer's prose. Verify against the files.

Repo root: `/Users/lge/Desktop/topic/leo-direct-sim-wt-step5-r04`
Revision under review: `CODE/work/WP-LEO-V2-T1-STEP5-GATE-EVIDENCE/R04/`
Earlier revisions are retained intact: `R01/`, `R02/`, `R03/`.

## Why this is a NEW CYCLE, not a fourth round of R03

F2 (PR #215) merged into main and added `execution.node_process_delay_s`, which changes **every**
`config_sha256`. On byte-unchanged resolved configs all four R03 hashes changed, `verify_compiled_matrix`
failed for all six earlier experiments, and `verify_authorization` failed on both R03
`authorization.json` files, so the old PR #216 was closed unmerged as superseded. R04 recompiles the
same authorised batch under the new main with new `experiment_id`s.

## Absolute constraints

1. You may CREATE exactly one file: your own receipt JSON at the path in your task prompt. Do not
   create, modify, move or delete anything else in the repo. Do not run the simulation. Do not commit.
   (Writing throwaway output under `/tmp` is fine.)
2. Read-only commands otherwise.
3. If a claim in `brief.json` is not supported by the files, that is a finding.

## Read first

- `CODE/work/WP-LEO-V2-T1-STEP5-GATE-EVIDENCE/R04/brief.json`
- `CODE/work/WP-LEO-V2-T1-STEP5-GATE-EVIDENCE/R04/PROCEDURE.md`
- `CODE/work/WP-LEO-V2-T1-STEP5-GATE-EVIDENCE/R04/artifact-set.json` (18 artifacts you must bind)
- `CODE/work/WP-LEO-V2-T1-STEP5-GATE-EVIDENCE/tools/check_procedure.py` (the new gate)
- `CODE/work/WP-LEO-V2-T1-STEP5-GATE-EVIDENCE/tools/step5_recompute.py` (the recomputation harness)
- `EXPERIMENTS/EXP-20260923-T1-STEP5-{MICRO,PRESSURE}-R04/` (compiled docs + `resolved/*`)
- `EXPERIMENTS/request-sources/EXP-20260923-T1-STEP5-{MICRO,PRESSURE}-R04.json`
- `CODE/data/traffic/t1_step5_micro_ab.csv`, `CODE/leo_sim/profiles/t1_pressure_corridor.yaml`

## THE ONE THING THIS REVISION EXISTS FOR — run the gate yourself

```bash
cd /Users/lge/Desktop/topic/leo-direct-sim-wt-step5-r04
PYTHONPATH=. python3 CODE/work/WP-LEO-V2-T1-STEP5-GATE-EVIDENCE/tools/check_procedure.py \
  --root . \
  --procedure CODE/work/WP-LEO-V2-T1-STEP5-GATE-EVIDENCE/R04/PROCEDURE.md \
  --artifact-set CODE/work/WP-LEO-V2-T1-STEP5-GATE-EVIDENCE/R04/artifact-set.json \
  --experiment-id EXP-20260923-T1-STEP5-MICRO-R04 \
  --experiment-id EXP-20260923-T1-STEP5-PRESSURE-R04 \
  --phase pre-review \
  --out /tmp/your-gate-pre-review.json
```

It must exit 0 with `all_checks_ok: true`. Then judge the gate itself — this is the point:

- **G1a** must actually SEE this revision's ids in the command lines, and its **negative control**
  (`G1a_negative_control_scan_is_not_vacuous`) must show that an injected earlier-revision id IS
  detected. A scan that matches nothing would pass vacuously; say so if you find that.
- **G1b** must show the documented run commands are exactly this revision's compiled cells (config
  path, authorization path, session name). Cross-check against both `run-manifest.json` files.
- **G2** must show the declared `primary_metric` is accepted by the actual analyzer dispatch
  (`v2_analysis._metric_from_result`), and should report which metric names it accepts/rejects.
- **G4** must show the gate and the procedure are themselves bound in `artifact-set.json`.
- **G3** is the pre-run phase: after an authorization exists it must reach the launcher accept/reject
  decision point for every cell by running the exact module `run-remote.sh` invokes. You cannot
  run it now (no authorization yet) — judge from code whether it will genuinely do that, and say so.

Three earlier review rounds found the same defect class: process documents never mechanically checked
against the tools they invoke. Judge whether this gate really closes that class or only its examples.

### Negative controls are mandatory - and you must RUN them, not read them

Every check now carries an executed negative control, and the report prints it:

- **G1a** injects a foreign revision id and requires the scan to report it as foreign.
- **G1b** corrupts one documented session name and requires a mismatch.
- **G2** probes an unsupported metric name and requires the analyzer to REJECT it.
- **G3** (phase all) probes a wrong run id and requires a REJECT.
- **G4** checks a synthetic artifact set with a zeroed gate hash and requires a reported problem.

**A check whose control did not trigger is reported as an UNPROVEN GATE, appears in `unproven_gates`,
and must NOT be counted as passed.** Verify that `all_checks_ok` is false whenever a check is unproven or
failed; if you can construct a case where an unproven or failed check still yields `all_checks_ok: true`,
that is a blocking finding. Checks that cannot run yet (G3 before an authorization exists) appear in
`skipped_checks` and are likewise not counted as passed.

Then try to DEFEAT a control yourself: copy the procedure to /tmp, put a stale revision id into a run
command, and confirm the gate (pointed at your copy) reports it; or tamper a session and confirm G1b
fires. Report exactly what you tried and the observed result.

**Disclosure you must check, not trust:** the first version of G1a used a regex that forbade hyphens,
matched none of the real ids, and passed vacuously (measured: real ids seen = `[]`). The producer found
and fixed it before requesting review, and added the control. Confirm the current gate cannot repeat
that: the report must show the real ids it actually saw.

## Also verify yourself

1. **F2 disclosure**: every one of the four R04 resolved configs must carry
   `execution.node_process_delay_s = 0.0` ("F2 code deployed, F2 disabled"). Evidence exists at compile
   time; re-read the configs and confirm the actual value.
2. **Compile cleanliness**: both R04 `compile-report.json`: `COMPILED_REVIEW_REQUIRED`, `errors=[]`,
   `execution_authorized=false`; `request-sources/*.json` byte-equal to the compiled `request.json`.
3. **Paired design**: within each pair the two cells share `trace_identity_sha256` and
   `controlled_signature` but differ in `config_sha256`; the resolved configs differ in exactly the
   `execution.compute_delay_s` line (0.0 vs 0.05) and nothing else.
4. **Corridor profile equivalence**: the corridor control-arm `config_sha256` must equal
   `CODE.leo_sim.config.load_config_file("CODE/leo_sim/profiles/t1_pressure_corridor.yaml")["sha256"]`
   = `52dfe3d92c1e0e6cbd7281c8b4725d9889c5c2376848a22a5b25a980ed4c47a3` (this is the F2-era value);
   the profile file's own sha256 is
   `60bcb666160e72159018feaf2f93c7f2f01888cc06cd4655c04ed5b5aab2dc4e` and `t1_pressure_corridor` is NOT a
   registered named profile (`CODE/leo_sim/config.py` `PROFILES` holds only `smoke`).
5. **Micro honesty**: the micro scenario is NOT a reproduction of the #212 fixture, because
   `tests/helpers.py` `StaticGeometry` scripted geometry cannot be expressed by a formal config
   (`CODE/leo_sim/kernel.py` ~1065 falls back to `model.Constellation`). Check nothing claims otherwise.
6. **Decision-log limitation**: `CODE/leo_sim/__main__.py` ~332 refuses `--decision-log` on a formal run
   and `remote_job.formal_command` never passes it; the substitute witness is admissible only when its
   identity checks all pass.
7. **Harness self-test**: run it (output to /tmp) and confirm 9/9, especially that a merged 2x
   `compute_delay_s` interval is REPORTED as a violation rather than summed into a passing total.
8. **The registered chain-2 blind spot** (`metrics_independent.py:297-363` does not enforce
   `end-start == served/rate`) stays OPEN and unrepaired. Confirm R04/PROCEDURE.md section 6b mandates the
   three backstops (witness reconciliation; read `identity_checks` not just `admissible`; cross-check
   `--cd` against the bound config).
9. **Artifact hashes**: re-hash all 18 entries of `artifact-set.json`.

## Your receipt — EXACTLY these 16 keys, no others (`CODE/work/review-receipt.schema.json`)

```json
{
  "schema": "agent-review-receipt/v2",
  "receipt_id": "<from your task prompt>",
  "work_id": "WP-LEO-V2-T1-STEP5-GATE-EVIDENCE",
  "revision": 4,
  "artifact_hashes": { "<copy every key/value from artifact-set.json verbatim>": "..." },
  "producer_id": "producer:step5-batch-deepseek",
  "producer_session_id": "P-step5-batch-r01",
  "reviewer_id": "<from your task prompt>",
  "reviewer_session_id": "<from your task prompt>",
  "independence": {
    "producer_and_reviewer_are_distinct": true,
    "review_started_from_declared_inputs": true
  },
  "role": "<from your task prompt>",
  "verdict": "PASS | BLOCK | UNKNOWN",
  "evidence": ["non-empty strings citing files/line numbers and what YOU ran"],
  "blocking_findings": [],
  "unknowns": [],
  "required_revision": []
}
```

PASS => `blocking_findings` MUST be `[]`. BLOCK => `blocking_findings` >= 1 AND `required_revision` >= 1.
UNKNOWN => `unknowns` >= 1. `evidence` non-empty, no blank entries. Every key of `artifact-set.json`
must appear in `artifact_hashes` with the identical value.

Write with `json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"`, then print its sha256.
