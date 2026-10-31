# PILOT-SCENARIO-20261031 — how to actually run the 140-sat mechanism pilot

> Read-only design audit. Worktree `/Users/lge/Desktop/topic/.wt-night-20261031`,
> branch `night/time-exp-20261031`, HEAD `63664f3`. **No code, no profile, no
> contract was modified.** Draft profiles/contracts/workloads live only in this
> file (and were validated in `/tmp/pilot`, outside the repo).
>
> Evidence tags used below:
> * **[READ]** — read from source at the cited `file:line`.
> * **[LOCAL-CHECK]** — computed locally with the repo's own modules
>   (`leo_sim.config`, `leo_sim.trace`, `scene_load_calibration`,
>   `synthetic_od`). Static/deterministic: config resolution, trace
>   compilation, analytic load calibration. **Not** a simulation and **not**
>   VM experiment evidence — no kernel was run.

---

## 0. Verdict (read this first)

| Question | Answer |
|---|---|
| Which runner drives a 140-sat **four-arm** run? | `t1_tasks --task network_alignment --config <profile> --arms stale,now,common,candidate`. **Not** `network_arm_run.py` (its `--scenario` is a closed enum of three scripted fixtures, `scripted_scenarios.SCENARIOS`, **4 sats / 1 plane**, hand-built rows). |
| Is the 140-sat scene already there? | Yes — two profile families: `CODE/leo_sim/profiles/experiment_base.yaml` (`experiment-base-140sats`) and `EXPERIMENTS/2026-10-24-corridor/profiles/*.yaml` (10 files, the `spread20` / `e2_*` family). Closest to the pilot: **`spread20_s7.yaml`**. |
| Can it be done with **zero** code changes? | **Yes, but only via Option B (new profile YAML).** Options A (load-calibration contract) is blocked by a hard-coded profile whitelist; Option C (new contract) needs code. |
| 30 % background + 70 % hotspot **and** a single `[8.0,8.5)` 1.6× burst in the same OD? | **Not expressible in native modes.** `demand.mode=hotspot` (the only declared destination-mix model) is *refused* together with a burst window by `config.py:811-832`. Only `demand.mode=csv` (existing generator `synthetic_od.py`) carries both. |
| Does the requested load bracket 40/120/200 Mbps hit ρ≈0.2-0.3 and ρ≈0.8-0.9? | **Only with the right OD.** [LOCAL-CHECK] on the real 140/7/600/98.6 Walker with a natural 8-site hub OD, `max_e b_e = 0.243` ⇒ ρ(40/120/200) = **0.10 / 0.29 / 0.49** — the high point is 1.8× short. With a 3-source hotspot (`b_e ≈ 0.39`) the bracket **60/120/200** (60 = 120/2, an allowed geometric point) lands at **0.24 / 0.47 / 0.79**, and a 2-source hotspot (`b_e ≈ 0.49`) gives **0.30 / 0.59 / 0.99** — i.e. 40 Mbps is always too low and 200 Mbps only reaches 0.8 when the OD is concentrated into 2-3 hotspot sources. The OD must be fitted with `scene_load_calibration.py` first; this is the single largest risk in the plan and it is *not* the arms' fault. |
| Is burst "the same trace at high load only"? | **No.** Burst is a **trace transform** (`trace.py:325-340`, exact thinning), i.e. it *is* the compiled row set. A bursted high-load trace is a different trace than the un-bursted one. What *is* guaranteed is: **all four arms inside one cell share one compiled trace** (`t1_tasks._arm_row`, `load_calibration` compiles once before the arm loop). |

Recommended path in one line: **Option B2** — one new 140-sat profile whose
demand is an explicit CSV workload produced by the existing
`CODE/experiment_platform/synthetic_od.py` (14 flows: 7 hotspot + 7 background,
3 load phases incl. the 0.5 s 1.6× window), driven by
`t1_tasks --task network_alignment`; calibration = three single-arm runs
(`--arms now`) at 60 / 120 / 200 Mbps, ρ read post-warmup with the existing
`CODE/experiment_platform/isl_pressure.py` window rebinner.

Fallback **Option B1** (fewer artifacts, native burst, no CSV) if the 30/70
split is allowed to be "≈hub-shaped": `demand.mode=gravity` + native burst
window. Its measured fidelity gap is given in §7.3.

---

## 1. Machinery map — what each file actually does

| File | Role | Key facts |
|---|---|---|
| `CODE/experiment_platform/network_arm_run.py` (289 L) | Whole-run driver for **scripted** arms | `--scenario` is `choices=sorted(scripted_scenarios.SCENARIOS)` (`:174-175`), arms from `time_alignment.ARMS`. It **cannot** take a profile. Only usable for the three fixtures. |
| `CODE/experiment_platform/scripted_scenarios.py` (789 L) | Three hand-written fixtures: `reachability`, `same_flow`, `contention` (`:447,461,476`) | Base config is **4 sats / 1 plane** (`NUM_SATELLITES = 4` `:90`, `:167-169`), rows are hand-built (`_row(...)`), `PROBE_FIRST_PID = 400`. Not a general scene. **Dead end for the pilot.** |
| `CODE/experiment_platform/t1_tasks.py` (2914 L) | **The profile-driven runner.** `--task {branch_alignment,network_alignment,execution_modes}` | `design()` (`:2061`) resolves the profile, applies overrides, compiles the trace **once**; `network_alignment()` (`:1847`) loops the four arms over the same `rows`; `_arm_row()` (`:1456`) forces `time_alignment.enabled=True` + `arm=<arm>` so the arms differ only in the arm; output carries `fairness.{rows_digest, same_trace, same_seed, same_predictor, arms_differ_only_in}` (`:1972-1982`). |
| `CODE/experiment_platform/load_calibration.py` (1101 L) | One-arm-at-a-time **contract+cell** diagnostic | `ALLOWED_PROFILES` is a **hard-coded 5-tuple** (`:38-56`) — none is 140-sat. `ALLOWED_ARMS = 4` (`:57`). Contract schema fixed: `deadline_s` **must be exactly 4.0** (`:258-261`), `max_duration_s ∈ [12,300]` (`:65-66`), `max_packets ∈ [1,100000]` (`:67-68`). Override whitelist `OVERRIDE_FIELDS` (`:73-109`) — constellation is **not** overridable. Forces `time_alignment.enabled=True` per arm (`:962`). |
| `CODE/experiment_platform/scene_trace_program.py` (349 L) | Declared stochastic demand **program → CSV** (`s2_independent_burst`, `s3_correlated_hotspot`) | Reachable **only** through a load-calibration `cell.trace_program` (`load_calibration.py:400-413`). Refuses `cycle_s > window/3` (`:153-157`) so it can only produce **recurring** bursts (≥3 per emission window), never one single 0.5 s window. `migration_groups` declared-but-unimplemented → hard refusal (`:231-234`). |
| `CODE/experiment_platform/synthetic_od.py` (305 L) | Explicit OD **flow matrix + load phases → per-packet CSV** + summary | This is the only existing declarer of a genuine "two OD groups + a burst phase". `render_trace` (`:141`), `write_trace` (`:240`), sidecar schema `t1-synthetic-od-workload/v1` (`:18`). Constraints: per-flow-per-phase `duration*rate` must be an integer (`:120-125`), per-flow phase count < 10 000 (`:127`), `jitter*2 < 1/rate` (`:129-132`), ≥3 sites, ≥2 flows, contiguous phases starting at 0 (`:106-110`). |
| `CODE/experiment_platform/isl_pressure.py` (451 L) | **Post-warmup directed-ISL utilisation** rebinner | `analyze_windows(ledgers, window_s=..., high_utilization=...)` (`:74`) returns per-link `windows[{start_s,end_s,served_bits,available_capacity_bits,available_time_s,utilization,eligible,matched_queue_wait_bits_s}]` (`:349-366`) plus `sustained_hotspot_link_ids`, `pressure_candidate_link_ids`. Fed by `{stop_time_s, packet_events, link_service_windows, link_available_windows}` — exactly the four keys `t1_tasks` keeps under `arms[i].replay` when `--capture-replay` is passed (`t1_tasks.py:1655-1662`). Prior art for the whole-horizon variant: `EXPERIMENTS/2026-10-24-corridor/profiles/spread_compare.py` (`util_max`, `frac = share of ISLs with util ≥ 0.2`). |
| `CODE/experiment_platform/scene_load_calibration.py` (1010 L) | **Analytic** `R(ρ)` ruler from a JSON spec | Takes a full spec (`--spec`, JSON): `geometry{num_satellites,num_planes,altitude_km,inclination_deg,min_elevation_deg,max_isl_km,geometry_epoch_s,dynamic}`, `cells`, `od_weights` **or** gravity keys, `packet_bits`, `links{rate_mbps,queue_bits}`, `control_plane{packet_bits,interval_s,vis_k}`, `access_layer`, `tiers`, `horizon_s`, `window_s`. Real Walker geometry via `leo_sim.model.Constellation` (`:122-175`). Returns `link_coefficients.max_b_e`, per-ρ `R_mbps` + `bottleneck_edge`, `traffic_split`, and an **`alternatives`** block (`reference_s`, `alternative_s`, `stretch`, `windows_with_alternative`) that is the only existing check for "≥2 plausible alternative next hops". Subcommands: `calibrate --spec --out`, `selftest`, `scope-comparison`. |
| `CODE/experiment_platform/analyze_scene_calibration.py` (417 L) | Reducer for **load-calibration** run dirs | `--runs <dirs> --calibration <json> --out <dir> [--figures]`. Expects `cal/<cell>/summary.json` + `cal/<cell>/arms/<arm>/result.json.gz` (`:41-59`). Reports **whole-horizon** `isl_util_max`, `isl_util_mean`, per-link CSV, `predicted_bottleneck_rho`. Does **not** window anything ⇒ not sufficient for the post-warmup ρ. |
| `CODE/leo_sim/trace.py` (1693+ L) | Trace compiler | `_rate_multiplier` (`:325-340`) = burst window, optional longitude band; generation with exact thinning (`:1330-1410`); `mode=csv` branch (`:1185-1300`) consumes `src_lat/src_lon/dst_lat/dst_lon` and maps to grid cells — i.e. exactly the `synthetic_od` column set. |
| `CODE/leo_sim/model.py` `Constellation` (`:469-524`) | Walker-delta, F=0 | `num_satellites % num_planes == 0`; phase = `2π(idx/per_plane + t/period)`, `raan = 2π·plane/num_planes`. 140/7/20 @600 km → `period = 5792.3 s`. |
| `CODE/leo_sim/metrics.py` (`:43-340`) | Whole-run utilisation | `utilization = served_bits / available_capacity_bits` **only if** `available_capacity_interval_s` is set; otherwise the denominator is the sum of service windows and the number is meaningless (the corridor profile says so at `t1_pressure_corridor.yaml:69-74`). |

### 1.1 The one enum you must not trip over

`network_arm_run.py --scenario` only accepts `{reachability, same_flow, contention}`;
`load_calibration.py --contract.base_profile` only accepts the 5-profile tuple.
Anything with 140 satellites must go through **`t1_tasks --config <path>`**.
That is the whole routing decision in §10.

---

## 2. Profile inventory (constellation / links / demand / control / horizon)

Legend: **bold** = differs from the pilot's speed-forced settings.

### 2.1 `CODE/leo_sim/profiles/t1_scene_trunk_v1.yaml` (156 L)

| Item | Value |
|---|---|
| Constellation | 96 sats / 12 planes / **800 km** / 53.0° (`:48-51`), min_elev 10, seed 101 |
| ISL | rate **1000 Mbps**, queue **20 Mbit**, `max_isl_km 6000`, dirs N/S/E/W, `ge_enabled false` (`:94-101`) |
| Packet | 12000 bit (`:69`) |
| Demand | `mode: uniform`, `offered_mbps 749.5`, emission 2.0-2.7 s, 3 gateway sites 4:1:1, `temporal_model constant` (`:55-78`) |
| Control | adv **0.05 s**, ttl 10, `vis_k 2`, packet 8000 bit, protocol **v2** (`:105-112`) |
| Compute | `compute_delay_s 1e-6`, servers 1, `decision_observation_mode frozen` (`:137-139`) |
| Horizon | **12.0 s** (`:46`) |
| `time_alignment` | enabled, arm `now`, predictor `hold_last`, history **8** (`:145-153`) |
| **Header staleness / inconsistency** | Header `:33-40` describes the compute plane as "1 server at **50 µs** per decision" and derives rho_cpu from it; the body **executes** `1e-6` (1 µs) and the inline `execution` comment (`:120-136`) says the 50 µs setting was **corrected on 2026-10-18**. Header is stale on exactly this point. Everything else in the header (96/12/8 geometry, 1 Gbps, 20 Mbit, 3 gateways 4:1:1) matches the body. |
| Pilot relevance | Different constellation; but it is the **reference for the v2 protocol + vis_k minimal** shape. |

### 2.2 `CODE/leo_sim/profiles/t1_scene_trunk_burst_v1.yaml` (156 L)

Byte-identical to 2.1 except 4 demand lines + `name` + `out_dir`:
`mode: uniform→burst`, adds `burst_start_s 2.0`, `burst_duration_s 0.010`,
`burst_multiplier 2.0` (verified by `diff`). **[READ]** This is the platform's
canonical "burst variant" pattern and it directly answers §6: the burst variant
is a **separate profile**, i.e. the burst changes the compiled trace.

### 2.3 `CODE/leo_sim/profiles/t1_hotspot_gravity.yaml` (102 L)

| Item | Value |
|---|---|
| Constellation | 96 / 12 / 800 km / 53.0°, seed 7, **duration 8.0 s** (`:18-27`) |
| Endpoints | regional bounds lat [20,30], lon [100,110], 1° aggregation (`:28-32`) |
| Demand | `mode: population_gravity`, raster path `CODE/population_map/gpw_v4_population_count_rev11_2020_15_min.tif`, `offered_mbps 5.0`, emission 2-4 s, `packet_bits 12000`, **burst 2.5-3.5 s ×2.0**, `destination_population_exponent 3.0`, `gravity_alpha 0.5`, sampler `alias_rejection` (`:33-51`) |
| Access | 4 slots, **100 Mbps** up/down, 64 Mbit queues (`:52-62`) |
| ISL | `rate_model: mcs` (**no** `isl_rate_mbps`, **no** `max_isl_km` ⇒ defaults 1000 Mbps / 6000 km), `ge_enabled false` (`:63-65`) |
| Control | adv **1.0 s**, ttl 10, `vis_k 17`, protocol v2 (`:69-76`) |
| Compute | `compute_delay_s 0.001`, servers 1, frozen, `max_packets 20000` (`:83-93`) |
| `time_alignment` | enabled, arm `candidate`, predictor **`bounded_linear`**, `common_rule median_eta`, history default 8 (`:94-99`) |
| **Header staleness** | Header `:12-14` claims "everything else … unchanged from the smoke profile". True for geometry/access/control/execution vs `t1_population_region_cost_smoke.yaml` (96/12/800/53 ✓). But header never mentions the 2.0× burst or `vis_k 17`, both of which are non-trivial body facts. Mildly incomplete, not wrong. |
| Pilot relevance | It is the repo's **precedent that a 2.0× burst window is legal together with a concentrated-destination OD** — because `population_gravity` is in the burst-allowed mode set (`config.py:811`). It is *not* a 30/70 declaration. |

### 2.4 `CODE/leo_sim/profiles/experiment_base.yaml` (51 L) — **a 140-sat profile already exists here**

| Item | Value |
|---|---|
| Constellation | **140 / 7 / 600 km / 98.6°**, min_elev **30**, seed 7, **duration 30 s**, `time_step 0.5` (`:2-11`) |
| Endpoints | 2 sites (`Malaga`, `Tokyo`), default grid 0.25/1.0 |
| Demand | `mode: uniform`, `offered_mbps 4`, `packet_bits 1_000_000`, `deadline_s null` (`:16-20`) |
| Access | 16 slots, 1000 Mbps up/down, 1 Gbit queues, `bbm`, hysteresis 0 (`:21-32`) |
| ISL | **only** `ge_enabled false` ⇒ defaults: `rate_model constant`, `isl_rate_mbps **1000**`, `isl_queue_bits **256 Mbit**`, `max_isl_km **6000**`, dirs N/S/E/W (`:33-34` + `config.py:487-505`) |
| Control | adv **2.0 s**, ttl **60**, `vis_k 12`, protocol **v1 (default)**, packet 8000 (`:35-39`) |
| Routing | `hop`, `max_hops 64` (`:40-42`) |
| Execution | `max_events 4e6`, `max_entities 2000`, `max_packets 30000` — **no** `available_capacity_interval_s`, **no** `decision_observation_mode`, `compute_servers 0` (`:45-48`) |
| `time_alignment` | **absent** ⇒ disabled, arm `candidate`, history 8 |
| **Header staleness** | No header/prose at all (only 3 Chinese comment lines on the access layer). Name says `experiment-base-140sats`; body is consistent. |
| Pilot relevance | Exact constellation, and the only leo_sim profile with `packet_bits = 1e6` (matches the pilot). Everything else (2 sites, uniform, no TA, util denominator unset) has to change. |

### 2.5 `CODE/leo_sim/profiles/stage_c_e2_6g_adv200.yaml` (97 L)

| Item | Value |
|---|---|
| Constellation | **140 / 7 / 600 km / 98.6°**, min_elev 5, seed 7, **duration 1.2 s** |
| Endpoints | 6 city sites (tokyo/singapore/london/newyork/saopaulo/lagos) |
| Demand | `mode: gravity`, `offered_mbps 6000`, `gravity_alpha 2.0`, floor 100 km, emission 0.1-1.0 s, `packet_bits 600000` |
| Access | 256 slots, 20 Gbps up/down, 4 Gbit queues, holding 256 Mbit, idle_release 0.2 |
| ISL | constant, **1000 Mbps**, queue **24 Mbit**, **`max_isl_km 3000`** |
| Control | adv **0.2 s**, ttl 10 (default), `vis_k 3`, protocol **v2** |
| Routing | `known_serving`, max_hops 64 |
| `time_alignment` | enabled, arm `candidate`, `bounded_linear`, history 8 |
| Execution | `max_events 6e7`, `max_entities 20000`, `max_packets 4e5`, `available_capacity_interval_s 1.0` |
| **Header staleness** | **Stale and misleading.** The 10 comment lines are copied from the `v7b150` probe (Chinese) and describe a *different* scene: "24 星 3 平面 53° 550 km", "ISL 5.5 Mbps", "包 8000 bit", "probe arm differs from v7b150 only in isl_rate_mbps". None of that is true of this body. The body is coherent with the filename (`e2_6g_adv200` = 6 gateways, advertise 0.2 s). **Do not trust the header.** |

### 2.6 `CODE/leo_sim/profiles/t1_pressure_corridor.yaml` (77 L)

| Item | Value |
|---|---|
| Constellation | 24 / 3, altitude **default 550 km** (absent), inclination **default 53°** (absent — `:21-29` only set name/duration/planes/sats/seed) |
| Endpoints | 2 sites (`equator`, `north`), weights 1:1 |
| Demand | `mode: uniform`, `offered_mbps 8.0`, `packet_bits 1e6`, emission window **unset** (⇒ whole horizon) |
| ISL | constant, **10 Mbps**, queue default 256 Mbit, `ge_enabled false` |
| Control | adv **2.0 s**, `vis_k 2` |
| Horizon | **600 s** |
| `time_alignment` | absent |
| `available_capacity_interval_s` | **1.0** — and the comment says why (without it the util denominator is degenerate) |
| **Header staleness** | **Not stale — checked.** Header `:25-26` says "24 sats / 3 planes: intra-plane ISL spacing ~5300 km < max_isl_km (6000)". 24/3 ⇒ 8 per plane ⇒ 45° spacing; at the default 550 km (r = 6921 km) the chord is 2·6921·sin(22.5°) = **5297 km** ✓ < 6000. The header is arithmetically correct (and it is the reason `max_isl_km` is absent: the default 6000 is required). |
| Pilot relevance | Methodologically important: it is the repo's statement that **access must not be the bottleneck** and that the **availability sampler is required** for a meaningful ρ. |

### 2.7 Bonus — `EXPERIMENTS/2026-10-24-corridor/profiles/` (this is the closest family)

10 profile YAMLs + `launch_spread20.sh` + `spread_compare.py`. The `spread20_*` /
`e2_*` families are **140 / 7 / 600 km / 98.6°** with `packet_bits 1_000_000`
(`spread20_s7.yaml`) or `600000` (`e2_*`), `advertisement_protocol_version: 2`,
`time_alignment.enabled: true`, `predictor: bounded_linear`, `history_limit: 8`,
`links.ge_enabled: false`, `available_capacity_interval_s: 1.0`, and were run by
**`t1_tasks --task network_alignment --config profiles/<p> --arms <one arm>
--deadline-s 0.4`** (see `launch_spread20.sh`). This is the operational
template for the pilot — same runner, same geometry, one arm per invocation.

---

## 3. Closest existing profile, and the exact diffs

**Closest = `EXPERIMENTS/2026-10-24-corridor/profiles/spread20_s7.yaml`.**

Why: identical constellation (140/7/600/98.6), identical `packet_bits = 1e6`,
already on protocol v2 with `time_alignment` enabled + `bounded_linear`,
already carries `available_capacity_interval_s`, and it was already driven by
`t1_tasks --task network_alignment --arms <one>` on the VM.

Diffs needed (left = existing, right = pilot):

| Key | `spread20_s7.yaml` | Pilot | Why |
|---|---|---|---|
| `scenario.duration_s` | 1.2 | **20.0** (or 18.0) | warm-up 0-4, eval 4-14, burst 8-8.5, drain 14-20 |
| `scenario.min_elevation_deg` | 5 | 10.0 | keep GSL serving set stable; 10° is the family default |
| `scenario.seed` | 7 | 7 (1 seed, as required) | — |
| `demand.mode` | `gravity` | **`csv`** (B2) *or* `gravity` (B1) | §7 |
| `demand.offered_mbps` | 12000 | 40/60/120/200 bracket | speed-forced load |
| `demand.emission_start_s` / `_end_s` | 0.1 / 0.6 | **0.0 / 14.0** | warm-up + steady + drain |
| `demand.packet_bits` | 1000000 | 1000000 ✓ | already correct |
| `links.isl_rate_mbps` | **400** | **100.0** | speed-forced |
| `links.isl_queue_bits` | 256000000 | **64000000** (10 ms @100 Mbps) | speed-forced |
| `links.max_isl_km` | **3000** | **6000.0** | **critical**: [LOCAL-CHECK] with `max_isl_km=3000` **every** E/W ISL is pruned (min cross-plane range over one orbit = 4922.7 km) → ring-only topology, ~280 directed ISLs, no 4-ISL mesh |
| `links.rate_model` | `constant` ✓ | `constant` | keep |
| `links.ge_enabled` | `false` ✓ | `false` | random GE OFF |
| `links.isl_dirs` | (default N/S/E/W) ✓ | explicit `[N,S,E,W]` | — |
| `topology.recompute_interval_s` | (absent ⇒ static) | **1.0** | 7-plane Walker must rematch; static topology at 98.6° would keep stale E/W peers |
| `control_plane.advertise_interval_s` | **0.2** | **1.0** | required |
| `control_plane.vis_k` | 3 | **2** (minimal — see §9.3) | "minimal but sufficient" |
| `control_plane.advertisement_protocol_version` | 2 ✓ | **2** | carries resource identity/generation/workload (`leo-control-ad-wire/v2`, `kernel.py:359,3758`; 192-bit ISL record, 416-bit processing fields, `receipt.py:1950-1968`) |
| `control_plane.ttl_s` | (default 10) ✓ | 10.0 | ≥ 1 s advertise × history_limit 4 ⇒ 4-5 s; 10 s is safe |
| `time_alignment.history_limit` | **8** | **4** | required |
| `time_alignment.predictor` | `bounded_linear` ✓ | `bounded_linear` | required |
| `time_alignment.arm` | `candidate` | **irrelevant** — `t1_tasks` forces it per arm | `_arm_row` |
| `execution.compute_delay_s` | (absent ⇒ 0) | **5e-5** (or 1e-6) | `time_alignment.enabled` requires `servers ≤ 1` and `servers > 0` requires `delay > 0` (`config.py:1216-1246`) |
| `execution.compute_servers_per_satellite` | (absent ⇒ 0) | **1** | same |
| `execution.decision_observation_mode` | (absent ⇒ `refresh`) | **`frozen`** | freeze observation at decision start; else staleness cannot exist |
| `execution.available_capacity_interval_s` | 1.0 ✓ | **1.0** | required for a real ρ denominator |
| `execution.max_events` | 6e7 | **5e6** (cap, not target) | §10.6 |
| `execution.max_entities` | 20000 ✓ | 20000 | — |
| `execution.max_packets` | 4e5 | **20000** | 1716-2860 packets expected; keep a fail-closed cap |
| `access.*` | 256 slots, 20 Gbps, 4 Gbit queues | keep (broadened) | access must not be the bottleneck |
| `routing.policy` | `known_serving` | `known_serving` | same across arms |
| `endpoints.sites` | 20 cities | **8 sites** (or none, csv mode) | §7 |
| `outputs.out_dir` | `leo_sim_out/d20` | new dir | — |
| absent | `demand.deadline_s` **required by t1_tasks?** | no (`--deadline-s` is the D) | `t1_tasks.py:2008-2016` |

`experiment_base.yaml` is the second-closest but needs ~20 diffs (run 2.4 vs the
table above) and has no TA/utilisation wiring at all.

---

## 4. Exact config keys: admissible ranges and validation constraints

Source: `CODE/leo_sim/config.py` (`DEFAULTS` `:414-624`, semantics `:672-1310`).

### 4.1 Constellation — `scenario`

| Key | Default | Constraint (`config.py`) |
|---|---|---|
| `num_satellites` | 66 | ≥1, **must be divisible by `num_planes`** (`:696-699`) |
| `num_planes` | 6 | ≥1 |
| `altitude_km` | 550 | **[300, 2000]** (`:686-688`) |
| `inclination_deg` | 53 | [0, 180] (`:690-692`) |
| `min_elevation_deg` | 25 | (0, 90) (`:701-702`) |
| `seed` | 42 | ≥ 0 integer (`:693-695`) |
| `duration_s` | 60 | > 0; the **only** horizon knob for `t1_tasks` |
| `time_step_s` | 0.1 | > 0 (event-driven; cosmetic) |
| `geometry_epoch_s` | 0.0 | ≥ 0 (`:900-901`) — **deliberately excluded from trace identity** (`TRAFFIC-MODEL-SPEC.md` §1.3) |

### 4.2 Links / queue / packet size

| Key | Default | Constraint |
|---|---|---|
| `links.isl_rate_mbps` | 1000 | > 0 (`:976`) |
| `links.isl_queue_bits` | 256e6 | ≥ 0 (`:978`) |
| `links.max_isl_km` | 6000 | > 0 (`:976`) |
| `links.isl_dirs` | `[N,S,E,W]` | non-empty subset of `{N,S,E,W}` (`:967-969`) |
| `links.rate_model` | `constant` | `{constant, mcs}` (`:991-993`); `mcs` additionally pins `mcs_table` and validates every `rf_*` field (`:1004-1030`) |
| `links.ge_enabled` | false | bool; `ge_gsl`/`ge_isl` must each be exactly `{mean_good_s, mean_bad_s}`, both > 0 (`:979-989`) |
| `topology.recompute_interval_s` | None (static) | None or > 0 finite (`:997-1004`); `matching` must be `markovian` (`:1005-1006`) |
| `demand.packet_bits` | 8e6 | > 0 (`:797-798`) |

### 4.3 Demand / OD / background+hotspot

| Key | Default | Constraint |
|---|---|---|
| `demand.mode` | `uniform` | `{uniform, gravity, population_gravity, hotspot, burst, diurnal, csv, mlab}` (`:371-374`) |
| `demand.offered_mbps` | 1.0 | > 0 (`:779-780`). **Ignored as a rate** when `mode=csv` (reported as `load_mode: observed_trace`) |
| `demand.emission_start_s` | 0.0 | finite, ≥ 0 (`:781-787`) |
| `demand.emission_end_s` | None | (0, duration_s] (`:788-791`) |
| `demand.csv_path` | None | required iff `mode=csv` (`:800-801`) |
| `demand.deadline_s` / `research_deadline_s` | None | > 0 when set (`:875-884`) |
| `demand.gravity_alpha` / `gravity_d_floor_km` | 1.5 / 500 | > 0 (`:885-886`) |
| `demand.source_population_exponent` / `destination_population_exponent` | 1.0 / 1.0 | > 0 (`:804-807`) |
| `demand.hotspot_fraction` | **0.1** | (0, 1] (`:899`) — share of the **other** endpoints that form the hot pool: `n_hot = max(1, round(len(others)*frac))` (`trace.py:298`) |
| `demand.hotspot_concentration` | **0.8** | [0, 1] (`:899`) — share of each source's packets aimed at the hot pool (`trace.py:299-305`) |
| `demand.population_destination_sampler` | `scan` | `{scan, alias_rejection}`, only with `population_gravity` (`:914-924`) |
| `demand.nested_master_offered_mbps` | None | only with `population_gravity`; ≥ `offered_mbps` (`:928-935`). **Not** a background/hotspot construct — it is a master/child replay device (`trace.py:1661-1678`) |
| `demand.temporal_model` | `constant` | `{constant, local_diurnal_cosine}`, the latter only with `population_gravity` (`:902-912`) |
| `endpoints.sites[].demand_weight` | 1.0 | > 0 finite (`:769-777`); drives **both** source rate (`trace.py:1363-1366`) and gravity destination weight (`:285-291`) |

### 4.4 Burst

| Key | Default | Constraint |
|---|---|---|
| `demand.burst_start_s`, `burst_duration_s` | None | if either is set, **`mode` must be one of `{burst, gravity, mlab, population_gravity}`** or the config is **refused** (`:811-832`) |
| | | window must lie **inside** the emission window: `burst_start ≥ emission_start` and `burst_start+burst_duration ≤ emission_end or duration` (`:840-858`) |
| | | `burst_start ≥ 0`, `burst_duration > 0` (`:834-836`) |
| `demand.burst_multiplier` | 2.0 | > 0 (`:897`); a **non-default** value under a burst-incapable mode is also refused (`:829-838`) |
| `demand.burst_lon_min/max` | None | must be set **together**, `-180 ≤ min < max ≤ 180`, and only with a declared window (`:859-874`); absent ⇒ **global** burst (`trace.py:331-338`) |
| interaction | — | `population_gravity` + `local_diurnal_cosine` + burst window = refused (`:855-858`) |

### 4.5 Control plane / advertisement protocol / vis_k

| Key | Default | Constraint |
|---|---|---|
| `control_plane.enabled` | True | learning requires it true (`:1066-1067`) |
| `control_plane.advertise_interval_s` | 1.0 | > 0 (`:1026-1027`) |
| `control_plane.ttl_s` | 10.0 | > 0 |
| `control_plane.packet_bits` | 8000 | ≥ 1 |
| `control_plane.vis_k` | 2 | ≥ 0 (`:1025`); `load_calibration` override additionally requires ≥ 1 (`:182-187`); `vis_k = 0` disables control caching (`kernel.py:1825`) |
| `control_plane.advertisement_protocol_version` | 1 | **only 1 or 2** (`:1030-1032`) |
| `control_plane.priority` | `nonpreemptive_priority` | only value accepted (`:1033-1034`) |

### 4.6 time_alignment / arm / predictor / history

| Key | Default | Constraint |
|---|---|---|
| `enabled` | False | — (`t1_tasks` and `load_calibration` both force True) |
| `arm` | `candidate` | `{stale, now, common, candidate}` (`:400`, `:1268-1272`) |
| `predictor` | `bounded_linear` | `{hold_last, bounded_linear, analytic_drain, zero, ou_shrink, drift, rate_aware, ewma, kalman}` (`:401`) |
| `history_limit` | 8 | **≥ 1** (`:1277-1279`) |
| `common_rule` | `median_eta` | `{median_eta, mean_eta, fixed_horizon}` (`:1301-1304`) |
| `common_horizon_s` | None | required only for `fixed_horizon` |
| `execution_mode` | `per_packet` | `{per_packet, per_flow, precomputed, async_point, async_window}` (`:403-405`) |
| `state_source` | `advertised` | `{advertised, ideal_current}` (`:1285-1289`) |
| `min_observation_age_s` | None | None or finite ≥ 0 (`:1290-1296`) |
| `tie_sig_digits` | None | None or int in [1,15] (`:1280-1284`) |
| **cross-key** | — | `time_alignment.enabled ⇒ execution.compute_servers_per_satellite ≤ 1` (`:1216-1237`); `servers > 0 ⇒ compute_delay_s > 0` (`:1207-1215`) |

### 4.7 Execution caps / audit caps

| Key | Default | Constraint |
|---|---|---|
| `execution.max_events` | 1e6 | ≥ 1 (`:1172-1174`); exceeded ⇒ `CapExceeded` ⇒ `natural_end=false` ⇒ cell fails (`kernel.py:7595-7596`) |
| `execution.max_entities` | 10000 | ≥ 1; live-entity formula = base + endpoints + live dynamic ISL generations (`kernel.py:1847-1860`) |
| `execution.max_packets` | 200000 | ≥ 1; **also** enforced against the compiled trace |
| `execution.available_capacity_interval_s` | None | None or finite ≥ 0.01, and `duration/interval ≤ 100 000` (`:1175-1195`) |
| `execution.compute_delay_s` | 0 | finite ≥ 0 |
| `execution.decision_observation_mode` | `refresh` | `{refresh, frozen}` (`:399`) |
| target-probe caps (CLI/env, not config) | — | `t1_tasks --target-probe/--target-probe-max/--target-probe-stride/--target-probe-horizons`; `T1_AUDIT_MAX_DECISIONS` (default 5000) and `T1_AUDIT_MAX_ATTEMPTS` (5000) via `_audit_cap` (`t1_tasks.py:819-831,939-940`); `target_probe` rows are capped by `max_samples` (`target_probe.py:100-175`) |

### 4.8 Horizon / warm-up / eval window

There is **no** config key for warm-up or evaluation window. The horizon is
`scenario.duration_s`; the evaluation window is a **runner argument**:
`t1_tasks --window-start 4 --window-end 14` (population window for the deadline
metric, `t1_tasks.py:2648-2653`) and, for ρ, the window is applied **post hoc**
by `isl_pressure.analyze_windows(window_s=0.5)` (§8.3). Warm-up is implicit:
the first 4 s are simply not analysed.

---

## 5. Trace identity — what is hashed and how to verify the arms shared it

* Compiler: `trace.compile_trace` writes `trace.csv` and returns a manifest with
  `trace_sha256 = sha256(trace.csv bytes)` (`trace.py:1504`, compiler at `:1143`) + a provenance
  block (`TRACE_PROVENANCE_SCHEMA`, `:1519+`).
* `t1_tasks.design()` puts `source.trace_sha256` into the document (`:2095-2105`).
* `network_alignment` builds `identity` from `artifact_identity.build_identity(
  config=resolved, trace_digest=source["trace_sha256"], driver_paths=…)` and a
  `fairness` block with **`rows_digest = _rows_digest(rows)`**, `same_trace: True`,
  `same_seed`, `same_predictor`, `arms_differ_only_in: "time_alignment.arm…"`
  (`t1_tasks.py:1955-1982`).
* Per-arm `config_sha256` is `resolved["sha256"]` of that arm's config (arm field
  substituted) — so the four arms' `config_sha256` **differ by design**; the
  shared-trace claim is `fairness.rows_digest` (and `source.trace_sha256`).
* `load_calibration` instead writes `trace-manifest.json` with
  `{rows, jsonl_sha256, source_trace_sha256, source_rows_digest}` (`:673-693,947-954`)
  and compiles **once** before the arm loop (`:939-954`) — one cell ⇒ one trace
  ⇒ all its arms share it.

**Verification recipe (no new code):**
```bash
python3 - <<'PY'
import json
d=json.load(open("runs/<cell>.json"))
print(d["source"]["trace_sha256"], d["fairness"]["rows_digest"], d["fairness"]["same_trace"])
for a in d["arms"]:
    print(a["arm"], a["config_sha256"][:16], a["source"]["trace_sha256"] if "source" in a else "")
PY
```
All four arms must report the **same** `rows_digest` / `trace_sha256`; the four
`config_sha256` must differ, and the diff must be exactly `time_alignment.arm`.

---

## 6. Burst — exact mechanism and the "same base trace" question

**[READ] Burst is a trace transform, t = compile time only.**
`trace._rate_multiplier(mode, t, src_lon, dm)` returns `burst_multiplier` for
`burst_start_s ≤ t < burst_start_s + burst_duration_s` (optionally only for
sources inside `[burst_lon_min, burst_lon_max]`), else 1.0 (`trace.py:325-340`).
The generator draws at `base_rate·max_mult` and thins by `m(t)/max_mult`
(`:1367-1410`), i.e. an exact non-homogeneous Poisson with a two-level step.
**The kernel never sees a burst** — `grep burst kernel.py` returns only
`initial_sync_burst_s` (a *control-plane* start-up burst, `kernel.py:1312-1321`).

Algebra (already published in `ANALYSIS/BURST-DESIGN.md` §1.1):
```
f = W/T_e                     (T_e = emission_end_s, W = burst_duration_s)
mean_mult = 1 + (B-1)·f
CV²_int   = (B-1)²·f·(1-f) / mean_mult²
```
For the pilot (`B=1.6`, `W=0.5`, `T_e=14`): `f = 0.035714`,
`mean_mult = 1.02143`, `CV² = 0.0119` — **a very mild burst**, and it *raises*
the mean load by 2.14 %.

### 6.1 YAML keys
```yaml
demand:
  mode: gravity            # or burst | mlab | population_gravity
  burst_start_s: 8.0
  burst_duration_s: 0.5
  burst_multiplier: 1.6
  # optional: burst_lon_min: <lon>; burst_lon_max: <lon>   # regional instead of global
```

### 6.2 Can burst be "high load only, same base trace"? **No — and it does not need to be.**
1. The burst **is** part of the compiled rows. Adding it changes
   `trace_sha256`. Therefore *the un-bursted and bursted traces are different
   inputs by construction*; the repo's own precedent (`t1_scene_trunk_burst_v1.yaml`)
   ships the burst as a **second profile** that differs in exactly 4 demand lines.
2. The requirement that actually matters — *"arms must share identical trace"* —
   is satisfied **inside a cell**: `network_alignment` compiles once and runs all
   four arms on the same `rows`; `load_calibration` compiles once before the arm
   loop. Cross-condition (low vs high load) the trace must differ anyway,
   because `offered_mbps` **also** changes the row set (packet count and emission
   times). So "same trace across the low and high condition" is unachievable for
   **any** load-calibration design, independent of burst.
3. Consequence for the design: burst at **high load only** is legal and cheap —
   one trace for the low point (no burst), one trace for the high point (burst).
   What must be held fixed across the two conditions is the **OD definition**
   (same flows, same weights, same 30/70), not the row set.

### 6.3 How burst interacts with trace-hash identity, concretely
* Native mode: the burst keys are inside `demand`, which is inside
  `resolved["sha256"]`, and the compiled rows → both `config_sha256` and
  `trace_sha256` change ⇒ two cells, two identities, correct.
* CSV mode (recommended, §7): the burst is a *phase rate* inside the workload
  CSV; the CSV's sha256 is bound twice — by `trace.compile_trace`'s
  `source.sha256` (`trace.py:1191-1194`) and by
  `source["synthetic_workload"].summary.input_sha256` with a hard cross-check
  against the compiled pids (`t1_tasks.py:2158-2198`, "synthetic workload
  sidecar does not bind the configured CSV" / "packet manifest differs from the
  compiled platform trace"). That is a **stronger** identity binding than the
  native burst.

---

## 7. "30 % background + 70 % hotspot" — feasibility matrix

### 7.1 What the platform actually offers

| Mechanism | Source (`file:line`) | Background? | Hotspot? | Burst in same profile? |
|---|---|---|---|---|
| `mode=hotspot` | `trace.py:293-306` | yes: `(1-conc)` of each source's packets uniform over the **cold** pool | yes: `conc` of each source's packets over `others[:n_hot]`, `n_hot = max(1, round(len(others)·hotspot_fraction))` | **NO** — `config.py:811-832` refuses the window; verified: `ConfigError: demand.burst_start_s/burst_duration_s are declared but demand.mode=hotspot never applies the burst transform` **[LOCAL-CHECK]** |
| `mode=gravity` (+ weights, α) | `trace.py:284-292`; source weights `:1363-1366` | implicit only | implicit only | **yes** |
| `mode=population_gravity` (+ region/exponents) | `trace.py:180-235,284-292` | implicit only | implicit only | **yes** |
| `mode=mlab` | `trace.py:283-298` | measured OD proxy | measured OD proxy | **yes** |
| `mode=csv` (from `synthetic_od`) | `trace.py:1185-1300`; `synthetic_od.py` | **explicit flow group** | **explicit flow group** | **yes, as a load phase** |
| `scene_trace_program.s3_correlated_hotspot` | `scene_trace_program.py:25-33,161-172` | explicit (non-hot ODs stationary) | explicit `hot_ods` subset | only **recurring** (≥3 cycles per window, `:153-157`) — **cannot** be one 0.5 s window — and only via a load-calibration cell ⇒ blocked by `ALLOWED_PROFILES` |

**Measured candidate shares [LOCAL-CHECK]** (140/7/600/98.6, 8 sites, 1650 packets,
`packet_bits=1e6`, all `demand_weight = 1.0`):

| Construction | top destination share |
|---|---|
| `gravity`, `α=2.0`, all weights 1 | **0.278** |
| `gravity`, hotspot weight 10 / 30 / 60, `α=2.0` | 0.464 / 0.590 / 0.642 |
| `gravity`, hotspot weight 60, `α=2.5` | **0.714** — but **90.2 % of packets then *originate* at the hotspot** ⇒ "background" collapses to 9.8 %, i.e. **hub-source, not 30/70** |
| `hotspot`, `frac 0.1`, `conc 0.70 / 0.75 / 0.80` | 0.640 / 0.677 / **0.726** ⇒ `conc ≈ 0.78` gives 0.70 exactly |

### 7.2 Verdict
* The declared **30/70 split with a shared corridor** is natively available **only**
  through `mode=hotspot`, and that mode **cannot carry the burst**.
* The declared **burst** is natively available only in modes that **cannot
  declare the 30/70 split**.
* ⇒ **The combination requires `demand.mode=csv` fed by the existing
  `synthetic_od.py` generator.** No new generator, no hand-written trace: the
  trace is produced by the repo's own tool from a declared flow/phase matrix.

### 7.3 Recommended OD construction (verified by actual trace compilation)

8 sites, 14 flows, one hotspot destination ⇒ a genuinely shared corridor:

```
sites: hotspot(35.68,139.69) seoul beijing singapore london newyork saopaulo sydney
HOT  (70 % of bits, 7 flows, each 10 %):  <each of the 7 others> -> hotspot
BG   (30 % of bits, 7 flows, each 4.286 %): ring among the 7 others
```
* 70 % of all bits converge on **one** destination cell ⇒ one shared corridor.
  **[LOCAL-CHECK]** the ≥2-alternative requirement is structural: across a full
  orbit sample, **min out-degree = 2 and min in-degree = 2 for every one of the
  140 satellites** (N and S are permanent in-plane; 476-504 directed ISLs total,
  i.e. 70-80 % of satellites also carry E and/or W). The per-OD check is §8.2.
* Background is spread over 7 disjoint flows (not a full mesh — a full mesh adds
  42 flows whose per-flow counts get tiny and increases the (t,pid) tie risk, §10.5).
* Fidelity gap vs native `mode=hotspot`: the hot pool here is a *single declared
  destination*, whereas `mode=hotspot` picks `others[0]` **per source**, so for
  the source equal to the hotspot the hot destination becomes the second site.
  The CSV construction is therefore **cleaner** than the native model for this
  pilot, not weaker.

### 7.4 What must be *added* (minimum, no implementation here)
1. **One workload spec** (JSON, `schema: t1-synthetic-od-workload/v1`) per load
   point — the flow list above + 1 or 3 phases (§10.3).
2. **Two to three generated CSV traces + their `.workload.json` sidecars**, by
   calling the existing `synthetic_od.write_trace(...)` and dumping the returned
   summary next to the CSV (exactly what `t1_suite._seed_config` does at
   `t1_suite.py:1332-1341`). The sidecar is **mandatory**: `t1_tasks.design()`
   refuses a `mode=csv` profile whose sidecar does not bind the CSV or whose
   `packet_manifest` differs from the compiled trace.
3. **One profile YAML** (§10.2).
Nothing else. No simulator change, no new demand mode.

---

## 8. Calibration: how to find ρ ≈ 0.2-0.3 and ρ ≈ 0.8-0.9, and how to measure it

### 8.1 The analytic ruler — and the bracket problem [LOCAL-CHECK]

`scene_load_calibration.calibrate()` on the real pilot geometry
(140/7/600 km/98.6°, min_elev 10°, `max_isl_km 6000`, `packet_bits 1e6`,
ISL 100 Mbps, queue 64 Mbit, control 8000 bit @1.0 s, `vis_k 2`, horizon 20 s,
window 1 s):

```
link_coefficients: directed_edges 59*, sum_b_e 4.871, max_b_e 0.2429, max_c_e 32 000 bit/s
traffic_split:     zero_hop 0.243, unroutable 0.0, routed 0.757, mean_isl_hops 6.43
checks:            serialization_s 0.010, max_control_util 0.00032
alternatives:      routed_pairs 11, pairs_with_no_alternative 1, median_alternative_stretch 1.72
tiers: rho 0.20->R 82.3 ; 0.30->123.5 ; 0.80->329.4 ; 0.85->349.9 ; 0.90->370.5 Mbps
```
*`directed_edges 59` is the count of directed links **with b_e > 0** on the
reference route set, not the physical ISL count.

With the natural 8-site hub OD (`70 % hub + 30 % ring`), the requested bracket
**does not reach the high band**:

| R (Mbps) | 40 | 60 | 120 | 200 | 240 | 400 |
|---|---|---|---|---|---|---|
| ρ at `b_e = 0.2429` | 0.10 | 0.15 | **0.29** | 0.49 | 0.58 | 0.97 |

So: 120 Mbps is a *perfect* low point, 40 is too low, and **200 is 1.8× short of
0.85**; the allowed ×2 point 400 overshoots into saturation. The bracket can
only work if `max_e b_e` is raised to ≈ 0.39-0.49 by reshaping the OD
(fewer, more-converged hotspot sources):

| OD design | `max_e b_e` | ρ@40 | ρ@60 | ρ@120 | ρ@200 | pairs w/o alt | zero-hop |
|---|---|---|---|---|---|---|---|
| 7 hub sources + 30 % ring | 0.2429 | 0.10 | 0.15 | 0.29 | 0.49 | 1 | 0.243 |
| 3 hub sources (35/20/15) + 30 % ring | **0.3929** | 0.16 | **0.24** | 0.47 | **0.79** | 1 | 0.043 |
| 4 hub sources + 30 % ring | 0.3929 | 0.16 | 0.24 | 0.47 | 0.79 | 1 | 0.043 |
| **2 hub sources (45/25) + 30 % ring** | **0.4929** | **0.20** | **0.30** | 0.59 | 0.99 | 1 | 0.043 |
| 3 hub sources + 30 % hub→others | 0.3500 | 0.14 | 0.21 | 0.42 | 0.70 | **0** | 0.086 |
| 100 % hub | 0.2857 | 0.11 | 0.17 | 0.34 | 0.57 | 0 | 0.286 |

**Recommended calibration bracket: {60, 120, 200} Mbps** on a **3-source hotspot
OD** (b_e ≈ 0.39): ρ ≈ 0.24 / 0.47 / 0.79, i.e. the low point is inside
[0.2, 0.3] and the high point is at the bottom of [0.8, 0.9] (0.79 analytic; the
measured value should be read from the run, and 240 Mbps — an allowed ×2 point of
120 — is the fallback in case the measured high point lands below 0.80).
All three points are legal under "40/120/200 plus at most 2 geometric ×2 or /2
points": 60 = 120/2, 120 and 200 are the declared points; 240 = 120×2 is the one
spare.

**Two design checks that must be run before committing** (both are existing
tools, see §9):
1. `pairs_with_no_alternative` must be **0 for the hotspot flows**; in the sweep
   above the single no-alternative pair was the *background* flow whose two cells
   resolve under the same satellite (`reference_s` 12.1 ms ⇒ 0 ISL hops). Either
   drop that background flow or accept and document it.
2. `zero_hop_weight` must be small and *reported*: the trace compiler rejects
   only same-1°-cell pairs (`trace.py:1250`), so a 0-ISL-hop pair is legal but it
   contributes no ISL load.

### 8.2 "≥ 2 plausible alternative next hops" — machine-checkable, already implemented
`scene_load_calibration`'s `alternatives` block, per OD pair, per 1 s window:
`{reference_s, alternative_s, stretch, windows_with_alternative, windows}` where
`alternative_s` is the shortest walk that **avoids every edge of the reference
path** (`first_alternative_s`, `:299-330`). Requirement: for every hotspot flow,
`windows_with_alternative == windows` (20/20) and `stretch ∈ [1.0, ~2.5]`.

### 8.3 Post-warmup directed-ISL ρ — exact recipe

Definitions to fix in the analysis plan (write them down before looking):
* `ρ_e(W) = Σ_{b∈W, eligible} served_bits_e(b) / Σ_{b∈W, eligible} available_capacity_bits_e(b)`
  with `W = [4,14)`, bins of 0.5 s (⇒ 20 bins; bin 16 is exactly `[8.0,8.5)`).
* `ρ_corridor(W) = max_e ρ_e(W)` over the corridor set, reported with the ranking.
* burst sub-window: `ρ_e([8.0,8.5))` and `ρ_e([4,8))`, `ρ_e([8.5,14))` separately.
* `frac_links_ρ≥0.2` and `p99` of active-window ρ (prior art:
  `spread_compare.py`).
* `eligible` = the bin's `available_time_s ≥ 0.9·0.5` (`DEFAULT_MIN_AVAILABLE_FRACTION`),
  i.e. a partially-down link is not counted as a low-ρ success.

Command (reads the run output only; `isl_pressure` is existing code):
```bash
python3 - <<'PY'
import json, statistics, sys
sys.path.insert(0, "CODE")
from CODE.experiment_platform import isl_pressure
d = json.load(open("runs/<cell>.json"))
for arm in d["arms"]:
    r = arm["replay"]
    ledgers = {"stop_time_s": r["stop_time_s"],
               "packet_events": r["packet_events"],
               "link_service_windows": r["link_service_windows"],
               "link_available_windows": r["link_available_windows"]}
    res = isl_pressure.analyze_windows(ledgers, window_s=0.5,
                                       high_utilization=0.8,
                                       min_consecutive_high_windows=2)
    rows = []
    for lid, link in res["links"].items():
        ev = [w for w in link["windows"] if 4.0 <= w["start_s"] < 14.0 and w["eligible"]]
        num = sum(w["served_bits"] for w in ev); den = sum(w["available_capacity_bits"] for w in ev)
        burst = [w for w in link["windows"] if 8.0 <= w["start_s"] < 8.5]
        rows.append((num/den if den else 0.0, lid, (burst[0]["utilization"] if burst else None)))
    rows.sort(reverse=True)
    print(arm["arm"], "rho_corridor(4-14)=", round(rows[0][0],3), rows[0][1],
          "burst_bin=", rows[0][2], "| top5:", [(l, round(u,3)) for u, l, _ in rows[:5]])
PY
```
(Requires the run to be produced with `--capture-replay`.)

**Corridor definition (deterministic, two views, report both):**
1. *Analytic*: the directed ISL that `scene_load_calibration` names as
   `bottleneck_edge` for the chosen ρ tier. It is an in-plane N/S pair, therefore
   permanent under rematching, so the id is stable for the whole run.
2. *Empirical*: the top-3 directed ISLs by `ρ_e([4,14))` from the recipe above,
   plus the symmetric directions. The calibration target is the **empirical max**;
   the analytic edge is the falsifiable prediction (this is exactly the
   predicted-vs-measured design of `analyze_scene_calibration.py:9-13`).

### 8.4 Whole-horizon cross-check (cheap, already wired)
`run["arms"][i]["congestion_metrics"]["links"][link]["utilization"]` is the
horizon-aggregate ρ (`metrics.py:334-338`). Use `analyze_scene_calibration.py
--runs … --calibration …` only if the runs are produced by `load_calibration`
(layout `cal/<cell>/arms/<arm>/result.json.gz`); for `t1_tasks` output it will
warn "no completed cell".

---

## 9. Preflight / validators and what each checks

| Command | Checks | Notes |
|---|---|---|
| `python3 -m CODE.leo_sim config validate <profile.yaml>` | schema + every semantic constraint in §4 | returns `{"status":"ok","version":"leo-sim-config/v1","sha256":…}`. **[LOCAL-CHECK]** ran clean on both drafts: the CSV draft of §10.2 as written ⇒ `sha256 574e506652c5385797928537a8ee817197ce81627165a914afb7bc06c5a6cd8c`; the §10.1-B1 gravity+burst variant ⇒ `sha256 7d3c838aeec7ec0f…`. |
| `python3 -m CODE.experiment_platform.load_calibration --contract <c.json> --cell <id> --out-dir <new> --preflight` | full contract schema, `deadline_s == 4.0`, `max_duration_s`, `max_packets`, `base_profile ∈ ALLOWED_PROFILES`, cell id regex, arm names, override whitelist, override-shape, **resolved config validity**, out-dir does-not-exist + ≥5 GiB free | creates no dirs, no kernel calls (`:845-871`) |
| `python3 -m CODE.experiment_platform.network_arm_run --scenario … --arm … --out … --replay-out … --preflight` | scenario enum, arm, config resolves, prints `config_sha256` + `trace_sha256`, `kernel_calls: 0`, `claimable: false` | **useless for 140 sats** (enum) |
| `python3 -m CODE.experiment_platform.scene_load_calibration calibrate --spec <spec.json> --out <dir>` | OD weights sum to 1, horizon/window integral, every tier feasible (never clamps) | this *is* the OD/bracket validator; also emits `alternatives` and `checks.access` |
| `python3 -m CODE.experiment_platform.scene_load_calibration selftest` | closed-form arithmetic self-test | exit 1 on any failure |
| `python3 -m CODE.experiment_platform.control_reach_probe --config <p> --vis-k N --drain-to T --out <f>` | is the advertisement **reach** the cause of missing info; asserts the row digest is unchanged across arms | the tool to justify `vis_k` |
| `python3 -B CODE/scripts/maintenance_check.py --repo .` | repo hygiene (read-only) | required at start/end by `AGENTS.md` |
| `t1_tasks --require-pinned-code` / `--launch-manifest` | refuses to launch off a non-commit/non-verified-release tree; writes pre-run manifest | use on the VM |
| contract validation for load-calibration | **only** via `load_calibration.py --preflight` (there is no standalone validator; `_validate_contract_document` `:231-280`) | — |

**Runner `--config` semantics (from `CODE/AGENTS.md`, verified against
`launch_manifest.py` usage):** on the VM the sanctioned entry point is
`CODE/scripts/remote/run-release-remote.sh`; its `--config` **only registers and
freezes the input snapshot**, it does **not** fill positional arguments — so
after `--` you still pass the release-relative config path explicitly and the
runner substitutes the run-isolated snapshot. Therefore every command in §10.4
is written as `<python> -B -m CODE.experiment_platform.t1_tasks … --config
<release-relative path>` and is launched through that script, not by bare
`python -m`.

---

## 10. Concrete plan

### 10.1 Option comparison

| | A: load-calibration contract + cell | **B: new profile + `t1_tasks network_alignment`** | C: new contract path / new demand mode |
|---|---|---|---|
| Code change | **1 line** (`ALLOWED_PROFILES += pilot profile`) — otherwise refused | **none** | new contract schema/validator or a new demand mode ⇒ new code |
| 140-sat possible | only after the whitelist edit | yes, today | yes |
| One-arm calibration | native (`arms: ["now"]` per cell) | `--arms now` | native |
| D fixed at 4.0 s | **forced** (`:258-261`) | free (`--deadline-s`, `--allow-non-binding-deadline`) | forced or new code |
| Trace-once-for-arms guarantee | explicit (compile before arm loop) | explicit (`design()` once) + `fairness.rows_digest` | — |
| Per-directed-ISL raw ledgers | yes (`result.json.gz`) | yes (`--capture-replay` → `arms[i].replay`) | — |
| Verdict | **rejected** (code edit + D=4 forced + no 140-sat profile in whitelist) | **ACCEPTED (B2)** | over-engineering |

**Option B is the answer. B2 (CSV workload) is recommended; B1 (native
gravity+burst) is the fallback.**

### 10.2 Draft profile (B2) — `CODE/leo_sim/profiles/t1_pilot140_hotspot_csv.yaml`

```yaml
config_version: leo-sim-config/v1
# PILOT-140-HOTSPOT-CSV: minimal time-alignment mechanism pilot.
# Demand is an explicit declared workload (synthetic_od.py) so that the
# 30/70 background/hotspot split AND the single [8.0,8.5) 1.6x burst can
# coexist; the platform refuses that combination in every native mode.
# Everything except `demand` and `out_dir` is identical between the low and
# the high cell; the two cells differ ONLY in `demand.csv_path`.
scenario:
  name: pilot-140-hotspot-hi-burst
  duration_s: 20.0          # 0-4 warm-up, 4-14 eval, 8.0-8.5 burst, 14-20 drain
  time_step_s: 0.1
  num_satellites: 140       # 7 planes x 20
  num_planes: 7
  altitude_km: 600.0
  inclination_deg: 98.6     # Walker-delta F=0 (model.py:507-524)
  min_elevation_deg: 10.0
  geometry_epoch_s: 0.0
  seed: 7                   # the ONE seed of the pilot
endpoints:
  grid_deg: 0.25
  aggregation_deg: 1.0
  # csv mode: serving cells come from the trace itself (trace.py:1290-1292)
demand:
  mode: csv
  csv_path: /data/论文/leo-t1-wt/scratch/pilot-140/pilot-140-hotspot-hi-burst.csv
  offered_mbps: 120.0       # declared base rate; csv mode reports load_mode=observed_trace
  packet_bits: 1000000      # speed-forced
  emission_start_s: 0.0
  emission_end_s: 14.0
  temporal_model: constant
access:                     # access must NOT be the bottleneck
  unavailable_policy: queue
  slots_per_satellite: 256
  uplink_rate_mbps: 2000.0
  downlink_rate_mbps: 2000.0
  uplink_queue_bits: 4000000000
  downlink_queue_bits: 4000000000
  holding_queue_bits: 268435456
  association: bbm
  hysteresis_deg: 0.0
  min_dwell_s: 0.0
  acquisition_delay_s: 0.01
  idle_release_s: 0.2
  slot_lease_s: 10.0
  drr_quantum_bits: 1000000
links:
  rate_model: constant
  isl_rate_mbps: 100.0            # speed-forced
  isl_queue_bits: 64000000        # 64 Mbit = 10 ms serialisation at 100 Mbps
  max_isl_km: 6000.0              # REQUIRED: cross-plane range reaches 5654 km
  geometry_loss: true
  ge_enabled: false               # random GE outage OFF
  isl_dirs: [N, S, E, W]
topology:
  recompute_interval_s: 1.0       # dynamic cross-plane rematching
  matching: markovian
control_plane:
  enabled: true
  advertisement_protocol_version: 2   # leo-control-ad-wire/v2: resource identity/generation/workload
  advertise_interval_s: 1.0
  ttl_s: 10.0
  vis_k: 2                            # minimal sufficient (2-hop resource view)
  packet_bits: 8000
  priority: nonpreemptive_priority
routing:
  policy: known_serving               # same for all four arms
  max_hops: 64
  learning_enabled: false
learning:
  algorithm: none
execution:
  compute_delay_s: 0.00005            # 50 us declared near-ideal decision plane
  compute_servers_per_satellite: 1    # TA requires <= 1 (config.py:1216)
  decision_observation_mode: frozen
  node_process_delay_s: 0.0
  available_capacity_interval_s: 1.0  # REQUIRED for a real rho denominator
  max_events: 5000000                 # fail-closed cap, not a target
  max_entities: 20000
  max_packets: 20000                  # fail-closed cap (1716-2860 expected)
time_alignment:
  enabled: true
  arm: now                            # overwritten per arm by t1_tasks._arm_row
  predictor: bounded_linear
  history_limit: 4
  common_rule: median_eta
  query_delay_s: 0.000001
outputs:
  out_dir: leo_sim_out/pilot-140-hotspot-hi-burst
  plotting: false
```
**Low cell** = byte-identical file with `scenario.name`, `demand.csv_path`,
`demand.offered_mbps` and `outputs.out_dir` changed only.

### 10.3 Draft workload specs (input to the existing generator)

`hotspot_flows`: `{"id": "hot_<city>", "src": "<city>", "dst": "hotspot"}` for
each of seoul, beijing, singapore, london, newyork, saopaulo, sydney;
`background_flows`: ring among those seven.
`packet_bits: 1000000`, `jitter_s: 0.002`.

Per-flow **counts** per phase (allocate 70 % to the 7 hot flows, 30 % to the 7
background flows, then `rate_pps = count / phase_duration` — this is what makes
the generator's integer check pass, `synthetic_od.py:120-125`):

| Load point | Phases | Packets / realised load **[LOCAL-CHECK, verified by compiling]** |
|---|---|---|
| low 60 Mbps (fallback 40) | `[{"id":"steady","start_s":0.0,"end_s":14.0}]` | 840 (60 Mbps) / 560 (40 Mbps) — exact |
| high 120 Mbps + burst | `pre [0,8)` · `burst [8,8.5)` · `post [8.5,14)` | 1716 total; phase rates 120 / **192** / 120 Mbps; **realised multiplier = 96/60 = 1.600 exactly** |
| high 200 Mbps + burst | same three phases | 2860 total; 200 / **320** / 200 Mbps; **realised multiplier 160/100 = 1.600 exactly** |

Generation (one-off, existing function; the sidecar is mandatory):
```bash
python3 - <<'PY'
import json, sys
sys.path.insert(0, "CODE")
from CODE.experiment_platform import synthetic_od
spec = json.load(open("workload-hi-120-burst.json"))     # the flow/phase matrix above
summary = synthetic_od.write_trace("<dir>/pilot-140-hotspot-hi-burst.csv", spec, seed=9)
open("<dir>/pilot-140-hotspot-hi-burst.workload.json", "w").write(
    json.dumps(summary, ensure_ascii=False, sort_keys=True))
PY
```
Then set `demand.csv_path` in the profile to that CSV and run
`config validate` on the profile.

### 10.4 Exact commands (VM, inside a verified release)

```bash
ROOT=/data/论文/leo-t1-wt
PY=$ROOT/envs/t1-linux-aarch64-py311-pkgset-v1/bin/python
RUN=$ROOT/runs/<run-id>                    # new run-id, never reused
SCR=$ROOT/scratch/pilot-140

# --- P0  static preflights (no kernel) -----------------------------------
python3 -m CODE.leo_sim config validate CODE/leo_sim/profiles/t1_pilot140_hotspot_csv.yaml
python3 -m CODE.experiment_platform.scene_load_calibration calibrate \
        --spec pilot-140-od-spec.json --out $SCR/calib     # b_e, R(rho), alternatives
python3 -m CODE.experiment_platform.control_reach_probe \
        --config CODE/leo_sim/profiles/t1_pilot140_hotspot_csv.yaml \
        --vis-k 2 --drain-to 20 --out $SCR/reach.json      # justify vis_k

# --- P1  load calibration: ONE arm, 3 points ----------------------------
for R in 60 120 200; do
  $PY -B -m CODE.experiment_platform.t1_tasks --task network_alignment \
      --config CODE/leo_sim/profiles/t1_pilot140_hotspot_csv_R${R}.yaml \
      --arms now --deadline-s 4.0 --window-start 4 --window-end 14 \
      --allow-non-binding-deadline --capture-replay \
      --out runs/<run-id>/cal-R${R}.json
done
# measure rho per §8.3, pick the two points, then

# --- P2  the mechanism cell: four arms, two conditions ------------------
for C in lo hi; do
  $PY -B -m CODE.experiment_platform.t1_tasks --task network_alignment \
      --config CODE/leo_sim/profiles/t1_pilot140_hotspot_csv_${C}.yaml \
      --arms stale,now,common,candidate --deadline-s 4.0 \
      --window-start 4 --window-end 14 --allow-non-binding-deadline \
      --capture-replay --target-probe --target-probe-stride 20 \
      --target-probe-max 5000 --out runs/<run-id>/arms-${C}.json
done
```
`--deadline-s 4.0` keeps one fixed D for all arms. **Expect
`T1TASKS WARNING: the frozen D rejected no packet`** at the low point (per-packet
delays are ~0.1-0.2 s) ⇒ `status` `DEGRADED` / exit 3 unless
`--allow-non-binding-deadline` is passed; the primary observable of this pilot is
ρ, not deadline loss, so accept it *explicitly in the plan*.

### 10.5 Trace-hash verification and the known CSV pitfall

* Within each condition, `fairness.rows_digest` + `source.trace_sha256` must be
  identical across the four arms and the four `config_sha256` must differ only by
  `time_alignment.arm` (§5 recipe).
* **Pitfall [LOCAL-CHECK, reproduced]:** `trace.compile_trace` requires the CSV
  rows to be sorted by `(emit_time_s, packet_id)` **after** the 6-decimal
  serialisation (`trace.py:125`, `_format_time`). `synthetic_od` sorts on the
  *unrounded* time, so two packets from different flows that round onto the same
  microsecond can violate it:
  `TraceError: trace row 758: rows must be sorted by (emit_time_s, packet_id)`.
  It is deterministic. It happened at generator seed 7 and 8 and disappeared at
  seed 9 for both 120 and 200 Mbps. **Mitigation: generate the CSV, compile it
  once locally (`t1_tasks.design` or `config`+`compile_trace`), and if it fails
  bump the generator seed and re-compile — record the accepted CSV's sha256.**
  Distinct per-flow counts (rather than 7 identical rates) reduce the frequency;
  a seed table is not needed.

### 10.6 Runtime estimate (per arm, high-load cell)

Event budget (analytic, 1716 packets, `vis_k 2`, adv 1 s, 20 s horizon):
* data plane: 1716 × (emit + uplink service + ~6.3 ISL hops × ~4 events + downlink) ≈ **50 k**
* control plane: 140 sats × 20 rounds = 2800 broadcasts × ≤ ~13 relay nodes ≈ 36 k control packets × ~3 events ≈ **110 k**
* topology rematch (20 × 140) + availability sampling (~500 links × 20) ≈ **13 k**
⇒ **~180 k events/arm**, ~700 k for a four-arm cell.
Wall clock: simpy sustains O(10⁴) events/s here, and the dominant cost is
JSON-serialising `packet_events`/`timeline`/decisions: expect **1-3 min per arm**,
**5-12 min per four-arm cell**, **3-10 min for the three-point calibration**.
(Confirm with one smoke arm before launching the batch; use
`pnpm`-free, single-process runs so the four arms stay comparable.)

### 10.7 Risks / blockers

| # | Risk | Severity | Handling |
|---|---|---|---|
| R1 | **The requested bracket 40/120/200 does not reach ρ 0.8-0.9** at the natural OD (`b_e = 0.243` ⇒ 0.49 at 200 Mbps). | **HIGH** | Fit the OD first with `scene_load_calibration` (§8.1); use `{60,120,200}` on a 3-source hotspot (`b_e≈0.39`) or `{60,120,240}` if the measured high point lands below 0.80. **Do not report a "high-load" arm whose measured ρ is 0.5.** |
| R2 | **30/70 + single burst is impossible in native modes** (`mode=hotspot` refuses the window). | **HIGH** | Use B2 (csv workload). If B2 is vetoed, declare B1 and state in the plan that the "background" share is emergent (measured 90 % hub-sourced) — i.e. the 30/70 claim would be **false** and must be dropped from the write-up. |
| R3 | CSV rows can fail the `(t,pid)` sorting gate. | MEDIUM | §10.5: compile-check the CSV, bump the generator seed if needed, record sha256. |
| R4 | `--deadline-s 4.0` is non-binding at low load ⇒ `DEGRADED`/exit 3. | MEDIUM | Pass `--allow-non-binding-deadline` and pre-declare that the pilot's primary observable is ρ; or additionally freeze a *second* D (e.g. 0.5 s) for the loss metric and say so before running. |
| R5 | `max_isl_km = 3000` (copied from the corridor family) silently removes every E/W ISL — a ring-only topology with no "2 alternative next hops". | MEDIUM | Bake `max_isl_km: 6000.0` into the profile; assert `directed ISL count ∈ [476, 504]` in the preflight. |
| R6 | One OD pair reported no alternative in the analytic sweep (a 0-ISL-hop background pair). | LOW | Drop/replace that background flow; re-run §9 `alternatives`; require `pairs_with_no_alternative == 0` for hotspot flows. |
| R7 | Burst is mild (`CV²=0.0119`, mean load +2.1 %) — a "burst" that cannot be seen in ρ may be attacked as decorative. | LOW-MEDIUM | Pre-declare the two window ρ's (`[4,8)` vs `[8,8.5)`) and the queue-depth trace as the burst evidence; `--capture-replay` + `isl_pressure` give both. |
| R8 | `vis_k=2` may be insufficient (audit shows `missing_directions`). | LOW | Run `control_reach_probe --vis-k 2` and, if it shows reach failures, step to 3 and re-document. |
| R9 | `mode=csv` makes `--offered-mbps` inert; the load is the CSV's. | LOW | One CSV per load point (already the plan); quote the *realised* load from `summary.phases[].nominal_mbps`, not from the flag. |
| R10 | Control-plane load at adv 1.0 s / 140 sats is 0.032 % of the 100 Mbps ISL (`max_control_util 0.00032` [LOCAL-CHECK]) — negligible, but confirms `advertise_interval_s=1.0` is affordable. | — | no action |

### 10.8 What the platform genuinely **cannot** do (state plainly)
1. **Declare 30 % background + 70 % hotspot *and* a single-window burst in one
   native profile.** Native modes are mutually exclusive: `hotspot` (mixture) has
   no burst; burst-capable modes (`gravity`, `population_gravity`, `mlab`,
   `burst`) have no declared mixture. Workaround = `mode=csv` (B2); there is no
   config-only path.
2. **Change the constellation from the CLI.** `t1_tasks` overrides are limited to
   compute/packet/load/async/horizon knobs; `load_calibration` cannot override
   `scenario` geometry either. A new profile file is unavoidable.
3. **Make the burst a runtime knob.** It is baked into the trace.
4. **Keep one trace across the low and high load conditions.** Load changes the
   row set for every demand mode.
5. **A single 0.5 s burst through `scene_trace_program`.** Its two-state
   envelope refuses `cycle_s > window/3`, so it can only produce ≥3 recurring
   bursts per emission window (and it is unreachable without editing
   `ALLOWED_PROFILES`).
6. **A per-directed-ISL ρ *within a window* straight out of the runner.** No CLI
   prints it; the ledgers are there (`--capture-replay`) but the reduction is the
   `isl_pressure.analyze_windows` snippet in §8.3. `analyze_scene_calibration.py`
   only windows=whole-horizon and only reads `load_calibration`'s directory layout.
7. **A "background + hotspot" declaration in `demand.mode=population_gravity`.**
   `nested_master_offered_mbps` looks like one but is a master/child replay
   device (`trace.py:1661-1678`), not a two-group demand model.

---

## 11. Open questions for the Lead (decide before writing any file)

1. **B2 (csv workload, exact 30/70 + exact 1.6× burst) or B1 (native
   gravity+burst, no declared 30/70)?** B2 costs one workload spec + 2-3 CSVs +
   sidecars; B1 costs the 30/70 claim.
2. **Calibration bracket**: accept `{60,120,200}` (needs the `b_e≈0.39` OD) or
   authorise `{60,120,240}` as the guaranteed way into [0.8,0.9]?
3. **Deadline D**: keep the frozen 4.0 s and mark the low arm `DEGRADED`
   (primary observable = ρ), or add a second 0.5 s D for a binding loss metric?
4. **`compute_delay_s`**: 50 µs (literal "compute cost cannot bind", matches the
   pilot's intent) vs 1 µs (`t1_scene_trunk_v1`'s idealised plane). 50 µs at
   ρ≈0.85 with ~6 hops is still far below the 10 ms serialisation, so 50 µs is
   the better-justified choice.

---

## 12. Concurrent work in this worktree (not mine — read before acting)

When I finished this audit the worktree was **no longer clean**. Two source
files are modified by another agent (task-1/task-2 scope), and they change the
Option A verdict of §10.1:

```
 M CODE/experiment_platform/load_calibration.py     (+14)
 M CODE/experiment_platform/scene_trace_program.py  (+73)
```

1. `load_calibration.ALLOWED_PROFILES` has been extended with
   **`CODE/leo_sim/profiles/ta_pilot_140_adv1.yaml`** — which **does not exist in
   the worktree yet**. Until that file is written, every load-calibration
   preflight/run that selects it will fail closed with
   `frozen base profile is unavailable`. (The file is a frozen *input*, so it must
   be created before any contract referencing it is preflighted.)
2. `scene_trace_program` is being extended with an optional **single global
   `pulse`** (`PULSE_KEYS = (start_s, duration_s, multiplier)`), explicitly
   documented as "a TREATMENT, not a mean-preserving burst envelope … the
   long-run mean is deliberately higher" — i.e. it is the missing single-window
   burst that §7.1 found absent, available through a declared trace program.
   `_project_decision` also gains `hop_index` as a cross-arm key.

**Consequences for this memo:**
* If that edit lands, **Option A becomes viable**: contract + cell, with the
  30/70 OD as `od_weights` (`hot_ods`/`s3`) and the burst as `pulse`, and the
  profile as the 140-sat base. Three differences from Option B remain and are
  *not* fixed by the edit: (i) contract `deadline_s` is still frozen at exactly
  **4.0** (`load_calibration.py:258-261`); (ii) the drive-by-`t1_tasks` free
  `--deadline-s` / `--allow-non-binding-deadline` is unavailable; (iii) the
  post-warmup per-directed-ISL ρ still needs `isl_pressure` on the arm
  `result.json.gz` (the contract path *does* give the `cal/<cell>/arms/<arm>/`
  layout that `analyze_scene_calibration.py` reads for whole-horizon checks).
* Everything in §4 (key ranges), §5 (trace identity), §6 (burst = trace
  transform), §8 (ρ measurement) and §10.3-10.7 (workload spec, commands,
  pitfalls, runtime, risks) is unaffected: they are properties of the committed
  HEAD `63664f3` machinery, not of the in-flight edit.
* My verdict "Option A is rejected because it needs a code edit" applies to
  HEAD. With the edit in place the choice becomes a *scientific* one, not a
  mechanical one: Option A gives a hard D=4 and a 12 s minimum horizon but
  enforces one-arm-at-a-time cells; Option B gives free D + `t1_tasks`'
  four-arm fairness block. **Recommendation if the edit lands: use Option A for
  the three-point calibration (it is built for exactly that) and Option B for
  the four-arm mechanism cells** — both then run on the same profile.
