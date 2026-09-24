# CURRENT-EVENT-TIMELINE — 平台事件链事实基线

| 字段 | 值 |
| --- | --- |
| MAIN_SHA | `549acd8b7cb7a1bb573e7146653f55273999086d` |
| 审计分支 | `agent/20260924-platform-audit` |
| 日期 | 2026-09-24 |
| 方法 | 逐行阅读代码 + 实跑取证。**本文所有结论以代码行号为准，不以本文件叙述为准。** |
| 权威代码 | `CODE/leo_sim/kernel.py`（4615 行） |

> 本文件只回答一个问题：**当前代码到底实现了什么事件链**。
> 凡本文与代码冲突，以代码为准；发现冲突应报缺陷，不得静默融合（AGENTS.md 规则 15）。

---

## 0. 总链（实现态）

```
packet emission          _emitter                        kernel.py:2677-2724
   ↓  (endpoint uplink queue)
queue enter (uplink)     _emit → _metric_queue_enter     kernel.py:2715 / 1538
   ↓
service start (uplink)   UplinkServer._run → _transmit   kernel.py:503 / 2592
service finish (uplink)  record_metric_window             kernel.py:2596 → 1587
   ↓
propagation start        _metric_propagation_start       kernel.py:547 / 1604
propagation arrival      _ingress_after_prop             kernel.py:4304-4307 / 1616
   ↓
satellite ingress        _metric_satellite_ingress       kernel.py:4308 / 1637
   ↓
[F2] node process        _node_process                    kernel.py:4261-4302
   ↓
decision start           decide_deferred / _decide        kernel.py:3846 / 4036
decision commit          _record_decision                 kernel.py:3537 / 3571
   ↓
service start (ISL)      ISLLink._run → _transmit         kernel.py:828 / 2592
service finish (ISL)     record_metric_window             kernel.py:2596 → 1587
   ↓
propagation start        _metric_propagation_start        kernel.py:1604
propagation arrival      _isl_arrive_after_prop           kernel.py:4320-4323 / 1616
   ↓
   ... (repeat node process → decision → ISL) ...
   ↓
propagation arrival      _deliver_after_prop              kernel.py:4335-4338
destination arrival      deliveries[pid].delivered_at    kernel.py:4352
```

**该链不是一条"流水线"，而是每跳重复的循环**：`satellite_ingress → node process → decision → ISL service → propagation → 下一跳 satellite_ingress`。
唯一例外是目的地下行：`_deliver_after_prop`（4335）**不做** `_node_process`，这是 F2 的显式作用域边界（见 §3）。

---

## 1. 事件表（要求格式）

| 事件 | 代码位置 | 时间字段 | 来源 |
| --- | --- | --- | --- |
| packet emission | `_emitter` kernel.py:2677-2689 | `DataPacket.emitted_at` = `self.env.now`（2688）；事件 `at` = `pkt.emitted_at`（1535） | trace 行 `emit_time_s`（`trace.py`；`_emitter` 2679 用它算 `delay`） |
| queue enter | `_metric_queue_enter` kernel.py:1538-1562 | 事件 `at` = `float(self.env.now)`（1557）；`queue_id` = 单调计数器（1552）；`pkt.metric_queue_id`（1554） | 调用点决定归属：上行 `_emitter` 2715（无 decision_id）、holding `_hold_packet` 1481（带 decision_id）、ISL/下行 见 §1.1 |
| service start | `_transmit` → `_metric_service_start` kernel.py:2592 / 1572-1585 | 事件 `at` = `t0` = `self.env.now`（2460）；`stage`/`link_id`（1564-1570）；`rate_bps`、`bits` | `rate_fn(t0)`（2467-2481）或 `owner._service_rate_bps`（2589）；`dur = pkt.bits / rate`（2481） |
| service finish | `record_metric_window` → `_metric_service_window` kernel.py:2596-2601 / 1587-1602 | 窗口 `start`=`t0`、`end`=`self.env.now`；`capacity_bits = rate_bps × (end-start)`（1595）；`served_bits` 仅当 `outcome=="ok"`（1596） | `outcome ∈ {ok, failed, retired, interrupted}`（2654/2648/2661/2651） |
| propagation start | `_metric_propagation_start` kernel.py:1604-1614 | 事件 `at` = `env.now`；`prop_id`（1606）；`delay_s`（1612） | 上行 `model.propagation_delay_s(slant_range)`（545-546）；ISL/下行同类 |
| node arrival（propagation arrival） | `_metric_propagation_arrival` kernel.py:1616-1635 | 事件 `at` = `env.now`（1623）；`prop_id` 归还（1625）；timeline `peer_arrival.at` | 三个到达进程：`_ingress_after_prop` 4305、`_isl_arrive_after_prop` 4321、`_deliver_after_prop` 4336 |
| satellite ingress | `_metric_satellite_ingress` kernel.py:1637-1649 | `pkt.metric_ingress_at` = `env.now`（1641）；重复调用抛 `KernelError`（1640） | 仅上行 `_ingress_after_prop` 4308 |
| **[F2] node process start/end** | `_node_process` kernel.py:4293-4302 | `node_process_start.at` = `env.now`（4296）；`node_process_end.at` = `env.now`（4300）；`started_at`（4301）；`node_cost_s`（4298/4302） | `execution.node_process_delay_s`（4293，读自 1144） |
| decision start | `decide_deferred` kernel.py:3846（`started = env.now`）；frozen 观测 `_observe_preferred_action` 3899（`now = env.now`） | decision 行 `t_decision_start`（3576-3578）；`decision_ledger.TIMELINE_FIELDS` 含 `t_decision_start` | `compute_delay_s > 0` 时由 `decide_deferred` 记录；否则 `t_decision_start == t` |
| decision commit | `_record_decision` kernel.py:3537（`committed_at = env.now`） | decision 行 `t`（3572）；ledger `t_decision_commit` | `decision_id` 由 `_next_decision_id`（1497）分配，只有存在 sink 时才非 None |
| arrival（目的地） | `_deliver_after_prop` kernel.py:4335-4353 | `deliveries[pid]["delivered_at"]` = `now`（4352）；事件 `delivered.at`（1654） | `ledger.record(pid, "DELIVERED", bits)`（4351） |

### 1.1 queue enter 的四个物理队列

```
queue 名       link_id 形状                      产生点
uplink        gsl:uplink:pending:<cell>         kernel.py:2716（新到）
                                                kernel.py:522 / 532（retire/stall 重入）
holding       holding:<sat>                     kernel.py:1481（_hold_packet）
isl / downlink（由 _metric_link_id 1564-1570 命名）
              isl:<sat>:<peer> / gsl:<stage>:<sat>:<cell>
```

**归属规则（R8-A8，kernel.py:1541-1551）**：`decision_id` **必须由调用方显式传入**，禁止用 `pkt.decision_id` 反推。
由 commit 直接造成的入队传已提交 id；链路 stall/retire 造成的重入队传 `None`；holding 入队传"这次 hold 尝试"的 id。
该规则有专门回归测试 `test_holding_queue.py` 与 `test_transmit_retirement.py`。

---

## 2. 时间账本：四个时间对象（不是四个时间戳）

`decision_ledger.build_ledger`（`decision_ledger.py`，模块 docstring 1-42）把每条 decision 折成**四个语义不同**的对象：

| 对象 | 含义 | refresh 模式 | frozen 模式 |
| --- | --- | --- | --- |
| `observation_at_start` | 这次决策**实际依据**的观测（含每个邻居的测量时刻） | commit 时刻重读的状态 | `t_decision_start` 的快照 |
| `estimate_at_start` | 该观测**合法支持**的唯一预测 | 同上 | 同左（**绝不**用 commit 时刻真值回填） |
| `truth_at_commit` | commit 时刻核内真值（`info_audit`） | 同左 | 同左 |
| `truth_at_target` | 包真正竞争到的资源的**已实现**真值 | 由到达快照回折 | 同左 |

`t_measure`（`decision_ledger.py:46`）= 动作实际依据的时刻：
`frozen → t_decision_start`，`refresh → t_decision_commit`；零计算延迟时二者相同。
**`t_measure` 是每决策标量，不表示任何单个邻居的测量时刻** —— 后者只存在于 `observation_at_start.neighbours`。

---

## 3. F2（node processing）在链上的精确位置

`_node_process`（kernel.py:4261-4302）的不变式，均有测试锚定（`CODE/leo_sim/tests/test_f2_node_cost.py`）：

1. **一次卫星访问 = 一次占用**：上行 ingress（4314）+ 每次 ISL 到达（4329）。
2. **区间端点在决策起点终止**：`node_process_end == t_decision_start`，与 `compute_delay_s` **互不重叠**
   （测试 `test_the_node_stage_ends_exactly_where_the_decision_stage_starts`，test_f2_node_cost.py:381-409）。
3. **不是 tx**：不参与 `pkt.bits / rate`，扫 PHY 速率不动读数
   （`test_sweeping_the_phy_rate_changes_tx_and_never_the_node_reading`，429-438）。
4. **不计入任何冻结阶段**：只写 timeline sink 的 `node_process_start`/`node_process_end`
   （4297/4300）；不新增 `packet_events` kind，不新增 mechanism counter
   （`test_the_node_cost_adds_no_mechanism_counter_and_no_new_event_kind`，490-501）。
5. **默认 0 时逐位不变**：`delay <= 0` 时生成器**不碰时钟直接返回**（4294-4295），不创建事件，
   历史 run 保持 bit-identical（`test_a_zero_cost_matches_the_unset_default_bit_for_bit`，292-300）。
6. **作用域边界**：仅数据包；目的地下行终点（`_deliver_after_prop`）与控制面**不受影响**（4283-4287）。
7. **fail-loud**：`node_process_delay_s > 0` 且无 `timeline_sink` → `KernelError`（kernel.py:1145-1152）。

---

## 4. 服务窗口的竞态语义（决定 `tx_s` 的真值）

`_transmit`（2417-2663）用一次 `while True` 循环反复重算竞态，**只有全程链路可用才算 `ok`**：

| 返回 | 触发 | 是否记 fate | `served_bits` |
| --- | --- | --- | --- |
| `ok` | `fail_kind is None`（2653） | 否（正常推进） | `pkt.bits` |
| `failed` | 几何/GE/截止期在中途触发（2619-2630） | 是，恰好一个 fate（2662） | 0（1596） |
| `retired` | 硬退役截止期（2645-2649 / 2550） | 否；调用方 requeue（512-527） | 0 |
| `stalled` | 地平线内不再可用且无截止期（2519/2558） | 否；结算为 `IN_SYSTEM_AT_STOP` | 0 |
| `interrupted` | 被 retire 中断唤醒、竞态需重算（2651-2652） | 否（`continue`） | 0 |

**关键不变式**：`_transmit` 只在**全部可用性检查通过之后**才把 `owner._svc_phase` 从
`"waiting_for_link"` 翻到 `"transmitting"`（2567-2575），所以"链路降级前的等待"不会被记成服务进度（K2）。

---

## 5. 链缺口：审查发现 → 修复状态

> **本节在 549acd8 基线上写作，随后由提交 `03aecfb`/`8e69d79`/`fb6b1a6` 部分修复。**
> 下表给出**修复后**的准确状态；凡与 549acd8 的旧叙述冲突，以本节为准。
> （独立对抗复核在本分支 rev 1 上指出旧叙述已过期 —— 该复核意见正确，已在此改正。）

### 5.1 F2 的入口可达性

| 状态 | 事实 |
| --- | --- |
| 基线 549acd8 | `node_process_delay_s > 0` 硬要求 `timeline_sink`（kernel.py:1145），而 CLI `run` 只接出 `decision_sink`（`__main__.py:407-412`），**无任何 timeline 出口** → F2 只能由手写 `kernel.Kernel(..., timeline_sink=[...])` 触发。 |
| **修复后（`03aecfb`）** | CLI 新增 `run --timeline-log PATH`；`__main__.py:422-431` 在 `node_process_delay_s > 0` 且未给该参数时**入口级拒绝**（exit 3）；`__main__.py:455-467` 预检 + `:528` 把 sink 传入 `run_simulation`。测试：`test_f2_runs_through_the_official_cli`、`test_node_process_delay_without_a_timeline_log_is_refused`。 |
| **仍然残留（B7，未修）** | canonical **远端** runner 仍不可用：`remote_job.py:304-309` 只传 6 个固定参数、从无 timeline stream；`run-remote.sh:44` 显式拒绝 `--`。**没有任何 passthrough**（独立对抗复核已尝试证伪并确认）。 |

### 5.2 `decision_compute_s` 与 F2 的混淆

| 状态 | 事实 |
| --- | --- |
| 基线 549acd8 | `decision_compute_s` 定义为「未覆盖区间之和」，而 F2 的占用**正是**未覆盖区间（`test_f2_node_cost.py:344-359` 明确断言）→ 启用 F2 会把节点处理时间读成决策计算时间。 |
| **修复后（`8e69d79`）** | 新增 keyword-only `node_spans` / `node_spans_by_pid` 与桥函数 `node_process_spans(timeline_rows)`；`node_process_s` 成为独立命名项，`decision_compute_s` 变成残差未覆盖时间。**实测（R02 设计，两臂均 `compute_delay_s=0.05`）**：f2 臂未标注读数 **1.800 s**、标注读数 **0.900 s**，差值 **0.900 s** 恰等于节点占用总量；对照臂两者均为 0.900 s。 |
| **仍然残留** | 该分离**必须由调用方显式传入 span**；`metrics_independent` 无法自行探测 F2（有无 span，未覆盖区间都一样）。`v2_analysis` 全库范围内**不 import** 它（唯一非测试调用者是另一个工作包的 `step5_recompute.py`）。 |

### 5.3 新增缺口：第二实现**读不了持久化产物**

`verify_delay_decomposition(events, windows, result)` 在 `result` 来自 `json.load(ledgers.json)` 时，
`deliveries` 的键是**字符串**（`"1"`）而事件 `pid` 是 **int**，因此 declared 集合与 observed 集合**交集为空**，
N 个包全部被报成 `declared delivered but has no delivered event`，`checked_packets = 0`。
→ 该模块**无法读取它本应复核的产物格式**，且失败形态像数据问题而非读取器缺陷。

**已在 `_declared_pid` 中修复**（接受 int 与其规范十进制 JSON 键形式；`"07"`/`"7.0"`/`" 7"` 一律 fail-loud），
测试 `test_the_persisted_ledger_mapping_is_accepted_after_a_json_round_trip`。

### 5.4 新增缺口：F2 的 e2e 可加性**在有竞争时失效**

在**无竞争**的 2 星装置上，e2e 增量恰等于节点占用（`test_f2_single_factor_sweep_moves_only_the_node_term`）。
但在**有竞争**的真实装置（8 星 / 1 面 / csv 微 trace，每包 3 次卫星访问）上实测：

**总 e2e 增量 0.850000245 s ≠ 6×3×0.05 = 0.900 s**；pid 99 的节点占用为 0.15 s 而 e2e 只增加 0.09999996 s，
其 `holding_wait_s` 减少 0.050000000000000266 s —— 一次完整的节点占用被**既有的 holding/ISL 队列等待吸收**。

→ 因此**不得**声明「F2 只移动节点处理阶段」或「e2e 增量恰为 包数×访问数×时延」；
   只能声明「节点占用被**独立记录**，且未被计入决策计算时间」。

---

## 6. 本文件的取证方式（可复现）

```bash
cd <repo>            # MAIN_SHA = 549acd8b7cb7a1bb573e7146653f55273999086d
git rev-parse HEAD
grep -n "def _node_process" -A 42 CODE/leo_sim/kernel.py     # §3
grep -n "decision_compute_s = math.fsum" CODE/leo_sim/metrics_independent.py   # §5.2
python3 -m pytest CODE/leo_sim/tests/test_f2_node_cost.py \
                  CODE/leo_sim/tests/test_frozen_observation.py \
                  CODE/leo_sim/tests/test_decision_ledger.py -q
```
