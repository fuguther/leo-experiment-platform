# Independent review instructions — WP-LEO-V2-T1-STEP5-GATE-EVIDENCE revision R06 (structural fix)

You are an INDEPENDENT REVIEWER, cold-started in a fresh session. You did not produce these artifacts
and must not trust the producer's prose.

Repo root: `/Users/lge/Desktop/topic/leo-direct-sim-wt-step5-r04`
Revision under review: `CODE/work/WP-LEO-V2-T1-STEP5-GATE-EVIDENCE/R06/`

## Why R06 exists and what is IN SCOPE

Four review rounds tried to close "the procedure points at the wrong revision" by SCANNING human prose
for suspicious experiment ids. Each patch was evaded: R04 scanned only fenced blocks (an unfenced
command passed); R05 scanned the whole document (an id split across backslash-continued lines, or a
lower-case id, passed). R06 therefore abandons scanning:

- The reviewed generator `CODE/work/WP-LEO-V2-T1-STEP5-GATE-EVIDENCE/tools/procedure_commands.py` **derives** the four run
  commands from the compiled run-manifests.
- Gate check **G0** requires `R06/PROCEDURE.md` to contain the derived marked block **byte-for-byte**
  between `GENERATED-RUN-COMMANDS` markers. Commands are reproduced, not checked.
- `G1a/G1b` are retained **only as hygiene checks** and are explicitly NOT the structural guarantee.
- `G3` walks the **derived** commands to the launcher accept/reject decision point.

### G0's guarantee boundary is now written down - judge the documents against it

R06/PROCEDURE.md section 3 states G0's boundary verbatim: G0 guarantees that the procedure contains,
byte-for-byte, the four commands derived from the run-manifests, and it does NOT claim that command text
elsewhere in the document is covered (`派生块之外的命令文本不受本保证约束`). Therefore:

- commands placed outside the marked block are **explicitly out of scope**. Report them as observations,
  not as blocking defects, unless you find a document that **claims** they are covered.
- **Any over-claim IS blocking**: if the procedure, the brief or the gate output presents G0 as
  guaranteeing that no wrong command can appear anywhere in the document, that is a defect. Check the
  wording, including the gate's own report fields.
- The gate must honour exactly the stated boundary: verify G0 fails on a single-character edit inside the
  block and on marker removal, and that it does not silently pass when the block is missing or duplicated.
**Scope: review the CHANGED artifacts** — the generator, the generated command block in PROCEDURE.md,
and the gate. The evidence chain (compiled cells, paired design, corridor equivalence, F2-disabled
evidence, harness self-test, registered chain-2 blind spot and its backstops) carries over from R05
unchanged; confirm it is unchanged rather than re-deriving it from scratch.

## Run the gate, then attack G0

```bash
cd /Users/lge/Desktop/topic/leo-direct-sim-wt-step5-r04
PYTHONPATH=. python3 CODE/work/WP-LEO-V2-T1-STEP5-GATE-EVIDENCE/tools/check_procedure.py \
  --root . --procedure CODE/work/WP-LEO-V2-T1-STEP5-GATE-EVIDENCE/R06/PROCEDURE.md \
  --artifact-set CODE/work/WP-LEO-V2-T1-STEP5-GATE-EVIDENCE/R06/artifact-set.json \
  --experiment-id EXP-20260923-T1-STEP5-MICRO-R06 --experiment-id EXP-20260923-T1-STEP5-PRESSURE-R06 \
  --phase pre-review --out /tmp/your-gate-pre-review.json
```

Must exit 0 with `all_checks_ok: true` and `structural_guarantee` naming G0. For EVERY executed
check report whether its negative control triggered; an UNPROVEN GATE must not be counted as passed.

Then attack G0 specifically, and report each attempt with the observed exit code and message:

1. Copy the procedure to `/tmp`, change **one character** inside the generated block, and run the
   gate against your copy (rebind an artifact set if needed) — this must FAIL.
2. Delete the two marker lines from your copy — must FAIL.
3. Replace a whole command with a plausible but wrong one (wrong revision, wrong session, wrong config
   path, re-ordered flags, an extra trailing space) — must FAIL, and report whether the failure is
   attributable to G0 or only to the hygiene checks.
4. Put an EXTRA, obfuscated run command somewhere OUTSIDE the marked block (for example inside a
   sentence, or with the id split across lines) and report honestly whether the gate catches it. G0
   guarantees the marked block, not arbitrary prose: say plainly whether you consider that acceptable
   given that the realistic failure mode this gate exists for is accidental drift, and say so if you
   think a remaining hole is blocking.
5. Try to make the generator itself produce something misleading (for example by tampering with a
   run-manifest in a sandbox) and report what happens.

## Also verify

1. **The generator is in the reviewed set** and its hash is bound: `artifact-set.json` must contain
   both `tools/procedure_commands.py` and `tools/check_procedure.py`, with hashes matching disk.
2. **Regenerate and diff**: run the generator yourself and confirm the block in PROCEDURE.md is
   byte-identical to its output; confirm the document contains no other generated block.
3. **Compile cleanliness**: both R06 `compile-report.json`: `COMPILED_REVIEW_REQUIRED`, `errors=[]`,
   `execution_authorized=false`; `request-sources/*.json` byte-equal to the compiled `request.json`.
4. **Paired design** unchanged: shared `trace_identity_sha256` and `controlled_signature` per pair,
   differing `config_sha256`, resolved configs differing in exactly the `compute_delay_s` line.
5. **Corridor equivalence on THIS main**: control-arm `config_sha256` must equal
   `load_config_file("CODE/leo_sim/profiles/t1_pressure_corridor.yaml")["sha256"]`; profile file sha256
   `60bcb666160e72159018feaf2f93c7f2f01888cc06cd4655c04ed5b5aab2dc4e`; `t1_pressure_corridor` is not a
   registered named profile (`PROFILES` holds only `smoke`).
6. **F2 disclosure**: gate check G5 must report the actual per-cell `execution.node_process_delay_s`
   (currently `0.0` for all four) with its control firing. State plainly: deployment contains F2 code
   with F2 disabled.
7. **Harness self-test** 9/9 including that a merged 2x `compute_delay_s` interval is REPORTED as a
   violation rather than summed into a passing total. The registered chain-2 blind spot
   (`metrics_independent.py:297-363` does not enforce `end-start == served/rate`) stays OPEN with section
   6b's three backstops mandatory.
8. **Artifact hashes**: re-hash all 19 entries of `artifact-set.json`.
9. No claim anywhere that a review gate is closed, or that lost raw data was restored.

## Your receipt — EXACTLY these 16 keys, no others (`CODE/work/review-receipt.schema.json`)

```json
{
  "schema": "agent-review-receipt/v2",
  "receipt_id": "<from your task prompt>",
  "work_id": "WP-LEO-V2-T1-STEP5-GATE-EVIDENCE",
  "revision": 6,
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
