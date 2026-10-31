# AUDIT-EXISTING-TESTS — 现有测试对资格考试 A–F 的覆盖与强度

> 任务：shared task `task-2`。作者：`test-auditor`（独立审计，只读，不改代码/测试，不 commit）。
> 代码身份：worktree `.wt-qual-20261031`，branch `qual/sim-qual-20261031`，HEAD `a90c1f9`；
> 审计时唯一 dirty 项是本目录 `ANALYSIS/SIM-QUAL-20261031/`（untracked）。
> 环境：macOS/Darwin 本机，`Python 3.14.2`，`pytest 9.1.1`。

## 0. 结论先行

1. **全绿，但绿≠A–F 被锁死。** 指定 9 文件 129 个测试：**129 passed / 0 failed / 0 skipped，4.07 s**。
2. **判定：A COVERED、B WEAK、C COVERED、D MISSING、E WEAK、F COVERED。**
   链路串行化、控制面占带宽+不抢占、计算时间/冻结观测/commit 重验 有真事件级断言；
   传播与发送分离只被"聚合恒等式 + 服务窗口背靠背"间接锁定；状态消息时间链基本只有单元级/结构性断言；
   **排队理论完全空白**。
3. **三个最危险缺口**（详见 §4）：(a) **D 整条无测试**（λ<μ 有界 / λ>μ 斜率≈λ−μ）；
   (b) **E 的 `received_at` 时间链无事件级断言**（分解、拥塞使 `received_at` 变大、kernel 级"t<received_at 读不到"），
   现有 TTL 测试只断言末态 `valid is False`；(c) **C 的控制包绝对传输时间只被"整数倍"锁定**
   （实测 gap=0.001600000=2×0.0008 s，但断言只要求 `gap/ctrl_service_s` 是整数 ⇒ control 带宽被整体 ×整数 的错误不会红）。
4. 方法论混杂：`test_control_nonpreemptive_priority_and_bandwidth` 的"有/无控制面"两臂**同时换了 policy**
   （on=hop，off=oracle），其 `delivered_at` 对比不是干净的控制面消融。

---

## 1. 实跑记录（可复算）

在 `/Users/lge/Desktop/topic/.wt-qual-20261031` 执行：

```
python3 -m pytest CODE/leo_sim/tests/test_control.py CODE/leo_sim/tests/test_micro_mechanism.py \
  CODE/leo_sim/tests/test_compute_delay.py CODE/leo_sim/tests/test_frozen_observation.py \
  CODE/leo_sim/tests/test_queue_ledger.py CODE/leo_sim/tests/test_kernel.py \
  CODE/leo_sim/tests/test_routing.py CODE/leo_sim/tests/test_counterfactual.py \
  CODE/leo_sim/tests/test_decision_ledger.py -q
```

结果 `129 passed in 4.07s`（wall 4.782 s，stderr 空，无 warning 摘要/无 skip）。

| 文件 | 数 | 与 A–F 关系 |
|---|---:|---|
| `test_micro_mechanism.py` | 3 | **A/B/C 主脊梁**：唯一有 ISL 服务窗口时间戳断言的集合 |
| `test_control.py` | 15 | C（顺序）、E（cache/AoI/TTL，多为单元级） |
| `test_compute_delay.py` | 15 | F（时钟推进、compute pool） |
| `test_frozen_observation.py` | 14 | F（frozen/refresh/commit 重验，最强） |
| `test_queue_ledger.py` | 2 | 队列账本**自洽性**，不是排队论 |
| `test_kernel.py` | 24 | 命运/容量/公平；A、B 只有弱聚合断言 |
| `test_routing.py` | 28 | 纯函数排序；手工构造 cache ⇒ 对 E 的时间链不可见 |
| `test_counterfactual.py` | 10 | 配对重放/fingerprint；旁证"合法集"边界 |
| `test_decision_ledger.py` | 18 | B（顺序）、E（`t_control_rx`）、F（三时刻拆分） |
| 合计 | **129** | |

单次运行足够：fixture 全是 `StaticGeometry` + 固定 seed 的确定性单跑，且测试自身用
`test_zero_delay_is_identical_to_the_unset_default`、`test_no_override_leaves_a_frozen_run_bit_identical`
做逐字节恒等断言，随机性来源为零。

---

## 2. A–F 判定与"实现坏了会不会红"

口径：**事件级**=读原始日志中单事件的时间戳/顺序并断言；**聚合级**=只断言端到端时延/平均/计数/"结果不同"。

### A 链路 serialization —— **COVERED**
- 事件级：`test_micro_mechanism.py::test_m3_*` 用 `_windows_by_pid(res,"isl:0:1")` 断言 pid1 `3.010001→3.810001`、
  pid2 `3.810001→4.610001`（dur=0.800000=8e6/10e6），`windows[2].start−windows[1].end ≤ 1e-6`，
  且 `windows[pid].start ≥ windows[target].end`；`test_m2_*` 另有 `windows[1].end == windows[2].start (1e-6)`；
  `_assert_data_fifo` 遍历全部 ISL。
- **会红**：同方向并行服务（窗口重叠）、漏算忙期（窗口缩水）、用错速率（时长≠0.8）。
- **不会红**：`T_tx` 写成常数 0.8 s（本 fixture 恰好一致）仍绿；**事件级只钉了一个 (S,R) 点**。
  第二点（8 Mbit/1 Gbps ⇒ 0.008 s）只在**相邻文件** `test_analytic_scenarios.py` 以 `abs=1e-9` 端到端和式出现，
  不是窗口断言；**没有"同链路变 S、R 不变"的比例性测试**。

### B transmission 与 propagation 分离 —— **WEAK**
- 事件级（只到"分离"）：M3 `windows[2].start == windows[1].end`（1e-6）证明传播期间链路可服务下一包
  （实测 ISL prop=3.336 ms 未计入忙期；若并入忙期，3.336 ms ≫ 1e-3 容差，3.81/4.6116 锚点立刻红）。
- 聚合级（覆盖数值）：相邻文件 `test_analytic_scenarios.py` 两个测试 `abs=1e-9` 断言
  `delivered_at = Σ(S/R + d/c)`；无排队时等价于逐跳 `arrival = service_start + S/R + d/c`——强，但**不读 arrival 事件**。
- **会红**：漏计/重复计 prop、prop 并入服务窗口、跨两跳只加一次 prop。
- **不会红**：`propagation_arrival.at == propagation_start.at + delay_s` 全仓无断言（`grep propagation_arrival`
  只命中 `test_metrics_independent.py`/`test_congestion_metrics.py`/`test_f2_node_cost.py` 的**手写事件列表**单元测试）；
  kernel 级"下一跳只能在 arrival 后读到"只有 `test_decision_ledger.py` 的 `t_service_finish ≤ t_peer_arrival`（纯顺序）。

### C control 真实占带宽且非抢占优先 —— **COVERED（核心）**
- 事件级：`test_control.py::test_control_nonpreemptive_priority_and_bandwidth` 用 `service_log["isl"]` 的 kind 序列
  断言 `any(d[0] < ci < d[1])`（data→ctrl→data）；`test_m3_*` 实测 `gap(pid2→pid3)=0.001600000`，断言它等于
  ctrl 服务时间（8000/10 Mbps=0.0008 s）的整数倍且 `round(...)≥1`；同测试 data 窗口连续 0.8 s ⇒ 未抢占。
- **会红**：control 零成本（gap=0 / on-off 两臂 `delivered_at` 相等）、control 抢占 data（窗口被切）、data2 越过排队 control（gap=0）。
- **不会红（关键弱点）**：control 服务时间整体 ×整数（bits 8_000 误当 80_000、rate 错 10 倍）时
  gap=0.016/0.0008=20 仍是整数 ⇒ **M3 绿**；`test_control_ledger_accounts_bits` 只断言 `>0`；
  `test_time_alignment_online.py` 只钉 control 的 **bits**（8_608+9_008）不钉**时间**。
  另："提高广播频率 ⇒ 链路服务工作量上升"无两臂测试；on/off 两臂 policy 混杂（见 §0.4）。

### D 基本排队理论 —— **MISSING**
- 现状：只有 (i) `queue_area_bits_s`/`queue_ledger` 的**area 恒等式**（只证"重建值==kernel 原生值"，
  **不证原生值对**）；(ii) `test_kernel.py` 的 access/isl **容量溢出**（有限缓存语义，不是 λ/μ）；
  (iii) `own_queue_bits` 逐决策采样（A/C 用）；(iv) on/off 时延对比（不是斜率）。
- **会红**：同方向排队改 LIFO → M3/M2 FIFO 红（属 A/C）；ISL 速率改错 → A 红。
- **不会红**：λ>μ 下队列"不增长/被丢弃/斜率错"、λ<μ 下"不回落/无界增长"——没有任何测试跑"稳定流量 × 速率"输入，
  也没有任何测试拟合 Q(t)。这是 A–F 中**唯一完全空白**的一条。

### E 状态消息时间链 —— **WEAK**
- 现有（都不到事件级）：单元级 `test_cache_entry_validity_and_aoi`（`valid_at` 前/后/TTL、`aoi(11)=1.0`）、
  `test_cache_keeps_freshest_only`；相邻文件 `test_review_round4.py::test_cache_entry_future_arrival_is_not_valid` 同义；
  kernel 级 `test_cache_contains_only_arrived_info`（链路断开⇒cache 空，**结构性**）；
  `test_ttl_expiry_blocks_use_of_stale_info` 仅断言 horizon 时 `entry["valid"] is False`（注释自认"包根本没发出去"）；
  相邻文件 `test_control_received_at_enters_ledger_and_matches_cache`（`cache.received_at==ledger`、
  `aoi == horizon − generated_at`，聚合）；`test_decision_ledger.py` 的 `t_control_rx` 非 MISSING（只证"有值"）。
- **会红**：cache 允许 future state、TTL 被忽略、`aoi` 改成 `t−received_at`。
- **不会红**：`received_at` 漏掉排队/传播分量；拥塞使 `received_at` 变大（无两臂）；kernel 级"t<received_at 读不到"
  （只有路由库层 `destinations_in_cache(now<received_at)` 的手工 cache 单测）；任意决策时刻的 `AoI(t)` 值
  （`caches[*]["aoi"]` 是 run 结束时算的）。

### F 计算时间推进时钟 + frozen/refresh + commit 重验 —— **COVERED**
- 事件级：`test_compute_delay.py`：`r["t"] − r["t_decision_start"] == 0.05`；
  `commit = start + wait + 0.5`（逐决策配对）；`chosen ∈ candidates`；零延迟与不配置**逐字节等价**（含
  `packet_events`/`link_service_windows`）。`test_frozen_observation.py`：`t_observed == t_decision_start`（5.082）、
  `commit_rejected{action="E", reason="action_no_longer_legal", inferred_at=5.082, at=7.082}`、
  frozen 拒绝使用 commit 时刻才出现的信息 vs refresh 使用它（判别性对照）、稳态两模式一致（负对照）、
  forced action 只允许 observation 分支点的合法集。
- **会红**：计算期间不推进时钟（t−t_start=0）；commit 不重验（frozen+up_before=True 会 `DELIVERED` 而非
  `IN_SYSTEM_AT_STOP`）；frozen 退化 refresh；重验时重新求解（`_target_rows==[]` 且无 `commit_rejected`）。
- **不会红**：**"计算期间其它网络事件继续发生"无断言**。frozen/refresh 的差异只用几何可用性
  `neighbors_at(t)`（纯 t 函数）；即使 compute 窗口内不处理任何排队/传播/服务完成事件，测试仍全绿。

---

## 3. 逐文件 · 逐相关测试清单（断言 / 级别 / 抓什么）

级别：**E**=事件级时间戳或顺序；**K**=kernel 行为但只到状态/计数；**U**=单元（手工对象）；**A**=聚合。

### `test_micro_mechanism.py`（A/B/C 主证据）
| 测试 | 断言什么 | 级别 | 能抓的错误 |
|---|---|---|---|
| `test_m1_idle_link_keeps_routing_order_and_isl_fifo` | 候选=合法集、`chosen==candidates[0]`、`own_queue_bits` 精确 0/0/0/8e6/16e6、每 ISL data 入队序==服务序、无 overflow | E | 候选过滤、队列未排队却报排队、FIFO 倒序 |
| `test_m2_burst_reverses_candidate_order_...` | 选择序列 `[E,E,W,W,E]`、`turned["candidates"]==["W","E"]`、`enqueue[2]=3.06 < 3.11 < windows[2].start=3.81`、`windows[1].end==windows[2].start`、两条等代价路径都真被使用 | E | 队列项未进入排序、窗口重叠、假路径 |
| `test_m3_later_data_packet_cannot_jump_ahead_...` | 窗口 3.01/3.81/4.6116（1e-3）+ 0.8 s 时长；后包 start ≥ target end；gap 是 ctrl 服务时间整数倍；data 窗口连续 | E | **A、C 核心**：乱序、control 零成本/抢占 |

### `test_control.py`（15 个）
| 测试（简写） | 断言什么 | 级别 | 抓什么 |
|---|---|---|---|
| `test_cache_entry_validity_and_aoi` / `test_cache_keeps_freshest_only` | `valid_at` 前/后/TTL、`aoi(11)=1.0`；旧广告不覆盖新广告 | U | future 可读、TTL 失效、AoI 公式、覆盖方向 |
| `test_advertisement_reaches_two_hops_with_vis_k_2` / `..._vis_k_1_limits_propagation` / `..._vis_k_4_...` / `test_cache_contains_only_arrived_info` | 2 跳可达 + `path==[0,1,2]`；vis_k=1 阻断；4 跳 payload 全字段 + `max_observed_hops==4`；无链路⇒无条目 | K | 控制面可达性/裁剪/凭空注入（非 A–F） |
| `test_ttl_expiry_blocks_use_of_stale_info` | **仅** `entry["valid"] is False`（末态） | K | 只抓 TTL 标记，**不抓"用"** |
| `test_control_nonpreemptive_priority_and_bandwidth` | on 臂 p2 `delivered_at` > off 臂；kind 序 data→ctrl→data | **A+E** | 零成本 control、顺序错；**policy 混杂** |
| `test_control_ledger_accounts_bits` | `offered>0, delivered>0, snapshots>0` | K | 只抓"全零" |
| `test_ring_topology_uses_bounded_shortest_path_broadcast_tree` | `DUPLICATE==0`、快照/注册数精确 | K | 环上控制风暴 |
| `test_real_no_info_ingress_path_keeps_auditable_hold_attempts` | hold 尝试入审计表且四向审计齐备 | K | 审计断链（非 A–F） |
| `test_data_packet_loop_cap` / `test_control_instances_do_not_consume_data_trace_packet_cap` / `test_access_fifo_no_queue_jump` / `test_try_grant_clears_granting_satellite_waiter` | 跳数/包数上限、接入 FIFO、白盒 waiter 清理 | K/U | 与 A–F 无关 |

### `test_compute_delay.py`（15 个 → F）
`test_a_positive_delay_separates_start_from_commit`（**E**：`t−t_start==0.05`）、
`test_the_wait_is_contention_and_the_service_is_cost`（**E**：逐决策 `t−t_start==wait+0.5`）、
`test_zero_delay_is_identical_to_the_unset_default`（**E**：12 字段+decision/timeline 行逐字节相等）、
`test_a_delayed_decision_still_commits_a_legal_action`（**E**：`chosen∈candidates`）、
`test_the_ledger_reports_the_computation_interval`（**E**）、
`test_a_bound_makes_decisions_wait_and_the_wait_is_recorded`（**E**：`compute_wait` 的 `service_s==0.5`）、
`test_more_servers_can_only_reduce_the_wait`（A：单调+3 服务器=0）、
其余 6 个（默认 0 / 非法值 fail-loud / config identity / pool 默认无界 / 池尺寸校验 / 无界池逐字节等价）为 U。

### `test_frozen_observation.py`（14 个 → F，本套最强）
`test_refresh_uses_information_that_did_not_exist_when_it_started` vs
`test_frozen_refuses_to_use_information_that_appeared_during_compute`（**判别性对照**，事件级 5.082/7.082）；
`test_frozen_records_a_rejected_action_instead_of_resolving_again`（`commit_rejected` 三字段 + 不退化为重解）；
`test_zero_cost_frozen_is_allowed_and_is_a_real_frozen_run`（`t==t_decision_start`、`t_observed==t_decision_start`、拒绝 t0 不支持的动作）；
`test_frozen_accepts_a_forced_action_taken_from_the_observation_legal_set`（`branch_instant=="observation"`、`t_observe==5.082`）、
`test_a_forced_action_illegal_at_the_frozen_branch_point_fails_loud`、`test_forcing_a_frozen_hold_branch_point_fails_loud`（合法集边界 fail-loud）；
`test_no_override_leaves_a_frozen_run_bit_identical`（零开销）；`test_frozen_agrees_with_refresh_when_nothing_changes_in_the_window`（负对照）；
`test_frozen_refusals_are_visible_only_through_the_timeline_sink`（审计边界：refused ⇒ 无 decision row；并声明
"分析者只拿 decision rows 时看不到 frozen 的代价"）；其余 4 个为 config/边界。

### `test_queue_ledger.py`（2 个）
`test_ledger_off_changes_nothing`（关闭时无新键、digest 相同）；
`test_ledger_on_reconstructs_queue_area_exactly`（**area 重建==kernel 原生值，<1e-6**，行数与 verbatim 列表一致，两次运行字节相同）。
**只证自洽，不证动力学** ⇒ D 仍空白；但 `levels.jsonl.gz`（`{t,queue,bits}`）正是 D 该用的通道。

### `test_kernel.py`（24 个，只列沾边）
`test_direct_uplink_downlink_delivery` / `test_isl_forwarding_delivery`：`expect = Σ(服务+prop)` 但容差 **abs=0.02**
（≈12% 相对误差，20 ms 内的串行化错误可蒙混）；严格版（1e-9）在**相邻文件** `test_analytic_scenarios.py`，不在本 9 文件内。
`test_geometry_loss_in_flight_accounts_occupied_time`：`occupied["gsl_uplink_s"]==0.05`（1e-6，真事件级占用）。
`test_access_queue_overflow` / `test_isl_queue_overflow`：有限**容量**语义（非 λ/μ）。
`test_uplink_fair_scheduling_alternates` / `test_drr_bit_fairness_...`：调度公平（非 A–F）。其余 16 个：fate/保守性/deadline/endpoint 生命周期/几何 epoch。

### `test_routing.py`（28）/ `test_counterfactual.py`（10）
routing：全部 `routing.choose_next_hop(...)` 纯函数测试，cache 由 `_cache_with()` **手工构造**（`generated_at/received_at/ttl` 手填）
⇒ 对 E 的真实时间链与 A/B/C 完全不可见。
counterfactual：`branch_states_identical` fingerprint、只强制目标决策、非法强制 fail-loud、`PRECOMMIT_FIELDS` 覆盖边界
（**显式声明不含 sim 状态 hash**）——对 F 的合法性边界有旁证，不覆盖 A–E。

### `test_decision_ledger.py`（18 个，B/E/F 旁证）
`test_ledger_rebuilds_the_full_eleven_field_chain`：`t_measure ≤ t_local_queue_enter ≤ t_service_start ≤ t_service_finish ≤
t_peer_arrival ≤ t_peer_redecision`（**顺序非等式**）；`test_ledger_marks_missing_instead_of_filling_zero`（MISSING 不读 0.0）；
`test_control_arrival_time_is_recorded_without_a_learner` / `..._stays_missing_with_the_control_plane_off`（`t_control_rx` 有/无值，不校验值）；
`test_holds_do_not_leak_decision_ids`、`test_default_off_allocates_nothing_even_when_holds_would_occur`（幂等/零开销）；
`test_commit_caused_enqueue_carries_the_committed_decision`、`test_holding_enqueue_is_not_credited_...`（事件归属）。

---

## 4. qual-author 缺口清单（可直接实现的断言）

> 只读不可变输出；括号内通道已实测存在。每条至少一个 `abs<=1e-9` 时间戳/顺序断言，禁止只比平均时延。

**A（3 条）** ① 同一 ISL 同方向、**两不同包长**（4/8 Mbit）@10 Mbps：窗口 `dur==bits/rate`（1e-9）⇒ 证明 `T_tx=S/R`
非常数。② 第二点 8 Mbit@1 Gbps：直接断言 `dur==0.008`（1e-9），而非靠端到端和式。③ 同一 `(sat,direction)` 上任意两窗口
不重叠且 `前.end==后.start`（1e-9）。通道 `res["link_service_windows"]`（`stage/link_id/start/end/bits/rate_bps/served_bits`）。

**B（3 条）** ① 同一 `prop_id`：`propagation_arrival.at == propagation_start.at + delay_s`（1e-9），且
`propagation_start.at ==` 对应 `link_service_windows.end`（1e-9）。② 下一跳 `satellite_ingress.at == propagation_arrival.at`（1e-9），
且该包在下一跳的任何 `queue_enter/service_start ≥ arrival`。③ 传播期间链路可服务下一包：`propagation_start.at` 之后、
`propagation_arrival.at` 之前，同一 link 上已有另一 pid 的 `service_start`（严格不等式）。
通道 `res["packet_events"]`（`propagation_start{link_id,prop_id,delay_s}` / `propagation_arrival{prop_id}` / `satellite_ingress{at}`）。

**C（3 条，第 1 条堵最危险缺口）** ① **绝对传输时间**：timeline 中 `packet_kind=="control"` 的
`service_finish.at − service_start.at == control_bits/link_rate`（1e-9；实测 control 行在 timeline 里，`bits==8000`）。
② **频率两臂**：同 trace/policy/seed，仅 `advertise_interval_s` 1.0→0.25：断言 control 占用的链路服务总时长严格增大，
且某 data 包 `service_start` 严格推迟（同时报 data bits 不变）。③ **非抢占**：control 到达时 data 在服务 ⇒ data 窗口
`dur==bits/rate`（1e-9，不被切短）且 `control.service_start ≥ data.service_finish`（1e-9）。
附：把 on/off 两臂 policy 对齐，消掉 §0.4 混杂。

**D（整条新建，2 组）** ① λ<μ：1 Mbps ISL + 8 Mbit（服务 8 s），每 16 s 发一包、跑 200 s：`max Q(t)` 有界且到达之间回落至 0
（误差 0）、`queue_area_bits_s["isl_data"]/T` 稳定。② λ>μ：每 4 s 发一包（λ=2>μ=1）：t∈[50,200] 上 Q(t) 线性、
`slope==λ−μ`（bits/s，误差<5%）、不再回零；用 area 交叉验证 `Q̄·T`。
通道：`--queue-ledger` 的 `levels.jsonl.gz`（`{t,queue,bits}`；`test_queue_ledger.py` 已有 `_read_levels/_reconstruct` 可复用）。

**E（4 条）** ① 时间链：`generated_at=t_m` → control `queue_enter` → `service_start` → `propagation_*` → `cache.received_at`，
断言 `received_at == generated_at + queue_wait + tx + prop`（1e-9）。② kernel 级不可读：取真实广告，用略早于 `received_at` 的
时刻决策，断言该节点为 `hold/no_info`（timeline `hold`/`frozen_inferred_hold`），而非使用了该广告。
③ TTL **增益版**：`t∈[gen,gen+ttl)` 可读、`t≥gen+ttl` 读不到（把现有"末态 valid==False"升级为"使用被阻止"）。
④ 拥塞臂：同 trace/policy，ISL 1 Gbps→1 Mbps 且制造长 data 服务，断言 `max(received_at)` 严格变大（并给 Δ 与 control 排队位数关系）。
附：`caches[*]["aoi"]` 是 **horizon 处**的值；断言 `AoI(t)=t−gen` 必须用 `t_decision_start − generated_at` 自算。

**F（1 条）** 在 `[t_decision_start, t)` 内安排另一包的网络事件：断言该包至少一个
`service_start/service_finish/propagation_arrival` 落在区间内部（严格不等式）⇒ 计算时间不冻结事件循环。
通道 `res["packet_events"]` + `link_service_windows` 与 `decision_sink` 行对齐。
（frozen/refresh 差异、commit 重验、零延迟等价、pool wait 已被现有测试覆盖，勿重写。）

---

## 5. 方法与限制

- **只读**：未修改本 worktree 任何代码/测试/配置，未 commit/push；只新建本报告一个文件。
- **"会不会红"的方法**：禁止改代码做变异测试，故结论由 (i) 逐断言静态阅读 + (ii) kernel 输出 schema 只读实测
  （`link_service_windows` 无 ctrl 行、`packet_events` 有 `propagation_start/arrival`、timeline 有 control 的
  `service_start/service_finish`、`caches[*]` 只有 6 字段、M3 实测 gap=0.0016）支撑；推断均写明依据，不冒充实测。
- **范围**：深度审计任务指定 9 文件 + `helpers.py`；当且仅当相邻文件实质改变判定时（`test_analytic_scenarios.py` 的 1e-9 恒等式、
  `test_review_round4.py` 的 cache received_at/AoI、`test_time_alignment_online.py` 的 control bits 与 compute FIFO）标注为"相邻文件"，不混入 129 统计。
- **分工**：本报告只回答"测试抓不抓得住"，不回答"源码有没有实现"；A–F 源码证据/行号以 `AUDIT-MECHANISMS.md` 为准。
  两者冲突（源码有、测试无）时以本报告 §4 为 qual-author 清单。
- **不构成研究结论**：129 passed 只说明这 9 文件的现有主张成立，不证明 A–F 已通过资格考试，也不证明任何实验结果有效。
