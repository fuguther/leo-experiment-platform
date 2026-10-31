# AUDIT-MECHANISMS — leo_sim 14 项机制源码审计（只读）

| 字段 | 值 |
| --- | --- |
| 工作树 | `/Users/lge/Desktop/topic/.wt-qual-20261031` |
| 分支 / base | `qual/sim-qual-20261031` / `a90c1f9`（= `night/time-exp-20261031` HEAD） |
| 审计对象 | `CODE/leo_sim/`（`kernel.py` 7961 行、`control.py`、`routing.py`、`config.py`、`time_alignment.py`、`queue_ledger.py`、`decision_ledger.py`、`target_probe.py`、`metrics.py`） |
| 方法 | 逐行读码（行号一律取 **base `a90c1f9`** 的原始文件，核对命令：`git show a90c1f9:CODE/leo_sim/kernel.py`）+ 实跑点名的现有测试（不修改任何文件；不写代码、不改测试、不 commit） |
| 判定口径 | **PASS** = 代码在事件级实现该机制，且有至少一个现有测试真正锁定该机制（不是只看平均时延）；**WEAK** = 代码实现成立，但现有测试的锁定是单点/间接/白盒，换成错误实现仍可能全绿；**GAP** = 代码未实现，或只有文档声称。 |
| 总计数 | **PASS 11 / WEAK 3 / GAP 0**；另有 **base 自带的 6 个测试红点**（§3.1，与本次审计的并发改动无关）与 **4 处文档漂移**（§2） |

> 本报告只给机制证据与测试强度，不含研究结论。**所有行号以 base `a90c1f9` 实查为准**；审计期间本 worktree 同时有 `policy-builder`/`qual-author` 的未提交改动（`kernel.py` 已从 7961 行增至 8044 行），当前工作文件的行号会整体漂移，核对时请用 `git show a90c1f9:<path>`。§5 的实跑结果对应**实跑时刻的工作树**（含并发改动），不是纯 base 树。

---

## 0. 结论先行：一页矩阵

| # | 机制 | 代码证据（file:line，函数） | 测试证据（file::test，断言什么） | 判定 |
| --- | --- | --- | --- | --- |
| 1 | 数据包逐包离散事件 | `kernel.py:171-212` `DataPacket`；`:3326-3382` `_emitter` 每行建一包；`:3345-3346` `_metric_packet_emitted`/`ledger.register`；`fates.py:39-104` | `test_kernel.py::test_conservation_across_mixed_fates`（offered=delivered+loss+in_system，每包恰一 fate）；`test_decision_snapshot.py::test_direct_arm_decision_sink_records_every_hop` | PASS |
| 2 | 每方向 ISL 独占发送资源 | `kernel.py:1723-1735`、`:2597-2600` 每 (sat,dir) 一个 `ISLLink`；`:929` 单一 `current`；`:1060` 单一 `_run`；`:1079-1109` 代际单收发机闸 | `test_dynamic_topology.py::test_continuous_rematch_never_overlaps_transceiver`（每事件步断言同 (sat,dir) 不得两条 `_svc`） | PASS |
| 3 | 有限发送队列 | `kernel.py:963-964` `room()`（`links.isl_queue_bits`）；准入 `:7323`/`:3778`/`:3952`；uplink `:3365-3366`；downlink `:716-717`；holding `:297-306`、`:2024-2043` | `test_kernel.py::test_isl_queue_overflow`（cap=8 Mbit，第 3 包 ISL_QUEUE_OVERFLOW）；`test_kernel.py::test_access_queue_overflow`；`test_holding_queue.py` | PASS |
| 4 | packet_bits/link_rate 真实 serialization | `kernel.py:641`/`:863`/`:1189` `dur = pkt.bits / rate`；MCS `:3091-3106`、`:3191-3192` `end=t0+dur`；`:3287` 占用结算；`:2192-2203` 服务窗口 | `test_analytic_scenarios.py::test_two_hop_forwarding_exact_total_latency`（ISL 项 8e6/1e9=0.008，abs=1e-9）；`test_f2_node_cost.py::test_fixed_node_cost_sweeping_the_phy_rate_keeps_the_node_reading`（200/400/1000 Mbps 三点）；`test_link_budget_integration.py::test_mcs_receipt_records_sampled_rate_range` | PASS |
| 5 | transmission 与 propagation 分离 | `kernel.py:1236-1246` service 返回 ok 后单独 `prop`；`:688-696`、`:896-904` GSL 同构；`:7473/7489/7504` 独立到达进程；链路在传播期间已释放 | `test_analytic_scenarios.py::test_two_hop_forwarding_exact_total_latency`（tx 与 prop 分别 1e-9 入式）；`test_micro_mechanism.py::test_m2`（`windows[1].end == windows[2].start`，传播中仍起下一起服务） | PASS |
| 6 | propagation 用实际链路距离 | `kernel.py:1236-1237` `propagation_delay_s(geometry.isl_range_km(sat,peer,now))`；`:689-690`/`:897-898` `slant_range_km`；`model.py:14,20-21,555` | `test_analytic_scenarios.py` 只锁 1000 km 单点；无 kernel 级距离变化测试（见 §1.6） | **WEAK** |
| 7 | control 与 data 共用真实 ISL 服务资源 | `kernel.py:1025-1054` `put_ctrl` 入同一 `ISLLink`；`:963` 共享 `room()`；`:1132/1167` 同循环；`:1175` service_log 记 ctrl；`:1192` `ctrl_isl_s`；`:3751-3762` v2 线格式计价 | `test_control.py::test_control_nonpreemptive_priority_and_bandwidth`（控制开/关两 run 送达时刻不同）；`test_micro_mechanism.py::test_m3`（data 间隔恰为整数个 0.8 ms 控制服务）；`test_time_alignment_online.py::test_compute_fifo_throttles_two_od_flows_before_fast_shared_isl`（v2 报价 8608+9008 bits，ctrl 服务行存在） | PASS |
| 8 | control 非抢占优先 | `kernel.py:1132` `is_ctrl=bool(ctrl_q)`；`:1167` 先取 ctrl；`:1200-1212` 在服务包只被 retire/horizon 打断，ctrl 到达不 cancel | `test_control.py::test_control_nonpreemptive_priority_and_bandwidth` 只断言 ctrl 序号插在两次 data 之间（该断言在"ctrl 抢占"下同样成立）；`test_micro_mechanism.py::test_m3` 只测插入次数 | **WEAK** |
| 9 | generated_at/received_at/TTL/AoI | `kernel.py:3773` generated_at；`:3896-3897` `mark_received`+TTL；`:415-431` 只许一次/`valid_at`/`aoi`；`control.py:26-54`、`:81-108`；队内 TTL `_expire_waiting` `:1248-1273`；服务中 TTL `:3078-3083,3176-3181` | `test_control.py::test_cache_entry_validity_and_aoi`（单元）；`test_control.py::test_ttl_expiry_blocks_use_of_stale_info`（只断言末尾 cache invalid）；`test_frozen_ledger.py::test_every_neighbour_keeps_its_own_measurement_time`（真实 run：received_at≤t_observed、含 age_s） | **WEAK** |
| 10 | node processing delay | `kernel.py:7430-7471` `_node_process`（uplink ingress `:7483`、每次 ISL 到达 `:7498`）；`:7462-7464` 0 时不动时钟；`:1624-1630` 需 timeline | `test_f2_node_cost.py`：one occupancy per visit（0.05 s）/ ends at decision start / disjoint from frozen stages / e2e 增量恰为节点占用 / zero-cost bit-identical | PASS |
| 11 | routing computation delay | `config.py:262` `compute_delay_s`；`kernel.py:6484-6580` `decide_deferred`（requested `:6499`、timeout `:6547`、commit `:6579`）；有界池 `:1564-1566`、`:6534-6556`；`t_decision_start` `:6080` | `test_compute_delay.py::test_a_positive_delay_separates_start_from_commit`（每行间隔==0.05）；`::test_the_wait_is_contention_and_the_service_is_cost`（commit=start+wait+0.5）；`test_query_service.py`（共享查询服务串行） | PASS |
| 12 | frozen / refresh observation | `kernel.py:6022-6032` refresh 重读并标源；`:6525` frozen 在 timeout 前取观测；`:7014-7113` 只校验合法性；`:6997-7001` `frozen_snapshot_before_compute`；`time_alignment.py:444,500,617`（拒真值/拒未来样本） | `test_frozen_observation.py` 16 项（默认 refresh、refresh 用了期间新信息、frozen 拒绝使用新信息、拒绝动作不再重解、静止窗口两者一致）；`test_time_alignment_online.py::test_the_snapshot_only_contains_already_received_history` | PASS |
| 13 | commit 时合法性重验 | `kernel.py:7295-7324` refresh 在提交刻重算 legal（geometry/rate/room）；`:7050`/`:7061` frozen 用 `_deliver_legal_now`/`_forward_legal_now` 校验原动作；`:7070-7113` 非法→`commit_rejected`+park，绝不重解 | `test_frozen_observation.py::test_frozen_records_a_rejected_action_instead_of_resolving_again`（无 decision 行、commit_rejected 一次、IN_SYSTEM_AT_STOP；refresh 对照无该行）；`test_compute_delay.py::test_a_delayed_decision_still_commits_a_legal_action` | PASS |
| 14 | packet/decision/service/propagation 时间日志 | `kernel.py:1756-1757` packet_events/link_service_windows；`:2127-2263` 七类事件；`:2192-2203` 窗口；`:6075-6109` 决策行；`:1905-1946` `queue_state`；`decision_ledger.py:46-59,236-334`；`metrics.py:43,190`；`queue_ledger.py:79-174`；`target_probe.py:97-367` | `test_queue_ledger.py::test_ledger_on_reconstructs_queue_area_exactly`（1e-6 重建）；`test_decision_ledger.py::test_ledger_rebuilds_the_full_eleven_field_chain`；`test_target_probe.py`；`test_congestion_metrics.py::test_summarize_recomputes_queue_tx_propagation_and_link_utilization` | PASS |

---

## 1. 逐条展开

### 1) 数据包逐包离散事件 — PASS

- **代码证据**：`kernel.py:171-212` `DataPacket`（每包一个对象，含 `pid/bits/deadline/emitted_at/path/decision_id`）；`:3326-3382` `_emitter` 按 trace 行逐条建包、逐条 `yield timeout(0)` 保序；`:3345-3346` `packet_emitted` 事件 + `DataFateLedger.register`；`:7531-7568` `_fail` / `:7525` 送达各写唯一 fate；`:7526` `deliveries[pid]`。
- **行为**：仿真时钟由 SimPy 逐事件推进，每个包每跳都是独立事件；不存在流级/平均级聚合充当物理过程。每包在 `packet_events` 中有自己的 emitted/queue_enter/service_start/propagation_start/propagation_arrival/delivered 序列（`:2127-2263`）。
- **测试证据**：`test_kernel.py::test_conservation_across_mixed_fates` 断言 `offered_bits == delivered+terminal_loss+in_system_at_stop` 且 `sorted(fates)==[1..6]`（每包恰一个 fate）；`test_decision_snapshot.py::test_direct_arm_decision_sink_records_every_hop` 断言每跳一行决策；`test_micro_mechanism.py::_windows_by_pid` 对同 pid 同链路重复服务直接报错。
- **缺口**：无（分片/重传不在本清单要求内，平台也未实现）。

### 2) 每方向 ISL 独占发送资源 — PASS

- **代码证据**：`kernel.py:1723-1735` 与 `:2597-2600` 为每个 `(sat, direction)` 构造一个 `ISLLink`；`:922-931` 每个对象只有一组队列、一个 `current`、一个服务阶段；`:1060-1246` 单一 `_run` 生成器串行服务；`:1079-1109` 同一物理槽位的**代际闸**：任何代际必须等所有更老代际 drain 后才能发（`_retired_isls` + `drained` 事件），`:2281-2298` `_purge_drained_retired` 回收。
- **行为**：一条有向 ISL 上同一时刻至多一个包在 serialization；不同方向（N/E/S/W）可并行，这是设计（每方向独立收发机）。
- **测试证据**：`test_dynamic_topology.py::test_continuous_rematch_never_overlaps_transceiver` 在每次 `env.step()` 后检查所有 live+retired 代际，断言 `len(active)==len(set(active))`（G0→G1→G2 无重叠）；`test_micro_mechanism.py::test_m3` 断言 `isl:0:1` 上 `windows[target].start < windows[2].start < windows[3].start` 且后续包 `start >= 前包 end`。
- **缺口**：只锁"不同时在服务"；未锁"退役代际在服务的剩余 bits + 新代际入队 bits 的合计不越过链路容量"（容量是队列概念，本条不要求）。

### 3) 有限发送队列 — PASS（两处语义注意）

- **代码证据**：容量键 `config.py:139-141`、`:151`、`:159-160`（`isl_rate_mbps/isl_queue_bits`）；`kernel.py:963-964` `ISLLink.room()` 用 `data_bits+ctrl_bits` 对 `cfg_links["isl_queue_bits"]`；data 准入在决策 legality `:7323`，control 准入 `:3778`（本星）与 `:3952`（中继），joint plan 预校验 `:2966-2970`；uplink 有限 `:3365-3366`（ACCESS_QUEUE_OVERFLOW）；downlink `:716-717`；holding `:297-306`、`:2024-2043`。
- **行为**：五类队列（uplink / downlink / holding / isl_data / isl_ctrl）都以 bit 为单位有限；ISL 的 data 与 control **共用同一个容量**，但分两个 `QueueArea` 分别记账（`:926-927`）。
- **测试证据**：`test_kernel.py::test_isl_queue_overflow`（`isl_queue_bits=8_000_000`，三包 8 Mbit：前两包交付、第三包 `ISL_QUEUE_OVERFLOW`）；`test_kernel.py::test_access_queue_overflow`；`test_holding_queue.py`（holding 容量与上限 fate）；`test_queue_ledger.py::test_ledger_on_reconstructs_queue_area_exactly` 从 `queue_state` 重建面积（说明"等待 bits"是一个可核算的量）。
- **缺口**：a) `put_data`/`put_ctrl`（`:994`/`:1025`）自身不校验 `room()`，容量由调用方在准入处保证（`_decide`、`_advertise`、`validate_joint_plan` 都检查，但目前是"约定"而非对象不变量）；b) `room()` 只计等待中的 bits，在服务包不在分母内，故链路上限 = cap + 一个在服务包；c) 无测试锁定"data+ctrl 共用同一容量上限"；d) 精确 Q(t) 的 `queue_state` 通道目前无绿测（§3.1 R1）。

### 4) packet_bits / link_rate 真实 serialization — PASS

- **代码证据**：常量速率 `dur = pkt.bits / k.ul_rate_bps`（`:641`）、`/ dl_rate_bps`（`:863`）、`/ isl_rate_bps`（`:1189`）；MCS 在 `_transmit` 内按服务开始时刻的距离采样 `rate` 再 `dur = pkt.bits / rate`（`:3091-3106`），完成时刻 `end = t0 + dur`（`:3191-3192`），到点才返回 `ok`（`:3302-3304`）；占用 `occupied[occ_key] += now - t0`（`:3287`）；`link_service_windows` 写 start/end/rate_bps/capacity_bits/served_bits/outcome（`:2192-2203`）。速率来源 `config.py:510-511`（`isl_rate_mbps=1000`）+`:1634-1636`。
- **行为**：serialization 是真实服务窗口，不是"每包固定时延"或"每跳固定开销"；包长来自 trace `bits`（`helpers.row` 默认 8 Mbit；`config.py:443` `demand.packet_bits`）。
- **测试证据**：`test_analytic_scenarios.py::test_two_hop_forwarding_exact_total_latency`（ISL 项 `8e6/1e9 = 0.008`，`abs=1e-9`，与独立光速常量一起入式）；`test_f2_node_cost.py::test_fixed_node_cost_sweeping_the_phy_rate_keeps_the_node_reading`（200/400/1000 Mbps 三点，ISL tx 分别 `8e6/(R*1e6)`）；`test_micro_mechanism.py::test_m2`（`DATA_BITS/ISL_RATE_BPS == 0.8`，窗口 3.01→3.81）；`test_link_budget_integration.py::test_mcs_receipt_records_sampled_rate_range`（`occupied["isl_s"] == bits/mcs_rate_bps(range)`）。
- **缺口**：MCS 速率在服务开始采样后固定到结束（`:3106`→`:3191`），服务中的速率变化不重采样；中途失败只结算已占用时间、`served_bits=0`（`:2201`），不模拟"部分 bits 已送达"。恒定速率模型不受影响。

### 5) transmission 与 propagation 分离 — PASS

- **代码证据**：`ISLLink._run` 等 `_transmit` 返回 `ok`（`:1200-1233`）后才算 `prop = propagation_delay_s(isl_range_km(sat,peer,now))`（`:1236-1237`），随后 `env.process(_isl_arrive_after_prop(...))`（`:1246`）；uplink `:688-696`、downlink `:896-904` 同构；传播由独立 `env.timeout(prop)` 进程承担（`:7473`/`:7489`/`:7504`）；链路在下一次循环只检查 `ctrl_q/data_q/current`（`:1064`），不检查在飞包，因此传播不占用发送资源。
- **行为**：`t_arrival = t_service_finish + prop`；发送资源在 service finish 即释放，下一包可在前包传播期间开始。
- **测试证据**：`test_analytic_scenarios.py::test_two_hop_forwarding_exact_total_latency` 把 ul/isl/dl 的 service 与 prop 分别以 `1e-9` 精度加和；`test_micro_mechanism.py::test_m2` 断言 `windows[1]["end"] == windows[2]["start"]`（3.81）且 pid2 已在 3.06 入队、3.81 才开始服务——传播在 3.8133，下一包同时开始发；`test_congestion_metrics.py::test_summarize_recomputes_queue_tx_propagation_and_link_utilization` 独立重建 `tx_s=0.25`、`prop_s=0.25`；`metrics.py:190` 还强制 `propagation_arrival - propagation_start == delay_s`。
- **缺口**：无。注意 prop 用"服务完成时刻"的几何（`:1236` 的 `now`），不是入队时刻；这是已记录的取值口径，不是缺陷。

### 6) propagation 使用实际链路距离 — **WEAK**

- **代码证据**：ISL `kernel.py:1236-1237`：`prop = model.propagation_delay_s(k.geometry.isl_range_km(self.sat, self.peer, now))`；GSL `:689-690`/`:897-898` 用 `slant_range_km`；`model.py:20-21` `propagation_delay_s(d)=d/C_KM_S`，`model.py:14` `C_KM_S=299_792.458`；真实星座 `model.py:555` `math.dist(ecef(a,t), ecef(b,t))`（WGS84 ECEF 三维距离）。
- **行为**：传播时延是每条链路、每个时刻的实际距离除以光速，不是固定常数。
- **测试证据**：`test_analytic_scenarios.py::test_two_hop_forwarding_exact_total_latency` 用 `PROP_ISL = 1000.0/LIGHT_SPEED_KM_S`（独立字面常量）以 `1e-9` 锁住 1000 km 单点；`test_model.py::test_slant_and_propagation_positive` 只证正数；`test_acceptance_review.py:224` 的 `isl_range_fn` 只服务路由距离，`test_link_budget_integration.py` 的 `isl_range_fn` 只服务 MCS 速率/服务时长。
- **缺口（WEAK 原因）**：**没有任何 kernel 级测试改变链路距离并断言 prop 随之改变**（`StaticGeometry` 默认所有 ISL 恒为 `isl_km=1000`，见 `helpers.py:90-117`）。把 `:1237` 改成固定 1000 km 或固定常数，上述测试仍会全绿。建议补：两条链路取 1000 km 与 5000 km 的一次 run，断言两段 `propagation_start.delay_s` 之差 == (5000−1000)/C（≥1e-9 精度），并断言 `propagation_arrival - propagation_start` 等于该值。

### 7) control 与 data 共用真实 ISL 服务资源 — PASS

- **代码证据**：control 包由 `_advertise` 生成并 `link.put_ctrl(pkt)`（`:3648-3782`，`:3751-3762` 把 `base_packet_bits + 资源遥测 bits` 作为真实 `bits`，v2 线格式另存 `wire_cost`）；`put_ctrl` 进同一 `ISLLink.ctrl_q` 并累加 `ctrl_bits`（`:1025-1054`）；`_run` 用同一个 `_transmit`（`:1200-1203`）与同一可用性/速率检查（`:1111-1190`）；`service_log["isl"]` 记 `("ctrl", iid)`（`:1175`）；占用分账 `occupied["ctrl_isl_s"]`（`:1192`）。
- **行为**：控制包真实占用 ISL 带宽：按自身 bits 序列化、受几何/GE/速率/TTL 约束、在 `room()` 里与 data 抢同一容量；中继时 bits 不变（`:3940-3943`）。
- **测试证据**：`test_control.py::test_control_nonpreemptive_priority_and_bandwidth` 用控制开/关两条 run 断言 `deliveries[2]` 因控制流而推后；`test_micro_mechanism.py::test_m3` 断言相邻 data 服务之间的空档恰为整数个 `CTRL_SERVICE_S = 8000/10Mbps = 0.8 ms`；`test_time_alignment_online.py::test_compute_fifo_throttles_two_od_flows_before_fast_shared_isl` 断言控制 `queue_enter`/`service_start` 行存在且 v2 报价 8608+9008 bits。
- **缺口**：控制服务**没有**写入 `link_service_windows`（`_transmit` 对 `ControlPacket` 只写 timeline `service_start/service_finish`，`:3227-3251`），因此 `metrics.summarize`/`congestion_metrics` 的利用率与 tx 分解不覆盖控制面；无测试锁定 data+ctrl 共用容量的分配顺序；控制队列电平只在 `queue_state` 行（无绿测，§3.1 R1）与 `queue_ledger` 的 `isl_ctrl:*` 里。

### 8) control 非抢占优先 — **WEAK**

- **代码证据**：`_run` 每轮 `is_ctrl = bool(self.ctrl_q)`（`:1132`），有排队 ctrl 就先发 ctrl、再考虑 data（`:1167`）；在服务包通过 `yield k.env.process(k._transmit(...))`（`:1200`）独占，直到返回才 `current=None`（`:1207`）；ctrl 到达只 `_poke(wake)`（`:1054`），不会 cancel/中断进行中的 `_transmit`，`_transmit` 的打断源只有 retire interrupt（`:3086`、`:3154`、`:3283`）。控制面优先级只有一个模式且 fail-loud（`config.py:1033-1034`）。
- **行为**：排队中的 control 绝对优先于排队中的 data（严格优先级，无 aging/权重），在服务包永不被 control 抢占。
- **测试证据**：`test_control.py::test_control_nonpreemptive_priority_and_bandwidth` 只断言 `service_log["isl"]` 中某个 ctrl 的序号落在两次 data 之间（`any(d[0] < ci < d[1])`）——若 ctrl 真的抢占在服务 data，data 会重新入队并在 ctrl 之后再次 service_start，序号仍是 `data, ctrl, data`，**该断言照样成立**；`test_micro_mechanism.py::test_m3` 测的是"data 之间插入了几个控制服务"，不测被插队 data 窗口的完整性。
- **缺口（WEAK 原因）**：现有测试**无法区分"非抢占优先"与"抢占优先"**。建议补：让一个 ctrl 包恰好在一个 data 服务窗口内到达，断言该 data 的 `link_service_windows` 窗口 `end-start == bits/isl_rate` 且 ctrl 的 `service_start.at >= data.end`；再补一条"ctrl 连续突发时 data 仍按 FIFO 被服务、不丢包"。

### 9) generated_at / received_at / TTL / AoI — **WEAK**

- **代码证据**：`ControlPacket.__init__` 校验并保存 `generated_at/ttl_s/remaining_hops/payload_bits`（`kernel.py:381-404`）；`mark_received` 只允许一次且 `>= generated_at`（`:415-422`）；`valid_at(t)` 为 `generated_at <= t <= generated_at+ttl`（`:424-425`）；`aoi(t)=t-generated_at`（`:427-431`）。生成侧 `generated_at=self.env.now`（`:3773`）；到达侧先 `mark_received(now)` 再判 TTL（`:3893-3904`），进 `CacheEntry`（`:3926-3928`）；`CacheEntry` 自身再校验一次（`control.py:26-42`），`valid_at` 要求 `received_at <= now`（`:44-51`），`aoi`（`:53-54`）；缓存只保留最新（`:81-89`）。TTL 在**队列内**丢弃（`_expire_waiting` `:1248-1273`，`CONTROL_EXPIRED`）、**服务中**到期失败（`:3078-3083`、`:3176-3181`）、**到达时**过期（`:3897-3904`）。决策行按邻居记录 `generated_at/received_at/age_s/hops`（`:4487-4489`）。
- **行为**：状态广告具备完整时间链：生成→排队→序列化→传播→到达→缓存；TTL 三个位置都强制；AoI 从生成时刻计；"未来到达"在 `valid_at` 上结构性不可读。
- **测试证据**：`test_control.py::test_cache_entry_validity_and_aoi`（单元：10.5 有效、12.1 过期、AoI=t−gen）；`test_control.py::test_ttl_expiry_blocks_use_of_stale_info` 只断言 horizon 末尾 `caches[0][2]["valid"] is False`；`test_frozen_ledger.py::test_every_neighbour_keeps_its_own_measurement_time` 在真实 run 中断言每邻居 `received_at <= t_observed`、`generated_at <= received_at`、`age_s` 字段存在；`test_time_alignment_online.py::test_the_snapshot_only_contains_already_received_history`。
- **缺口（WEAK 原因）**：a) **队列内/服务中 TTL 到期（`CONTROL_EXPIRED`）在真实 run 里没有测试**——`test_fates_outage.py` 只是手工调 ledger，不是 kernel 路径；b) 没有测试断言"某条广告过期后，决策不得再使用它"（现有 TTL 测试停留在 cache 标志位）；c) 信息边界有书面例外：`oracle`/`known_serving` 读 `_serving_sats()` 真值（`:4159-4182`、`:7232-7234`），与 `control.py:1-8` 的"ONLY source of remote state"字面冲突——凡用 oracle 的对照必须按 `routing.py:35` / `kernel.py:7818` 的 `analysis_upper_bound` 标签排除在控制面证据之外。

### 10) node processing delay — PASS

- **代码证据**：`kernel.py:7430-7471` `_node_process`：`delay = self.node_process_delay_s`（`:7462`），`<=0` 直接返回不碰时钟（`:7463-7464`），否则写 `node_process_start`（`:7466-7467`）→ `timeout(delay)`（`:7468`）→ `node_process_end`（`:7469-7471`）；调用点仅 uplink ingress（`:7483`）与每次 ISL 到达（`:7498`）；目的地 terminus（`_deliver_after_prop` `:7504-7528`）不做；控制面不做。配置 `config.py:276`（schema）、`:584`（默认 0）；`node_process_delay_s>0` 无 timeline sink 直接 fail（`kernel.py:1624-1630`，`__main__.py:460-467`）。
- **行为**：一次卫星访问 = 一次 receive/process/schedule 占用；与 serialization 无关（不随 bits 缩放），与 compute delay 不重叠（node_process_end == t_decision_start）。
- **测试证据**：`test_f2_node_cost.py::test_the_node_stage_records_one_occupancy_per_satellite_visit`（3 包 × 2 访问 × 0.05 s，逐条 ±DUR_TOL）；`::test_the_node_stage_ends_exactly_where_the_decision_stage_starts`；`::test_the_node_stage_is_disjoint_from_every_frozen_stage`；`::test_end_to_end_delay_grows_by_exactly_the_recorded_node_cost`（无竞争装置上 e2e 增量 == 记录的 node 占用）；`::test_f2_delay_is_not_decision_compute`；`::test_a_zero_cost_matches_the_unset_default_bit_for_bit`。
- **缺口**：节点处理是**纯延迟**，不是有排队/争用的服务资源（没有 node server pool，多个包不会在该阶段互相排队）；F2 作用域刻意排除控制面与目的地（`:7452-7457`，`ANALYSIS/CURRENT-EVENT-TIMELINE.md:117`）；有竞争时 e2e 增量可能被既有 queue/holding 等待吸收，不得声明"e2e 增量恰为 包数×访问数×时延"（`ANALYSIS/CURRENT-EVENT-TIMELINE.md:171-180`）。

### 11) routing computation delay — PASS

- **代码证据**：`config.py:262` `execution.compute_delay_s`（注释明写 "how long one routing decision takes to compute"，默认 0）；`kernel.py:6467-6482` `_deferred_enabled`；`:6484-6580` `decide_deferred`：`requested = env.now`（`:6499`）→（frozen 时先观测 `:6525`）→ 有界计算池请求/等待/服务（`:6534-6556`）→ `yield timeout(compute_delay_s)`（`:6547`/`:6563`）→ commit（`:6579`）；`compute_servers_per_satellite` 单星 N 服务器（`:1564-1566`，`config.py:286`）；决策行保留 `t_decision_start`（`:6080-6082`）；表查模式不付逐包计算（`:6503-6523`、`:6650-6663`）。
- **行为**：路由决策可消耗仿真时间（opt-in），并可在有界每星计算服务器上排队；wait（争用）与 service（成本）分开记录。
- **测试证据**：`test_compute_delay.py::test_a_positive_delay_separates_start_from_commit`（每行 `t - t_decision_start == 0.05`）；`::test_a_bound_makes_decisions_wait_and_the_wait_is_recorded`（三包一服务器出现 wait>0）；`::test_the_wait_is_contention_and_the_service_is_cost`（`commit = start + wait + 0.5` 逐行核对）；`::test_the_ledger_reports_the_computation_interval`；`::test_zero_delay_is_identical_to_the_unset_default`；`::test_more_servers_can_only_reduce_the_wait`；`test_query_service.py` 断言同一卫星查询服务不重叠。
- **缺口**：默认 `compute_delay_s=0`，历史/默认 run 的路由计算是**瞬时**的；"拓扑重算/路由表构建"本身不耗仿真时间（`_recompute_topology` `:2543` 无 timeout；`_precompute_build` `:4717-4768` 只记录 host wall time 并明说 `NOT a per-packet compute charge`）。A–F 中若要"路由计算延迟"，必须显式配置 `compute_delay_s>0`（或 `time_alignment.query_delay_s>0`），否则该机制在事件上不可观测。

### 12) frozen / refresh observation — PASS

- **代码证据**：默认 refresh：`_record_decision` 在提交时刻重读缓存与队列并标 `obs_mode="refresh"`、`source="commit_time_state"`（`:6019-6034`）；frozen：`decide_deferred` 在计算超时**之前**取 `_observe_preferred_action`（`:6525`，内部 `:6936-7012`），提交半段 `_decide_from_frozen_observation` 只做合法性检查、拒绝即 park（`:7014-7113`）；观测记录自带 `mode/source/t_observed`（`:6997-7001`、`:4530-4546`）。时间对齐侧：`time_alignment.make_snapshot`（`:500-611`）只接收 kernel 显式字段，`reject_future_input`（`:444-470`）拒绝 TruthSample/BranchOutcome/kernel 句柄，`order_history`（`:617-634`）丢弃 `received_at > snapshot_at` 的样本。零成本 frozen 允许（`config.py:1214-1226`）。
- **行为**：frozen = 在计算开始冻结观测、提交只校验合法性、绝不重解；refresh = 在计算落地时重读状态。零延迟时两者数值相同但语义标签不同。
- **测试证据**：`test_frozen_observation.py` 共 14 个 test 函数，含 `::test_the_default_mode_is_refresh`、`::test_refresh_uses_information_that_did_not_exist_when_it_started`（refresh 在 flip 后改选 E）、`::test_frozen_refuses_to_use_information_that_appeared_during_compute`（观测 5.082 / 提交 7.082，仍按旧观测 hold）、`::test_frozen_records_a_rejected_action_instead_of_resolving_again`、`::test_frozen_agrees_with_refresh_when_nothing_changes_in_the_window`、`::test_frozen_refusals_are_visible_only_through_the_timeline_sink`（无 sink 时行为不变）、`::test_the_mode_is_part_of_the_config_identity`；`test_time_alignment_online.py::test_the_snapshot_only_contains_already_received_history`、`::test_all_four_arms_see_the_same_t0_input`。
- **缺口**：frozen 与 learning 互斥（`test_frozen_observation.py::test_frozen_may_not_be_combined_with_a_learning_arm`），frozen 只在 `compute_delay_s>0`/`query_delay_s>0` 或零成本诊断路径存在；**零成本 frozen 是同刻冻结，不产生真实陈旧**，资格测试不能靠它在零延迟下证明 stale。

### 13) decision commit 时合法性重验 — PASS

- **代码证据**：refresh 路径在提交刻重建候选并重算 legal：候选 `routing.choose_next_hop`（`:7227-7246`）→ loop 过滤（`:7282-7283`）→ TA 排序（`:7288-7290`）→ 逐方向检查 geometry / MCS rate / `link.room`（`:7295-7324`）→ `put_data`（`:7352`）；frozen 路径在提交刻只校验原动作：`_deliver_legal_now`（`:6694-6706`）与 `_forward_legal_now`（`:6708-6724`），非法即 `commit_rejected` + park（`:7070-7113`），注释明确"绝不重解"（`:7018-7027`）。deliver 分支同样在提交刻检查（`:7151-7152`、`:7204-7208`）。
- **行为**：任何造成时间推进的机制（compute、query）之后，提交动作都重新对着提交时刻的状态验合法；frozen 下非法就拒绝并放弃该次计算，不替换成最优。
- **测试证据**：`test_frozen_observation.py::test_frozen_records_a_rejected_action_instead_of_resolving_again`（t0 合法 E、t1 非法：decision sink 无该行、timeline 恰一条 `commit_rejected`、`reason=action_no_longer_legal`、包 `IN_SYSTEM_AT_STOP`；refresh 对照无 `commit_rejected`）；`::test_frozen_accepts_a_forced_action_taken_from_the_observation_legal_set`；`test_compute_delay.py::test_a_delayed_decision_still_commits_a_legal_action`（`chosen in candidates`）。
- **缺口**：refresh 的"重验"与"重新求解"在语义上重合，因此只有 frozen 能区分"拒绝 vs 替换"；`_forward_legal_now` 与 `_decide` 内的 legal 过滤是两段代码（语义相同、非同一函数），只能靠测试防漂移。

### 14) packet / decision / service / propagation 时间日志 — PASS

- **代码证据**：`kernel.py:1756-1757` 创建 `packet_events` 与 `link_service_windows`；`:2127-2263` 七类包事件（emitted / queue_enter / satellite_ingress / service_start / propagation_start / propagation_arrival / delivered），每类带 `at`；`:2192-2203` 服务窗口含 start/end/rate_bps/capacity_bits/served_bits/outcome；`:1751` `service_log`（uplink/downlink/isl/uplink_bits）；`:6075-6109` 决策行（20 键，含 `t/t_decision_start/decision_id/hop_index/observation_at_start/truth_at_commit/four_direction_audit`）；`:2079-2125` timeline 里程碑与 `_timeline_ctrl`；`:1905-1946` `queue_state`（每次 ISL 转变后的精确 queued_data/control bits、in-service kind/phase、generation；落盘 `:1946`）；`decision_ledger.py:46-59` 十一时刻、`:236-334` 折叠；`metrics.py:43-218` 独立重算；`queue_ledger.py:79-174`；`target_probe.py:97-367`。
- **行为**：四类时间（包/决策/服务/传播）都有原始、可重算的日志；`metrics.summarize` 从原始事件重建 queue_wait/tx/prop/利用率并做一致性校验（例如 `metrics.py:190` 强制 realized prop == declared delay；`:218` 拒绝未知事件 kind）。
- **测试证据**：`test_queue_ledger.py::test_ledger_on_reconstructs_queue_area_exactly`（level 序列重建 `queue_area_bits_s`，逐 stage <1e-6；off 时字段与 digest 不变）；`test_decision_ledger.py::test_ledger_rebuilds_the_full_eleven_field_chain`（11 字段齐全、四段顺序、peer 侧字段来自 successor）；`::test_ledger_marks_missing_instead_of_filling_zero`；`test_target_probe.py::test_probe_records_one_row_per_candidate_with_a_reason_or_a_truth`；`test_congestion_metrics.py::test_summarize_recomputes_queue_tx_propagation_and_link_utilization`；`test_f2_node_cost.py::test_the_mirror_agrees_with_the_second_implementation`（第二实现闭合 1e-9）。
- **缺口**：a) 控制面服务时间只进 timeline（`packet_kind=control`），不进 `link_service_windows`/`congestion_metrics`；b) `queue_ledger`/`target_probe` 只能由 `experiment_platform/t1_tasks.py`（`:1481`、`:1493`；CLI `:2601`、`:2619`）传入，`leo_sim run` 的 CLI 只有 `--decision-log/--timeline-log`（`__main__.py:772-789`），远端 v2 正式运行也只带这两条流（`remote_job.py:332-333`）——Q(t) 级证据需要另走 t1_tasks 或库调用；c) `queue_state` 行的唯一单测审计时是红的（base `a90c1f9` 原始树复验仍红；工作树已有未提交修复，见 §3.1 R1）。

---

## 2. 文档声称 vs 代码（文档漂移，按当前 HEAD 实查）

1. `ANALYSIS/CURRENT-EVENT-TIMELINE.md` 以 `549acd8` 的 4615 行 kernel 为基线，文件里所有行号（如 `_node_process` 4261、`_transmit` 2417）在 `a90c1f9` 的 7961 行 kernel 上全部失效（现为 7430、3042）。该文件自己声明"以代码为准"，但仍被当作事实基线引用，**照行号 grep 无法定位**。
2. 同文件 §5.1 "残留 B7：remote runner 无 timeline passthrough"（`:151`）在 `a90c1f9` **已不成立**：`CODE/scripts/remote/remote_job.py:332-333` 的 v2 `formal_command` 恒带 `--decision-log` 与 `--timeline-log`，`pull-results-remote.sh:296-297` 也回传 timeline。
3. `lines/TIME-SEMANTICS.md:80` "`frozen` 要求 `compute_delay > 0`" 与 `config.py:1214-1226` 冲突：代码明写 **zero-cost frozen is ALLOWED**（并有 `test_frozen_observation.py::test_zero_cost_frozen_is_allowed_and_is_a_real_frozen_run` 锁定）。
4. `control.py:1-8` "control packets … are the ONLY source of remote state" 有书面例外：`oracle`/`known_serving` 用 `_serving_sats()` 真值（`kernel.py:4159-4182`、`:7232-7234`）。凡引用该句的对照必须显式排除 oracle 臂（标签见 `routing.py:35`、`kernel.py:7818`）。

---

## 3. 现实风险：红点与可达性

### 3.1 base a90c1f9 自带的测试红点（6 个，与并发改动无关）

**验证方式**：`git archive a90c1f9 | tar -x -C /tmp/mech-audit-base` 后在 pristine 树运行同一组测试，失败集合与当前工作树**完全相同**（纯净树 3 文件：6 failed, 52 passed）。因此这些不是 `policy-builder`/`qual-author` 未提交改动造成的；本机 Python 3.14.2。

| # | 测试 | 报错 | 与 14 条机制的关系 |
| --- | --- | --- | --- |
| R1 | `test_four_direction_audit.py::test_queue_state_trace_contains_exact_resource_counts` | `AttributeError: 'Kernel' object has no attribute 'resource_journal'`：测试用 `Kernel.__new__` 只设 `timeline_sink/env`，而 `_record_isl_queue_state` 在 base `:1921`/`:1946` 访问 `self.resource_journal`（真实 run 由 `__init__` `:1517` 设 `None`，故真实仿真不受影响） | **直接相关**：#3/#7/#14 的 `queue_state` Q(t) 通道**审计时无绿测**。审计收尾时该文件已被另一 teammate 在工作树补上 `kernel.resource_journal = None`（未提交，现 2 passed）；**base `a90c1f9` 原始树仍红**（已用 pristine 树复验） |
| R2–R5 | `test_receipt.py::test_frozen_v2_receipt_still_verifies_under_new_code`、`::test_frozen_v2_receipt_fails_when_trace_determining_field_tampered`、`::test_v5_receipt_rejects_unknown_trace_identity_contract`、`::test_v5_receipt_never_guesses_from_manifest_schema_or_code_version` | 同一根因：`config.trace_identity_sha256_v2()` 现在算得 `c1ff30ba…`，与冻结常量 `2715dfb3…` 不符 | 不属 14 条机制；属 trace/receipt 身份链回归（night 线 config 变更后冻结常量未更新） |
| R6 | `test_trace.py::test_population_gravity_profile_trace_bytes_regression` | 同 R2–R5：trace 字节 sha 断言通过，`trace_identity_sha256_v2` 冻结常量断言失败 | 不属 14 条机制；同上 |

**对审计结论的影响**：审计时 R1 使 `queue_state` 通道只能记为"代码存在、无绿测"（§1.3/§1.7/§1.14 已注明）；审计收尾时 R1 已由另一 teammate 在工作树修复（未提交），但 **base `a90c1f9` 仍为红**，且 R2–R6 仍未处理——**"现有测试全绿"不是事实**。任何以"测试全绿"为前提的资格/发布流程必须先处理这 6 个红点（或 R2–R6 的冻结常量）或显式豁免；修测试属于资格测试作者/Lead 的范围，本审计不改测试。

### 3.2 证据可达性

- 每次 `leo_sim run`（`__main__.py:772-789`）可产出 decision + timeline（receipt v6 绑定哈希）；`node_process_delay_s>0` 强制要求 timeline（`:460-467`）。
- `queue_ledger` / `target_probe` / `resource_journal` 不进 `leo_sim run` CLI，只能经 `t1_tasks.py`（`:1481`、`:1493`，CLI `:2601`、`:2619`）或库调用传入；VM 正式 v2 链路只带 decision/timeline（`remote_job.py:332-333`）。→ 若要 Q(t) 重建或同 run 真值探针，必须走 t1_tasks 路径并单独固定 run-id/回执。

---

## 4. 对资格考试 A–F 的工程含义（不写研究结论）

- **出厂默认关闭的机制**：`execution.compute_delay_s=0`（默认瞬时决策）、`node_process_delay_s=0`、`decision_observation_mode="refresh"`、`control_plane` 默认 enabled 但 `make_cfg`（测试 helper）显式关闭、`time_alignment.enabled=false`。A–F 若要在事件上观测这些机制，必须显式配置，否则"机制在跑"只是代码存在。
- **默认开启**：ISL/access 的有限队列、有限速率 serialization、tx/prop 分离、control 与 data 共用 ISL 服务与非抢占优先、TTL/AoI 时间链、逐包事件、packet_events/windows/decision/timeline 日志。
- **最强现有锁定**（可直接引用，无需补测）：#1、#2、#4、#5、#10、#11、#12、#13、#14 的事件级测试；#3 的 ISL/access 溢出测试；#7 的"控制开/关 + 控制服务插入整数倍"测试。
- **必须补的最小锁定**（按风险排序见 §5），否则资格测试可能"全绿但没测到"：
  1. #6：两个不同链路距离的 run，断言 prop 差 = Δd/C（当前只锁 1000 km 单点）。
  2. #8：ctrl 在 data 服务窗口内到达，断言 data 窗口长度仍 = bits/rate 且 ctrl 起于 data 结束之后（当前断言在抢占实现下也会通过）。
  3. #9：TTL 在队列内/服务中到期的真实 run（`CONTROL_EXPIRED`）+ 过期后决策不得使用该广告（当前只有 cache 标志位测试）。
- **必须与 oracle 臂隔离**：凡引用"控制面是唯一远端信息来源"的证据，必须排除 `oracle`/`known_serving`（§2.4）。

---

## 5. 复现方式（本审计实跑过的命令）

```bash
cd /Users/lge/Desktop/topic/.wt-qual-20261031

# 6 个核心机制文件：54 passed（2026-10-31，实跑时刻工作树含并发改动）
python3 -m pytest CODE/leo_sim/tests/test_control.py \
  CODE/leo_sim/tests/test_micro_mechanism.py \
  CODE/leo_sim/tests/test_compute_delay.py \
  CODE/leo_sim/tests/test_frozen_observation.py \
  CODE/leo_sim/tests/test_analytic_scenarios.py \
  CODE/leo_sim/tests/test_queue_ledger.py -q

# 13 个文件：177 passed, 1 failed（R1，见 §3.1；该时点 R1 未修复）
python3 -m pytest CODE/leo_sim/tests/test_kernel.py \
  CODE/leo_sim/tests/test_f2_node_cost.py \
  CODE/leo_sim/tests/test_decision_ledger.py \
  CODE/leo_sim/tests/test_dynamic_topology.py \
  CODE/leo_sim/tests/test_time_alignment_online.py \
  CODE/leo_sim/tests/test_query_service.py \
  CODE/leo_sim/tests/test_four_direction_audit.py \
  CODE/leo_sim/tests/test_frozen_ledger.py \
  CODE/leo_sim/tests/test_transmit_retirement.py \
  CODE/leo_sim/tests/test_congestion_metrics.py \
  CODE/leo_sim/tests/test_target_probe.py \
  CODE/leo_sim/tests/test_acceptance_review.py \
  CODE/leo_sim/tests/test_model.py -q

# 全量 leo_sim 测试（实跑工作树，2026-10-31 21:16 起，210 s；此时 R1 尚未修复）：
#   6 failed / 1180 passed / 2 skipped —— 6 个失败与下面纯净树完全同一集合
python3 -m pytest CODE/leo_sim/tests -q

# 纯净 base 树复验（证明 6 个红点是 base a90c1f9 自带，不是并发改动）：
#   rm -rf /tmp/mech-audit-base && mkdir -p /tmp/mech-audit-base
#   git archive a90c1f9 | tar -x -C /tmp/mech-audit-base
#   cd /tmp/mech-audit-base && python3 -m pytest CODE/leo_sim/tests -q
#   => 6 failed / 1165 passed / 2 skipped in 276 s（失败集合与工作树逐项相同）
```

**关键源码入口**：`kernel.py`（`ISLLink:907`、`_transmit:3042`、`_decide:7115`、`_decide_from_frozen_observation:7014`、`decide_deferred:6484`、`_record_decision:5971`、`_node_process:7430`、`run:7571`、`run_simulation:7887`）；`control.py`；`routing.py:171` `destinations_in_cache`、`:185` `choose_next_hop`；`config.py:1205-1245`；`time_alignment.py:500/617`；`queue_ledger.py:79`；`decision_ledger.py:236`；`target_probe.py:97`；`metrics.py:43`。
