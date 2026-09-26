# 01 · 正式模型（时间轴 / 队列与服务语义 / estimand / 反事实定义）

**时间**：2026-09-25 00:47+08:00
**身份**：commit 98c858f 冻结快照（code_sha256 2a114890…）；本文件的每条语义均在 E1 校验中经实测确认
**用途**：E3 的评分器、注入组与估计量都建立在本文件的语义之上；与 00e（字段/时间/信息权限表）配套阅读

---

## 1. 时间轴

### 1.1 十一个时刻（内核实测枚举，decision_ledger.TIMELINE_FIELDS）

t_measure, t_control_rx, t_decision_start, t_decision_commit, t_local_queue_enter,
t_service_start, t_service_finish, t_peer_arrival, t_peer_redecision,
t_peer_target_egress_enter, t_peer_target_egress_service_start

三者相等时（t_measure = t_decision_start = t_decision_commit）**仍分别记录、不折叠**——否则计算时延落地后无法回填语义。

### 1.2 与任务书四量的映射

| 任务书 | 内核 | 含义 |
|---|---|---|
| t_q | t_local_queue_enter | 包进入本地队列的时刻 |
| t_s | t_service_start | 本跳服务开始 |
| t_f | t_service_finish | 本跳服务完成 |
| t_a | t_peer_arrival | 到达对端（对端入队） |

### 1.3 已实测确认的时序关系

| 关系 | 实测 | 来源 |
|---|---|---|
| t_service_finish − t_service_start = packet_bits / 链路速率 | ISL 0.1 s、接入 0.001 s；**最大误差 2.36e-14 s** | E1-1 |
| t_peer_arrival − t_service_finish = 距离 / c | ISL 弦长相对误差 **1.50e-08**；GSL 全在 [550, 2205] km | E1-2/E1-3 |
| 单调性（十一时刻按序不减） | 7,051 条可判定，**违例 0** | E1-5 |

### 1.4 观测模式语义

| 模式 | 冻结条件 | 实测 |
|---|---|---|
| refresh（默认） | 提交时刻重新观测 | t_measure == t_decision_commit（10,579 条，违例 0） |
| frozen | 观测冻结于决策开始，提交只做合法性校验 | t_measure == t_decision_start（10,576 条，违例 0） |

---

## 2. 队列与服务语义

- **数据面**：FIFO；**控制包非抢占优先**（控制优先占用可测，且不抢占正在服务的数据包）。
- **队列类别**：uplink / downlink / holding（星上重决策停泊区）/ isl_data；按 queue_id 逐次配对，**不得把四类等待相加两次**（R8 已闭合）。
- **服务窗口**：每跳一次 service_start → service_finish；被中断时会重写 service_finish（因此账本的"首条"语义有陷阱，E1 已加 outcome 过滤防线）。
- **holding 驻留**：holding 无 service_start，其驻留与"下一次入队"配对；停泊到地平线的包**不虚构退出时刻**（R7 已闭合）。
- **利用率分母**：必须用 execution.available_capacity_interval_s 采样出的**可用容量窗口**；否则恒速服务的利用率会读出 ~1.0（该配置陷阱已被记录在 T1-PRESSURE-WINDOW.md）。
- **F2（节点处理时延）与计算时延是**不相交区间**（内核注释与测试双重保证）；但相位分解必须显式传 node_spans_by_pid，否则 F2 被记成 decision_compute（N-6）。

---

## 3. Estimand（估计对象）

任务书 §R6 S5 要求**明确区分两个估计对象**：

| # | 估计对象 | 可回答性 |
|---|---|---|
| **E-A** | **受控物理机制因果效应**：在本受控压力场景内，到达时刻对齐是否降低配对反事实后悔值 | ✅ E3 可回答 |
| **E-B** | **运营服务真实平均净改善** | ❌ 公开数据不足时**不可声称**（缺真实 ISL 内部真值与真实业务重放） |

主指标：**Regret(O2strong) − Regret(O3)**（同一决策快照、零额外计算耗时），并同时报告决策反转率、反转的正负成本与整体配送指标。

---

## 4. 反事实定义

- **分叉点**：目标 decision 的 PRE_COMMIT 字段（t, t_decision_start, decision_id, state_version, pid, src, dst, sat, kind, policy, candidates, own_queue_bits, obs, info_audit）**刻意不含 chosen**。
- **分叉前一致性**：branch_fingerprint = SHA-256（该决策之前的全部决策行 + 目标行 PRE_COMMIT）必须**逐位相同**（E3M-1 实测 true）。
- **干预**：仅强制一个合法动作；其余按同一 π_ref 演化；外生到达/故障按稳定身份配对。
- **后果**：OUTCOME_KEYS（fate_counts, totals, occupied, queue_area_bits_s, access, events_processed）+ §6 逐候选字段；不发事件记 null + reason，**不得写 0**。
- **硬约束**：分支差异只允许目标动作及其因果后果；O3（各候选自身到达时刻真值）与 O4（最小代价动作评估器）**仅作离线诊断器**，不得进入可部署路径，也不得反向改善评分。

---

## 5. 信息权限（摘要，详表见 00e）

**允许**：自身队列、自身 GSL 可见性、cache 中满足 generated ≤ received ≤ now ≤ generated+ttl 的广告及其 isl_queue_bits（须锁定 peer 身份）、静态有向拓扑、仅第一跳几何/速率、oracle 全局当前知识（仅分析上界，不喂学习）。
**禁止**：truth_at_commit / info_audit.candidate_truth.downstream / egress_snapshot / truth_at_target / t_peer_* / score_downstream_predictions 读数 / 未选候选的未来状态 / 未来星历与全局队列。

---

## 6. 三套数据流（强制分离）

| 流 | 内容 | 可见性 |
|---|---|---|
| policy_visible | 在线策略实际可见、合法且 TTL 有效的字段 | 在线 |
| truth_audit | 实际内核状态/未来标签（leo-sim-decision-info/v1） | 仅离线 |
| counterfactual_outcome | 每候选独立分支的最终命运与代价 | 仅离线评估 |

**不得**通过读取同一日志文件把后两者并入前者；每个臂须有可自动检查的字段白名单，并在测试中主动尝试非法访问、期望 fail-loud。

---

## 7. 决策规则的信息结构（capacity，实测验证；2026-09-25T03:00+08:00）

本节把「下一跳决策如何消费状态」写成可引用的模型，全部经实测验证（评分器复刻与内核选择 200/200 一致）。

### 7.1 六个策略的信息边界

| 策略 | 消费的信息 | 是否消费邻居广告状态 |
|---|---|---|
| hop | 拓扑（单位代价 BFS） | 否 |
| info_queue | 本星自己的出口队列 + 拓扑 | 否 |
| info_physical | 本星队列 + 本跳距离/可用性/速率 | 否 |
| delay | cache 的 isl_propagation_s | 部分 |
| **capacity** | **cache 的 isl_queue_bits 与 isl_propagation_s** + 本星队列 | **是** |
| oracle | 全局当前真值（标注为上界，不喂学习） | 是 |

**重要事实**：仓库 18 个既有 profile **全部使用 hop**，因此在那些实验里「邻居状态影响决策」按定义不可能发生。

### 7.2 capacity 的代价结构（routing.py:247-279）

    对候选方向 d（首跳 sat -> peer_d）：
      total(d) = [ prop(sat,peer_d) + own_queue_bits[d]/rate ] + dist(peer_d)
      dist(.)  = 反向多源最短路（目标 = 广告里服务目的地的星），边代价为：
         若 a == sat：prop(几何真值) + own_queue_bits[dir(a,b)]/rate
         若 a != sat：cache.isl_propagation_s + cache.isl_queue_bits（带 TTL、peer 匹配、跳数限制）
         队列未知 -> 代价 inf（失败关闭）
     并列 -> 按 (cost, direction) 排序

### 7.3 三个由实测确认的推论

1. **决策是总代价最小，不是本星队列最小**：本星队列（即时观测、永不陈旧）与路径长度**互相权衡**；
   实测案例：本星队列 N=1e6/S=0，但 N 路径短 0.135 s -> 内核仍选 N（队列惩罚被路径优势补偿）。
2. **陈旧信息的作用面很窄**：只有 a != sat 的远端边才使用缓存值；
   当目的地的服务星就是候选首跳 peer 时，dist(peer)=0，缓存队列**根本不进入代价**。
3. **陈旧性的现实表现形式是二值的**：窗口内状态可用时不改变排序（advertise_interval 2 s 与 10 s 逐位相同）；
   窗口过长时条目过期 -> 代价 inf -> 走 holding/重决策路径 -> 堆积溢出（30 s 时 1211 次 holding 溢出）。

### 7.4 对研究设计的强制后果

任何声称「按预计到达时刻对齐邻居状态能改善下一跳选择」的设计，必须先满足：

- 决策规则里**远端项在总代价中占主导**（否则本星即时队列与路径长度会淹没它）；
- 平台能记录**每个决策所用全部缓存条目的数值**（否则无法构造 O1/O3 的注入对照；当前缺口见 08-recording-gap-proposal.md）。
