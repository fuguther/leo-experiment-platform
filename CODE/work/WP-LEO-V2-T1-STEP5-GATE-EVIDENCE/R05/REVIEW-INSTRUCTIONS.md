# Independent review instructions — WP-LEO-V2-T1-STEP5-GATE-EVIDENCE revision R05

You are an INDEPENDENT REVIEWER, cold-started in a fresh session. You did not produce these artifacts
and must not trust the producer's prose. Verify against the files.

Repo root: `/Users/lge/Desktop/topic/leo-direct-sim-wt-step5-r04`
Revision under review: `CODE/work/WP-LEO-V2-T1-STEP5-GATE-EVIDENCE/R05/`
Earlier revisions are retained intact: `R01/`, `R02/`, `R03/`, `R04/`.

## Why R05 exists

R04 was BLOCKed by the independent adversarial review with three findings, all re-verified as real by
the producer. R05 fixes all three:

1. **The gate's G1 was defeatable**: it scanned only backtick-fenced bash blocks, so a complete
   `run-remote.sh` command placed in unfenced prose was ACCEPTED. R05's G1a scans the **whole document
   text** with no fence exemption, and G1b fails loud on any command-like run-remote line outside a
   fence. Both now carry negative controls that inject an **unfenced** foreign id / an **unfenced**
   complete invocation.
2. **Stale claim**: `brief.json` asserted the pre-F2 corridor equivalence value
   `d45c6a50…`, which is false on this main where the compiled corridor control-arm
   `config_sha256` is `52dfe3d92c1e0e6cbd7281c8b4725d9889c5c2376848a22a5b25a980ed4c47a3`. Verify the
   declaration now matches, and that no earlier-revision path lingers in `allowed_inputs`.
3. **Unbacked F2 disclosure**: the procedure claimed the gate reports the resolved
   `execution.node_process_delay_s`, but no such reporting existed. R05 adds gate check **G5**, which reads
   each cell's resolved config, reports the actual value, and has a control proving a non-zero value is
   flagged.

## Absolute constraints

1. You may CREATE exactly one file: your own receipt JSON at the path in your task prompt. Do not
   create, modify, move or delete anything else in the repo. Do not run the simulation. Do not commit.
   (Throwaway output under `/tmp` is fine.)
2. Read-only commands otherwise.
3. If a claim in `brief.json` is not supported by the files, that is a finding.

## Read first

- `CODE/work/WP-LEO-V2-T1-STEP5-GATE-EVIDENCE/R05/brief.json`
- `CODE/work/WP-LEO-V2-T1-STEP5-GATE-EVIDENCE/R05/PROCEDURE.md`
- `CODE/work/WP-LEO-V2-T1-STEP5-GATE-EVIDENCE/R05/artifact-set.json` (18 artifacts you must bind)
- `CODE/work/WP-LEO-V2-T1-STEP5-GATE-EVIDENCE/tools/check_procedure.py` (the gate)
- `CODE/work/WP-LEO-V2-T1-STEP5-GATE-EVIDENCE/tools/step5_recompute.py` (the recomputation harness)
- `CODE/work/WP-LEO-V2-T1-STEP5-GATE-EVIDENCE/R04/review-adversarial.json` and `R04/decision.json` (the findings)
- `EXPERIMENTS/EXP-20260923-T1-STEP5-{MICRO,PRESSURE}-R05/` and the matching `request-sources/*.json`
- `CODE/data/traffic/t1_step5_micro_ab.csv`, `CODE/leo_sim/profiles/t1_pressure_corridor.yaml`

## Run the gate, then attack it

```bash
cd /Users/lge/Desktop/topic/leo-direct-sim-wt-step5-r04
PYTHONPATH=. python3 CODE/work/WP-LEO-V2-T1-STEP5-GATE-EVIDENCE/tools/check_procedure.py \
  --root . \
  --procedure CODE/work/WP-LEO-V2-T1-STEP5-GATE-EVIDENCE/R05/PROCEDURE.md \
  --artifact-set CODE/work/WP-LEO-V2-T1-STEP5-GATE-EVIDENCE/R05/artifact-set.json \
  --experiment-id EXP-20260923-T1-STEP5-MICRO-R05 \
  --experiment-id EXP-20260923-T1-STEP5-PRESSURE-R05 \
  --phase pre-review \
  --out /tmp/your-gate-pre-review.json
```

It must exit 0 with `all_checks_ok: true`. Then:

- Report, for **every** executed check, whether its negative control triggered. A check whose control did
  not trigger is an **UNPROVEN GATE** and must not be counted as passed; confirm `all_checks_ok` is false
  in that case, and say so if you can construct a counterexample.
- **Re-run the exact R04 defeat**: copy the procedure to `/tmp`, add a complete `run-remote.sh` command
  referencing an earlier-revision experiment id **outside any fence**, rebind an artifact set if you
  like, and confirm the gate now REJECTS it. Report exactly what you did and the observed exit code and
  message. Also try to find a NEW defeat the producer has not thought of — a stale id in a comment, in a
  table, in an HTML comment, split across lines, in a link target, in a different case, and so on.
  Report each attempt as caught or not caught.

## Also verify yourself

1. **F2 disclosure is mechanically backed**: run the gate and read G5's detail; it must list the actual
   resolved `execution.node_process_delay_s` per cell (currently `0.0` for all four) and its control must
   show a non-zero value is flagged. Then confirm the same values directly in the four resolved configs.
   State plainly: "deployment contains F2 code with F2 disabled".
2. **Compile cleanliness**: both R05 `compile-report.json`: `COMPILED_REVIEW_REQUIRED`, `errors=[]`,
   `execution_authorized=false`; `request-sources/*.json` byte-equal to the compiled `request.json`.
3. **Paired design**: within each pair the cells share `trace_identity_sha256` and
   `controlled_signature` and differ in `config_sha256`; the resolved configs differ in exactly the
   `execution.compute_delay_s` line (0.0 vs 0.05).
4. **Corridor equivalence on THIS main**: the corridor control-arm `config_sha256` must equal
   `load_config_file("CODE/leo_sim/profiles/t1_pressure_corridor.yaml")["sha256"]` =
   `52dfe3d92c1e0e6cbd7281c8b4725d9889c5c2376848a22a5b25a980ed4c47a3`; the profile file's own sha256 is
   `60bcb666160e72159018feaf2f93c7f2f01888cc06cd4655c04ed5b5aab2dc4e`; `t1_pressure_corridor` is not a
   registered named profile (`PROFILES` holds only `smoke`).
5. **Micro honesty**: the micro scenario is NOT a reproduction of the #212 fixture, because
   `tests/helpers.py` `StaticGeometry` scripted geometry cannot be expressed by a formal config
   (`CODE/leo_sim/kernel.py` ~1065 falls back to `model.Constellation`). Check nothing claims otherwise.
6. **Decision-log limitation**: the run entry point refuses `--decision-log` on a formal run and the
   remote launcher never passes it; the substitute witness is admissible only when its identity checks
   all pass.
7. **Harness self-test**: run it (output to /tmp) and confirm 9/9, especially that a merged 2x
   `compute_delay_s` interval is REPORTED as a violation rather than summed into a passing total.
8. **The registered chain-2 blind spot** (`metrics_independent.py:297-363` does not enforce
   `end-start == served/rate`) stays OPEN and unrepaired; PROCEDURE section 6b must mandate the three
   backstops.
9. **Artifact hashes**: re-hash all 18 entries of `artifact-set.json`.

## Your receipt — EXACTLY these 16 keys, no others (`CODE/work/review-receipt.schema.json`)

```json
{
  "schema": "agent-review-receipt/v2",
  "receipt_id": "<from your task prompt>",
  "work_id": "WP-LEO-V2-T1-STEP5-GATE-EVIDENCE",
  "revision": 5,
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
