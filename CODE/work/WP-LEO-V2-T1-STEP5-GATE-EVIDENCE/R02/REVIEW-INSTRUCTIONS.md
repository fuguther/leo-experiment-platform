# Independent review instructions — WP-LEO-V2-T1-STEP5-GATE-EVIDENCE revision 2

You are an INDEPENDENT REVIEWER, cold-started in a fresh session. You did not produce these
artifacts and you must not trust the producer's prose. Verify against the files.

Repo root: `/Users/lge/Desktop/topic/leo-direct-sim-wt-step5`
Work package revision: `CODE/work/WP-LEO-V2-T1-STEP5-GATE-EVIDENCE/R02/`
Revision 1 (BLOCKed) is retained intact at `CODE/work/WP-LEO-V2-T1-STEP5-GATE-EVIDENCE/R01/`.

## Absolute constraints

1. You may CREATE exactly one file: your own receipt JSON at the path given in your task prompt.
   Do NOT create, modify, move or delete anything else. Do not run the simulation. Do not commit.
2. Read-only commands otherwise (cat/grep/sed/python3 reading files).
3. If a claim in `brief.json` is not supported by the files, that is a finding.

## Read first

- `CODE/work/WP-LEO-V2-T1-STEP5-GATE-EVIDENCE/R02/brief.json` — producer claims, incl. the
  revision-2 responses to the R01 review.
- `CODE/work/WP-LEO-V2-T1-STEP5-GATE-EVIDENCE/R02/PROCEDURE.md` — the literal reproducible procedure.
- `CODE/work/WP-LEO-V2-T1-STEP5-GATE-EVIDENCE/R02/artifact-set.json` — the 17 artifacts your
  receipt MUST bind.
- `CODE/work/WP-LEO-V2-T1-STEP5-GATE-EVIDENCE/tools/step5_recompute.py` — the recomputation harness.
- `EXPERIMENTS/request-sources/EXP-20260923-T1-STEP5-{MICRO,PRESSURE}-R02.json`
- `EXPERIMENTS/EXP-20260923-T1-STEP5-{MICRO,PRESSURE}-R02/` (request.json, compile-report.json,
  run-manifest.json, analysis-request.json, RUNBOOK.md, resolved/*)
- `CODE/data/traffic/t1_step5_micro_ab.csv`, `CODE/leo_sim/profiles/t1_pressure_corridor.yaml`

## What the R01 review found (context — verify the fixes are real, do not take them on trust)

- **COLD-B1**: the generated RUNBOOK mandates `python3 -m CODE.experiment_platform.v2_analysis`,
  and that module rejected revision 1's `primary_metric` ("unsupported V2 primary metric").
  Revision 2 declares `e2e_delay_mean_s`. Check it is really supported
  (`CODE/experiment_platform/v2_analysis.py`, `_metric_from_result`, around line 249) and that
  nothing else in the RUNBOOK's mandated flow contradicts the declared analysis.
- **COLD-B2**: the harness had no entry point, no production comparator, no witness verifier, and
  was outside the reviewed hash set. Check `step5_recompute.py` really has `analyze` and
  `witness` subcommands, really diffs against the production reading, and that it is really in
  `artifact-set.json`.
- **COLD-B3**: revision 1 truncated mismatch lists and left the tolerance undeclared. Check that
  no `[:n]` truncation remains in the report paths and that a `tolerance_contract_s` is emitted.

## A self-test you must actually run

```bash
PYTHONPATH=. python3 CODE/work/WP-LEO-V2-T1-STEP5-GATE-EVIDENCE/tools/step5_recompute.py \
  selftest --out /dev/stdout
```

It must exit 0 and report `all_checks_ok: true` over 9 synthetic fail-loud checks, including
that a MERGED uncovered interval of 2 x compute_delay_s is REPORTED as a violation rather than
summed into a passing total, and that a tampered production utilisation IS reported as a
mismatch. Judge for yourself whether those checks genuinely cover the risk, and say so if not.

## Machine-checkable claims to verify YOURSELF

Run python from the repo root with `PYTHONPATH=.`.

1. **Compile cleanliness.** Both `compile-report.json`: `status == "COMPILED_REVIEW_REQUIRED"`,
   `errors == []`, `execution_authorized == false`.
2. **Paired design.** In each `run-manifest.json` the two cells share `trace_identity_sha256` and
   share `controlled_signature` but have different `config_sha256`. `controlled_signature`
   (`CODE/leo_sim/matrix.py`, `_control_projection`) is computed after removing the declared
   intervention path `execution.compute_delay_s`, so equality proves the only difference is that field.
3. **Corridor profile equivalence.** `brief.json` claims the corridor `common_config` is
   field-for-field identical to `CODE/leo_sim/profiles/t1_pressure_corridor.yaml` and that the
   compiled control-arm `config_sha256` EQUALS `CODE.leo_sim.config.load_config_file(<profile>)["sha256"]`.
   Verify with code. The expected value is
   `d45c6a50580958c3d14bc7ff576cb36f0f57c91953e12c8c80478842277fd27f` and the profile file's own
   sha256 is `60bcb666160e72159018feaf2f93c7f2f01888cc06cd4655c04ed5b5aab2dc4e`.
   Also confirm `t1_pressure_corridor` is NOT a registered named profile
   (`CODE/leo_sim/config.py` `PROFILES`).
4. **Micro honesty.** `brief.json` states the micro scenario is NOT a reproduction of the #212
   fixture because `CODE/leo_sim/tests/helpers.py` `StaticGeometry` scripted geometry cannot be
   expressed by a formal config (`CODE/leo_sim/kernel.py` ~line 1065 falls back to
   `model.Constellation`). Verify, and check nothing in the package claims reproduction.
5. **The decision-log limitation.** `CODE/leo_sim/__main__.py` ~line 332 refuses `--decision-log`
   for a formal run; `CODE/scripts/remote/remote_job.py` `formal_command` never passes it, so no
   formal artifact has decision rows. The substitute is a same-VM deterministic non-formal
   re-execution, admissible only when `witness.admissible` is true. Verify the code citations and
   judge whether the admissibility rule is genuinely fail-loud (see `witness()` in the harness).
6. **Harness independence.** `step5_recompute.py` must never import or call `CODE.leo_sim.metrics`;
   it enforces this on itself with an `ast` walk (`_assert_independent`). Confirm, and confirm the
   production numbers are read only as persisted `ledgers.congestion_metrics` data.
7. **Artifact hashes.** Re-hash all 17 entries of `artifact-set.json` yourself.

## Your receipt file — exact required shape

Valid JSON with EXACTLY these 16 keys and no others (schema `CODE/work/review-receipt.schema.json`,
`additionalProperties: false`):

```json
{
  "schema": "agent-review-receipt/v2",
  "receipt_id": "<RR-... from your task prompt>",
  "work_id": "WP-LEO-V2-T1-STEP5-GATE-EVIDENCE",
  "revision": 2,
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

- PASS ⇒ `blocking_findings` MUST be `[]`. BLOCK ⇒ `blocking_findings` >= 1 AND `required_revision` >= 1.
  UNKNOWN ⇒ `unknowns` >= 1. `evidence` non-empty, no blank entries.
- **Every** key of `artifact-set.json` must appear in `artifact_hashes` with the identical value.

Write with `json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"`, then print its sha256.
