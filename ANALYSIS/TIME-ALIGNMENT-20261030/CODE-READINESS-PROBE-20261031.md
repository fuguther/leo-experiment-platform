# CODE READINESS — in-run truth probe + offline oracle re-ranking (2026-10-31)

Status: **read-only audit, code NOT modified.** One memo file written (this one).
Worktree `/Users/lge/Desktop/topic/.wt-night-20261031`, branch `night/time-exp-20261031`,
HEAD `63664f3` (`evidence(ta-age): record verified information-age run ta-age-20261031-02`), tree clean at start.
All `file:line` below are HEAD of that worktree. Local runs used synthetic fixtures in a system temp dir only.

Legend: **[F]** = fact read/executed with evidence cited; **[I]** = inference from the cited facts.

---

## 0. Verdict summary (details in §7)

| # | Requirement | Verdict |
|---|---|---|
| 1 | Registered only after the action is committed | **PASS-WITH-CAVEAT** — after the *decision* commit, before the resource mutation (kernel.py:7346 vs :7352) |
| 2 | Does not influence the online policy | **PASS** — business outputs identical A/B; only `state_version` (decision field) and `events_processed` change |
| 3 | No future reads for decisions | **PASS** — no write-back path, no reader of `kernel.target_probe` except start/write |
| 4 | No business RNG consumption | **PASS** — module imports only `bisect/json/math`; probe events are `env.timeout`/`env.event`, not a random source |
| 5 | No business resource occupancy | **PASS** — only attribute reads + SimPy event scheduling |
| 6 | Same resource identity + generation | **FAIL** — `used_state` path is correct, but the `live_audit_not_used_by_predictor` fallback attaches the **local first-hop ISL** to predictions about a **downlink terminal** (proved by rate 1e8 vs 5e6 bps) |
| 7 | Reads true workload at the eval target instant | **PASS-WITH-CAVEAT** — `sampled_at_s == eval_target_s` exactly (0/30 mismatches); but `eval_target_s` is derived from `eta_targets` (T_arrival), not from `query_targets` (the instant the scorer actually predicted for) |
| 8 | Truth consistent with predicted workload definition | **PASS for ISL rows** — both are queued(data+ctrl)+in-service-remaining. **FAIL for downlink rows** — predicted_bits there is terminal *queued* bits only, and the truth is read from the wrong resource anyway |
| 9 | Probe evaluates at the SAME instant the candidate arm predicts | **FAIL under current configs / PASS only with `candidate_target_rule: arrival`** — default is `arrival_plus_queue` (config.py:590-599, time_alignment.py:1396-1413) |
| 10 | Existing MAE / median / p90 / bias / coverage support | **PASS-WITH-GAP** — `prediction_accuracy.py` does all five; `prediction_error_seconds` is **not** produced anywhere (derivable) |
| 11 | Offline oracle re-ranking from one run's outputs | **PARTIAL** — data is sufficient in principle (probe truth + decision-log terms), but no script joins them, and (6)/(9) must be fixed first |
| 12 | Targeted tests green | **FAIL COUNT: 2 failed, 29 passed** (2/8 in `test_target_probe.py`) |

Headline blockers: **B1** wrong-resource labels on the identity fallback; **B2** label instant ≠ scored instant
under the default target rule; **B3** 2 red tests in the probe truth-read path; **B4** no `prediction_error_seconds`
and no per-decision label completeness for oracle gating.

---

## 1. `CODE/leo_sim/target_probe.py` — lifecycle

**Construct [F]** — `TargetProbe(kernel, path, *, max_samples=None, note=None, stride=1, horizons=None)`
(target_probe.py:100-139). Holds `pending` (rows to sample), `_targets` (sorted targets, parallel),
`records` (output rows), counters `{enqueued, sampled, dropped_overflow, skipped_by_stride}`, `reasons`.
`stride = max(1, int(stride))` (:109), `horizons` tuple (:114). No RNG, no resource handle beyond `self.kernel`.

**Start [F]** — `start()` sets `started/started_at` and `self.kernel.env.process(self._sampler())`
(:175-181). Called once from `Kernel.run()` before the first event (kernel.py:7575-7578).

**Enqueue [F]** — `enqueue(rows)` is called once per decision with one row per candidate direction
(kernel.py:6130-6188 → probe.enqueue). Behaviour:
- rows without `eval_target_s` are counted `no_eval_target` and dropped (:147-151);
- **stride is applied to the BASE candidate row stream before horizon expansion** (:152-155): `_seq += 1`;
  `if self.stride > 1 and (self._seq % self.stride): skipped_by_stride += 1; continue`;
- each kept row is expanded by `expand_horizons` (:156, :65-94);
- the cap `max_samples` is applied to **expanded** records and drops the tail (`overflow`) (:157-161),
  so a hit cap biases the sample to the earliest decisions (module comment :106-113);
- insertion is sorted by target (`bisect.bisect_right` :164-166), and a sleeping sampler is poked
  (`self._wake.succeed()` :167-172) so an earlier target is never read late.

**Sample [F]** — one long-lived generator `_sampler()` (:182-213): takes the earliest pending target, sleeps
`env.timeout(target-now) | wake`, on resume calls `_sample_one(row, now)` at the target instant, then advances
the cursor. `sampled_at_s = now` (:217) — verified equal to `eval_target_s` in every local row (0 mismatches, §6).

**Truth read [F]** — `_sample_one` (:215-255):
```python
state = self.kernel._isl_resource_state_advertisement(link, now)      # :232
work = state["work_ahead_bits_proxy"]                                  # :233
record["truth"] = {..., "queued_bits": int(state["value"]), "work_ahead_bits": work,
                   "in_service_remaining_bits": (None if work is None
                       else float(work) - float(state["value"])),      # :234-245
                   "definition": "queued_data_and_control_bits + in_service_remaining",
                   "source": "kernel_live_resource_at_target"}
```
Reasons instead of a substituted truth: `no_prediction`, `no_resource_identity`, `beyond_horizon`
(`now > kernel.horizon`), `resource_missing`, `generation_changed`, `link_retired`,
`prediction_generation_unknown`, `unparseable_resource_id` (:221-231, :248-250, :256-307).

**Identity resolution [F]** — `_find_link` (:256-307) parses `resource_id` as `isl:<host>:<peer>`
(:270-273 — the PREDICTED peer egress, not the local candidate link), locates the link in
`kernel.isls[host]` whose `link.peer == peer` (:275-281), then **refuses** on generation mismatch
(:296-303) and on `retired` (:304-306). Retired generations are searched separately (:285-291).
This part is sound.

**Sampling / stride / horizon semantics [F]**
- One probe row per (decision, candidate direction); stride is deterministic 1-in-N over the monotone stream.
- **Horizon rows**: each sampled row is repeated at `base + h` with the SAME `predicted_bits`
  (`expand_horizons` :65-94, `horizon_offset_s` 0.0 row kept explicitly). This measures truth drift over
  time; it is a diagnostic and must NOT be mixed into the main MAE (only offset 0 is the prediction's own target).
- Evaluated instant(s): base `eval_target_s` = `eta_targets[direction]` (kernel.py:6179) i.e. **T_arrival at peer egress**;
  horizon rows additionally read `base+h`. It never reads the *scored* query instant unless that equals the ETA (see §4).

**Output [F]** — `write()` (:357-362) writes JSONL with `json.dumps(record, sort_keys=True)`, only at end of run
(kernel.py:7864-7868); the summary is attached to `result["target_probe"]`. Because rows are buffered in RAM and
written after the simulation stops, an interrupted run loses all unsampled rows (FG memo: 47k-191k rows per run).

---

## 2. Kernel integration and causality

**Hook [F]** — inside `_record_decision` after the commit bookkeeping:
```python
if self.target_probe is not None:                                       # kernel.py:6063
    effective_ta = (ta_audit if ta_audit
                    else (observation_record or {}).get("time_alignment"))  # :6068-6069
    self._enqueue_target_probe(pkt, sat, kind, candidates, chosen, committed_at,
                               decision_id=decision_id, ta_audit=effective_ta,
                               direction_audit=direction_audit,
                               decision_started_at=decision_started_at)     # :6070-6074
```
`_enqueue_target_probe` (:6111-6188) only reads the decision's own audit objects (`used_state`, `eta_targets`,
`query_targets`, `scores`, `candidate_details`) and appends dict rows; "it does not build a snapshot, score
anything, or touch any resource" (:6115-6120). Row fields include `predicted_bits`, `service_rate_bps`,
`predicted_work_s`, `score_total_s`, `score_missing`, `fallback`, `resource_id`, `generation`,
`identity_source`, `eval_target_s`, `algo_query_target_s`, `t_commit_s` (:6149-6187).

**Registration point [F]** — all four `_record_decision` call sites sit after the action is chosen and
`pkt.decision_id` is assigned, but **before** the resource mutation:
- frozen deliver: record :7052 → `downlinks[sat].put(pkt)` :7056
- frozen forward: record :7063-7067 → `isls[sat][action].put_data(pkt)` :7068
- non-frozen deliver: record :7205 → `dl.put(pkt)` :7208
- non-frozen forward: `_apply_forced_action` :7343, `pkt.decision_id =` :7345, record :7346 → `put_data` :7352

So "registered after the action commit" is true for the *decision*, not for the action's *effect*.
**[I]** harmless for label validity because sampling happens at a later simulated instant, and even a
same-instant target is sampled after the synchronous handler returns (SimPy `succeed()`/`timeout` both
schedule at NORMAL priority 1 and the sampler resumes on a later `env.step()`; verified against simpy 4.1.1).

**No policy influence [F, executed A/B]** — same config/seed, probe OFF vs ON (2-sat fixture, 12 packets):
- result dicts differ in exactly one key: `events_processed` 1868 → 1927 (probe timeout/wake events);
- 24 decision-sink rows differ, **only** in `state_version` (e.g. 154→155, 165→167, …);
- fates, deliveries, `chosen`, packet_events length, stop time identical;
- `state_version` is read only by `validate_joint_plan` (kernel.py:2897-2905 → `q0.validate_plan_version`,
  q0.py:71-75), whose only caller is `apply_joint_plan` (kernel.py:2977) and that has **no live caller**
  (tests only: test_q0_contract.py:58/75/90/108). So no policy channel exists.

> The module docstring claim "the baseline run is bit-identical" (target_probe.py:44-45) is **false at artifact
> level**: `state_version` in every decision record and `events_processed` shift. Business outcomes, not artifacts,
> are invariant. Cross-arm analyses must not key on `state_version` when probe settings differ per arm.
> The recorded 47,755-row run compared stride=1 vs stride=4 and found identical fates/areas
> (`CODE/work/WP-T1-SCENE-CAL/PROBE-RESULTS-20261019.md`), consistent with this.

**RNG [F]** — `target_probe.py` imports only `bisect, json, math` (:57-59). The truth accessor is pure w.r.t. RNG:
`_link_rate` (kernel.py:1948-1968) is a table/geometry lookup; `arrival_rate_bps` (kernel.py:978-982) only reads the
EWMA state via `_ta.arrival_rate_at` ("pure; no arrivals seen", time_alignment.py:90-98). No `random`/`numpy` use.

**Business resources [F]** — the sampler never requests/releases links, slots, pools or queue entries; it only
reads `link.data_bits`, `link.ctrl_bits`, `link.current`, `link._svc_phase`, `link._tx_started_at`,
`link._service_rate_bps`, `link.gen`, and schedules SimPy events.

**FEL / event ordering [F]** — the probe inserts `timeout`/`event` only; the sampler resumes at NORMAL priority
(heap key `(time, priority, eid)`), i.e. **after** already-scheduled same-instant business events and **before**
those scheduled later. That matches the documented `same_instant_convention` (:343-347). Insertion is always
sorted, and `eta - t_commit > 0` in every local row (min 54.9 ms; 0 negatives), so a row can never be inserted
behind the cursor and silently skipped.

---

## 3. Truth definition vs predicted definition

**Truth (ISL) [F]** — one definition, reused, kernel.py:3784-3838:
```python
queued_bits = int(link.data_bits + link.ctrl_bits)                        # :3798
# active head is NOT in data_bits: dequeue subtracts it before service      # :1167-1174
active_bits = current.bits - elapsed*link._service_rate_bps               # :3809-3820 (transmitting)
            | current.bits                                                # :3821-3825 (not started)
            | None                                                        # :3810-3816 (no start rate)
work_ahead  = None if active_bits is None else queued_bits + active_bits   # :3826-3827
```
So truth = `queued(data+ctrl)` + `in-service remaining`, the head counted **once** (only inside
`in_service_remaining`, never inside `queued_bits`). Same function the platform already uses to advertise
(kernel.py:3653-3655, `_advertise`).

**Prediction [F]** — the predictor's state sample is the advertised **work-ahead** field, not the queued field:
kernel.py:5097-5099 reads `advertised_isl_work_ahead_bits_proxy`, produced from the same accessor at
advertisement time (kernel.py:4491-4495 / 4637-4641), and `score_candidates` divides that projection by the
resource rate: `terms["resource_work_s"] = work_bits / service_rate`
(time_alignment.py:1131-1145; `predicted_bits`/`service_rate_bps` kept separately, :1037-1051, :1203-1207).
`state_source: ideal_current` also uses `work_ahead_bits_proxy` (kernel.py:5057-5058, :5068-5079).

**Verdict [F]** — for ISL candidates the two are the **same quantity** (work ahead = queued+in-service); they differ
only in *knowledge* (prediction projects an old advertisement to the target instant) and in *instant* (§4).
No normalization is needed for bits; for seconds use the same rate on both sides.

**Exceptions [F]**
1. `rate_model = mcs`, while a packet is in service: `_service_rate_bps is None` → `work_ahead_bits_proxy = None`
   (kernel.py:3810-3816; unit-checked: `{'value': 1200, 'work_ahead_bits_proxy': None}`). Then the advertisement
   carries no sample, the prediction is `no_received_history`, and the truth is `None` → row unlabeled. Coverage
   therefore drops exactly when a link is busy, i.e. where error matters most. [I] MCS pilot cells will
   under-report coverage and bias MAE downward.
2. **Downlink candidates are a different quantity**: `predicted_bits` comes from the terminal
   advertisement's `queue_bits` (queued only, kernel.py:5083-5090), there is no in-service component, and the probe
   cannot parse a downlink id — see B1 below.
3. If `queue_weight != 1.0` the scorer scales `resource_work_s` after prediction (time_alignment.py:1194-1201);
   an oracle re-pricing must re-apply that weight.

---

## 4. Candidate target instant — mismatch confirmed

**Where instants are derived [F]**
- `estimate_eta` returns `snapshot_at + sum(terms)` (time_alignment.py:886-967), i.e. **T_arrival** at the peer egress
  (the peer decision delay term is `peer_process_s`); persisted as `eta_targets` (kernel.py:5508-5510).
- The instant each arm actually **queries** is `targets[direction]` from the shared planner, persisted as
  `query_targets` (kernel.py:5514): `arm_target_at` maps stale→last measurement, now→snapshot,
  common→snapshot+horizon, candidate→ETA (time_alignment.py:844-861).
- For the candidate arm the default rule adds one fixed-point step:
  `target = target + predicted_bits / resource_service_bps` then re-predicts (time_alignment.py:1396-1413).
  Default `candidate_target_rule = "arrival_plus_queue"` (config.py:590-599, time_alignment.py:383);
  `"arrival"` (T_arrival exactly) was added today in `171c8ef` and is **set nowhere** outside
  `CODE/experiment_platform/ta_controlled.py:277` (grep over all `*.yaml/*.json/*.py`).
- The probe takes its label instant from `eta_targets`, not `query_targets`:
  `"eval_target_s": etas.get(direction)` (kernel.py:6179), while `"algo_query_target_s": queries.get(direction)`
  (:6178) is recorded beside it.

**Consequence [F/I]** — the probe labels truth at T_arrival, while the scored `predicted_bits` (and
`resource_work_s`) were produced for `query_targets[d]`. Measured offsets in local runs:
- candidate + ad available + `predicted_bits > 0`: offset = `predicted_bits / rate` (the queue-wait step);
- candidate + no history (fallback): `query_target_s = snapshot_at`, offset = `eta − snapshot`
  (observed 100 ms with `compute_delay_s=0.1`; 122/160 rows in one fixture);
- arms stale/now/common: offset = `eta − (last_measured | snapshot | snapshot+horizon)` — i.e. the mismatch
  exists for **every non-candidate arm** too, because the probe always labels at the ETA.
[I] For the pilot semantics defined by `171c8ef` (candidate target = T_arrival at peer egress) the probe's
instant is the intended one, and the *scorer* must be told `candidate_target_rule: arrival`; with the default
`arrival_plus_queue` the probe measures "prediction for T_service, truth at T_arrival", which understates
prediction error by exactly one predicted queue interval. The recorded 2026-10-19 run predates `f666b59`
(2026-10-26, which introduced the extra step), so its published bit-error numbers were taken with
`query == eta` and are **not** comparable to a post-`f666b59` run unless `arrival` is set.

*Recorded-run documentary evidence [F]:* `PREDICTION-PROBE.md` states the probe reads "于预测自己的评估目标时刻"
and that it checks `eta_targets == eval_target` (0 mismatches), but no check compares `eval_target` with
`query_targets`. So the mismatch is currently invisible to the existing self-checks.

---

## 5. Existing analysis / metrics support, and the oracle gap

**Already implemented and wired [F]**
| Path | Flags / entry | Output fields |
|---|---|---|
| `CODE/experiment_platform/prediction_accuracy.py` | `python3 -m ... prediction_accuracy.py <probe.jsonl...> [--json out.json]` | `rows`, `labeled`, **`coverage`** (=labeled/rows, :118), `missing_reasons`, `overall{n, bias_mean_bits, mae_bits, median_abs_bits, p90_abs_bits, max_abs_bits, truth_nonzero_rate, pred_nonzero_rate}` (:87-101), plus `by_arm`, `by_direction`, `by_age_bucket`, `by_horizon_bucket`, `by_horizon_offset`, `by_selection{chosen,not_chosen}` (:130-170) |
| `CODE/experiment_platform/t1_tasks.py` | network_alignment task, `--target-probe --target-probe-max N --target-probe-stride N --target-probe-horizons 0.005,0.01,...` (:2601-2612) | probe written per arm as `<out>-target-probe-<arm>.jsonl` (:1872-1875); accuracy computed automatically per arm and stored as `prediction_accuracy` in the arm result (:1560-1571); decisions retained in `routing_audit_log.decision_records` (:927-975, append :950, sampling :938/:869, key :917; :1637) with `four_direction_audit` + `observation_at_start.time_alignment` (all candidate scores/terms/eta/query instants) |
| `CODE/work/WP-T1-SCENE-CAL/probe_labels.py` | `--probe … --arms-root … --arm candidate [--stride N] [--out]` | coverage + missing reasons, prediction/score consistency (`predicted_bits/rate == predicted_work_s`), `sampled_at == eval_target`, `eta_targets == eval_target`, identity/generation correspondence, `errors_bits{signed,predicted,label}` incl. `mean_abs`/`median_abs`/`p90_abs`, nonzero rates, selected/rejected split |
| `CODE/work/WP-T1-SCENE-CAL/probe_cell_summary.py` | `--cell-dir … [--out]` | probe summary roll-up (rows/enqueued/stride/sampled/reason_counts/target range) |
| `CODE/work/WP-T1-SCENE-CAL/scene_scorecard.py` | reads `arms/<arm>/target_probe.jsonl` | per-cell scorecards |
| `ANALYSIS/TIME-ALIGNMENT-20261030/analyze_truth_trajectory.py` | `--extract … --qlevels … --svcwin …` | offline truth re-pricing: `C_X = recorded_total − resource_work_s + Q_X/rate` at `t_g`/`t_d`/`T_arrival`, rankings `C0/C1/C2`, `rank_changed_*`, `action_changed_vs_recorded_*`, margin/regret distributions (:186-247, :192, :205). Uses **queue-ledger reconstruction**, not the probe |

**Executed check [F]** — `prediction_accuracy.summarize` on a local 160-row probe file: `rows=160 labeled=38
coverage=0.2375`, overall `n=38 bias=-5773.04 MAE=5773.04 median=0.0 p90=24375.08`, `by_selection.chosen` also
present. Note that all 38 of those "labeled" rows were the **invalid identity-fallback rows** of B1 — the script
does not filter them, so its accuracy numbers can be wrong (bias here is an artifact, not a predictor property).

**Missing for the pilot [F]**
1. **`prediction_error_seconds` is produced nowhere** (grep over CODE/ANALYSIS: only `predicted_work_s` and
   `resource_work_s` exist). It is derivable per row:
   `(predicted_bits − truth.work_ahead_bits) / service_rate_bps` (prediction-consistent rate; the truth dict also
   carries `rate_bps`, the live request-time estimate at target, if a second variant is wanted).
2. **No oracle re-ranker over probe truth.** `analyze_truth_trajectory.py` re-prices with reconstructed truth at
   `T_arrival` and reports rank/action changes, but it does not read the probe, and it uses `eta` while the
   recorded `total_s` was produced at `query_target` (same instant issue as §4).
3. **No per-decision label-completeness metric** (fraction of decisions whose *every* candidate direction has a
   valid truth). `coverage` is per-row only; oracle re-ranking needs per-decision completeness.
4. **No `resource_kind` in probe rows** — a consumer cannot tell from a row alone whether `predicted_bits` refers
   to an ISL egress or a downlink terminal; it must join `candidate_resources` from the decision log.

**Can the oracle be reconstructed from one run's outputs? [F/I]**
- Per-candidate `predicted_bits`, `service_rate_bps`, `score_total_s`, `predicted_work_s`, `fallback`,
  `selected`, `resource_id`, `generation`: **in the probe row**.
- Per-candidate all other score terms (`terms`, incl. `resource_work_s`): **in the decision record**
  (`observation_at_start.time_alignment.scores[d]`, persisted via `routing_audit_log`, t1_tasks.py:943-975),
  joined by `(pid, decision_id)` exactly as `probe_labels.py` already does (:39-45).
- Truth bits: **only** in probe rows (and only for ISL, valid identity).
→ **Yes in principle** for decisions where (a) every candidate has a valid labeled truth, (b) `eval == query`
instant, (c) `queue_weight` is known/re-applied, and (d) the decision survived the audit-log sampling cap
(`T1_AUDIT_MAX_DECISIONS`, default 5000, t1_tasks.py:938). **Not yet possible as-is** because (a) the identity
fallback silently produces wrong labels, (b) the default instant differs, and no script performs the join.
Local evidence for the coverage risk: in the 4-candidate fixture, 0/40 decisions had ≥2 labeled candidates
(1 labeled direction each), so an oracle join would have had **zero** usable decisions; the recorded
population run claims ~2.7 candidates/decision (17,348 selected + 29,666 rejected labels over 47,014 labels),
so real runs do produce multi-candidate labels — but that must be re-measured per pilot cell, not assumed.

### B1 — wrong-resource labels via the identity fallback (reproduced)
`_enqueue_target_probe` uses `used_state[direction].resource_id` (the predicted peer egress) when present, else
falls back to the **live audit's** `candidate_details[direction].resource_id`:
```python
if resource_id is None and detail.get("resource_id"):                    # kernel.py:6137
    resource_id = detail.get("resource_id")
    identity_source = "live_audit_not_used_by_predictor"                 # :6143
```
but `candidate_details[*].resource_id` is the **local first-hop** link:
`f"isl:{sat}:{peer}"` with the local link generation (kernel.py:6813-6826 region, `resource_id` :6851,
`generation` :6853). For a candidate whose prediction resource is the terminal downlink
(`candidate_resources[d].status == "delivered_downlink"`, kind `downlink`), the prediction is about the GSL
terminal, while the probe resolves `isl:<sat>:<peer>` and reads the local ISL egress.
Reproduced locally (5-sat plus fixture, `downlink_rate_mbps=100`, `isl_rate_mbps=5`):
```
candidate_resources[E] = {status: delivered_downlink, kind: downlink, peer: 2, downlink_rate_bps: 1e8}
score[E] = {service_rate_bps: 1e8, predicted_bits: 0.0, terms: {..., terminal_tx_s: 8e-05, ...}}
probe row = {identity_source: "live_audit_not_used_by_predictor (resource_id+generation)",
             resource_id: "isl:0:2"}
truth    = {resource_id: "isl:0:2", rate_bps: 5e6, queued_bits: 0, work_ahead_bits: 0.0}
```
Prediction rate 1e8 vs truth rate 5e6 — the label is for a **different resource**. In that fixture
58/58 labeled rows were of this kind. `probe_labels.py` cannot catch it because it computes the expected
identity with the same fallback (`expect = used.get("resource_id") or detail.get("resource_id")`, :117),
so its "resource_identity_mismatch = 0" is self-consistent with the bug.
**This is the defect the module docstring says was already fixed** (target_probe.py:264-269: "5979/6313 rows
resolved to isl:74:82 while the prediction was about isl:82:90") — reintroduced on the fallback path, and now
pointing the other way (predictor resource = downlink, probe resource = local ISL).

---

## 6. Targeted tests — exact counts (run at HEAD, read-only)

Command: `python3 -m pytest -q <file>` from the worktree root (python 3.14, simpy 4.1.1).

| Test file | Result |
|---|---|
| `CODE/leo_sim/tests/test_target_probe.py` | **2 failed, 6 passed** |
| `CODE/leo_sim/tests/test_probe_causal_review.py` | **9 passed** |
| `CODE/leo_sim/tests/test_probe_horizons.py` | **1 passed** (stride applied before horizon expansion) |
| `CODE/leo_sim/tests/test_eta_terms.py` | **14 passed** |
| `CODE/experiment_platform/tests/test_prediction_accuracy.py` | **3 passed** |
| **total** | **29 passed, 2 failed** |

Failures (both in the truth-read path, both `AttributeError: 'Link' object has no attribute 'arrival_rate_bps'`
raised at `target_probe.py:232` → `kernel.py:3837` `link.arrival_rate_bps(float(now))`):
1. `test_probe_reads_the_kernel_own_work_ahead_definition` (the test that pins
   `queued 1200 + in_service 300 = 1500`);
2. `test_out_of_order_targets_are_each_read_at_their_own_instant`.

Root cause **[F]**: kernel commit `7e31c79` (2026-10-24, ancestor of HEAD) added the `arrival_rate_bps` call to
`_isl_resource_state_advertisement` (kernel.py:3837); the test-local `Link` stubs in
`test_target_probe.py` (classes at :90, :140, :208) were never updated (test file last touched by
`5e9d9cd`, 2026-10-24). Production `ISLLink.arrival_rate_bps` exists (kernel.py:978-982), so only the tests are red.
**Consequence: at HEAD there is no green test of the probe's truth-read path**; the passing probe tests cover the
sampler/stride/horizon/off logic only. Do not fix by changing production code.

---

## 7. Requirements table + direct answers

### 7.1 Requirement verdicts

| Requirement | Verdict | Evidence / blocker |
|---|---|---|
| Registered only after action commit | **PASS-WITH-CAVEAT** | hook kernel.py:6063-6074 inside `_record_decision`; called after `_apply_forced_action`/decision_id (:7343-7345) but before `put_data` (:7352) / `dl.put` (:7056, :7210) / frozen `put_data` (:7068). Only rows are stored; sampling is later. Strict reading "after the action took effect" is NOT met. |
| No influence on online policy | **PASS** | A/B: fates/chosen/deliveries/stop identical; only `state_version` in decisions and `events_processed` differ (§2). No reader of `kernel.target_probe` except `run()`/`write()` (kernel.py:7575-7578, :7864-7868). `state_version` gate (`validate_joint_plan`) has no live caller. |
| No future reads for decisions | **PASS** | truth is read only into `records`; no path returns it to routing/learning/metrics; `feeds_online_policy: False` in summary (target_probe.py:351-355). |
| No business RNG consumption | **PASS** | imports `bisect/json/math` (:57-59); accessor/RNG-free `_link_rate`/`arrival_rate_bps`; recorded run stride 1 vs 4 identical fates (PROBE-RESULTS-20261019.md). |
| No business resource occupancy | **PASS** | attribute reads only; no request/release/hold (§1, §2). |
| Same resource identity + generation | **FAIL** | `used_state` path validates `isl:<peer>:<peer_egress>` + generation (target_probe.py:270-303) — correct. Fallback path (kernel.py:6137-6147) labels a downlink-target prediction with the local first-hop ISL (kernel.py:6851/6853) → B1, reproduced (§5). |
| Reads true workload at eval target instant | **PASS-WITH-CAVEAT** | `sampled_at_s == eval_target_s` (0/30 mismatches locally; probe_labels check in recorded run); truth = live accessor at that instant. Caveat: eval instant is ETA (T_arrival), not the scored query instant (§4). |
| Truth matches predicted workload definition | **PASS (ISL) / FAIL (downlink)** | both = queued(data+ctrl)+in-service for ISL (§3); downlink predicted_bits = terminal queued bits only and truth resource is wrong. MCS in-service → both sides unknown (unlabeled). |
| Same instant as candidate prediction | **FAIL (default) / PASS with `candidate_target_rule: arrival`** | kernel.py:6179 uses `eta_targets`; candidate default target = ETA + predicted_bits/rate (time_alignment.py:1396-1413; config.py:590-599); no profile sets `arrival`. For stale/now/common the mismatch exists structurally. |
| MAE/median/p90/bias/coverage + seconds | **PASS-WITH-GAP** | `prediction_accuracy.py` gives the first five per arm/group; **`prediction_error_seconds` absent everywhere** (derivable from `predicted_bits`, `truth.work_ahead_bits`, `service_rate_bps`). |
| Offline oracle re-ranking | **PARTIAL** | data sufficient in principle (§5); no probe-based script, no per-decision completeness gate; blocked in practice by B1 + §4. `analyze_truth_trajectory.py` is the reconstruction-based precursor (uses queue ledger, not probe). |

### 7.2 Direct answers

- **After a run, can we compute predicted vs true workload at T for every sampled candidate (coverage)?**
  We can compute it for every row that gets a *valid* label, and coverage is **reported** (`coverage`,
  `missing_reasons`, per arm/direction). But **not for every sampled candidate**: (i) downlink candidates are
  either unlabeled or mislabeled via B1; (ii) candidates without a peer-egress advertisement are `no_prediction`;
  (iii) MCS in-service rows have no truth and no prediction; (iv) stride/cap sample only a subset;
  (v) generation change/retire drop rows. [I] Therefore coverage must be quoted per cell/arm together with the
  reason histogram, and B1 rows must be excluded before any accuracy number is claimed.
- **Can we do the offline oracle re-ranking with existing data?**
  **Not with existing scripts; yes with one small offline script once B1 and the instant rule are fixed.**
  Required join: probe rows ⋈ `routing_audit_log.decision_records` on `(pid, decision_id)`; require every
  candidate of the decision to have a valid truth; then
  `total_oracle = total_s − terms.resource_work_s + truth.work_ahead_bits / service_rate_bps`
  (re-apply `queue_weight` if ≠ 1.0); re-rank; never feed back online.
- **Minimal instrumentation change (specified, NOT implemented):**
  1. `kernel.py:6179`: record truth at the instant the prediction was made for —
     `"eval_target_s": queries.get(direction, etas.get(direction))` and keep the ETA as a new
     `"eta_target_s"` field. (Alternative, config-only: set `time_alignment.candidate_target_rule: arrival`
     in the pilot profile if T_arrival is the intended contract; that makes candidate rows coincide but leaves
     stale/now/common mismatched.)
  2. `kernel.py:6137-6147`: delete/guard the `live_audit_not_used_by_predictor` identity fallback. Only probe
     when the prediction's resource really is an ISL egress (`candidate_resources[d].status == "ok"` and
     `kind == "isl"`); otherwise enqueue a reason row (e.g. `target_resource_not_an_isl`) with no truth.
  3. Probe row: add `resource_kind` (from `candidate_resources`) and `predicted_target_s` (= the planner's query
     target) so the audit is self-describing without a decision-log join.
  4. `prediction_accuracy.py`: add `prediction_error_seconds` (per-row and in `_stats`), a `labeled_valid`
     filter on `identity_source == "used_state"`, and per-decision completeness
     (`decisions`, `decisions_all_candidates_labeled`, histogram of labeled candidates/decision).
  5. New offline script only: join + oracle re-rank + regret (`oracle_only: true`), no kernel change.
  6. Tests: add `arrival_rate_bps` to the local `Link` stubs in `test_target_probe.py` (test-only), then re-run.

### 7.3 Blockers for the pilot

- **B1** wrong-resource labels (must fix before any MAE/coverage can be claimed from a run).
- **B2** label instant ≠ scored instant unless `candidate_target_rule: arrival` (semantics of the error metric).
- **B3** 2 red probe tests at HEAD (no green truth-read regression test).
- **B4** no `prediction_error_seconds`, no per-decision completeness, no oracle script.

---

## 8. Evidence index (all read-only)

- `CODE/leo_sim/target_probe.py`: 62, 65-94, 100-172, 175-213, 215-255, 256-307, 317-362.
- `CODE/leo_sim/kernel.py`: 978-982, 1167-1174, 1948-1968, 2897-2905, 2977, 3653-3655, 3784-3838,
  4491-4495/4637-4641, 4578-4605, 5057-5079, 5093-5135, 5489-5530 (5508-5514), 6063-6074, 6111-6188
  (6130-6147, 6178-6179, 6188), 6841-6853, 7052-7068, 7206-7210, 7343-7352, 7575-7593, 7864-7868.
- `CODE/leo_sim/time_alignment.py`: 281-296, 646-667, 844-861, 886-967, 1037-1051, 1131-1145, 1194-1207,
  1370-1415.
- `CODE/leo_sim/config.py`: 292-306, 589-599, 1284-1287.
- `CODE/experiment_platform/prediction_accuracy.py`: 87-101, 104-171 (coverage :118), 208-221.
- `CODE/experiment_platform/t1_tasks.py`: 927-975, 1560-1571, 1637, 1873-1877, 2601-2612.
- `CODE/work/WP-T1-SCENE-CAL/probe_labels.py`: 39-45, 66-133 (fallback expectation :117);
  `PREDICTION-PROBE.md`; `PROBE-RESULTS-20261019.md` (recorded 47,755-row run: coverage 98.45%,
  MAE 766.4 bit, bias −489.0, `no_prediction` 741; stride 1 vs 4 business-identical).
- `ANALYSIS/TIME-ALIGNMENT-20261030/analyze_truth_trajectory.py`: 1-30, 186-247 (C0/C1/C2, :192, :205),
  249-360 (rank/action change summarisation).
- Local executions (this audit; fixture-only, /tmp; **not** VM evidence):
  A/B probe on/off; 2-sat 30-row instants; 5-sat plus 160/240/800-row coverage + downlink rate-mismatch proof;
  MCS `work_ahead=None` unit check; pytest counts above.
