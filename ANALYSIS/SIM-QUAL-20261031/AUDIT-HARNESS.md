# AUDIT-HARNESS-20261031 — 实验台架 / 指标 / A–F 证据链只读审计

> 性质：**只读源码+配置+脚本+已回传证据审计**，不产生科研数值、不改代码、未跑 VM、未 publish/pull。
> 代码身份：worktree `.wt-qual-20261031`，branch `qual/sim-qual-20261031`，base commit `a90c1f9`。
> 审计期间工作树被并行改动：`M CODE/leo_sim/config.py`、`M CODE/leo_sim/kernel.py`、`M CODE/experiment_platform/load_calibration.py`、未跟踪 `CODE/leo_sim/state_policy.py`（本报告只描述 HEAD `a90c1f9` + 已核实的上述差异）；
> 行号口径：`kernel.py`/`config.py` 用 `git grep a90c1f9` 的 committed blob（工作树改动更大）；
> `load_calibration.py` 用**当前工作树**（已含 +17 行未提交白名单扩展，`git diff` 可核；committed 差异见 §0.10/§6.2）。
> 记号 **[V]** 实测/读原始产物 · **[C]** 源码可核对 · **[I]** 推导。
> 旧文档（`TIME-ALIGNMENT-20261030/{RUNBOOK,PILOT}-20261031.md`）已逐条核对 HEAD，偏差集中在 §1.1。

## 0. 结论先行

1. **诊断链路是 contract 驱动，不是 profile 驱动。** profile 只是 `ALLOWED_PROFILES` 6 个 yaml 之一（`load_calibration.py:38`）；每次运行必须 `--contract` + `--cell`（`:1101-1110`）。`-- python3 -m CODE.experiment_platform.load_calibration` 的依据就是这个 parser，已回传 run 的 `run-manifest.json["argv"]` 逐字证实 [V]。
2. **没有独立的"编译 trace"步骤。** `scene_trace_program` 无 CLI；contract 的 `trace_program` 在 `run_calibration` 内被 `materialize_trace_program()`（`:365,423-437`）写成内容寻址 CSV，注入 `demand.mode=csv / csv_path`，再经 `t1_tasks.design()` → `trace.compile_trace()` 编译。**trace sha 是运行产物。**
3. **`diagnostics.decisions` 是 contract 字段**，不是 profile/config 字段；允许键仅 `timeline / decisions / control_audit / truth_probe / truth_probe_max / truth_probe_stride`（`:249-252,305`），`decisions` 默认 true。
4. **决策行有两条不同的 sink**：`load_calibration`（ta-pilot 路）**全量无采样无上限**，但 `_project_decision`（`:566-581`）投影掉 `candidate_resources`/`neighbours`/`own_queue_bits`/观测级 `compute_state`；`t1_tasks network_alignment` 全字段但按 `(pid,hop_index)` blake2b 采样、上限 `T1_AUDIT_MAX_DECISIONS`=5000（`t1_tasks.py:819,852,869,927,939`）。
5. **`T1_AUDIT_MAX_DECISIONS` 在 ta-pilot 路完全无效**（该模块从不读它）；只进 `artifact_identity.ENV_NAMES`（`artifact_identity.py:36`）和 t1_tasks。**跑 4 策略矩阵不需要它。**
6. **queue ledger 只有 `t1_tasks --queue-ledger` 能开**（`t1_tasks.py:2619,2793`）；`load_calibration` 的 kernel 调用不传 `queue_ledger_dir`（`:1038-1043`）。**ta-pilot 那 5 个 run 没有任何 Q(t) 真值。**
7. **target_probe 有真数据陷阱**：实测 `ta-pilot-arms-high-20261031-01/arms/now/target_probe.jsonl` 49,288 行中 1,266 行 `identity_source` 非 `used_state`，其中 **1,143 行解析到 `host==sat` —— 读的是本地首跳链路而非预测的对端出口**，且不报错 [V]。分析必须硬过滤。
8. **跨 run/跨策略同 trace 成立且可核对**：`_trace_identity_payload`（`config.py:1437-1465`）只含 `identity_version, config_version, scenario.{emission_end_s,seed}, endpoints, demand, execution.max_packets`，显式排除 routing/time_alignment/control_plane/links/learning；pid 在 csv 模式逐字保留（`trace.py:34`）。**但同 program 不同 cell 的 `trace_identity_sha256` 会因 `demand.csv_path` 内嵌 cell id 而不同**，"同 trace"要用 `trace-manifest.json.source_trace_sha256`。
9. **`(pid,hop_index)` 只保证"同一包的第 k 次决策分配"，不是同一决策状态**：`hop_index = pkt.decision_ordinal`，每次决策分配 +1（含 hold/fail/重试）（`kernel.py:_allocate_decision`，`a90c1f9`）。首次动作分叉后同 key 已是不同 (sat, 队列)。
10. **4 策略矩阵在 HEAD `a90c1f9` 不可直接跑，但阻塞已在工作树里解掉（未提交）。** HEAD 的 `OVERRIDE_FIELDS["time_alignment"]` 不含 `state_policy/hysteresis_eta_s/ewma_alpha`；工作树 `M load_calibration.py`（+17 行）已把这三个键、以及 `control_plane.packet_bits` 加入白名单 —— **这是 uncommitted 改动，必须提交+推送+发布后才生效**。`ALLOWED_ARMS`（`:65`）仍是 4 个 TA arm，故 1 策略 = 1 cell = 1 run。
11. **成本标度律**（5 个实测 run 拟合）：约 **7 ms/决策**、**19–28 决策/包** ⇒ 单臂 40/120/200 Mbps ≈ **60/175/430 s**；产物 ≈ `800 B×决策 + 2.4 MB`（开 truth_probe 再加 `≈1.4 KB×2×决策`）。
12. **当前 worktree 不能 publish**：树脏（`config.py`/`kernel.py`/`load_calibration.py` 均有未提交改动），`build` 在 `release_protocol.py:351` 以 `source Git worktree is dirty; release build refused` 拒绝。branch 已推（`origin/qual/sim-qual-20261031 = a90c1f9`），**提交并推送当前改动后 publish 即 `verified`**。

## 1. 一次诊断运行的完整流程 + 可直接照抄的模板

### 1.1 必须先纠正的旧文档偏差

| 旧文档 | HEAD 实际 | 影响 |
|---|---|---|
| profile → contract → 编译 trace | contract 的 `trace_program` 运行内物化；无 trace CLI | 模板里不写编译步骤 |
| `load_calibration` 跑不了 140 sat | **已支持**（`:63` 白名单已加 `ta_pilot_140_adv1.yaml`）；`deadline_s` 仍强制 `==4.0`（`:284`），该 profile `duration=20 s`、`emission_end=14 s` ⇒ 视界 6 s ≥ 4 s，D4 合法 | ta-pilot 路可用；`t1_tasks` 的 `D≤0.2 s` 只约束 1.2 s 视界的 Stage C |
| `--config` 会补齐子命令参数 | 不会，只快照 + 改写 argv 字符串（`release_protocol.py:1163-1172,1367-1370`） | 目标 argv 写全，且路径字符串与 `--config` 完全一致 |
| `T1_AUDIT_MAX_DECISIONS` 提高采样率 | 对 load_calibration 无效（§0.5） | 忽略 |
| ta-pilot 5 run 有 queue 真值 | 没有（§0.6） | 要 Q(t) 必须另跑 t1_tasks |

### 1.2 前置（每次发射前，只读）

```bash
cd /Users/lge/Desktop/topic/.wt-qual-20261031
git status --porcelain=v1 --untracked-files=all     # 必须为空（build 硬门）
git rev-parse HEAD                                   # 必须等于 <COMMIT40>
git ls-remote --heads origin | grep <COMMIT40>        # 命中 ⇒ remote_backup=verified
ssh vm 'hostname; nproc; cat /sys/fs/cgroup/cpu/cpu.cfs_quota_us; cat /sys/fs/cgroup/memory/memory.limit_in_bytes; df -h /data | tail -1'
ssh vm 'sha256sum /data/论文/leo-t1-wt/releases/<RID>/CODE/dependencies/t1-vm-linux-aarch64/runtime-identity.json'
#   期望 b3b548fdba7f364d5f48ed0bcc5c77ca667f6bd4ad2c463844eca8a18923e5d0
```
容器真实上限 **24 核 / 64 GiB**（host 报 256/628 GiB）；同一时刻 ≤2 个全网络 run；`t1_tasks` 每臂常驻内存 ≈10 GiB。

### 1.3 contract 骨架（`load-calibration-contract/v1`，改数值即可用）

```json
{"schema":"load-calibration-contract/v1",
 "base_profile":"CODE/leo_sim/profiles/ta_pilot_140_adv1.yaml",
 "deadline_s":4.0, "max_packets":50000, "max_duration_s":20.0,
 "diagnostics":{"timeline":false,"control_audit":false,"decisions":true,
                "truth_probe":true,"truth_probe_max":200000,"truth_probe_stride":1},
 "cells":[{"id":"<CELL_ID>","arms":["now"],
   "overrides":{"scenario":{"duration_s":20.0},
     "demand":{"packet_bits":1000000,"emission_start_s":4.0,"emission_end_s":14.0},
     "links":{"isl_rate_mbps":100.0,"isl_queue_bits":64000000,"rate_model":"constant"},
     "control_plane":{"advertise_interval_s":1.0,"vis_k":3},
     "execution":{"compute_delay_s":0.0,"compute_servers_per_satellite":0,"max_packets":40000}},
   "trace_program":{"schema":"scene-trace-program/v1","model":"s1_stationary","seed":7,
     "total_mbps":120.0,"packet_bits":1000000,"emission_start_s":4.0,"emission_end_s":14.0,
     "cells":{"tokyo":[35.68,139.69],"singapore":[1.35,103.82],"london":[51.51,-0.13],
              "newyork":[40.71,-74.01],"saopaulo":[-23.55,-46.63],"lagos":[6.52,3.38]},
     "od_weights":{"tokyo,singapore":0.7,"<其余 29 个有向对各 0.010344827586206896>":0.0},
     "pulse":{"start_s":8.0,"duration_s":0.5,"multiplier":1.6}}}]}
```
约束（`_validate_contract_document :254-303`、`_validate_override_shape :185-206`）：`od_weights` 必须和为 1（`scene_trace_program.py:102`）；`duration_s∈[12,300]`；`max_packets∈[1,100000]`；`deadline_s` **必须恰好 4.0**；`overrides` 逐字段需在白名单；`trace_program` 与 `demand.mode/csv_path` 互斥（由注入器填）。实测 high/low 两 cell 除 `arm` 外各臂 `config_sha256` 逐字段相同 [V]。

### 1.4 本机 preflight（不跑核、不占 VM）

```bash
python3 -B -m CODE.experiment_platform.load_calibration \
  --contract EXPERIMENTS/contracts/<CONTRACT>.json --cell <CELL_ID> \
  --out-dir /private/tmp/pre-<CELL_ID>-01 --preflight
# 期望 {"trace_compiled":false,"kernel_calls":0,"resolved_config_sha256":...}
```
`preflight` 也要求 out-dir 不存在（`_check_output_destination :454`）且 ≥5 GiB 空闲（`:78`），故 preflight 与正式 run 必须用不同目录。

### 1.5 publish（仅当 `CODE/` 有改动才需要）

```bash
CODE/scripts/remote/publish-release-remote.sh --commit <COMMIT40>
# 解析 stdout，不要猜 release id：
#   release_id=<COMMIT40>-<64hex>   ← 64hex 是 deployed file manifest 的 sha256，不是 git tree
#   source_git_commit=<COMMIT40>
#   remote_backup_status=verified|remote_backup_pending
#   remote_install={"status":"published"|"already_present",...}
#   remote_incoming={"status":"cleaned_after_verified_publish",...}
```
拒因（`release_protocol.py:351`；publish sh `:38-40`）：`--commit` 必须小写 40 hex；`HEAD` 必须等于该 commit；树必须干净；必须具名分支；origin 必须 `fuguther/leo-experiment-platform`。未推到 origin 只降级为 `remote_backup_pending`，**不阻断**。

### 1.6 run（`--mode diagnostic`；formal 在此不被接受）

```bash
CODE/scripts/remote/run-release-remote.sh \
  --release-id <RID> --run-id <RUN_ID> --mode diagnostic \
  --config EXPERIMENTS/contracts/<CONTRACT>.json --timeout-seconds 1800 \
  -- python3 -m CODE.experiment_platform.load_calibration \
      --contract EXPERIMENTS/contracts/<CONTRACT>.json \
      --cell <CELL_ID> --out-dir /data/论文/leo-t1-wt/runs/<RUN_ID>/<CELL_ID>
```
- `<RID>` 正则 `^[0-9a-f]{40}-[0-9a-f]{64}$`；`<RUN_ID>` 正则 `^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$`；run-id **永不复用**（run sh `:50-56`）。
- `--config` 与目标 `--contract` 必须是**同一 release-relative 字符串**：runner 等值替换为 `<run_dir>/inputs/config`（`release_protocol.py:1367-1370,1163-1172`）。已回传 argv 正是 `/data/…/runs/ta-pilot-arms-high-20261031-01/inputs/config` [V] —— 这就是"只固化不补参"的可核对证据。
- 所有输出必须**绝对路径**且落在 `runs/<RUN_ID>/` 下（子进程 cwd = 只读 release）[V 历史 `PermissionError`]；`--timeout-seconds` 默认 1800/上限 7200（本矩阵最慢 430 s，足够）；**多 cell 必须多次 run**（`--cell` 一次一个）。

### 1.7 pull + 核验 + 索引

```bash
CODE/scripts/remote/pull-release-results-remote.sh --run-id <RUN_ID> --release-id <RID> \
  --evidence-root /Users/lge/Desktop/topic/三端文件与同步治理/evidence/t1
# 期望 {"status":"verified","run_id":...,"receipt_sha256":"<64hex>"}；只读复核：
python3 -B CODE/scripts/remote/release_protocol.py verify-run \
  --run-dir "<evidence-root>/<RUN_ID>" --run-id <RUN_ID> --release-id <RID>
```
副作用：`ANALYSIS/DEPLOYMENT-INDEX.jsonl` 追加 `{run_id, release_id, receipt_sha256, pullback_status:"VERIFIED", execution_class, analysis_commit:null, evidence_uri:"evidence://t1/<run-id>"}` → 树变脏，**下次 publish 前必须先提交**。

### 1.8 一个 cell 的产物树（实测 `ta-pilot-arms-high-20261031-01` [V]）

```
inputs/config                      ← --config 快照（实测 sha = contract sha a9101cab…, 2964 B）
<CELL>/{contract.json, resolved-config.json, trace.jsonl.gz, trace-manifest.json, summary.json}
<CELL>/arms/<arm>/{config.json, decisions.jsonl.gz, result.json.gz, target_probe.jsonl?}
run-manifest.json  run-receipt.json  run.log  stdout.log  stderr.log
```
三臂 20 s cell：wall **513.6 s**（10:45:07→10:53:44 UTC）、本地 **260 MB**；`decisions.jsonl.gz` 20 MB/臂（24–25 k 行），`target_probe.jsonl` **69 MB/臂**（49,288 行）。`summary.json` 给每臂 `execution_wall_s / decisions.summary / natural_end / target_probe.summary` 与 `trace.{rows,jsonl_sha256,source_trace_sha256}`。

## 2. 决策行到底记录了什么

### 2.1 两条 sink 的字段差（最易踩错处）

| 字段 | `load_calibration` 投影后 | `t1_tasks` 全量 |
|---|---|---|
| `t,t_decision_start,pid,hop_index,decision_id,sat,kind,candidates,chosen,src,dst` | ✓ | ✓ |
| `observation_at_start.{time_alignment, legal_directions, four_direction_audit}` | ✓ | ✓ |
| `observation_at_start.candidate_resources` | ✗ **被投影掉** | ✓ |
| `observation_at_start.{neighbours, own_queue_bits, local_egress_in_service_s}` | ✗ | ✓ |
| `truth_at_commit` / `estimate_at_start` | ✓ | ✓ |
| 行上限/采样 | **无上限、无采样** | `T1_AUDIT_MAX_DECISIONS`=5000，`blake2b16(pid:hop_index)` 取最小 5000（`t1_tasks.py:852,869`） |

投影白名单 14 个顶层键 + `observation_at_start` 的 3 个子键（`load_calibration.py:551-566`）；实测顶层键 `candidates, chosen, decision_id, estimate_at_start, hop_index, kind, observation_at_start, pid, sat, t, t_decision_start, truth_at_commit` [V]。

### 2.2 `time_alignment` 审计块（`kernel.py:5490`，schema `leo-sim-time-alignment-at-start/v1`）

实测键 [V]：`applied_order, arm, cache_hit, common_horizon_s, common_horizon_status, common_rule, compute_state, dropped_zero_rate_directions, enabled, eta_targets, eta_terms, eta_unknown_terms, execution_mode, fallback_directions, history_samples, inference_policy, missing_directions, observation_age, predictor, query, query_delay_s, query_targets, ranking, rate_lookup_errors, schema, scores, snapshot_at, source, state_source, used_state`。要点：

- `scores.<dir>` = `{total_s, terms, missing, fallback, predicted_bits, service_rate_bps}`；`terms` **11 项**：`tx_s, prop_s, remaining_prop_s, local_egress_wait_s, peer_process_s, compute_wait_s, compute_service_s, query_wait_s, query_service_s, resource_packet_tx_s, resource_work_s` [V]。`scores` **只含合法候选**，而 `eta_targets/query_targets/eta_terms` 覆盖全部 4 方向 [V]。
- `used_state.<dir>` = `{source, resource_id, generation, bits, measured_at, received_at, used_at}`（`:5068,5124`；`ideal_current` 分支另带 `queued_bits`）。**没有 age 字段**：τ 需自算 `snapshot_at − measured_at`。
- `eta_targets.<dir>`=该候选评估真值时刻 T_ρ；`query_targets.<dir>`=实际查询时刻（`candidate` arm + `candidate_target_rule=arrival` 时两者相等，可核）。
- `observation_age.per_direction.<dir>` = `{resource, samples_available, used_measured_at, used_actual_age_s, used_queue_bits, used_rate_bps}`，**仅在 `time_alignment.min_observation_age_s` 显式设置时生成**（`:5433` 的 `if self._min_observation_age_s is not None`）；ta-pilot profile 未设 ⇒ 实测 `{}` [V]。**逐候选 τ 用 `snapshot_at − used_state[dir].measured_at`，或把 `min_observation_age_s` 写进 cell overrides（在白名单内，`:117-133`）。**

### 2.3 候选资源映射（只有 t1_tasks 侧留得下）

`observation_at_start.candidate_resources.<dir>`（`kernel.py:4422,5664`）：`{status, peer, egress_direction, kind, generation, measurement{generated_at,received_at,age_s,hops}, isl_rate_bps, candidate_isl_rate_bps, propagation_s, remaining_prop_s, downlink_*}`；不可解析时记 `status:"missing"` + `reason`（`direction_has_no_peer` / `candidate_peer_already_visited`），**绝不发明 0**。`four_direction_audit.candidate_details.<DIR>`（`:6792`）给物理/策略/环/队列四道门逐方向判定 + `resource_id="isl:<sat>:<peer>"` + `generation` + `queue_bits_before` + `queue_capacity_bits` + `filter_reasons` [V]。

## 3. truth_probe（target probe）与 queue ledger

### 3.1 target probe 记什么

调用链：`_record_decision` 提交成功后 `_enqueue_target_probe`（`kernel.py:5971,6075,6111`）→ `TargetProbe.enqueue`（`target_probe.py:142`）→ 采样进程在 `eval_target_s` 瞬时读活资源（`:_sample_one :215`）。行 schema `leo-sim-target-probe-decision/v1`，字段 = 决策侧 `decision_id, hop_index, pid, sat, kind, chosen, selected, direction, candidate_directions, t_commit_s, t_decision_start_s, arm, predictor, state_source, snapshot_at_s, resource_id, resource_peer, identity_source, generation, state_bits, state_queued_bits, state_source_used, state_{measured,received,used}_at_s, algo_query_target_s, eval_target_s, predicted_bits, service_rate_bps, predicted_work_s, score_total_s, score_missing, fallback` + 采样侧 `sampled_at_s, truth{resource_id,generation,rate_bps,queued_bits,work_ahead_bits,in_service_remaining_bits,definition,source}, missing_reason`。
- 真值定义**唯一**：`work_ahead_bits = queued_bits(data+ctrl) + in_service_remaining_bits(head × 剩余比例)`（`target_probe.py:234-247`），取自 `kernel._isl_resource_state_advertisement`，与在线口径同源。
- 失败**只记原因不给值**：`no_eval_target / no_prediction / no_resource_identity / beyond_horizon / resource_missing / generation_changed / link_retired / prediction_generation_unknown / unparseable_resource_id / overflow`（`:215-307`）。
- 覆盖控制：`stride` 先 1-in-N 采样再按 horizon 展开（`:149-166`，顺序错会只留一个 offset，代码有注释）；`truth_probe_max` 是**记录数硬上限**（按候选方向计，不是决策数），超出计 `dropped_overflow`。

### 3.2 identity_source 过滤陷阱（必修）

三态（`kernel.py:6124-6144`）：`used_state`（预测器自己用的 `resource_id+generation`，**唯一可作标签**）· `live_audit_not_used_by_predictor[ (resource_id+generation)]`（预测无身份，退回 `candidate_details.resource_id`）· `None`。
**陷阱**：`candidate_details.resource_id` 是 `isl:<sat>:<peer>`（**本地首跳**），而 `target_probe._find_link`（`:256-307`）按 `isl:<host>:<peer>` 去 `kernel.isls[host]` 找 `peer` ⇒ fallback 行读的是 **host 自己那条链路**，与"对端出口"是两个资源且不报错。实测 [V]：49,288 行中 `used_state` 48,022、fallback 1,266（**其中 1,143 行 `host==sat` 即错资源**）、`no_prediction` 123。⇒ 分析必须 `identity_source=="used_state"` 且 `truth.generation==row.generation`（`ta_pilot_analyze.valid_truth_rows :372-399` 已实现）。

### 3.3 queue ledger 记什么、怎么离线重建 Q(t)

三份文件（`queue_ledger.py:53-60,79-174`），前缀 `<out stem>-<arm>-`，写在 `--out` 同目录（`t1_tasks.py:2793`）：
- `-queue-levels.jsonl.gz`：header `{kind:"header",schema:"leo-sim-queue-ledger/v1",columns:[t,queue,bits,event]}`；每行 `{kind:"level",t,queue,bits,event:"add"|"remove"|"close"}`，**post-transition 值**；trailer `{kind:"trailer",rows,queues,closed_at}`。
- `-packet-events.jsonl.gz` / `-service-windows.jsonl.gz`：kernel 的 `packet_events` / `link_service_windows` **逐字导出**。
- 唯一钩子在 `QueueArea.add/remove/close → _emit`（`kernel.py:222-252`），**覆盖每个模拟队列**，不改调用方；qid = `uplink:cell:<cell>` · `downlink:sat:<sat>` · `isl_data:isl:<sat>:<peer>:gen<gen>` · `isl_ctrl:isl:<sat>:<peer>:gen<gen>` · `holding:sat:<s>`（`:464,707,926,927,1699`）。

```python
rows = [json.loads(l) for l in gzip.open("…-queue-levels.jsonl.gz","rt") if '"level"' in l]
by_q = defaultdict(list)                      # 追加顺序即时间序，首行为 header、末行为 trailer
for r in rows: by_q[r["queue"]].append((r["t"], r["bits"]))
def q_at(q, t): return bits of last row with t_i <= t   # t 早于首行 ⇒ 0
area = sum(bits_i * (t_{i+1} - t_i))          # 末段用 trailer.closed_at
# 校验：Σ over isl_data:* 应等于 result.queue_area_bits_s["isl_data"]（kernel.py:7636-7642，5 个聚合键）
```
注意：ledger **只在 t1_tasks 路有**；若走 load_calibration（更省），**Q(t) 不可重建**，只能用 `result.json.gz` 的 `packet_events + link_service_windows` 做逐包事件级重建（能重建每包排队/服务区间，不能直接给任意 t 的水位）。

## 4. `ta_pilot_analyze.py` 能算什么 / 缺什么

已实现（`ANALYSIS/TIME-ALIGNMENT-20261030/ta_pilot_analyze.py`，796 行，6 子命令，**只读 load_calibration cell 产物**）：

| 子命令 | 已算 | 实现 |
|---|---|---|
| `rho` | 窗口内逐有向 ISL 实测利用率 | `:118-202` |
| `network` | E2E/p50/p95/p99、吞吐、delivery、fate、queue-wait/tx/prop/holding/access 分解 | `:213-264` |
| `decisions` | 状态年龄（`snapshot_at−measured_at`）、候选 ETA dispersion、**margin m**（完整候选前二 `total_s` 差）、fallback 率、`query==eta` 率、term 出现率 | `:277-369` |
| `probe` | 预测→真值**秒**误差：覆盖、signed bias、MAE、median、p90、p99、≤10 ms 占比（ISL + `identity_source=used_state` + generation 一致） | `:372-464` |
| `oracle` | 真值替换 `resource_work_s` 后离线重排：`ranking_reversed`、`action_reversed`、**signed differential D**（top1.error−top2.error）、crossed 率、direction bias `b`、run 长度、burst 相位分解 | `:466-614` |
| `compare` | 跨臂按 `(pid,hop_index)` 配对：动作不一致率、转移计数、方向偏流、run 长度、逐 ISL 净流量差 | `:659-726` |

**缺口（算 L/Γ/P/R 还差什么）：**

| 量 | 能否算 | 缺什么 |
|---|---|---|
| AoI / τ | **能**（`snapshot_at − used_state[d].measured_at`，`decisions` 已给分位） | 无；若要 `received_at−generated_at` 网络传播龄，同两字段可算 |
| margin m | **能**（`score_margin_s` p10/p50/p90） | 无；但它是**预测总分差**，不是真值差 |
| D_ab（候选差分误差，秒） | **能**（`oracle.differential_s`，仅 top-2 对） | 只有 top1/top2 一对，全候选对需扩函数 |
| **L = \|dΔJ_ab/dt\|** | **不能** | 缺"时间序列"函数：按 `(sat, dst, 候选资源对)` 把不同决策的 ΔJ 聚成 `[(t,ΔJ)]` 再有限差分。现脚本每决策只算一个点、从不跨时间求导；数据**够**（`scores.*.terms.total_s` + `t`），只是函数不存在。另需定 ΔJ 用预测值还是 probe 真值（真值只在 `eval_target` 有单点） |
| **Γ = Lτ/m** | **不能** | 依赖 L；τ 口径需冻结（建议 `snapshot_at − used_state[d].measured_at`）；m 需固定"取哪两个候选" |
| **P(a_stale≠a_fresh)** | **部分** | ①`compare.disagreement_rate` 是**序数配对**，首次分叉后状态不同；②`oracle.action_reversal_rate` 是**同决策**但只换 `resource_work_s`（其余 10 项仍是旧观测）；③真正同状态翻转需 `counterfactual.replay_with_forced_action`（`counterfactual.py:75`）逐决策复放，或 `minimal_branch_compare.py --decision-id N --forced-action X`（2 run/决策，不可规模化） |
| **regret R** | **不能** | **决策级** `R=(D_ab−m)_+` 只需在 `oracle_report` 里加一行正部（数据已在 `rows`）；**业务级** regret 需反事实分支（`kernel.run_simulation(forced_actions=…)`，`OUTCOME_KEYS` 是聚合块非逐包），历史上本线从未在 VM 跑过 |
| W | **不能且未定义** | `ta_pilot_analyze.py`、`TIME-STATE-SEMANTICS.md`、`00-CURRENT-VS-TARGET.md` 均无 W 定义，只有 `问题理论推导.md` 的 D_ab/m/R 三角。**需 Lead 先冻结公式**，否则做出不可复算的量 |

## 5. 跨 run 配对：依据与边界

### 5.1 同 trace / 同 pid 依据（三条，均可核对）

1. **trace 由 `(trace_program, seed)` 唯一决定**：`generate()` 纯函数，每 OD 用 `random.Random(crc32(od) ^ (seed*0x9E3779B1))`（`scene_trace_program.py:261,312`）。代码注释记录了历史缺陷：曾用 `tuple.__hash__`（受 PYTHONHASHSEED 影响）致同 program 每次 trace 不同，又因**按 digest 缓存**而无人发现 ⇒ 不要再用任何进程相关 hash 生成 trace。
2. **pid 逐字保留**：`PACKET_ID_CONTRACT = "csv: source packet_id preserved verbatim"`（`trace.py:34`）；`generate()` 先按 `(emit_time_s,packet_id)` 排序再重编号 1..N（`:358-362`）⇒ 同 CSV ⇒ 同 pid 序列。
3. **routing / time_alignment / control_plane 显式排除出 trace identity**：`trace_identity_payload` docstring 与 `_trace_identity_payload`（`config.py:1477-1492` / committed `:1437-1465`）。`test_pairing_trace_identity.py` 的实测注释另给对照（routing.policy hop/oracle/delay 同 identity，`demand.offered_mbps` 不同）——**但该测试测的是 `CODE.leo_sim.matrix` 编译门，不是 load_calibration 路**，引用勿张冠李戴。

### 5.2 四条边界，必须写进分析口径

- **同 program、不同 cell ⇒ `trace_identity_sha256` 不同**：注入的 `demand.csv_path = $T1_TRACE_PROGRAM_ROOT/<cell_id>-<digest16>.csv`（`:365-382,423-437`）含 cell id，而 `demand` 整组进 identity payload。判"同 trace"用 `trace-manifest.json.source_trace_sha256`（编译后 `trace.csv` 的 sha，`trace.py:1504,1599`）或 `input_sha256`（program CSV 字节 sha，`:1193`），**不要用 `trace_identity_sha256`**。
- **广告周期不影响 trace**：`control_plane` 不在 identity payload，`advertise_interval_s` 也不参与 trace 生成 ⇒ 改广告周期仍同 trace/同 pid（§6 周期轴的必要条件）。
- **`(pid,hop_index)` ≠ 同一状态**：两臂在**首次动作分叉前**逐决策同状态；之后同 key 对应不同 (sat, 队列)。故 `compare` 的 disagreement 只能读作"两策略动作序列差异"，`decision_consequence.py` 的 `alignable/unalignable` 口径同理。
- **`scenario.seed` ≠ trace seed**：csv 模式 trace 字节由 CSV 决定；`scenario.seed` 只进 identity payload 与运行期 RNG（本 profile GE off、无其它随机源）⇒ 改它只改 `trace_identity_sha256`，**不产生新流量**。seed 重复必须改 `trace_program.seed`。

## 6. 最小 pilot 矩阵与成本

### 6.1 成本标度律（5 个实测 run 拟合，全部 `DIAGNOSTIC_COMPLETE` [V]）

| run | 负载 | 包 | 臂 | 决策/臂 | 单臂 wall | 总 wall | 产物 |
|---|---|---|---|---|---|---|---|
| `ta-pilot-calib-20261031-01` | 40 Mbps | 427 | 1 | 7,992 | 59.4 s | 59.5 s | 7 MB |
| `ta-pilot-calib-20261031-02` | 120 Mbps | 1,224 | 1 | 26,357 | 177.8 s | 177.9 s | 90 MB (probe) |
| `ta-pilot-calib-20261031-03` | 200 Mbps | 2,047 | 1 | 57,083 | 429.3 s | 429.5 s | 48 MB |
| `ta-pilot-arms-low-20261031-01` | 40 Mbps | 427 | 2 | 7,990 | 61.7/62.4 s | 124.1 s | 56 MB (probe) |
| `ta-pilot-arms-high-20261031-01` | 120 Mbps | 1,255 | 3 | 24.3–25.3 k | 166–174 s | 513.6 s | 260 MB (probe) |

⇒ **单臂 wall ≈ 7 ms × 决策数**（三个单臂点 7.4/6.7/7.5 ms，极稳）；**决策数 ≈ 19–28 / 包**（随利用率上升）；**产物 ≈ 800 B×决策 + 2.4 MB**，开 `truth_probe` 再加 **≈1.4 KB×2×决策**；pull 另计每 run 1–2 min。

### 6.2 先说硬阻塞（不解决任一条，矩阵跑不起来）

1. **白名单扩展已存在但未提交**：HEAD `a90c1f9` 的 `OVERRIDE_FIELDS` 不含三个策略键，写了会直接 `unsupported override fields`；当前工作树的 `M CODE/experiment_platform/load_calibration.py`（+17 行，`git diff` 可核）已加入 `state_policy / hysteresis_eta_s / ewma_alpha`，并顺带允许 `control_plane.packet_bits`（"强同步"基线 = 快广告 + 小控制包，注释已写明）。⇒ 这一步**不需要再写代码，只需要提交+推送+publish**；发布后先用 §1.4 preflight 验三个键真能被 cell 覆盖。
2. `ALLOWED_ARMS` 固定 4 个 TA arm（`:65`）⇒ 不可能把 4 策略做成同 cell 的 4 个 arm，只能 **1 策略 = 1 cell = 1 run**；"除策略外逐字段相同"靠事后掩码比对（ta-pilot 已有此法：掩掉 `time_alignment.arm` 后三臂 JSON 相同 [V]）。
3. `DEFAULTS` 加这三个键后**所有 profile 的 `config_sha256` 都会变**（policy-builder 注释已承认）⇒ 新 release 与归档 `fd1cb14` 那 5 个 run **不可按 sha 互比**；跨代比较必须"掩码后逐字段"。
4. 替代路线（不改 `ALLOWED_ARMS`）：写 4 个 profile yaml（只差 `state_policy`）+ 直接 `t1_tasks --task network_alignment --config <profile>`（t1_tasks **无 profile 白名单**，`_is_controlled_population_region_profile` 只拦 `demand.mode=population_gravity`）。代价是失去 contract 的多臂与 trace_program 物化，但能拿 `--queue-ledger` + `--target-probe`（全字段、带 `candidate_resources`）——**唯一能同时拿 Q(t) 真值与真值探针的路**。

### 6.3 推荐的最小矩阵（三阶段，先探后铺）

统一：`duration_s=20`、`emission 4–14 s`、`packet 1 Mbit`、`ISL 100 Mbps / 64 Mbit`、`vis_k=3`、`compute=0`、`GE off`、`trace_program.seed=7`、`D=4.0`；每格一个新 run-id。

| 阶段 | 格 | 数量 | 目的 | 估时 |
|---|---|---|---|---|
| S1 门（先跑） | 4 策略 × {120 Mbps, adv=1.0 s} × seed7 | 4 run（单臂） | 同 trace 下动作是否真被策略改变；`decisions`+`probe` 够不够算 m/D | 4×~180 s ≈ 12 min |
| S2 对照轴 | ①queue_greedy × 40 Mbps ②queue_greedy × adv∈{0.25, 2.0} s | 3 run | 低负载应≈0 效应；周期轴给 τ 因果支点 | 60 + 2×180 ≈ 8 min |
| S3 复现 | S1 四格换 `trace_program.seed=11` | 4 run | 同策略不同 seed，防单轨迹 | ≈12 min |
| 合计 | | **11 run** | | **≈35 min 计算** + ~10 min publish/pull；证据 ~1.5 GB |

若还要 Γ 的 τ 轴：把 S2 周期轴扩成 `{0.25,0.5,1.0,2.0}`（+2 run ≈6 min）。**不建议**第一轮就上 4×3×2×2=48 run（≈5.5 h、约 12 GB，且 `(pid,hop_index)` 不能给同状态翻转，投入产出不成立）。若必须拿 Q(t)：S1 四格改走 §6.2-4 的 t1_tasks 路，每臂 +15 MB gz / +~90 s [I]。

每格的命令 = §1.4/1.5/1.6/1.7 四段拼接，只替换 `<CONTRACT>`=`EXPERIMENTS/contracts/simqual-pol-<POLICY>-20261031.json`、`<CELL_ID>`=`pol-<POLICY>-120`、`<RUN_ID>`=`simqual-<POLICY>-120-20261031-01`、`<RID>`=publish 第 1 行、`<COMMIT40>`=本次 commit；pull 后立即提交索引行（否则下次 publish 被脏树拒）。t1_tasks 变体的目标段（§6.2-4）：
```bash
  -- python3 -m CODE.experiment_platform.t1_tasks \
      --task network_alignment --config CODE/leo_sim/profiles/ta_pilot_<POLICY>.yaml \
      --arms now --deadline-s 4.0 --offered-mbps 120 \
      --target-probe --target-probe-max 200000 --queue-ledger --autopsy \
      --out /data/论文/leo-t1-wt/runs/<RUN_ID>/<STEM>.json
```
（输出 `<STEM>.json` + `<STEM>-summary.json`；`--out` 必须不存在；`--autopsy` 在 summary 构建失败时**主动失败**，可当额外台架自检。注意 `-summary.json` 里 `routing_audit_log` 只留计数与采样规则，决策明细只在主文件。）

## 7. 三个关键数据缺口（按对结论的杀伤力排序）

1. **同状态翻转率 / 业务级 regret 没有可规模化的证据源。** `(pid,hop_index)` 只给序数配对；同状态翻转要么逐决策反事实复放（2 run/决策），要么接受"只换 `resource_work_s` 一项"的 oracle 近似。⇒ P(a_stale≠a_fresh) 与 R 这两个主线量当前只能给近似或抽样，**必须先冻结口径再跑矩阵**，否则跑完也算不出预注册判据。
2. **ta-pilot 路没有 Q(t) 真值、也没有逐候选 `candidate_resources`。** queue ledger 仅 t1_tasks 可开，load_calibration 的 sink 投影又丢掉了候选资源映射。⇒ 要验证"定向 ISL 排队由决策偏流造成"（而非只看 `queue_area_bits_s` 聚合），必须走 t1_tasks，或先扩 load_calibration 的投影与 `queue_ledger` 透传。
3. **策略轴的表达依赖一个未提交改动，且会迁移全部 config identity。** 白名单扩展已在工作树（§6.2-1），但 `DEFAULTS` 新增三个键会让**每个 profile 的 `config_sha256` 都变**；同时 `ALLOWED_ARMS` 没有策略名 ⇒ 4 策略只能 1 策略 1 run。⇒ 开跑前必须：提交+推送+publish（否则矩阵发不出去）、冻结"新证据 vs 归档 `fd1cb14`"的跨代比较口径（掩码后逐字段，不按 sha）、并在首个 cell 上核对 `trace-manifest.source_trace_sha256` 与归档一致。

**附带风险（影响复算，不单列缺口）**：`materialize_trace_program` 的 `sha256` 在缓存命中时返回 program digest、未命中时返回 CSV 字节 sha（`:365-382`），该字段不进 manifest 故当前无害，但下游若引用会得到两个不同数；`T1_TRACE_PROGRAM_ROOT` 默认 `/tmp`（VM 重启即清）且进 `artifact_identity.ENV_NAMES`。
