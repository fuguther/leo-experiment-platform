# 仪器化充分性审计：三个时刻能否从**现存**日志重建？

**任务**：`task-1` / `audit-instr` · **作者**：ledger-auditor · **日期**：2026-10-30
**代码身份**：`/Users/lge/Desktop/topic/.wt-instr-20261030`，分支 `instr/ledger-export-20261030`。审计基于 `7e7fd5a` 读取；写报告期间该 worktree 前进到 **`8838977`**（"fix(verify): read queue_area from outcome, reconcile uplink/downlink/ISL queue keys"），该提交**只改** `CODE/scripts/verify_queue_ledger.py`（+41/−5）与 `ANALYSIS/DEPLOYMENT-INDEX.jsonl`，**未触及** `kernel.py / t1_tasks.py / time_alignment.py / decision_ledger.py / queue_ledger.py / target_probe.py / replay_codec.py / config.py / __main__.py`，故除 `verify_queue_ledger.py` 的行号已按新 HEAD 重算外，其余引用在两个 commit 上同值。HEAD `8838977` 下 `git status --porcelain` 仅见本报告所在的未跟踪目录。
**方法**：逐行读码 + 交叉引用测试/校验脚本。**未运行任何仿真**（本机 `out/` 为空，无任何 run 产物）；所有结论均可由下列 `file:line` 复核。不采信任何既有文档的转述，凡引用一律回到源码。

---

## 0. 中文摘要（结论先行）

- **判据**：对每一个真实路由决策，能否用现有日志重建候选资源 ρ 在三时刻的状态（t_g 源测量、t_d 决策、T_ρ 物理使用），且资源身份稳定？
- **结论：充分（sufficient），不需要新增任何日志。** 硬要求只有一个：`--capture-replay`（它才把逐决策行与时间线落盘）。**完整、可交叉验证的配方是同一个 run 同时开 `--capture-replay --target-probe --queue-ledger`**（三者都是 `t1_tasks.py --task network_alignment` 的开关）。分工：`--capture-replay` 给三时刻与身份；`--target-probe` 给 run 内瞬时真值采样 + 12 种失败原因；`--queue-ledger` 给与决策流完全独立的精确队列电平（可独立重算队列面积并做守恒校验）。单靠 `--queue-ledger` **不够**：它只描述队列的物理世界（level 行甚至没有 pid），而**决策侧**（候选集、t_d、候选身份信念、ETA/打分）只存在于 `decision_sink`，那份流**只有 `--capture-replay` 会落盘**。
- **最关键的缺口/歧义**：`target_probe` 行里的 `resource_id` 有**两个互不兼容的命名空间**，字段名和字符串格式完全相同：
  - `used_state.resource_id = "isl:<候选的peer P>:<P的下一跳 R>"`（**被打分的、两跳以外的目标出口**，kernel.py:5126-5129）；
  - 当无广告样本时回退到 `four_direction_audit.candidate_details[d].resource_id = "isl:<本机 sat>:<候选 peer P>"`（**本机第一跳**，kernel.py:6846-6847），回退发生在 kernel.py:6132-6142。
  → 两者只能用 `identity_source` 区分（`"used_state"` vs `"live_audit_not_used_by_predictor"`）。这是**已存在但极易误读**的歧义，不是缺失。
- **第二个必须写进分析协议的事实**：t_d 时刻被打分的资源是**该节点对 peer 出口的信念**，T_ρ 时刻实际使用的资源是 **peer 真实选出的出口**，二者**可能不同**。这一点已有专门字段与打分（`estimate_at_start.peer_egress_direction` vs 后继决策的 `chosen`，`egress_match`），所以"身份稳定"不是构造性保证而是一个**可检验事实**，必须在结论里显式报告比例。
- **唯一"稳健性"缺口（不是缺失）**：tie-break 的输入顺序 `visible_order` 没有**显式**字段；它只能从 `ta_audit.eta_targets` / `eta_terms` 的 **JSON 键序**反推（因为 `network_alignment` 文档由 `replay_codec.dump_graph` 以**不排序**的 encoder 写出，见 §6.6）。能取到，但依赖序列化器行为，属"脆弱可推"而非"缺失"。
- **另一个"有代码但没产出"的事实**：`decision_ledger.build_ledger / build_attempts / score_downstream_predictions / score_start_estimates` 这套折叠器**只有测试在调用**，run 路径不产出它的结果；需要离线对 `replay.decision_rows + replay.timeline_rows` 调用。

---

## 1. 决策对象与三时刻的**精确定义**（全部有码位）

### 1.1 被打分的资源是一个**有向出口**，不是一个卫星

```python
# CODE/leo_sim/time_alignment.py:206-238
@dataclass(frozen=True)
class ResourceKey:
    """A named directed egress, not a whole satellite."""
    satellite: int
    direction: str
    kind: str
    generation: int | None = None
```

在线构造点（**这是唯一一处把候选变成 ResourceKey 的地方**）：

```python
# CODE/leo_sim/kernel.py:5015-5025  (_build_ta_snapshot)
if cr.get("status") == "delivered_downlink":
    key = _ta.ResourceKey(int(cr["peer"]), str(target_dst), _ta.KIND_DOWNLINK)   # 5016-5017
else:
    key = _ta.ResourceKey(int(cr["peer"]), str(cr["egress_direction"]), "isl",
                          generation=int(cr["egress_generation"]))               # 5023-5025
```

语义读法：
- `satellite` = **候选的 peer P**（本机 sat 的下一跳）；`direction` = **P 自己的罗盘方向 e**；`generation` = **P 那条出口的 FIFO 代号**。
- 即：决策节点 n 打分的是 **n 的下一跳 P 的、P 自己的出口 (P,e)**——**两跳以外**，不是 n 的本地出口。
- P 的出口 e 由 n **在 P 的广告上重跑同一个路由策略**得出（kernel.py:5768-5783 `candidate_resource_map` → `routing.choose_next_hop(..., peer, target_dst, ...)`），因此 `egress_direction` 是 **n 的信念**，不是 P 的真实决定。→ 见 §6.1。

身份是否"可解析"本身是一个记录字段：

```python
# CODE/leo_sim/kernel.py:5799-5815
if (not isinstance(egress_record, dict)
        or egress_record.get("peer") != egress_peer
        or ... generation ... ):
    out[str(direction)] = {"status": "missing",
                           "reason": "target_egress_identity_unavailable", ...}
```

即：**没有 generation 就没有身份**，该候选被记为显式 missing，而不是被赋予一个数字。

### 1.2 三时刻的来源

| 时刻 | 含义 | 产生字段 |
|---|---|---|
| **t_g** | 源测量时刻（该广告在**源卫星上被测量**的时刻） | 广告 `generated_at`；进快照后为 `StateSample.measured_at` |
| **t_d** | 决策时刻 | 决策行 `t`（commit）/ `t_decision_start`；快照 `snapshot_at` |
| **T_ρ** | 包**物理使用**该出口的时刻 | `packet_events[kind=queue_enter, link_id="isl:P:R"].at`（入队）/ `service_start`（开始串行化） |

关键定义（time_alignment.py:241-259）：

```python
@dataclass(frozen=True)
class StateSample:
    """One advertisement measured at its source and received locally.
    measured_at is a SOURCE timestamp. ...
    """
    resource: ResourceKey
    measured_at: float
    received_at: float
    queue_bits: float
    rate_bps: float | None
    arrival_rate_bps: float | None = None
```

`measured_at/received_at` 顺序被强制校验（`received_at < measured_at` 直接抛错，time_alignment.py:274-276）——所以 t_g ≤ t_到达 ≤ t_d 是**结构性**成立的。

---

## 2. (a) 控制面到底广告了什么？

### 2.1 生成点

每个卫星按 `advertise_interval_s` 周期调用 `_advertise(sat)`：

```python
# CODE/leo_sim/kernel.py:3601-3624  (_control_advertiser)
interval = float(self.cfg_cp["advertise_interval_s"])
...
yield self.env.timeout(max(0.0, next_periodic - self.env.now))
self._advertise(sat)
next_periodic += interval
```

```python
# CODE/leo_sim/kernel.py:3648-3655
def _advertise(self, sat: int):
    self.ctrl_seq += 1
    isl_bits = {
        d: self._isl_resource_state_advertisement(link, self.env.now)
        for d, link in self.isls[sat].items()}
```

**语义**：卫星 `sat` 广告的是 **`self.isls[sat][d]`——它自己那台发射机朝 d 方向的那条有向链路**。所以接收方 n 缓存到的、origin=s 的 `isl_queue_bits[d]` 描述的是 **link s→topo[s][d]**，即 **s 的出口**，**不是**"peer 的聚合队列"，**不是**"目的地相关"，**不是**"本地出口"。

### 2.2 每条记录的确切字段与定义

```python
# CODE/leo_sim/kernel.py:3784-3838  (_isl_resource_state_advertisement)
queued_bits = int(link.data_bits + link.ctrl_bits)                      # 3798
...
current = link.current                                                  # 3806
if current is None:
    active_bits = 0.0
elif link._svc_phase == "transmitting":
    service_rate = getattr(link, "_service_rate_bps", None)
    ...
    elapsed = max(0.0, float(now) - float(link._tx_started_at))
    active_bits = max(0.0, float(current.bits) - elapsed * float(service_rate))   # 3818-3820
else:
    # 未开始物理串行化（等链路窗口）：整个包都是"前方工作"
    active_bits = float(current.bits)                                   # 3825
work_ahead = (None if active_bits is None else
              float(queued_bits) + float(active_bits))                  # 3826-3827
return {
    "peer": int(link.peer),                    # 3829  身份绑定
    "value": queued_bits,                      # 3830  "queued"
    "generation": int(link.gen),               # 3831  物理代次
    "rate_bps": rate,                          # 3832  请求时刻速率估计
    "work_ahead_bits_proxy": work_ahead,       # 3833  "work ahead"
    "arrival_rate_bps": link.arrival_rate_bps(float(now)),  # 3837
}
```

**"queued" 与 "work ahead" 的确切区别**（这是 (a) 的核心答案）：

| 字段 | 定义 | 是否含在服务的当前包 | 码位 |
|---|---|---|---|
| `value`（= `queued_bits`） | `data_bits + ctrl_bits` = FIFO 里**等待**的数据+控制比特 | **不含** | 3798, 3830 |
| `work_ahead_bits_proxy` | `value + active_bits`；`active_bits` = 在服务包**按该次传输开始时的速率**折算的剩余比特 | **含**（残值） | 3806-3827, 3833 |
| `generation` | `link.gen`，同一 (sat,direction) 单调递增，rematch 换新 | — | 3831 |

证明"当前包已不在 `data_bits/ctrl_bits` 里"：出队瞬间即扣减。

```python
# CODE/leo_sim/kernel.py:1168-1177
pkt = self.ctrl_q.popleft() if is_ctrl else self.data_q.popleft()
self.current = pkt
if is_ctrl:
    self.ctrl_bits -= pkt.bits
    self.ctrl_area.remove(pkt.bits, k.env.now)
else:
    self.data_bits -= pkt.bits
    self.data_area.remove(pkt.bits, k.env.now)
```

**打分用的到底是哪个？** 是 `work_ahead_bits_proxy`，不是 `value`：

```python
# CODE/leo_sim/kernel.py:5092-5104   (_build_ta_snapshot, ISL 分支)
resource_direction = str(cr["egress_direction"])
generation = (rec.get("advertised_isl_generation") or {}).get(resource_direction)
if generation != key.generation:          # ← 代次不符即丢弃，绝不跨代复用
    continue
advertised_bits = (rec.get("advertised_isl_work_ahead_bits_proxy")
                   or {}).get(resource_direction)
```

随后 `StateSample(key, generated_at, received_at, advertised_bits, rate, arrival)`（kernel.py:5114-5119），`predict_resource` 把它当 `queue_bits` 用。`value`（纯 queued）**只进审计、不进预测**（kernel.py:4459-4463 vs 4479-4483）。

### 2.3 控制面下行/处理池（同一份快照里的另外两类资源）

```python
# CODE/leo_sim/kernel.py:3717-3725
downlink_resources[str(cell)] = {
    "queue_bits": float(work_bits) if queue_known else None,
    "rate_bps": float(rate) if rate > 0 else None,
    "propagation_s": float(propagation),
    "available": available,
    "queue_scope": "satellite_shared_drr",                        # ← 关键
    "queue_semantics": "all_waiting_bits_plus_active_residual_work_proxy",
}
```

`downlink` 的 `queue_bits` 是**整星共享 DRR 队列**（一个 `QueueArea(qid=f"downlink:sat:{sat}")`，kernel.py:707），却按 cell 复制广播；而 `ResourceKey` 的 `direction` 位放的是 **cell 名**（kernel.py:5016-5017）。→ 见 §6.3，这是**唯一一处身份与物理队列不一一对应**的地方。

---

## 3. (b) 逐项字段可用性矩阵

> 记号：**✅完整**＝该 run 产物里逐决策完整保留；**◐采样**＝逐决策保留但被确定性上限截断（`T1_AUDIT_MAX_DECISIONS` 默认 5000，t1_tasks.py:939-940，可用环境变量改）；**—**＝无。
> "默认"＝`t1_tasks.py --task network_alignment` 不加任何可选 flag。默认 run 里决策行**在内存中存在**（t1_tasks.py:1497-1504 恒传入 `decision_sink`/`timeline_sink`），但**只落盘聚合块 + `routing_audit_log`**（t1_tasks.py:1635-1652）。

| # | 需要的量 | 产生字段（可读路径） | 默认 | `--capture-replay` | `--queue-ledger` | `--target-probe` |
|---|---|---|---|---|---|---|
| 1 | **候选资源 id（目标出口）** | `observation_at_start.candidate_resources[d]`：`status/peer/egress_direction/egress_peer/egress_generation/kind`（kernel.py:5847-5868） | ◐ | ✅ | — | ✅（`resource_id`/`generation`，但有命名空间歧义，§6.2） |
| 2 | 被打分资源→快照映射 | `observation_at_start.time_alignment.used_state[d].resource_id`（kernel.py:5124-5135） | ◐ | ✅ | — | ✅（`state_*` 家族） |
| 3 | **t_g：所用样本的源测量时刻** | `used_state[d].measured_at`（= 广告 `generated_at`，kernel.py:5115, 5132）；`observation_at_start.neighbours[origin].generated_at`（4487）；`estimate_at_start.neighbour_measurement.generated_at`（5946） | ◐ | ✅ | — | ✅（`state_measured_at_s`，6170） |
| 4 | t_到达：样本本地接收时刻 | `used_state[d].received_at`（5133）；`neighbours[origin].received_at`（4488）；`age_s`（4489） | ◐ | ✅ | — | ✅（`state_received_at_s`，6171） |
| 5 | **t_d：决策时刻** | 行 `t`（commit，kernel.py:6071）、`t_decision_start`（6075-6077）、`obs_mode`（6099） | ◐ | ✅ | — | ✅（`t_commit_s`/`t_decision_start_s`，6155-6158） |
| 6 | 快照冻结时刻 | `time_alignment.snapshot_at`（kernel.py:5498） | ◐ | ✅ | — | ✅（`snapshot_at_s`，6162） |
| 7 | **候选 ETA / 目标瞬时** | `time_alignment.eta_targets[d]`（5503-5505）、`eta_terms[d]`（5500-5502）、`query_targets[d]`（5509） | ◐ | ✅ | — | ✅（`algo_query_target_s`/`eval_target_s`，6173-6174） |
| 8 | 全部 8 项 ETA 分解 | `eta_terms[d]`，键集见 `ETA_TERM_KEYS`（time_alignment.py:155-164） | ◐ | ✅ | — | —（只给 `eval_target_s` 汇总） |
| 9 | **每个打分的分量** | `time_alignment.scores[d]`：`total_s` / `terms` / `missing` / `fallback` / `predicted_bits` / `service_rate_bps`（kernel.py:5527-5537，构造见 time_alignment.py:1108-1195） | ◐ | ✅ | — | ◐（`predicted_bits`/`service_rate_bps`/`predicted_work_s`/`score_total_s`/`score_missing`/`fallback`，6175-6181；**不含 terms 其它项**） |
| 10 | `resource_work_s` 的两个因子 | `scores[d].predicted_bits` 与 `scores[d].service_rate_bps`（time_alignment.py:1034-1039, 1191-1195） | ◐ | ✅ | — | ✅ |
| 11 | `queue_weight` 是否被改 | `snapshot.queue_weight`（config 值）→ 只有 `terms["resource_work_s"]` 被乘（time_alignment.py:1176-1189）；默认 `T1_QUEUE_WEIGHT=1.0`（time_alignment.py:131） | 由 config 决定 | 同 | — | — |
| 12 | **合法掩码** | `four_direction_audit`：`final_legal_mask`/`committed_legal_directions`/`route_candidate_mask`/`loop_free_mask`/`physical_legal_mask`/`queue_room_mask`/`filter_reason_by_direction`（kernel.py:6864-6900）；行 `candidates`（6092） | ◐ | ✅ | — | ◐（`candidate_directions`，6154） |
| 13 | 掩码各门的原因 | `four_direction_audit.candidate_details[d].filter_reasons`（6860）、`queue_room`（6855）、`queue_bits_before`（6856-6857）、`rate_bps`（6852） | ◐ | ✅ | — | — |
| 14 | **被选动作** | 行 `chosen`（6093）；`time_alignment.applied_order`（5522）/`ranking`（5519） | ◐ | ✅ | — | ✅（`chosen`/`selected`/`direction`，6151-6153） |
| 15 | **fallback 标记** | `scores[d].fallback` + `fallback_directions`（5520）；`missing_directions`（5521）；`missing`（5531） | ◐ | ✅ | — | ✅（`fallback`/`score_missing`，6180-6181） |
| 16 | **missing-state 标记** | `missing`（每候选）+ `eta_unknown_terms`（5516-5518）+ `dropped_zero_rate_directions`/`rate_lookup_errors`（5540-5541） | ◐ | ✅ | — | ◐（`score_missing`；`identity_source` 另行标注身份缺失） |
| 17 | **tie 标记 / tie-break 输入顺序** | **无显式字段**。tie 可用 `scores[*].total_s` 相等**发现**；`visible_order`（time_alignment.py:1082-1083, 1202-1206 排序键第 3 分量）可从 `eta_targets`/`eta_terms` 的 **JSON 键序**反推（插入序 = `probe.legal_directions`，kernel.py:5500-5505；且 `network_alignment` 用不排序 encoder 写出，replay_codec.py:114-121） | ◐（键序在 `routing_audit_log` 里被 `sort_keys=True` 抹掉，t1_tasks.py:2349） | ✅（键序保留） | — | — |
| 18 | **T_ρ：包物理入该出口的时刻** | `packet_events`：`{"kind":"queue_enter","pid","at","queue","link_id"}`（kernel.py:2158-2162；ISL 入队处 1014-1021，`link_id="isl:{sat}:{peer}"`）。**注意 `link_id` 不带 generation**（§5.4） | — | ✅ | ✅（`<prefix>-packet-events.jsonl.gz`，verbatim） | — |
| 19 | T_ρ 起服务时刻 | `packet_events[kind=service_start]`（2181-2186）；`link_service_windows` 行 `pid/stage/link_id/start/end/rate_bps/capacity_bits/served_bits/bits/outcome`（2192-2203）；同样**不带 generation** | — | ✅ | ✅（`-service-windows.jsonl.gz`） | — |
| 19b | **把 T_ρ 绑到具体代次** | 时间线 `queue_state` 行：`resource_id`（1919）、`generation`（1924）、`event`（1915）、`queued_*`/`in_service_*`（1926-1941） | — | ✅（`replay.queue_state_events` / `timeline_rows`） | —（qid 有 gen，但 `packet_events` 侧无 gen，接不上） | — |
| 20 | 入队瞬间的**真**工作前量 | 时间线 `queue_enter` 行内 `backlog_before`：`queued_bits_before`/`in_service_remaining_bits_before`/`measured:"before_insertion"`/`excludes:"the enqueueing packet"`（kernel.py:999-1013 → 2163-2167；ISLLink.put_data） | — | ✅（`replay.timeline_rows`） | ◐（由 level+window 反推，见 §6.4） | — |
| 21 | 任意瞬时的**精确队列电平** | `<prefix>-<arm>-queue-levels.jsonl.gz`，行 `{"bits","event","kind":"level","queue","t"}`（queue_ledger.py:105-113, 24-27） | — | — | ✅ | — |
| 22 | 该出口的 `work_ahead` 真值采样 | `target_probe` 行 `truth.{queued_bits, work_ahead_bits, in_service_remaining_bits, generation, rate_bps}`（target_probe.py:232-247） | — | — | — | ✅ |
| 23 | 决策期真值（hindsight） | 行 `truth_at_commit.candidate_truth`（kernel.py:6047-6057） | — | ✅ | — | — |
| 24 | 到达瞬间真值（目标资源的 realized 状态） | `decision_ledger.truth_at_target` ← 时间线 `peer_arrival.egress_snapshot`（kernel.py:2231-2240；折叠 decision_ledger.py:189-233） | — | ✅（原料在） | — | — |
| 25 | 后继决策（peer 真实选了哪个出口） | 时间线 `redecision[prev_decision_id]`（kernel.py:7027-7028, 7125-7126） + 后继行 `chosen` | — | ✅ | — | — |
| 26 | **`egress_match`（信念出口 == 实际出口？）** | `decision_ledger.score_downstream_predictions`（decision_ledger.py:515-525） | — | ✅（**须离线调用**） | — | — |
| 27 | 拓扑时钟（(sat,dir)↔(sat,peer) 反演） | `topology_trace`：`at/reason/peer_by_satellite`（kernel.py:1693, 1893-1903） | — | ✅（`replay.topology_trace`） | — | — |
| 28 | hold/fail 尝试（未 commit 的决策） | 时间线 `decision_attempt`/`hold`/`fail`/`commit_rejected`（6909-6929） | ◐（`attempt_records`） | ✅ | — | — |
| 29 | 代码/契约身份 | `identity` + `source.config_sha256/trace_sha256`（t1_tasks.py:1929-1936） | ✅ | ✅ | 同 run | 同 run |

**一句话**：三时刻与"候选身份"本身**全部有字段，无一缺失**；唯一的 (17) tie-break 输入顺序也能从 JSON 键序反推（脆弱但可取），(26) 属于"折叠器没人调用"的**使用**问题，不是**采集**问题。

---

## 4. (c) 结论与运行配方

### 4.1 结论（verdict）

**充分（sufficient），条件是把同一个 run 的三个 flag 一起开。** 具体分层：

1. **`--capture-replay` 是唯一的硬要求**。它把 `decision_rows`（完整契约）、`timeline_rows`、`packet_events`、`link_service_windows`、`queue_state_events`、`topology_trace` 全部嵌入 `network-alignment/v1` 文档（t1_tasks.py:1656-1674）。有了它：
   - t_g ← `used_state[*].measured_at` / `neighbours[*].generated_at`；
   - t_d ← 行 `t` / `t_decision_start`；
   - T_ρ ← `packet_events[queue_enter].at`（按 `pid` + `link_id=="isl:P:R"`，`at ≥ t_d`）；
   - 身份 ← `ResourceKey(P, e, "isl", generation)`，generation 来自广告、并被代次相等性过滤（kernel.py:5092-5096），还用 `topology_trace` 可把 `(P,R)` 反演回 `direction`。
   - **代次绑定**：`packet_events`/`service_windows` 的 `link_id` 只有 `(P,R)`，**没有 generation**（§5.4），所以"这个 (P,R) 的 T_ρ 属于哪一代资源"必须从时间线 `queue_state`（`generation`，kernel.py:1924）拿——时间线同样只有 `--capture-replay` 落盘。
   **不需要**新日志。
2. **`--target-probe` 提供独立交叉验证**：在 run 内、在每个候选的 `eval_target_s` 精确读活资源真值（target_probe.py:215-247），并显式记录 12 种失败原因（`target_in_past / resource_missing / generation_changed / link_retired / no_prediction / no_resource_identity / prediction_generation_unknown / beyond_horizon / overflow`…，target_probe.py:49-53）。它的 `same_instant_convention` 也被写进 summary（343-347），即"读到的是该时刻已排定事件之后的状态"。
3. **`--queue-ledger` 提供与决策流**完全独立**的精确队列电平**：每次 mutation 一行 POST-transition level（queue_ledger.py:105-113），可独立重算 `queue_area_bits_s` 并做逐队列守恒（`scripts/verify_queue_ledger.py:1-22` 三问契约、`89-166` 梯形复算 + 守恒）。它同时把 `packet_events`/`link_service_windows` **原样**导出（kernel.py:7848-7857），所以即使不做 replay 也能拿到 T_ρ。

### 4.2 精确配方（不改代码、不动 SimPy 进程/超时/RNG）

```bash
python3 -B CODE/experiment_platform/t1_tasks.py \
  --task network_alignment \
  --config CODE/leo_sim/profiles/stage_c_e2_6g_adv200.yaml \
  --out  <run-dir>/<run-id>.json \
  --deadline-s <D> \
  --capture-replay \
  --target-probe --target-probe-stride 1 \
  --queue-ledger \
  --launch-manifest --require-pinned-code
```

要点与约束（全部有码位）：

- `--queue-ledger` / `--target-probe` / `--resource-journal` **只允许** `network_alignment`（t1_tasks.py:2793-2814，否则 `T1TASKS REFUSED`）。`leo_sim.__main__ run` 子命令**没有** `--queue-ledger`（`__main__.py:772-789` 只有 `--config/--out/--decision-log/--timeline-log`），所以这条诊断必须走 `t1_tasks.py`。
- 产物命名：ledger 为 `<out-stem>-<arm>-queue-levels.jsonl.gz`（arm 由 t1_tasks.py:1889-1892 拼进 prefix）；probe 为 `<out-stem>-target-probe-<arm>.jsonl`（1870-1874）。四臂 `NETWORK_ARMS = ("stale","now","common","candidate")`（t1_tasks.py:70）。
- `--capture-replay` 对**四个臂同时生效**，且"请求了但哪几个臂真抓到"都写进文档：`replay_capture.requested` + `captured_arms` + 每臂 `replay.captured`（1937-1945）。事后必须核验 `captured_arms` 覆盖全部 `arms`，否则会退化成"只有一臂有逐决策流"的静默缺口。
- **`--target-probe` 依赖 `time_alignment` 打开**：`_enqueue_target_probe` 在 `not ta_audit` 时直接返回（kernel.py:6117-6118）。`network_alignment` 路径强制 `cfg["time_alignment"]["enabled"]=True`（t1_tasks.py:1472），所以本条满足；但若换成 `leo_sim.__main__`，默认 `enabled=False`（config.py:585）。
- 不改仿真：`--queue-ledger` 契约写明 opt-in / write-only / no RNG / no resource / 确定性 gzip（queue_ledger.py:33-45），并有"开关打开后既有内容逐字节相同"的验收测试（`leo_sim/tests/test_queue_ledger.py:1-20`）。
- **不需要**的 flag：`--resource-journal`（1-in-stride 采样的 queue_state，信息量被 ledger 完全覆盖，且 stride>1 会丢行）。
- **probe 体量**：不设 `--target-probe-max`/`--target-probe-stride` 时按**每个候选一行**全量记录（`stride=(target_probe_stride or 1)`，kernel.py:1510-1515；上限只由 `max_samples` 约束，target_probe.py:119, 156-161）。四臂全网 run 会很大；若只做抽样，`stride` 是"单调候选流上的确定性 1/stride"（target_probe.py:105-114），**先抽样再按 horizon 展开**（113-114 记录的采样 bug 修复），所以可安全用于统计，但**不能**用于"逐决策完整"的目标——逐决策完整靠 `--capture-replay`。

### 4.3 离线折叠（这一步目前**没有产出者**）

```python
from CODE.leo_sim import decision_ledger as DL
by_decision, diag = DL.build_ledger(replay["decision_rows"], replay["timeline_rows"])
gaps = DL.audit_ledger(by_decision)          # 11 个 TIMELINE_FIELDS 还缺哪个
attempts, adiag = DL.build_attempts(...)     # 含 hold/fail/rejected
scores, s_summary = DL.score_start_estimates(...)      # t0 合法估计 vs 到达真值
scores2, s2_summary = DL.score_downstream_predictions(...)  # commit 真值（hindsight）
```

**重要事实**：这四个函数**只被测试调用**（`leo_sim/tests/test_decision_ledger.py`、`test_frozen_ledger.py`、`test_compute_delay.py`），run 路径里没有任何调用者。也就是说：`DECISION_ROW_KEYS` 契约、`TIMELINE_FIELDS` 十一项、`truth_at_target`、`egress_match` 都是**已实现、已测试、但没有任何 run flag 会写进产物**的。这不是缺日志，是缺一次离线调用（或一个 output-only sidecar writer）。

`build_ledger` 产出的十一项与四对象（decision_ledger.py:45-57, 280-322）：`t_measure, t_control_rx, t_decision_start, t_decision_commit, t_local_queue_enter, t_service_start, t_service_finish, t_peer_arrival, t_peer_redecision, t_peer_target_egress_enter, t_peer_target_egress_service_start` + `observation_at_start / estimate_at_start / truth_at_commit / truth_at_target`。缺失值一律 `"missing"` 而不是 0（decision_ledger.py:42, 179-186），这个设计直接对上了"不能把没发生当成 0"的要求。

---

## 5. 队列账本 qid ↔ `ResourceKey` 是否 1:1？（问题 5）

### 5.1 qid 的真实构造

```python
# CODE/leo_sim/kernel.py:926-927
self.data_area = QueueArea(qid=f"isl_data:isl:{sat}:{peer}:gen{gen}")
self.ctrl_area = QueueArea(qid=f"isl_ctrl:isl:{sat}:{peer}:gen{gen}")
```
```python
# CODE/leo_sim/kernel.py:464 / 707 / 1699
QueueArea(qid=f"uplink:cell:{cell}")
QueueArea(qid=f"downlink:sat:{sat}")
QueueArea(qid=f"holding:sat:{s}")
```

故 `"isl_ctrl:isl:0:1:gen0"` = **sat 0 朝 peer 1 的那条有向链路的控制队列、第 0 代**。与题面给的一致（题面说行是 `{"t","queue","bits","event"}`，实际**多一个 `"kind":"level"`**，且 `t` 用 `%.17g` 序列化——可无损往返，见 queue_ledger.py:105-113）。

### 5.2 映射关系：**2 个 qid → 1 个 ResourceKey**，且 direction 不在 qid 里

| 方向 | 结果 |
|---|---|
| `kind` | `isl_data:` / `isl_ctrl:` → `ResourceKey.kind = "isl"`（**两个 qid 共用**） |
| `satellite` | qid 第 3 段 = `ResourceKey.satellite`（**出口的拥有者 P**） |
| `generation` | qid 的 `gen{n}` = `ResourceKey.generation`（整数直取） |
| `direction` | **qid 里没有**。qid 给的是 peer R，需要 `topo[P][direction] == R` 反演 |

反演素材存在：`topology_trace` 记录 `at / reason / direction_order / peer_by_satellite`（kernel.py:1893-1903），初始拓扑一条（1693），每次拓扑重算一条（2603），并整体进入 `replay.topology_trace`（7772）。

### 5.3 与"广告值"的对应（这才是能对账的关系）

```
advertised.value(P,e,t)                  = level["isl_data:isl:P:R:genG"](t)
                                         + level["isl_ctrl:isl:P:R:genG"](t)          # 2:1
advertised.work_ahead_bits_proxy(P,e,t)  = 上式 + in_service_remaining(t)              # 需要第 3 项
```
`in_service_remaining` **不在 level 序列里**（因为头部出队即扣减，kernel.py:1168-1177），要从 `link_service_windows`（pid/stage/link_id/start/end/rate_bps/bits）反推，见 §6.4 的边界。

**因此**：qid ↔ `ResourceKey` 是 **2:1（data+ctrl 求和）**，而且 `direction` 要经 `topology_trace` 反演。**不是**1:1 直映。对 `downlink` 更差：`ResourceKey(sat, cell, "downlink")` vs `qid "downlink:sat:{sat}"` 是 **N:1**（见 §6.3）。

### 5.4 附带确认：`packet_events` / `service_windows` 的 `link_id` **不含 generation**

`T_ρ` 的物理事件用的是 `link_id="isl:{sat}:{peer}"`，**没有代次**：

```python
# CODE/leo_sim/kernel.py:2158-2162   packet_events[queue_enter]
self.packet_events.append({
    "kind": "queue_enter", "pid": pkt.pid,
    "at": float(self.env.now), "queue": queue,
    "link_id": link_id, "queue_id": qid,
})
```
```python
# CODE/leo_sim/kernel.py:2196-2203   link_service_windows
self.link_service_windows.append({
    "pid": pkt.pid, "stage": stage, "link_id": link_id,
    "start": float(start), "end": float(end), ...})
```

而 ledger 的 qid **含** `:gen{gen}`（926-927）。这个不对称被 HEAD `8838977` 新增的注释直接点名：

```python
# CODE/scripts/verify_queue_ledger.py:167-175   (commit 8838977 新增)
"""Normalise an event's (queue, link_id) to the ledger's physical qid.
  ...
  * the ISL qid carries a link generation, which the packet events do not.
"""
if queue == "isl":
    sat, peer = link_id.split(":")[1], link_id.split(":")[2]
    return f"isl_data:isl:{sat}:{peer}"
```

**含义（加成 §4.1 的结论）**：把 `T_ρ` 绑到**具体代次**的资源上，单靠 `--queue-ledger` 的 `packet-events` / `service-windows` **做不到**（会退化成"这条 (P,R) 边在这一代被用了"，rematch 后无法区分两代）。**能**把代次绑上去的是时间线 `queue_state` / `queue_enter` 行，它们带 `generation`（kernel.py:1924）与 `resource_id`（1919）——但时间线**只有 `--capture-replay` 会落盘**。这是"`--capture-replay` 是硬要求"的第二个独立理由。

---

## 6. 已确认的歧义/缺口（按严重度排序，逐条给证据）

### 6.1 最重要：t_d 的"资源信念"与 T_ρ 的"资源实际"是**两个身份**

- t_d 侧：`candidate_resources[d].egress_direction` 是 n **在 P 的广告上重跑策略**得到的（kernel.py:5768-5783），记入 `estimate_at_start.peer_egress_direction`（5952）。
- T_ρ 侧：P 真正 re-decide 时 `chosen`（行 6093 / 时间线 `redecision` 7027-7028）。
- 两者不等的比例**已被显式打分**：`score_downstream_predictions` 给 `predicted_egress / realized_egress / egress_match / egress_bits_delta`（decision_ledger.py:519-538），并汇总 `egress_match_rate`（543-557）。

**含义**：当 `egress_match == False` 时，"候选资源的 t_d 状态"和"包真正使用的那个出口的 T_ρ 状态"**不是同一个物理队列**，此时把两者相减得到的差**不是** stale-state 效应，而是"资源选错了"的二阶量。三时刻重建必须**先分层**：只在 `egress_match == True` 的子集上谈 ρ 的时间错位。**这是本审计对科学问题最直接的约束性发现。**

### 6.2 `resource_id` 命名空间撞车（target-probe 内）

```python
# CODE/leo_sim/kernel.py:6129-6142
resource_id = state.get("resource_id")              # ← used_state: "isl:P:R"（目标出口）
identity_source = "used_state" if resource_id is not None else None
if resource_id is None and detail.get("resource_id"):
    resource_id = detail.get("resource_id")         # ← four_direction_audit: "isl:{本机}:{peer}"
    identity_source = "live_audit_not_used_by_predictor"
```
```python
# CODE/leo_sim/kernel.py:6846-6847   (four_direction_audit)
"resource_id": (None if not connected else f"isl:{int(sat)}:{int(peer)}"),
```

`used_state` 只在 **ISL 且有广告样本**时才设置（kernel.py:5120-5123），所以"候选存在但该出口从未收到广告"的决策会**把本机第一跳的 link_id 记成 `resource_id`**——字段名与字符串格式与目标出口完全相同。而 `target_probe._find_link` 的注释明确警告过这正是要避免的错误：

```python
# CODE/leo_sim/target_probe.py:264-269
# THE RESOURCE IS THE PREDICTED ONE, NOT THE LOCAL CANDIDATE LINK.
# ... Looking up isls[this_sat][candidate_direction] would silently read a
# DIFFERENT link (measured: 5979/6313 rows resolved to isl:74:82 while
# the prediction was about isl:82:90).
```

结论：**回退路径与 `_find_link` 的契约冲突**。`identity_source` 让它可见（不是静默），但消费者必须按 `identity_source` 过滤；否则会把"本地第一跳"的实测当真值去和"两跳外出口"的预测比。**这是一条真正需要写进分析协议的红线，不是缺失。**

### 6.3 downlink 的 `ResourceKey` 不是物理队列

`ResourceKey(sat, cell, "downlink")`（kernel.py:5016-5017）把**目的地 cell 名**放进 `direction` 位；物理队列却是**整星共享**的 `downlink:sat:{sat}`（707），且广告自己标注 `queue_scope: "satellite_shared_drr"`（3722）。后果：
- 多个 `ResourceKey` 共享同一队列，`work_ahead` 真值只能给到卫星聚合级，**不能按候选分解**；
- `target_probe._find_link` 只解析 `"isl:..."`（target_probe.py:270-273），downlink 候选一律 `unparseable_resource_id` → probe 对 downlink 候选**永远没有真值**。

这是**模型语义**问题（DRR 共享），不是日志缺失；改日志也解决不了，只能改模型（会动语义，**本次禁止**）。分析上应把 downlink 候选**单独排除或单独报告**。

### 6.4 `--queue-ledger` 单独使用时，`work_ahead` 的 `in_service` 项不完全可推

- level 行只有 `{"bits","event","kind","queue","t"}`，**没有 pid、没有原因**（queue_ledger.py:55, 105-113）。
- 队列电平下降有三种成因，在 level 侧无法区分：出队去服务（1168-1177）、控制 TTL 过期（`_expire_waiting`，1254-1270）、数据 deadline 过期（1273-1290）。后两者**不改** `packet_events`——其 kind 是**封闭白名单**（kernel.py:7442 明确写了"a new packet_events kind would extend the closed kind whitelist in metrics.py:90-218"），没有 drop 事件。
- 更关键：**控制包没有 service window**。`link_service_windows` 只对 `DataPacket` 写（kernel.py:3217-3224 `_metric_service_start`；3227-3232 `record_metric_window` → 2192-2203 的 `_metric_service_window`），控制包的 start/finish 只进时间线（`record_metric_window` 的 control 分支 3233-3241 写 `service_finish`；3245-3251 写 `service_start`）。所以若某瞬时在服务的是**控制包**，仅凭 ledger 无法算出它的剩余比特。

→ 结论：**`--queue-ledger` 单独**能给出 `queued`（data/ctrl 分别精确）+ 数据头的残值，但 `work_ahead` 的控制头残值需要 `--capture-replay` 的时间线。而 `work_ahead` 在瞬时真值上另有**两个不依赖反推**的来源：`--target-probe` 的 `truth.work_ahead_bits`（target_probe.py:232-242）与时间线 `queue_enter.backlog_before.in_service_remaining_bits_before`（kernel.py:1008-1010, 2163-2167）。故**不构成阻塞**。

### 6.5 `eval_target_s` ≠ `algo_query_target_s`（仅 `arm=candidate`）

```python
# CODE/leo_sim/kernel.py:6173-6174
"algo_query_target_s": queries.get(direction),
"eval_target_s": etas.get(direction),
```
而 `candidate` 臂的预测时刻是在 ETA 之上再加一步排队（time_alignment.py:1384-1398：
`target = target + bits/rate`，再 `predict_at(target)`）。

→ probe 采真值的时刻是 **ETA 到达瞬时**，预测所关于的时刻是 **到达 + 排队**。两者都记录了，**但真值只落在前者**。任何"prediction vs truth"的配对必须显式选一个，否则会把"关于 T+Δ 的预测"和"T 时刻的真值"相减，人为制造一个偏差。这是一条**会让结果静默错**的坑，必须在分析协议里钉死。

### 6.6 唯一"脆弱可推"项：tie-break 输入顺序（**不是缺失**，已更正）

排序键是 `(s.fallback, value, visible_order[s.direction], s.direction)`（time_alignment.py:1202-1206），`visible_order` 来自 `enumerate(snapshot.legal_directions)`（1082-1083），而 `snapshot.legal_directions` 是**臂重排之前**的候选顺序（`_build_ta_snapshot` 内由 `cands` 构建，kernel.py:5008-5014, 5221）。

**它确实能被取回**，路径如下（我先前以为是缺失，核对序列化器后更正）：

```python
# CODE/leo_sim/kernel.py:5500-5505   —— 按 probe.legal_directions 顺序插入
"eta_terms": {d: dict(_ta.estimate_eta(probe, d).terms)
              for d in probe.legal_directions},
"eta_targets": {d: _ta.estimate_eta(probe, d).target_at
                for d in probe.legal_directions},
```
```python
# CODE/experiment_platform/replay_codec.py:114-121   —— 注意：无 sort_keys
encoder = json.JSONEncoder(ensure_ascii=True, allow_nan=False,
                           separators=(",", ":"))
```
```python
# CODE/experiment_platform/t1_tasks.py:2494-2502   —— network_alignment 走不排序的 dump_graph
if (... document.get("task") == "network_alignment"):
    from CODE.experiment_platform.replay_codec import dump_graph
    dump_graph(document, stream)
else:
    _write_json_pieces(document, stream)      # 这个才 sort_keys=True（2349）
```

所以：`replay.arms[*].…observation_at_start.time_alignment.eta_targets` 的**键出现顺序** = `probe.legal_directions` = `visible_order`。**注意不要用 `scores`**——`scores` 是 `{s.direction: … for s in scored.scores}`（5527-5537），而 `scored.scores` 已被排序（time_alignment.py:1208），其键序是**ranking**，不是 visible_order。

脆弱点（值得为它保留一条记录，但不是"缺测量"）：
- 该推导依赖"序列化器不排序"，不是契约；
- 在**默认 run**里 `routing_audit_log` 走 `_write_json_pieces`（`sort_keys=True`，t1_tasks.py:2349），键序被抹掉 → 默认 run 无法恢复 visible_order。

若要让并列可审计且不依赖序列化器，**最小新增**（output-only，不新增 SimPy event / 不读 RNG / 不改任何返回值）是一条 dict 字面量字段：

```
_score_order 的 ta_audit 里加：  "legal_directions_order": list(probe.legal_directions)
```

**仅此一项，且对三时刻问题非必需。** 除它之外，**没有真正缺失的字段**——三时刻重建不需要任何新日志。

---

## 7. 复核清单（交给他人独立验证）

1. **广告语义**：`kernel.py:3784-3838` 是唯一生成点；`value` vs `work_ahead_bits_proxy` 的差别就在 3798/3826-3827/3830/3833。
2. **打分用的是 work_ahead 不是 queued**：`kernel.py:5097-5099` + `5114-5119`。
3. **决策行契约**：`decision_ledger.py:80-88`（`DECISION_ROW_KEYS`，21 个键，写入点是 kernel.py:6070-6104 的**单一无条件 dict 字面量**，因此"无条件字段"是构造性的）；校验器两个方向都 fail-loud（98-118）。
4. **十一项时间线**：`decision_ledger.py:45-57`；折叠在 236-331；MISSING 语义 42/179-186。
5. **ledger 精确性**：`scripts/verify_queue_ledger.py:1-22`（IDENTITY / AREA / CONSERVATION 三问）+ `89-166`（梯形积分复算 + 逐队列守恒 `Q_end = Q_start + entered − left`）+ `167-175`（`_queue_key` 把 `queue="isl"` 映射到 `isl_data:{link_id}`，即**守恒校验只看 data 队列**）；`leo_sim/tests/test_queue_ledger.py`（开关关闭时 canonical digest 不变）。
6. **probe 的 same-instant 约定**：`target_probe.py:37-47`（单个常驻 sampler，按 target 升序 `timeout(max(0,target-now))`，只在 target 读），`343-347`（写进 summary）。
7. **每臂都抓到 replay**：`t1_tasks.py:1937-1945` 的 `replay_capture.captured_arms`。
8. **资源身份绑代次**：`kernel.py:5092-5096`（代次不等即 `continue`）、`5799-5815`（无代次即 missing）、`target_probe.py:296-303`（`generation_changed` 是两个代次都记下来，不替换）。

---

## 8. 与既知说法的逐条核对（verify, not assume）

| 题面/既有说法 | 核验结果 |
|---|---|
| 被打分资源是 `ResourceKey(satellite, direction, kind, generation)` 的有向出口 | ✅ 成立，`time_alignment.py:206-238`，在线构造 `kernel.py:5016-5025`。**补充**：`satellite` 位放的是**候选的 peer P**（出口拥有者），不是决策节点 |
| 分数来自 `estimate_eta` + 在**该资源**上的预测工作前量 | ✅ 成立。ETA 只装本地项（`estimate_eta`，time_alignment.py:874-955），资源项是 `resource_work_s = predicted_bits/service_rate`（1133），另有 `resource_packet_tx_s`（1143）/`terminal_tx_s`（1156）。**补充**：`estimate_eta` 的 8 项与 `resource_work_s` **不重叠**（`ETA_TERM_KEYS` 注释 152-164） |
| ledger 行是 `{"t","queue","bits","event"}` | ◐ 基本正确；实际多 `"kind":"level"`，`t` 为 `%.17g`（queue_ledger.py:105-113） |
| qid 形如 `"isl_ctrl:isl:0:1:gen0"` | ✅ 成立（`kernel.py:926-927` 的 f-string）。**补充**：这是 **2 个 qid / 1 个 ResourceKey**，且 direction 需反演 |
| `DECISION_ROW_KEYS` 是决策行契约 | ✅ 成立，`decision_ledger.py:80-88`；但**只在 `leo_sim.__main__` 的正式路径被 append-time 校验**（`__main__.py:72-75, 495`，`validate=decision_ledger.validate_decision_row`）；`t1_tasks.py` 直接用 list 收行，不校验 |
| `network_alignment` 仅在 `--capture-replay` 时内嵌逐决策行 | ✅ 成立，`t1_tasks.py:1656-1674`（`else {"captured": False}`）。**补充**：默认**仍然**有 `routing_audit_log.decision_records` 的**采样**版本（≤5000，按 `(pid,hop_index)` 键采样，`927-1063`），所以"默认完全没有逐决策记录"是不准确的 |

---

## 9. 一页版结论

- **三时刻**：t_g ✅、t_d ✅、T_ρ ✅（三者同一 run 均可取，`--capture-replay` 是硬要求）。
- **身份稳定**：`ResourceKey(P,e,"isl",generation)` 有代次过滤与代次缺失拒答；`(P,R)→direction` 需 `topology_trace`；**而 `T_ρ` 的代次只能从时间线 `queue_state` 拿**（`packet_events`/`service_windows` 的 `link_id` 无 generation，§5.4）；`downlink` 例外（共享队列，`ResourceKey` 非物理队列）。
- **最需要警惕的两件事**（都不是"缺日志"）：
  1. `egress_match` 可能为 False → 候选资源 ≠ 实际使用资源，ρ 的时间错位只能在同身份子集上定义（§6.1）；
  2. probe 的 `resource_id` 有两个命名空间，回退值为本机第一跳（§6.2）。
- **真正缺失的字段**：**没有**。tie-break 的 `visible_order` 只能靠 JSON 键序脆弱反推（默认 run 里被 `sort_keys` 抹掉），建议补一条显式字段，但它是稳健性改进、**不是缺失测量**；三时刻重建无需任何新日志。
- **落地缺口**：`decision_ledger` 的折叠器**无 run 产出者**，必须离线调用（§4.3）。
