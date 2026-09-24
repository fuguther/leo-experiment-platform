# Independent review instructions — WP-LEO-V2-T1-STEP5-GATE-EVIDENCE revision 1

You are an INDEPENDENT REVIEWER, cold-started in a fresh session. You did not produce these
artifacts and you must not trust the producer's prose. Verify against the files.

Repo root: `/Users/lge/Desktop/topic/leo-direct-sim-wt-step5`
Work package: `CODE/work/WP-LEO-V2-T1-STEP5-GATE-EVIDENCE/R01/`

## Absolute constraints

1. You may CREATE exactly one file: your own receipt JSON at the path given in your task prompt.
   Do NOT create, modify, move or delete anything else. Do not run the simulation. Do not commit.
2. Read-only commands only, otherwise (cat/grep/sed/python3 reading files).
3. If a claim in `brief.json` is not supported by the files, that is a finding. Report it rather
   than accepting it.

## What to read

- `CODE/work/WP-LEO-V2-T1-STEP5-GATE-EVIDENCE/R01/brief.json` — the producer's claims.
- `CODE/work/WP-LEO-V2-T1-STEP5-GATE-EVIDENCE/R01/artifact-set.json` — the exact artifact hash
  map your receipt MUST bind (14 entries: every compiled artifact of both experiments).
- `EXPERIMENTS/request-sources/EXP-20260923-T1-STEP5-MICRO-R01.json`
- `EXPERIMENTS/request-sources/EXP-20260923-T1-STEP5-PRESSURE-R01.json`
- `EXPERIMENTS/EXP-20260923-T1-STEP5-MICRO-R01/{request.json,compile-report.json,run-manifest.json,analysis-request.json,RUNBOOK.md,resolved/*}`
- `EXPERIMENTS/EXP-20260923-T1-STEP5-PRESSURE-R01/{...same...}`
- `CODE/data/traffic/t1_step5_micro_ab.csv` — the micro row table (6 packets).
- `CODE/leo_sim/profiles/t1_pressure_corridor.yaml` — the corridor profile.
- `CODE/work/WP-LEO-V2-T1-STEP5-GATE-EVIDENCE/tools/step5_recompute.py` — the recomputation harness.

## Machine-checkable claims you should re-verify yourself

Run python from the repo root with `PYTHONPATH=.`. Useful facts to confirm independently:

1. **Compile cleanliness.** Both `compile-report.json` files have `status == "COMPILED_REVIEW_REQUIRED"`,
   `errors == []`, `execution_authorized == false`.
2. **Paired design is real.** In each `run-manifest.json` the two cells share `trace_identity_sha256`
   and share `controlled_signature`, but have DIFFERENT `config_sha256`. Confirm by reading the JSON.
   `controlled_signature` is computed by `CODE/leo_sim/matrix.py` (`_control_projection`) by removing
   the declared intervention path `execution.compute_delay_s` from the resolved config and hashing the
   rest — so equality proves the only declared difference is that one field.
3. **Corridor profile equivalence (a specific claim that must be checked, not believed).**
   `brief.json` claims the corridor `common_config` is field-for-field identical to
   `CODE/leo_sim/profiles/t1_pressure_corridor.yaml` and that this is machine-checked by an equal
   resolved config SHA. Verify with code:
   ```python
   # PYTHONPATH=. python3
   from CODE.leo_sim import config as C
   import hashlib
   p = "CODE/leo_sim/profiles/t1_pressure_corridor.yaml"
   print(C.load_config_file(p)["sha256"])
   ```
   That value must equal the compiled control-arm `config_sha256` in
   `EXPERIMENTS/EXP-20260923-T1-STEP5-PRESSURE-R01/run-manifest.json`, and the profile file's own
   sha256 must be `60bcb666160e72159018feaf2f93c7f2f01888cc06cd4655c04ed5b5aab2dc4e`.
   Also confirm the profile is NOT a registered named profile (`CODE/leo_sim/config.py` `PROFILES`).
4. **Micro scenario honesty.** `brief.json` states the micro scenario is NOT a reproduction of the
   #212 fixture because `CODE/leo_sim/tests/helpers.py` `StaticGeometry` scripted geometry cannot be
   expressed by a formal config (`CODE/leo_sim/kernel.py` around line 1065 falls back to
   `model.Constellation`). Check that this is true, and check that the micro request does not claim
   otherwise anywhere.
5. **Acceptance gates.** `acceptance` in the micro request is `{min_delivered_packets: 6,
   min_multisat_deliveries: 6, require_data_isl: true, require_control_delivery: true}`; the corridor
   is `{1, 1, true, true}`. The enforcement site is
   `CODE/scripts/remote/remote_job.py` `v2_governance_errors`. Judge whether the corridor's low gate
   is adequately disclosed.
6. **The decision-log limitation.** `brief.json` claims a formal authorized run cannot carry
   `--decision-log` (`CODE/leo_sim/__main__.py` around line 332) and that `remote_job.py`
   `formal_command` never passes it, so the original witness is unobtainable and a
   deterministic local diagnostic re-execution is the substitute — admissible ONLY if proven
   bit-identical. Verify the code citations and judge whether the substitution is sound and
   adequately bounded.
7. **Artifact hashes.** Every one of the 14 entries in `artifact-set.json` must match the actual
   file bytes on disk. Re-hash them yourself; do not trust the file.

## Your receipt file — exact required shape

The receipt must be valid JSON with EXACTLY these 16 keys and no others
(`CODE/work/review-receipt.schema.json`, `additionalProperties: false`):

```json
{
  "schema": "agent-review-receipt/v2",
  "receipt_id": "<RR-... from your task prompt>",
  "work_id": "WP-LEO-V2-T1-STEP5-GATE-EVIDENCE",
  "revision": 1,
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
  "evidence": ["at least one non-empty string citing files/line numbers and what YOU ran"],
  "blocking_findings": [],
  "unknowns": [],
  "required_revision": []
}
```

Rules the schema enforces:
- verdict PASS  ⇒ `blocking_findings` MUST be `[]`.
- verdict BLOCK ⇒ `blocking_findings` MUST have >= 1 entry AND `required_revision` MUST have >= 1 entry.
- verdict UNKNOWN ⇒ `unknowns` MUST have >= 1 entry.
- `evidence` must be non-empty and none of its entries may be blank.
- **Every** key of `artifact-set.json` must appear in your `artifact_hashes` with the identical value.

Write the file with `json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"`.
Then print the sha256 of the file you wrote.
