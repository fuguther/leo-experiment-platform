# AUDIT-ROUTER — the distributed hop-by-hop router as a policy

Read-only source audit. **No code was modified, no test was run, no simulation was started, no VM
was contacted.** Every claim below is a static-reading claim against the cited line.

- Repository: `/Users/lge/Desktop/topic/.wt-instr-20261030/CODE` (worktree `instr/ledger-export-20261030`)
- Commit: `7e7fd5a` — *instrument(queue): opt-in exact queue ledger for offline truth reconstruction* (clean tree)
- Path prefix: every `file:line` below is relative to `CODE/`. Main files read:
  `leo_sim/time_alignment.py` (1 496 lines), `leo_sim/routing.py` (367), `leo_sim/kernel.py` (7 956),
  `leo_sim/config.py` (1 588), `leo_sim/model.py`, `leo_sim/control.py`, `leo_sim/receipt.py`,
  `leo_sim/decision_ledger.py`, `leo_sim/queue_ledger.py`,
  `leo_sim/profiles/stage_c_e2_6g_adv200.yaml` (98).
- Claim status convention: **[CODE]** verified at the cited line · **[INFERENCE]** follows from code but
  was not measured in a run · **[ESTIMATE]** arithmetic from the constants named in §8.1.

Purpose: make sure a temporal ("state time-misalignment") effect can never be produced *silently* by a
router artefact. The conclusion of this audit is (a) the router is fully describable as a deterministic
ordering over a frozen snapshot, (b) under the four arms exactly **one** scored term can change (§7.2), and
(c) the artifact already records, for ISL targets, both *which* queue was priced
(`observation_at_start.candidate_resources`) and *how old* the sample was (`time_alignment.used_state`,
kernel.py:5120–5135) — so a decision-level attribution is possible **today**. What remains unrecorded is the
reference needed for a path-length claim (the serving set and a shortest-path propagation distance, §6.4).

---

## 0. Verdict summary (read this if nothing else)

| Question | Answer | Anchor |
|---|---|---|
| Is the action set closed and small? | `{deliver} ∪ {N,E,S,W} ∪ {hold}`; forwards are only current 4-direction ISL neighbours | §2.1 |
| Does the scorer choose the action? | No. It **reorders** the legal forward candidates; the commit rule is `action = legal[0]` | §2.3 |
| Is there a path-progress / bounded-detour constraint? | **No.** Only (i) no revisit of a visited satellite, (ii) `max_hops` (64 in this profile), (iii) a *priced* remaining-hop prior. `routing.min_remaining_hop_only` exists but is **off** | §5 |
| Is the router queue-sensitive or propagation-prior-dominated? | Score magnitudes are **propagation-prior dominated**; the queue is a **narrow tie-breaker** limited to ≈3.3 hop-equivalents of score range | §8, §9 |
| `known_serving` semantics | Unit-hop policy + the **true current serving set** substituted for the advertisement-derived serving set. No progress constraint, no cost change | §5.2 |
| Queue weight default | **1.0**; precedence `config queue_weight` > `T1_QUEUE_WEIGHT` env > 1.0 | §4.3 |
| Path stretch measurable today? | Numerator **exact** from `ledgers.json`; denominator **not** a recorded field — requires an offline geometry recomputation or one new field | §6 |
| Number of scored terms that depend on the arm | **exactly 1** — `resource_work_s`, out of 11 (ISL target) / 12 (downlink target) scored terms — plus one instant shift for the `candidate` arm | §7.2 |

Three corrections to premises that are worth stating before any number is quoted:

1. `time_alignment.state_source` does **not** change the instant the state is queried. It changes the
   **source of the value** (newest *received advertisement* vs the peer's *true current* queue at t0).
   The *instant* is changed by `time_alignment.arm` only. See §7.3.
2. The "remaining propagation" term is **not** a distance: it is `remaining_hops × local per-hop
   propagation` (§4.1 T11). It is a hop-count prior wearing a seconds unit.
3. `deliver` is a **hard priority**, never a scored candidate (§2.1). No score ever compares
   "deliver now" against "forward and deliver later".

---

## 1. Where the decision happens (call graph, deterministic router)

`Kernel._decide` (kernel.py:7110–7394) is the whole policy for one hop. It is reached after a packet
arrives at a satellite (`_ingress_after_prop` kernel.py:7468–7482 / `_isl_arrive_after_prop`
kernel.py:7484–7498 append the satellite to `pkt.path` first, kernel.py:7476, 7491).

```
_decide(pkt, sat, now)                                        kernel.py:7110
├─ deadline expired                     → _fail(DATA_DEADLINE_EXPIRED)       :7128–7135
├─ len(pkt.path) > routing.max_hops     → _fail(NO_ROUTE)                    :7136–7143
├─ deliver legal now? (downlink active, GSL visible, rate>0, downlink room)
│    yes → commit "deliver"                                                 :7144–7212
├─ cands, status = routing.choose_next_hop(policy, …)                        :7222–7241
│    status=="unreachable" → _fail(NO_ROUTE)                                 :7242–7251
│    status=="no_info"     → hold (cp enabled) / _fail(NO_ROUTE) (cp off)     :7252–7275
├─ loop filter: drop directions whose peer ∈ pkt.path                        :7276–7279
├─ [optional] routing.min_remaining_hop_only filter (default False)          :6721–6785, 6976
├─ cands, ta_audit = _time_aligned_order(...)   ← the state-time scorer      :7283–7285
├─ legality re-check on the reordered list: geometry, MCS rate, queue room   :7286–7319
│    any legal → action = legal[0] → commit "forward"                        :7320–7348
│    else if some candidate was "unavailable" → hold                         :7349–7365
│    else if cands non-empty → _fail(ISL_QUEUE_OVERFLOW)                     :7366–7384
│    else                    → _fail(NO_ROUTE)  (all candidates loop)         :7385–7394
```

`_time_aligned_order` (kernel.py:5260–5334) dispatches on `time_alignment.execution_mode`:
`per_packet` → `_score_order` (kernel.py:5368–5549, the full path described below); `per_flow` → cached
ranking for `per_flow_ttl_s`; `precomputed` → installed table; `async_point|async_window` → `_async_order`
(installed schedule bins, kernel.py:4842–4875). **The profile under study is `per_packet`**
(profiles/stage_c_e2_6g_adv200.yaml:84–88 sets only `enabled/arm/predictor/history_limit`;
`execution_mode` default is `per_packet`, config.py:591).

Two consequences that matter for any temporal study:

- **[CODE]** With `time_alignment.enabled: false` the candidate order is untouched and the audit is
  `None` (kernel.py:5276–5277): the time-alignment machinery is a pure optional overlay.
- **[CODE]** The scorer is **ordering-only**: `_score_order` returns `order` (kernel.py:5421–5426) and
  `_decide` takes `legal[0]` (kernel.py:7337). Legality/loop/deliver/fallback rules are shared by all
  four arms, so an arm difference can only be a *permutation* difference.

---

## 2. Action space and legal-candidate construction

### 2.1 Action space [CODE]

| Action | Precondition (in order of evaluation) | Anchor |
|---|---|---|
| `deliver` | endpoint has an **active** association with this satellite ∧ `geometry.gsl_available` ∧ (`rate_model != mcs` ∨ downlink rate > 0) ∧ `downlinks[sat].room(pkt.bits)` | kernel.py:7144–7212, `_deliver_legal_now` 6689–6702 |
| `forward d` (`d ∈ {N,E,S,W}`) | `d` is a current ISL neighbour (`self.topo[sat][d]`), not in `pkt.path`, passed the candidate filter (§3), geometry up, MCS rate > 0, `isls[sat][d].room(pkt.bits)` | kernel.py:7222–7319 |
| `hold` | (a) candidate temporarily unavailable (geometry/rate down); (b) `no_info` with control plane enabled; (c) deliver blocked by zero MCS rate; (d) deliver blocked by full downlink queue? **no** — that is a failure, see below | kernel.py:7349–7365, 7252–7275, 7177–7184 |
| `fail` | deadline, `max_hops`, `no_info` with control plane disabled, `ISL_QUEUE_OVERFLOW`, `ACCESS_QUEUE_OVERFLOW`, `HOLDING_QUEUE_OVERFLOW`, "every candidate loops" | §4.5 |

Notes:
- `deliver` is evaluated **before** any scoring and is not an element of the scored candidate list.
  A "peer is serving the destination" *forward* is scored, but it is priced as an ISL action whose target
  resource is the peer's downlink (§4.1 T9/T12/T13), not compared against local delivery.
- There is no "wait for a better future" action and no future-information action: `hold` is the only
  temporising move, and it re-decides later (see §4.5).

### 2.2 Candidate construction: `routing.choose_next_hop` [CODE]

`routing.py:185–360`. For the deterministic router (`learning.algorithm: none`) it runs with
`cache_hops=None` (kernel.py:7218–7221) and `best_only=False` (kernel.py:7234).

1. **Serving set (the sole destination anchor)**
   - `policy ∈ {hop, delay, capacity, info_queue, info_physical}` → `destinations_in_cache(...)`
     (routing.py:171–182): satellites whose *valid, actually-arrived* advertisement lists the destination
     cell in `serve_cells`. **Visibility alone never makes a satellite a legal egress** (routing.py:173–175).
   - `policy ∈ {oracle, hop_known_serving, known_serving}` → `oracle_targets`, i.e. the caller-supplied
     true serving set `[s for s in _serving_sats(dst) if s != sat]` (routing.py:208–219,
     kernel.py:7227–7229, `_serving_sats` 4159–4163, `_serving_source` 4165–4177).
2. **Visited-node exclusion**: `forbidden = set(pkt.path)`; targets ∩ forbidden removed; if empty →
   `("unreachable")`. The same `forbidden` set is removed from the reverse-adjacency graph
   (`without_nodes`, routing.py:153–168, 237).
3. **Distance to the nearest target**, over the **true directed** graph, expanded backwards from the
   targets over the reverse adjacency (`_reverse_adj` routing.py:96–103, so `dist[x]` = cost of a real
   directed path `x → target`):
   - `hop`, `info_queue`, `info_physical`, `hop_known_serving`, `known_serving`: unit cost, multi-source
     BFS (routing.py:263–269, `_multi_source_bfs` 131–150).
   - `delay`: edge cost = locally observed propagation (`a == sat` → current `isl_range_km`; remote edge →
     *cached advertised* per-direction propagation that is generation/peer-bound, routing.py:241–261).
   - `capacity`: `c + q_bits/rate`, unknown queue ⇒ `inf` (routing.py:278–310).
   - `oracle`: perfect current propagation (routing.py:270–273).
4. **First-hop score and ordering**: for each neighbour `d` with finite `w + dist[peer]`, sort by
   `(total, d)` (routing.py:321–360). `known_serving`/`hop`/`hop_known_serving` use `w = 1.0`
   (routing.py:323–324) — **the ordering is pure minimum-hop-to-serving, ties broken alphabetically by
   direction**.

So the candidate list is: *"every current ISL neighbour that can still reach (in the policy's metric) at
least one satellite believed to serve the destination, ordered by that metric"*. It is a **reachability**
criterion, not a progress criterion: a neighbour that is *further* from the destination is still a legal
candidate as long as *some* path remains.

### 2.3 What the scorer may do with the candidates [CODE]

`_score_order` builds an `ObservationSnapshot` (`_build_ta_snapshot`, kernel.py:4958–5254), calls the
single shared planner `_ta.plan_decision` (kernel.py:5408; time_alignment.py:1404–1416), then

```
order  = [d for d in scored.ranking if d in scored_cands]      kernel.py:5421
order += [d for d in cands if d not in order]                  kernel.py:5422
```

i.e. **candidates the scorer marked `fallback` keep their policy order and are appended**, and any
candidate the scorer cannot see at all is preserved at the end. Then `_decide` re-runs the legality gates on
that order and commits `legal[0]` (kernel.py:7290–7337). The scorer therefore cannot add, remove or
manufacture an action.

Two sanctioned *action* overrides sit outside the scorer and must be off (they are off in this profile):
`learning.algorithm != none` hands the choice to the learner over the same legal mask
(`_learning_action`, kernel.py:7325–7335, which fails loud if the learner returns an illegal action); a
supplied `inference_policy` may pick any candidate inside `probe.legal_directions`
(kernel.py:5411–5417, 5423–5426).

---

## 3. Filters, in the order they execute [CODE]

| # | Filter | Rule | Where | On failure |
|---|---|---|---|---|
| F1 | deadline | `now >= pkt.deadline` | 7128–7135; also on every arrival 7477, 7491, 7504 | `fail(DATA_DEADLINE_EXPIRED)` |
| F2 | **max_hops** | `len(pkt.path) > routing.max_hops` → fail. `pkt.path` grows by one on each satellite ingress (7476, 7491), so `max_hops = 64` permits a 64-satellite walk (63 ISL hops) before the 65th satellite fails. Default is 16 (config.py:528); this profile sets 64 (profile:83) | 7136–7143, 7034 | `fail(NO_ROUTE)` |
| F3 | terminal/downlink availability (live) | active association ∧ `gsl_available` ∧ rate > 0 ∧ downlink `room()` | 7144–7212, 6689–6702 | parked as `hold` (zero MCS rate, 7177–7184) or `fail(ACCESS_QUEUE_OVERFLOW)` (7205–7211) |
| F4 | reachability | no serving target reachable in the policy metric → `unreachable` | routing.py:319–320, kernel.py:7242–7251 | `fail(NO_ROUTE)` |
| F5 | information | no serving advertisement at all → `no_info` | routing.py:220–221, kernel.py:7252–7275 | `hold` if `control_plane.enabled` else `fail(NO_ROUTE)` |
| F6 | **loop prevention** | drop `d` whose peer ∈ `pkt.path` (also applied inside the resource map: `candidate_peer_already_visited`, kernel.py:5701–5708) | 7276–7279 | candidate removed; if that empties the set → `fail(NO_ROUTE)` (7385–7394) |
| F7 | visibility of control state | advertisement usable only if `generated_at <= received_at <= now <= generated_at + ttl_s` (default `ttl_s = 10.0`, config.py:522); per-direction records must match the **current** neighbour of that direction and the **current** resource `generation` | control.py:44–51; kernel.py:4443–4460, 5092–5096, 5714–5724, 5796–5803 | that sample/resource is missing → §3.1 fallback |
| F8 | geometry (ISL) | `cfg_links.geometry_loss and not isl_available(sat,peer,now)` → not legal (`isl_available` = range **<** `max_isl_km` ∧ chord clears the Earth) | 7292–7296; model.py:273–293 | candidate unavailable → `hold` (7349–7365) |
| F9 | MCS rate (ISL) | `rate_model == mcs and link_rate <= 0` → not legal; attributed to GE outage if GE down, else counted `mcs_zero_rate_holds` | 7297–7317 | candidate unavailable → `hold`, wake at certified recovery |
| F10 | queue room | `isls[sat][d].room(pkt.bits)` ⇔ `data_bits+ctrl_bits+bits <= isl_queue_bits` | 7318–7319; kernel.py:963–965 | if candidates existed but none had room → `fail(ISL_QUEUE_OVERFLOW)` (7384) |
| F11 | scorer fallback (missing state) | any missing term ⇒ `total_s = inf`, `fallback = True` (time_alignment.py:1090–1107, 1167–1175) | §4.4 | candidate ranked after all fully-known candidates |
| F12 | schedule-table filters (async / per_flow modes only) | `lookup_schedule` accepts an action only if it is in `legal` and not in `path`, and the bin covers `now` | time_alignment.py:1474–1496 | fallback to the unchanged policy order (`_async_order` kernel.py:4851–4859) |
| F13 | restricted routing (diagnostic, **default off**) | keep only candidates achieving the minimum remaining hop count to a *received* serving advertisement; not applied if any distance is unknown | 6721–6786; config default False config.py:529 | not applied (audit says why) |

**Not present anywhere:** a "must get closer" test, a detour budget, a stretch cap, a per-packet
visited-set beyond the satellite list, a hop-count parity/plane constraint, or a serving-set freshness
requirement (under `known_serving` the serving set is truth, under `hop` it may be seconds old).

### 3.1 Visibility of the control information (the real information boundary) [CODE]

- The router reads **only** `self.caches[sat]` (`_observed_cache_entries`, kernel.py:4400–4416) plus its
  own queues and current geometry. `_candidate_resource_map` explicitly forbids reading the peer's live
  queue (docstring kernel.py:5662–5670).
- An advertisement carries: per-direction `isl_queue_bits[d] = {peer, value, generation, rate_bps,
  arrival_rate_bps}` and `work_ahead_bits_proxy` (kernel.py:3784–3838), plus `serve_cells` and
  `downlink_resources[cell] = {queue_bits, rate_bps, propagation_s, available}` where
  `available = active ∧ gsl_available ∧ rate > 0` (kernel.py:3687–3726).
- Propagation bound: advertisements travel the real control plane up to `control_plane.vis_k` hops
  (kernel.py:2270–2274 uses `routing.control_broadcast_children`, routing.py:38–67); TTL is on **source
  time**. This profile: `vis_k = 3`, `advertise_interval_s = 0.2`, protocol v2 (profile:78–80).
- **Consequence for the study:** "freshness" of the queue state the scorer sees is set by
  `advertise_interval_s`, the control-plane's own queueing/propagation, and `ttl_s` — not by the arm.
  The arm only decides *which instant that already-received history is projected to*.

---

## 4. Score decomposition

### 4.1 The terms [CODE]

The snapshot (`ObservationSnapshot`, time_alignment.py:314–378) is built from kernel fields only
(kernel.py:4958–5254). `estimate_eta` (time_alignment.py:874–955) produces 8 non-overlapping intervals;
`score_candidates` (time_alignment.py:1059–1214) copies those 8 verbatim and adds at most 3 more. All
terms are **seconds**, and the total is their **plain sum** (time_alignment.py:1190) — no normalisation,
no per-term weights except `queue_weight` on T9.

| ID | term | unit | formula (code) | value source | class | arm-sensitive? |
|---|---|---|---|---|---|---|
| T1 | `compute_wait_s` | s | FIFO work ahead of a new compute request | local compute pool at t0 (`_compute_state_now`, 5583–5605) | local service state | no |
| T2 | `compute_service_s` | s | `execution.compute_delay_s` | config | local service (config) | no |
| T3 | `query_wait_s` | s | FIFO work ahead of the later query, drained by compute time | local query pool (`_query_state_at`, 5607–5626) | local service state | no |
| T4 | `query_service_s` | s | `time_alignment.query_delay_s` (default 1e-6) | config | local service (config) | no |
| T5 | `local_egress_wait_s` | s | `max(0, egress_bits/rate + local_in_service − (T1+T2+T3+T4))` | **own** directed-egress queue + own in-service residual | **queued work (local)** | no |
| T6 | `tx_s` | s | `pkt_bits / local ISL rate` | config/geometry | physical | no |
| T7 | `prop_s` | s | `isl_range_km(sat,peer,now)/C` | geometry now | **physical link (1 hop)** | no |
| T8 | `peer_process_s` | s | advertised peer compute+query work projected to the packet's projected arrival (`_project_advertised_peer_processing`, 958–1019; kernel 5159–5194) | **received advertisement** | peer service state (stale by construction) | no (computed from t0 fields) |
| T9 | `resource_work_s` | s | `[predicted_bits(arm instant) × (exp(−age/τ) if `state_age_tau_s`)] / target_egress_rate` | **received advertisement history + predictor at the arm's query instant** (+ optional age-reliability weight, time_alignment.py:1126–1132) | **queued work (target egress)** — *the queue term* | **YES** |
| T10 | `resource_packet_tx_s` | s | `pkt_bits / target egress rate` (ISL targets only) | advertised target rate | physical | no |
| T11 | `remaining_prop_s` | s | `remaining_hops × prop_s(local first hop)` | **hop count** over the path-constrained visible graph × local per-hop propagation | **topology prior** | no |
| T12 | `terminal_tx_s` | s | `pkt_bits / advertised GSL rate` (downlink targets only) | advertisement | physical | no |
| T13 | `terminal_prop_s` | s | `slant_range_km/C` (downlink targets only) | advertisement | physical | no |

An ISL-target candidate scores **11** terms (T1–T11); a downlink-target candidate scores **12**
(no T10, plus T12 and T13, and T11 enters as a hard `0.0`, kernel.py:5743–5758 — time_alignment.py:
1134–1166). Grouping:

- **Pure topology/geometry prior** (deterministic function of config + known geometry + packet size; no
  measurement): **T6, T7, T10, T11, T12, T13**.
- **Congestion/state** (a measured queue or service occupancy enters): **T5, T9** (network queues) and
  **T1, T3, T8** (compute/query occupancy, local or advertised).
- **Configured constants**: T2, T4.
- **Arm-sensitive: only T9** — plus, for `arm == candidate`, a shifted query instant (§7.2).

Structural facts that decide how much a "time-misalignment" effect can move anything:

- T11 is a **hop count times a local per-hop propagation**, produced at kernel.py:5835–5846:
  `remaining_hops = BFS(safe_reverse, safe_serving).get(peer)` on the graph with
  `pkt.path` removed, then `remaining_prop = remaining_hops * propagation(sat→peer)`. It is therefore
  **path-dependent** (removing visited nodes changes the distance) and **not** a physical distance: long
  and short hops are all priced at the *first* hop's length.
- `resource_work_s` is the only term whose **value** changes with the query instant, and the only term
  that `queue_weight` scales (time_alignment.py:1187–1189).
- **The predictor is unclipped online.** `max_resource_queue_bits` is a `make_snapshot` argument
  (time_alignment.py:501, 546–549) but the kernel snapshot call (kernel.py:5219–5254) **does not pass
  it**, so the `bounded_linear` slope extrapolation (time_alignment.py:802–822) is not capped at the
  physical queue capacity. The *physical* work ahead is bounded by `isl_queue_bits + one packet`, but the
  *priced* value is not enforced to be. **[CODE]** — this is a genuine bound gap for interpreting T9.

### 4.2 The ETA and the target instant (the only arm-dependent input) [CODE]

`estimate_eta` sums T1–T8 and returns `target_at = snapshot_at + Σ` (time_alignment.py:953–955). The four
arms differ **only** in which instant the predictor is asked about
(`arm_target_at`, time_alignment.py:832–849):

| arm | query instant |
|---|---|
| `stale` | `last_measured_at` = max source `measured_at` among received samples (and only those with `received_at <= snapshot_at`, `_last_measured_at` 1431–1438) |
| `now` | `snapshot_at` (t0) |
| `common` | `snapshot_at + common_horizon_s` (median/mean of candidate ETA offsets, or a fixed configured horizon; 1284–1320) |
| `candidate` | `eta.target_at`, then one fixed-point step `+ predicted_bits / target_rate` and re-predict (time_alignment.py:1384–1398) |

**`candidate` is not a pure "instant" arm** — it deliberately adds the queue wait it just predicted
(time_alignment.py:1386–1391). If a study wants "same instant, different vintage", `stale` vs `now` is the
clean pair; `candidate` adds an instant shift as a second factor.

### 4.3 `queue_weight`: default 1.0, and how to prove it stayed 1.0 [CODE]

Module default and precedence, quoted verbatim from time_alignment.py:1176–1189:

> ```
> # OPTIONAL queue weight.  Default 1.0 reproduces the historical scorer
> # exactly.  Values >1 make queueing the primary criterion, which is the
> # LQF / backpressure design point: those methods rank on congestion, not
> # on path length.  No link parameter is touched.  Precedence: explicit
> # config value (part of config_sha256) > T1_QUEUE_WEIGHT env override >
> # historical 1.0.
> queue_weight = (_QUEUE_WEIGHT if snapshot.queue_weight is None
>                 else _finite("queue_weight", snapshot.queue_weight))
> if queue_weight <= 0: raise TimeAlignmentError(...)
> if queue_weight != 1.0 and "resource_work_s" in terms:
>     terms = dict(terms)
>     terms["resource_work_s"] = terms["resource_work_s"] * queue_weight
> ```

- Default constant: `_QUEUE_WEIGHT = _positive_env_float("T1_QUEUE_WEIGHT", 1.0)` (time_alignment.py:131;
  helper 109–127 fails loud on 0/negative/NaN).
- Config path: `time_alignment.queue_weight` has **no default** so that unset profiles keep their
  `config_sha256` (config.py:599–604); validated as `None` or finite positive (config.py:1308–1315);
  forwarded into the snapshot at kernel.py:5253.
- **Only T9 is scaled.** T5 (local egress queue) is *not* scaled. A `queue_weight > 1` therefore makes the
  router prefer an empty **peer** egress over a short path, while still paying full price for its own
  local queue. This is not symmetric "LQF"; say so if it is ever used.
- Env var is part of artifact identity: `ENV_NAMES` includes `T1_QUEUE_WEIGHT`
  (experiment_platform/artifact_identity.py:33–45), recorded in the launch manifest
  (launch_manifest.py:106) and the identity doc (artifact_identity.py:272).

**How to check it stayed 1.0 in a run (three independent checks):**

1. `resolved_config.json` → `config.time_alignment` must **not** contain a `queue_weight` key
   (absence ⇒ `None` ⇒ env/default branch). If present, its value is what was used, whatever the env says.
2. Launch manifest / artifact identity → `env` must not list `T1_QUEUE_WEIGHT`.
3. From the decision stream, per candidate:
   `terms["resource_work_s"] * scores[d]["service_rate_bps"] / scores[d]["predicted_bits"] == 1.0`
   within float tolerance. Both factors are recorded separately for exactly this purpose
   (time_alignment.py:1034–1039; kernel.py:5527–5537) — note that when the weight *is* 1.0 no scaling is
   applied at all, so check (3) can only confirm, not distinguish "1.0" from "unset".
   **[CODE] coverage gap:** no test in `leo_sim/tests/` references `queue_weight` or `T1_QUEUE_WEIGHT`
   (grep, this commit), so nothing in CI pins this default.

### 4.4 Fallback, tie-break, missing state [CODE]

- A candidate is `fallback` iff any of: no resource mapping, no prediction, a prediction
  `missing_reason` (`no_received_history`, `non_positive_rate`, `no_arrival_rate`, `no_rate_ahead`,
  `non_positive_rate_ahead`), no ETA, an unknown ETA term, `predicted_bits is None`, unknown/non-positive
  target rate, downlink availability `False`/unknown, missing `terminal_prop_s`, or missing
  `remaining_prop` (time_alignment.py:1088–1175; `no_strong_common` for the `common` arm: 1078–1101).
- **Fallback ordering**: sort key `(s.fallback, value, visible_order[dir], dir)` with
  `value = 0.0 if fallback else total_s` (time_alignment.py:1202–1208). Because `fallback` is the first
  key element, **every fully-known candidate precedes every fallback candidate**; fallbacks are then
  ordered by `visible_order`, i.e. by the **route-policy candidate order** (the min-hop list from §2.2).
  `math.inf` is used only as a stored total for logging; it never enters the key.
- **Tie-break** on an exact score tie: `visible_order` (the policy order from §2.2), then the direction
  string. Note the module docstring claims an arm-independent `DIRECTION_ORDER` final tie-break
  (time_alignment.py:39–42) but `_direction_index` (196–200) is **never called** — dead code; the real
  tie-break is the policy order. Ties are therefore *not* arm-independent in the strict sense; they are
  policy-determined, which is arm-independent only because all four arms share the same policy.
- `tie_sig_digits` (diagnostics only, default `None`) quantises the score before the key (1200–1206).
  **Must stay `None`** in a temporal study: it changes which candidates tie.
- `NO_STRONG_COMMON` (common arm with no finite shared horizon, e.g. <2 fully-known candidates) marks
  every candidate fallback → the ranking collapses to the policy order (1078–1101). This is a silent
  "the common arm became the hop policy" case and must be counted, not ignored (kernel.py:5494–5496
  records `common_horizon_status`).

### 4.5 Missing-state semantics, NO_ROUTE, and the fate taxonomy [CODE]

- Nothing is imputed: an unknown term is left **out** of the sum and the candidate is marked fallback
  (time_alignment.py:1072–1074, 1116–1118). A prediction with no history returns
  `missing_reason="no_received_history"` rather than `0.0` (time_alignment.py:676–680).
- Scorer exceptions are **fail-loud**, never a silent fallback: `_ta.TimeAlignmentError` is re-raised as
  `KernelError` (kernel.py:5418–5420).
- Fatal fates reachable from the routing path: `DATA_DEADLINE_EXPIRED` (7134), `NO_ROUTE` (7142, 7250,
  7269, 7393), `ACCESS_QUEUE_OVERFLOW` (7210), `ISL_QUEUE_OVERFLOW` (7384), `HOLDING_QUEUE_OVERFLOW`
  (`_hold_packet` 2024–2044).
- `hold` is finite: it parks the packet in the satellite's `pending` area and re-decides on the
  `time_step` ticker or a certified wake (`_schedule_pending_wake` 7406–7417; `_redecide_pending` 7396–7404),
  and each hold consumes holding queue capacity. A hold is a decision and is recorded as one
  (kernel.py:2028–2033).

---

## 5. Can the router keep detouring / wandering to dodge local queues?

### 5.1 The explicit answer

**No hard bound exists, but the mechanism is self-limiting and *priced*, not *constrained*.** [CODE]

What exists (all of it):

1. **No revisits** — kernel.py:7276–7279: a direction whose peer is already in `pkt.path` is dropped.
   `pkt.path` only grows (7476, 7491), so the walk is a simple path / DAG walk. This forbids 2-cycles and
   any loop, **not** detours.
2. **`max_hops`** — kernel.py:7136–7143. This profile: **64** (profile:83). A 64-entry path is ~63 ISL
   hops ≈ up to ~460 ms of pure propagation (at 7.28 ms/hop, §8.1). That is a *loose* cap, not a detour budget.
3. **A priced remaining-path prior** — T11 adds `remaining_hops × per-hop prop` to every candidate, and
   T7 adds one hop of propagation. This is a *soft* anti-detour force, defeated whenever the queue term's
   differential exceeds it (§8).
4. **Reachability only** — a candidate is legal if its peer can still reach *some* serving satellite
   (routing.py:319–320). There is **no** requirement that the peer be closer, nor that
   `remaining_hops(peer) ≤ remaining_hops(sat) − 1`.
5. **`routing.min_remaining_hop_only`: the bounded-detour constraint that exists but is OFF** —
   kernel.py:6721–6786 keeps only candidates achieving the minimum remaining hop count, and its docstring
   names exactly this risk: *"the arm scores ONLY the shortest-remaining-hop candidates, so a detour the
   scorer prices partially can no longer be chosen"* (6973–6975). Default is `False`
   (config.py:529); this profile does not set it (profile:81–83), so **it is off in the run under study**.

Therefore: **there is no path-progress constraint and no bounded-detour guarantee.** The router *can*
take an action that increases the hop count to the destination, and it can do so repeatedly, because
each choice is independently priced and the only structural brake is the increasing remaining-path prior.
What stops unlimited wandering is the prior's arithmetic (§8), not a rule. Any claim of the form "the
router could not have detoured" is false; any claim of "the router was paid 7.3 ms per hop to stay on the
shortest path" is defensible.

### 5.2 What `routing.policy: known_serving` actually is [CODE]

`known_serving` = **`hop` policy, with the serving set taken from kernel truth instead of the received
advertisement**, and that substitution unified across *every* consumer (candidate admission, resource
map, ETA targets, scoring). Evidence:

- `VALID_POLICIES` comment, config.py:372–387: *"Equal-cost information intervention: unit-hop routing,
  but candidate admission and the path metric use the TRUE current serving set instead of the received
  advertisement. Control-plane traffic, decision cadence, scorer and path cost stay identical to `hop`,
  so a difference isolates the effect of stale state rather than of a different deployment policy."*
- `choose_next_hop`: `advertised_targets = list(oracle_targets or [])` for `oracle` /
  `hop_known_serving` / `known_serving` (routing.py:208–219); everything else identical, including
  `w = 1.0` unit first-hop cost (routing.py:323–324).
- `_serving_source` is the single source for one decision, keyed on the **effective** policy
  (kernel.py:4165–4177); `_candidate_resource_map` uses it too (kernel.py:5677–5686) — the comment there
  records that mixing the two sources *"produced a genuinely mixed-information decision (measured: 3.1% of
  known-serving decisions had every candidate marked fallback, against 0.08% for `hop`)"*.
  **That 3.1% figure is the single most important quantitative caveat in this audit** (§9.3): in those
  decisions the ranking collapses to the policy order, i.e. the router is queue-blind.
- `hop_known_serving` is the earlier, *mixed-source* version kept only for identity of published runs
  (config.py:380–384).

**Does `known_serving` imply a path-progress or bounded-detour constraint? No.** It does not touch the
cost function, the candidate set, `max_hops`, the tie-break, or `min_remaining_hop_only`. It removes the
*advertisement* as the source of the serving fact: the legal set changes accordingly, and an empty cache
no longer yields `no_info` at all (test `test_known_serving_works_with_an_empty_cache`,
`leo_sim/tests/test_routing.py:393–404`), while the policies are provably order-identical when the cache and
truth agree (`test_hop_known_serving_prices_every_candidate_at_unit_cost`, test_routing.py:414–428).
**A `known_serving` run is not "a router with a progress constraint"; it is a router with an oracle for
the destination anchor and *no* progress constraint at all.**

### 5.3 Two structural caveats that make "detour" arguments harder than they look

1. **The prior graph is denser than the physical graph.** `model.SatelliteGeometry.neighbors` returns
   `E`/`W` **by index with no range or clearance test** (model.py:560–573), and
   `routing.build_topology` only removes self-links and verifies bidirectionality (routing.py:70–93).
   With `geometry_loss: true` (config.py:509 default, not overridden by the profile) the *legality* gate
   rejects an edge whose range is `>= max_isl_km` (model.py:273–293; kernel.py:7292–7296) — for this
   constellation the intra-plane chord is ≈2 181 km (fine) but the same-index cross-plane chord near the
   equator is ≈6 048 km ≫ 3 000 km (rejected). So `remaining_hops` / BFS distances, computed on
   `self.topo`, can route through edges that **cannot transmit now**, and the packet then pays a `hold`
   instead. **[INFERENCE]** — arithmetic is in §8.1; the code path is unambiguous. Corollary: the prior
   *underestimates* hop distance, so "the router chose the closest path" is not established by T11 alone.
2. **The prior is evaluated on the path-constrained graph.** `remaining_hops` excludes `pkt.path`
   (kernel.py:5836–5841), so after one non-shortest choice the prior of the remaining candidates changes
   for reasons that are pure bookkeeping. Cross-arm comparisons of T11 at hop index `k` are therefore
   only valid while the two arms are still at the same satellite (§9.1).

---

## 6. Path stretch today: `D_actual_prop / D_shortest_prop`

### 6.1 Definition

- `D_actual_prop` = sum of realized propagation **distances** on the packet's ISL path (uplink/downlink
  ground segments excluded or reported separately).
- `D_shortest_prop` = the minimum propagation distance from the same origin satellite to the *serving
  set* of the destination at the decision instant, over the same directed topology rule (4 neighbours,
  `max_isl_km`, Earth clearance).

### 6.2 Numerator: **fully available today, exact** [CODE]

`receipt.build_ledgers` (receipt.py:626–650) always includes `packet_events` and `deliveries`, and
`write_run` always writes `ledgers.json` (receipt.py:741–743). Each realized propagation interval is
recorded at its start with stage, link identity and delay:

```
{"kind":"propagation_start","pid":…,"at":…,"stage":"uplink|isl|downlink",
 "link_id":"isl:12:13" | "gsl:uplink:…" | "gsl:downlink:…","prop_id":…,"delay_s":…}
{"kind":"propagation_arrival","pid":…,"at":…,"prop_id":…}
```

(kernel.py:2209–2237, emitted at 691, 899, 1241). Therefore, exactly:

```
D_actual_prop = Σ_{propagation_start of pid, stage=="isl"} delay_s × C_KM_S
C_KM_S = 299_792.458 km/s                              model.py:14, 20–21
```

`delay_s` is the model's own `distance/C`, so multiplying back is exact (no rounding loss beyond the
recorded float). Satellite path: `deliveries[pid]["path"]` (kernel.py:7521) gives the satellite sequence
and hence the hop count; `packet_events` covers **lost and held** packets too, not only delivered ones.

### 6.3 Denominator: **not a recorded field** — three options, one new field [CODE]

Nothing in the artifact is the shortest-path propagation distance. What exists is only:

- `remaining_prop_s` per candidate = `remaining_hops × prop_s` (kernel.py:5835–5846) — a hop count priced
  at the local hop length, on the visited-excluded graph, i.e. **neither a distance nor a global shortest
  path**;
- `prop_s` per candidate = the exact length of that one edge (kernel.py:5730–5734), which *is* useful: it
  recovers each edge's true distance `prop_s × C`;
- `candidate_resources[d].remaining_hops` per decision (kernel.py:5847–5866, copied into the decision row
  through `observation_at_start.candidate_resources`, kernel.py:4551–4556).

Options, in increasing rigour:

| Option | What it needs | Status |
|---|---|---|
| A. Hop-count stretch: `(len(deliveries[pid].path) − 1)` ÷ `(1 + remaining_hops[chosen])` at the uplink decision (`remaining_hops` is absent for downlink-target candidates, kernel.py:5743–5758, where the distance is 1 by definition) | existing decision log row's `observation_at_start.candidate_resources[chosen].remaining_hops` | **available today**, but it is a *hop* ratio, not a propagation ratio — different units from the numerator |
| B. Propagation stretch with a recomputed denominator | offline re-instantiation of the deterministic geometry (`resolved_config.json` has `scenario.num_satellites/num_planes/altitude_km/inclination_deg`, `geometry_epoch_s`, `links.max_isl_km`, `links.isl_dirs`) and a multi-source Dijkstra with edge cost `propagation_delay_s(isl_range_km)` — the code for exactly this exists (`routing._multi_source_dist` with `fwd_cost` = propagation, routing.py:106–128, 270–277) | **computable, not recorded**: reproducible without new instrumentation, but the artifact alone does not contain it, and the "shortest path" definition (which serving satellite? at which instant?) must be pinned by the analyst, not by the router |
| C. One field: log the denominator per decision | add `shortest_prop_distance_km` (and the serving set actually used) to the decision row | **missing**; smallest correct fix, seems sufficient |

### 6.4 What is missing, exactly

1. **The serving set actually used by the decision** (`_serving_source` result, kernel.py:4165–4177) is
   not recorded. Under `known_serving` it is kernel truth; the decision row only shows
   `candidate_resources[d].status == "delivered_downlink"` for the peers that happened to be candidates
   and `observation_at_start.neighbours[*].advertised_serve_cells` (kernel.py:4495–4496) — the latter is
   the *advertised* set, not the set used. Without it, a "shortest path to the serving set" is
   under-determined.
2. **No reference distance**: no per-decision `shortest_path_prop_s`/`km`, and no recorded serving-set
   snapshot. (Everything else needed for option B *is* recoverable: `t`, `sat`, `dst`, and the
   deterministic geometry parameters.)
3. **Path-constrained vs global**: T11's denominator is the *visited-excluded* graph, so if stretch is
   computed from T11 it inherits the path-dependence of §5.3-2. Compute the denominator on the full
   topology at the decision instant, not from `remaining_prop_s`.
4. **Survivorship**: if stretch is computed only over `deliveries`, lost/held/expired packets drop out.
   `packet_fates` + `packet_events` allow an all-packets version — use it.

**Verdict:** `D_actual_prop` is measurable **exactly today**; `D_shortest_prop` is **not** measurable from
recorded fields, but is computable offline from the resolved config + deterministic geometry, and becomes
directly measurable by logging one number plus the serving set per decision.

---

## 7. Topology prior vs congestion state, and what a stale-vs-fresh comparison must hold fixed

### 7.1 Classification (from §4.1)

| Family | Terms | Why it matters for a temporal study |
|---|---|---|
| **Topology/geometry prior** | T6, T7, T10, T11, T12, T13 | deterministic; identical in *all* arms and at *all* vintages. Any difference here is an artefact, not a state effect |
| **Congestion state** | T5 (own egress), T9 (target egress), T1/T3 (local compute/query), T8 (peer process) | the only terms that can carry "state time-misalignment"; of these **only T9 depends on the query instant** |
| **Configured constants** | T2, T4 | must never change within a comparison |

### 7.2 The one degree of freedom, stated as a checkable claim

**[CODE]** Given identical `resolved_config` (except `time_alignment.arm`), identical trace and seed, and a
`per_packet` execution mode, the four arms produce the same snapshot except for the instant passed to
`predict_resource`:

- `estimate_eta` reads snapshot fields only → T1–T8 identical (time_alignment.py:874–955);
- `_build_ta_snapshot` derives every other field from t0 state, including `peer_process_s`
  (kernel.py:5159–5194) → arm-invariant;
- `build_predictions` differs only through `arm_target_at` (time_alignment.py:1346–1367) and the
  `candidate`-arm fixed-point adjustment (1384–1398);
- therefore **only T9 varies**, and only because `predicted_bits(target_at)` varies.

So the *maximum* possible arm effect on a score is `|ΔT9|`. Everything else is held by construction —
**as long as `state_source`, `min_observation_age_s`, `execution_mode`, and the estimator parameters are
also equal**, and as long as the arms start from the same state (which requires the same prefix — hence
`warmup_until_s`, `initial_sync_burst_s`, staged policy are shared-prefix devices, kernel.py:1314–1360).

### 7.3 What `time_alignment.state_source` really changes (correction)

- `advertised` (default): the history for a target resource is built from **received advertisements**
  (kernel.py:5081–5135), each with source `generated_at`/`received_at` and a `generation` identity check.
- `ideal_current`: for **ISL** resources only, one synthetic sample is injected with
  `measured_at = received_at = now` and the **peer's true current** queue/work-ahead
  (kernel.py:5045–5080). Downlink resources fall back to the advertisement path.
- Consequence **[CODE]**: with `ideal_current` the per-resource history has exactly **one** sample, so
  `bounded_linear` takes its `len(series) < 2` branch and **holds that value flat** (time_alignment.py:
  802–805) — "ideal current" is a perfect *t0 state*, not a perfect *prediction at the query instant*.
  It is therefore not an arm; it is a separate factor that changes the *value*, not the instant.
- `min_observation_age_s` withholds samples younger than `a` seconds (kernel.py:4981–4990, 5107–5113) —
  again a value-side factor.
- **No test in `leo_sim/tests/` references `state_source` or `ideal_current`** (grep, this commit):
  the intervention that substitutes oracle state for stale state is not covered by CI.

### 7.4 The three score-component quantities that must be held fixed

If the study compares two state vintages (stale vs fresh), these three *families* must be identical across
conditions, and their equality must be **asserted from the artifacts**, not assumed:

1. **The remaining-path prior — `remaining_prop_s` and `prop_s` (per candidate).**
   Hold fixed: the topology rule (4 dirs, `max_isl_km`, clearance), the hop-count metric (unit cost, not
   `delay`/`capacity`), **the serving set used to compute distances**, the visited-node exclusion rule, and
   `pkt_bits`/rates that feed T6/T10/T12. Check: per decision, the multiset of
   `scores[d].terms.remaining_prop_s` and `prop_s` across conditions must be bit-identical **at the same
   `(pid, satellite)`**, not merely at the same `hop_index`.
2. **The queue term's identity *and* scaling — target resource `(peer, egress_direction, generation)`,
   `service_rate_bps`, `queue_weight`, and the predictor family.**
   T9 is a quotient; if the target egress identity or its rate changes between conditions, T9 changes for
   a reason unrelated to staleness. The target egress is itself *predicted from the same cache*
   (kernel.py:5768–5793) — so a mis-identified queue is a second, distinct failure mode from a stale
   value. Check: `candidate_resources[d]` identity fields and `scores[d].service_rate_bps` equal across
   conditions; `queue_weight` = 1.0 by the three checks in §4.3.
3. **The local self-delay family — `compute_wait_s + compute_service_s + query_wait_s + query_service_s`
   and `peer_process_s`.**
   These are state-dependent but arm-invariant *only if* `execution.compute_delay_s`,
   `compute_servers_per_satellite`, `time_alignment.query_delay_s` and the advertisement contents are
   equal. Check: `eta_terms[d]` (recorded per decision, kernel.py:5500–5505) minus
   `local_egress_wait_s`/`prop_s` must be equal across conditions.

Fourth, non-score, but decisive: **the decision mechanics** — `tie_sig_digits = None`, the tie-break order
(`visible_order` = policy order), the fallback rule, `routing.policy`, `max_hops`,
`execution.decision_observation_mode` (`refresh` vs `frozen`: *when* the state is re-read, kernel.py:6462–6470,
so it is a time-misalignment factor in its own right), `learning.algorithm = none`,
`inference_policy = none`, and `min_remaining_hop_only = false`. If any of these changes, a "belief"
difference and a "mechanism" difference are no longer separable.

---

## 8. Magnitudes: would flips be propagation- or queue-dominated? (ESTIMATE)

All numbers in this section are **[ESTIMATE]**: closed-form from the profile's declared constants plus the
Walker geometry parameters. **No simulated queue occupancy was measured**; the empirical distribution of
`terms["resource_work_s"]` is available in any run's decision log and must be used to replace the bounds
below with measured values before any claim is written.

### 8.1 Constants and derivations

Inputs (profiles/stage_c_e2_6g_adv200.yaml): `packet_bits: 600000` (:55), `isl_rate_mbps: 1000.0` (:72),
`isl_queue_bits: 24000000` (:73), `max_isl_km: 3000.0` (:74), `num_satellites: 140`, `num_planes: 7`,
`altitude_km: 600.0` (:16–18), `uplink/downlink_rate_mbps: 20000` (:62–63).
Physics: `C = 299 792.458 km/s`, `EARTH_RADIUS_KM = 6371.0` (model.py:12, 14).

```
r                = 6371 + 600                      = 6 971 km
per_plane        = 140 / 7                          = 20
intra-plane Δ    = 360/20                           = 18°
intra-plane chord= 2·r·sin(9°) = 2·6971·0.156434    = 2 181 km
τ_prop(1 hop, intra-plane) = 2181 / 299792.458      = 7.275 ms
τ_prop(1 hop, max range)   = 3000 / 299792.458      = 10.007 ms
τ_ser (ISL)      = 600 000 / 1e9                    = 0.600 ms
τ_ser (GSL)      = 600 000 / 2e10                   = 0.030 ms
Q_cap drain time = 24e6 / 1e9                       = 24.00 ms
ratio τ_ser/τ_prop = 0.600 / 7.275                  = 0.082      (light-load regime, per the profile's
                                                                  own ratio_ser ≤ 0.1 rule, profile:4–10)
```

### 8.2 Dimensionless ratios (the whole argument in one table)

| Ratio | Value | Reading |
|---|---|---|
| one hop of prior (T7 or one unit of T11) | 7.28 ms (2 181 km) … 10.01 ms (3 000 km) | the "currency" of the score |
| one hop expressed in queue bits at 1 Gbps | 7.28 Mbit = **30.3 %** of the 24 Mbit queue | to beat one hop, the competitor must be ~30 % of a queue emptier |
| max physical work-ahead (T9, target egress) | 24.0 ms (≈24.6 with one in-service packet) | the entire queue budget |
| max physical local egress wait (T5) | 24.0 ms (≈24.6 with in-service residual) | second, independent queue budget |
| both queue budgets | ≈48 ms = **6.6 hop-units** | theoretical ceiling on queue-driven path-length change (requires one candidate saturated in *both* queues while the other is empty in both) |
| one queue budget | 24 ms = **3.3 hop-units** | realistic ceiling |
| wrong-way hop in a 4-neighbour graph (d+1 vs d−1) | 2 hop-units = **14.55 ms** = 60.6 % of one queue budget | the price of actually moving *away* from the destination |
| 3 hop-units of detour | 21.8 ms = 90.9 % of the queue budget | 4 hop-units (29.1 ms) cannot be bought by one full queue |
| remaining-path prior for a 5-hop flow | 5 × 7.275 = 36.4 ms = **1.52 ×** the max queue term | the prior is the largest single term for any long-haul flow |

### 8.3 Answer to "would flips be dominated by propagation differences?"

Two different questions live inside that sentence:

1. **Magnitude of the score.** Yes, propagation dominates: for any flow whose remaining path is ≥4 hops,
   T11 alone (≥29.1 ms) exceeds the entire physically possible T9 (24.0 ms); at 3 hops (21.8 ms) it is within
   10 % of it. A 5-hop flow carries ~36 ms of prior against ≤24 ms of queue.
   **Verdict: the router is a propagation-prior router with a queue tie-breaker, not a queue-balancing
   router** — under `queue_weight = 1.0`.
2. **Magnitude of an *action flip*.** Because both arms share all prior terms exactly (§7.2), a flip can
   only be caused by `ΔT9`. A flip is possible only where the prior/self-delay gap between the top two
   candidates is smaller than that `ΔT9`:
   - **equal-`remaining_hops` candidates** (the typical case: two neighbours both at distance d−1):
     prior gap = difference of one-edge lengths only, 0…≈2.7 ms (7.28 vs 10.01). Here a queue difference
     of a few Mbit decides. **This is where most flips will be, and the flip changes which of two
     near-equidistant routes is taken** — an end-to-end delay difference of the same order as the queue
     difference, not a path-length change.
   - **length-changing candidates**: a one-hop prior gap (7.3 ms) needs ≈7.3 Mbit ≈ **30 % of capacity** of
     queue differential to flip; the wrong-way pair (+2 hop-units = 14.55 ms) needs ≥14.55 Mbit =
     **61 % of capacity**, and simultaneously the *own* local queue term (T5) must not go the other way.
     With
     `τ_ser/τ_prop = 0.082` and the profile's declared light-load intent, such differentials should be rare
     — **to be confirmed from the recorded `resource_work_s` distribution, not assumed.**
   - A *sustained* wandering pattern would need ΔT9 ≥ 14.6 ms at every step while the prior keeps growing —
     i.e. every avoided link ~60 % full and every taken link empty, for many consecutive hops. That is
     self-extinguishing: taking the empty link fills it, which raises its own `resource_work_s` on the next
     decision.

**Answer:** in this score, propagation differences dominate *in magnitude and in the identity of the
chosen path*, and queue differences dominate *only as a tie-breaker among prior-equal candidates*. Action
flips are therefore expected to be (a) concentrated on equal-`remaining_hops` pairs, (b) small in
consequence, and (c) rare where they would change the path length. Any measured flip rate that is *not*
of that shape is a signal that something else moved (fallback incidence, serving-set identity, target
egress identity) — see §9.

---

## 9. Most dangerous confounds for a "state time-misalignment" conclusion (ranked)

1. **Path divergence destroys downstream pairing.** The cross-arm join key is `(pid, hop_index)`
   (decision_ledger.py:85–87; kernel.py:206–212, 6079–6084). `hop_index` counts decisions, not satellites.
   After the **first** flipped decision the two arms are at *different satellites* with different
   candidate sets, so every later `(pid, hop_index)` pair compares different states, not different
   beliefs. **Use only the first divergent decision per packet, or end-to-end outcomes.** Any
   "misalignment rate over all hops" that ignores this is not interpretable.
2. **Flip rate is a threshold-crossing statistic, not a magnitude of misalignment.** Only T9 is
   arm-sensitive (§7.2); the prior gaps that an action flip must cross are 0–2.7 ms among equal-hop
   candidates and 7.3–14.6 ms when the path length changes, while T9's whole range is ≤24 ms (§8). So a flip rate can be near zero (prior decides) or large (many near-tied candidates) *without any
   change in the accuracy of the stale belief*. Report the conditional flip rate by prior gap, and report
   `ΔT9` and its sign, never the flip rate alone.
3. **Queue-blind decisions are not uniform.** All-candidates-fallback collapses the ranking to the policy
   order (§4.4). The code itself records 3.1 % of `known_serving` decisions in that state vs 0.08 % for
   `hop` (kernel.py:5678–5683) — because under `known_serving` the serving fact comes from truth while the
   *queue* fact still needs an advertisement. Those decisions cannot respond to any state-value change, and
   their fraction depends on control-plane age (i.e. on load), so they bias flip rates in a
   load-correlated direction. Count them from `fallback_directions` / `scores[d].fallback`.
4. **Silent mechanism changes that are not the arm.** `state_source`, `min_observation_age_s`,
   `execution_mode` (`per_flow` caches an order for 0.5 s; `precomputed` uses a static table; `async_*`
   uses installed bins), `execution.decision_observation_mode` (refresh vs frozen), `tie_sig_digits`,
   `queue_weight`, predictor/tau/alpha, staged policy, and the advertisement cadence
   (`advertise_interval_s`, `vis_k`, protocol version, initial-sync burst, event adverts). Each of these
   changes the *decision input* or the *ordering mechanics*. A study must assert byte-equality of
   `resolved_config.json` on everything except `time_alignment.arm` (and the run-time identity must include
   `T1_QUEUE_WEIGHT`/`T1_OU_TAU_S`, which the identity module already does).
5. **`candidate` arm is not an "instant" arm.** It adds `predicted_bits/rate` to the instant
   (time_alignment.py:1384–1398). For a clean "stale vs fresh vintage" claim, compare `stale` vs `now`;
   `candidate` introduces a second factor (instant shift by the predicted wait) that is itself
   state-dependent.
6. **Artifact-property, not belief-property, differences.** §5.3-1 (prior graph denser than the physical
   graph → `hold`s) and §4.1 (predictor not clipped to queue capacity online) both change *absolute*
   scores and hold counts. They are arm-invariant, but they make "score gap" a noisy proxy for "delay
   gap"; prefer realized delay/fate outcomes for any magnitude claim.

**Minimum measurement recipe from today's artifacts** (no new instrumentation):

- **`ledgers.json` is always written; per-decision fields are not.** Without `--decision-log`
  (`leo_sim/__main__.py:781`) there is no decision stream: `_record_decision` returns immediately and
  `_candidate_resource_map` returns `{}` when no sink is attached (kernel.py:5999–6000, 5671–5672), so
  `remaining_hops`, per-candidate terms and the state-time audit do not exist for that run.
- `ledgers.json` → `packet_events` (filter `stage=="isl"`, `delay_s`) ⇒ `D_actual_prop` and hop count;
  `packet_fates`, `deliveries[pid].path`.
- decision stream (`--decision-log`) → per row: `t`, `sat`, `dst`, `pid`, `hop_index`, `candidates`,
  `chosen`, `observation_at_start.time_alignment.query_targets`, `.ranking`, `.fallback_directions`,
  `.scores[d].terms`, `.scores[d].predicted_bits`, `.scores[d].service_rate_bps`,
  `observation_at_start.candidate_resources[d].{status,peer,egress_direction,egress_peer,
  remaining_hops,propagation_s}`; and `observation_at_start.time_alignment.used_state[d].{source,
  measured_at,received_at,used_at,bits}` for the **vintage actually priced** (recorded for ISL targets only,
  kernel.py:5120–5135).
- `resolved_config.json` + launch manifest `env` ⇒ the held-fixed checklist of §7.4. **Do not** rely on
  `receipt.json` for this: its `mechanisms.requested` block binds only a small subset
  (`policy`, `association`, `ge_enabled`, `control_enabled`, `monitor`, `learning_*`, `topology_*`,
  `rate_model`, `forward_step_penalty`, `learning_policy` — receipt.py:97–107), so
  `time_alignment.*`, `advertise_interval_s`, `ttl_s` and `vis_k` are **not** in the receipt. The full
  config is only in `resolved_config.json` (receipt.py:733–736) and its hash
  `config_sha256` in the manifest.

---

## 10. What this audit does **not** establish

- No simulation was run, so **no claim about realised queue occupancy, hold counts, flip rates or
  delay** is made here. §8 is arithmetic on declared constants, not measurement.
- The profile's own header (`stage_c_e2_6g_adv200.yaml:1–10`) is a **stale copy** from `v7b150`
  (it describes 10 Mbps and 8 000-bit packets, while the file itself sets 1 000 Mbps and 600 000-bit
  packets). Trust the YAML body, not the comment — and check `resolved_config.json` for what actually ran.
- The `hold`/`unavailable` path and the topology-density caveat (§5.3-1) are code-read inferences; a run
  with `four_direction_audit` present would confirm their frequency (`filter_reasons:
  ["geometry_unavailable"]`).
- Nothing here decides the research question. It only fixes what a temporal claim is allowed to say:
  *the router is an ordering function over a frozen snapshot whose only arm-dependent term is the
  predicted target-egress work; any visible temporal effect is a threshold crossing of that one term
  against a propagation prior of 7.3–10.0 ms per hop.*
