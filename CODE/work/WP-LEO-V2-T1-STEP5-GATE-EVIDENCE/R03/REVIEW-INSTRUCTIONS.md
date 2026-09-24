# Independent review instructions — WP-LEO-V2-T1-STEP5-GATE-EVIDENCE revision 3

You are an INDEPENDENT REVIEWER, cold-started in a fresh session. You did not produce these
artifacts and must not trust the producer's prose. Verify against the files.

Repo root: `/Users/lge/Desktop/topic/leo-direct-sim-wt-step5`
Revision under review: `CODE/work/WP-LEO-V2-T1-STEP5-GATE-EVIDENCE/R03/`
Earlier revisions are retained intact: `R01/` (BLOCKed → REVISE), `R02/` (BLOCKed → REVISE).

## Absolute constraints

1. You may CREATE exactly one file: your own receipt JSON at the path given in your task prompt.
   Do NOT create, modify, move or delete anything else. Do not run the simulation. Do not commit.
2. Read-only commands otherwise.
3. If a claim in `brief.json` is not supported by the files, that is a finding. Report it.

## Read first

- `CODE/work/WP-LEO-V2-T1-STEP5-GATE-EVIDENCE/R03/brief.json`
- `CODE/work/WP-LEO-V2-T1-STEP5-GATE-EVIDENCE/R03/PROCEDURE.md`
- `CODE/work/WP-LEO-V2-T1-STEP5-GATE-EVIDENCE/R03/artifact-set.json` (17 artifacts you must bind)
- `CODE/work/WP-LEO-V2-T1-STEP5-GATE-EVIDENCE/tools/step5_recompute.py`
- `CODE/work/WP-LEO-V2-T1-STEP5-GATE-EVIDENCE/R01/decision.json` and `R02/decision.json` (the
  recorded REVISE decisions; five blocking findings in total across the two rounds)
- `EXPERIMENTS/request-sources/EXP-20260923-T1-STEP5-{MICRO,PRESSURE}-R03.json`
- `EXPERIMENTS/EXP-20260923-T1-STEP5-{MICRO,PRESSURE}-R03/` (all five compiled documents + resolved/*)
- `CODE/data/traffic/t1_step5_micro_ab.csv`, `CODE/leo_sim/profiles/t1_pressure_corridor.yaml`

## The two defects THIS revision exists to fix — verify they are really gone

- **COLD-R02-B1**: revision 2's procedure named the superseded `-R01` experiment directories in its
  run commands, while `run-remote.sh` derives the run id from the config **filename**
  (`run-remote.sh:97`) — so following it would have run revision-1 configs and attributed the runs
  to the wrong revision. Check that **no run command** in R03/PROCEDURE.md references an earlier
  revision's experiment id, and that the four commands match the four `run_id`s in the two R03
  `run-manifest.json` files.
- **COLD-R02-B2**: revision 2's procedure never documented how `finalization.json` and
  `authorization.json` are produced, so the mandated ordering was not achievable from it alone.
  Check that section 3 now gives exact commands for reviews → decision → finalization →
  authorization, that the `--experiment`/`--finalization`/`--out` arguments match the real CLI
  (`CODE/work/finalize_decision.py`, `CODE/experiment_platform/authorize_experiment.py`), and that
  `request.json`'s `work_finalization` equals the finalization path the procedure names.

Also verify the two non-blocking findings were fixed: `tolerance_contract_s` is now emitted by the
`analyze`, `witness` **and** `selftest` reports, and `witness` now fails loud (non-zero exit) when
the decision log is missing or empty instead of reporting an admissible-looking zero-row witness.

## Machine-checkable claims to verify YOURSELF

Run python from the repo root with `PYTHONPATH=.`.

1. **Harness self-test** — run it (write to a real path, not /dev/stdout):
   ```bash
   PYTHONPATH=. python3 CODE/work/WP-LEO-V2-T1-STEP5-GATE-EVIDENCE/tools/step5_recompute.py \
     selftest --out CODE/Results/_step5_review_selftest.json
   ```
   It must exit 0 with `all_checks_ok: true` over 9 checks. Judge whether check (2) — a **merged
   interval of 2 x compute_delay_s must be REPORTED as a violation**, not summed into a passing
   total — genuinely closes the hole that makes a bare `residual < 1e-9` insufficient, and whether
   check (7) makes "0 mismatches" non-vacuous. Say so if you think it does not.
2. **Missing decision log is a hard error**: run `witness` with a non-existent `--decision-log` and
   confirm a non-zero exit, not a zero-row report.
3. **Compile cleanliness**: both R03 `compile-report.json`: `status == "COMPILED_REVIEW_REQUIRED"`,
   `errors == []`, `execution_authorized == false`.
4. **Paired design**: within each R03 pair the two cells share `trace_identity_sha256` and
   `controlled_signature` but differ in `config_sha256` (`controlled_signature` is computed after
   removing the declared intervention path `execution.compute_delay_s`, `matrix.py` `_control_projection`).
5. **Corridor profile equivalence**: the corridor control-arm `config_sha256` must equal
   `CODE.leo_sim.config.load_config_file("CODE/leo_sim/profiles/t1_pressure_corridor.yaml")["sha256"]`
   = `d45c6a50580958c3d14bc7ff576cb36f0f57c91953e12c8c80478842277fd27f`; the profile file's own
   sha256 is `60bcb666160e72159018feaf2f93c7f2f01888cc06cd4655c04ed5b5aab2dc4e`; and
   `t1_pressure_corridor` is NOT a registered named profile (`CODE/leo_sim/config.py` `PROFILES`).
6. **Micro honesty**: the micro scenario is NOT a reproduction of the #212 fixture, because
   `tests/helpers.py` `StaticGeometry` scripted geometry cannot be expressed by a formal config
   (`CODE/leo_sim/kernel.py` ~1065 falls back to `model.Constellation`). Check nothing claims otherwise.
7. **Decision-log limitation**: `CODE/leo_sim/__main__.py` ~332 refuses `--decision-log` on a formal
   run and `remote_job.formal_command` never passes it; the substitute is admissible only when
   `witness.admissible` is true (trace + three digests identical).
8. **Harness independence**: it must never import or call `CODE.leo_sim.metrics`; it enforces this
   on itself with an `ast` walk. Production numbers are read only as persisted
   `ledgers.congestion_metrics` data.
9. **Artifact hashes**: re-hash all 17 entries of `artifact-set.json` yourself.

## Your receipt file — exact required shape

Valid JSON with EXACTLY these 16 keys and no others (`CODE/work/review-receipt.schema.json`,
`additionalProperties: false`):

```json
{
  "schema": "agent-review-receipt/v2",
  "receipt_id": "<RR-... from your task prompt>",
  "work_id": "WP-LEO-V2-T1-STEP5-GATE-EVIDENCE",
  "revision": 3,
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
