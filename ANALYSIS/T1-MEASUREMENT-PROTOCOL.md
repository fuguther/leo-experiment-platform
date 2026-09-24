# T1 测量与反事实协议（leased from 2026-09-23 分层审查）

> **CURRENT-CONTRACT**；最后核验：2026-09-23。本文定义 T1（邻居状态时间错位 / 候选到达时刻对齐）研究所需的测量与反事实能力契约与五个门禁的验收标准。它**不授权任何实验**：正式运行仍须走编译 → 独立三角色审阅 → 授权 → clean-main 部署 → 自然结束回执 → 分析重算。

## 1. 来源与范围

本文条款来自 2026-09-23 的《leo-direct-sim 平台分层审查报告》，并经 2026-09-23 的实测复核（见 `CURRENT-EXPERIMENT-READINESS.md` 的 2026-09-23 节）。审查结论：

- 平台的数据平面 / SimPy 事件、控制平面 / AoI、信息泄漏控制与正式证据链**足以承担 T1 研究，不需要重写模拟器**；
- 真正缺的是**与这个新因果问题对应的测量能力和反事实能力**，共五类，对应五个门禁。

范围边界：本文只覆盖 T1 的**平台能力**。论文问题候选、claim 冻结与统计设计不在本文内。

## 2. 五个门禁

进入正式 T1 实验前必须依次通过。门禁状态以 `EXPERIMENTS/experiment-program.yaml` 的 `gates` 为准；本文只定义标准。

| 门禁 | 通过标准 |
|---|---|
| `T1-TIME-LEDGER-PASS` | 每个决策有唯一 `decision_id`；同一包的重决策可区分；第 3 节列出的 11 个时间戳可从落盘工件重建，且缺项显式标记为缺失而非静默填 0 |
| `T1-DOWNSTREAM-RESOURCE-PASS` | 对每个候选动作记录**该包到达后实际竞争的出口**（固定 downstream policy 下的具体 egress）、到达前 workload、在服务剩余量与控制积压；不再用「邻居全部方向求和」代替 |
| `T1-COUNTERFACTUAL-REPLAY-PASS` | 存在严格配对的反事实 harness：同 immutable trace / config / seed 重放到同一 decision，校验 pre-branch state hash，只强制改变目标包这一次动作；**不得** deepcopy SimPy 环境，**不得**从原轨迹读取未选候选的未来状态 |
| `T1-COMPUTE-DELAY-PASS` | 决策计算时延进入模拟时间，且**两种模式分别建模、分别可证**：(a) `refresh`（**已实现**）= `decision_start → timeout → 在提交时刻重新观测并重新求解 → commit`；(b) `frozen`（**已实现 2026-09-23**）= `在 decision_start 冻结观测 o(t0) → 对 o(t0) 推理 → commit 时只做合法性校验（链路是否断开、队列是否已满），不得用最新状态重新求最优`。默认关闭（延迟 0），关闭时与旧行为等价。**只有 (b) 代表「计算期间状态陈旧」这一类真实同步推理** |
| `T1-PRESSURE-WINDOW-PASS` | 存在可解析的 ISL 压力窗口（固定 OD corridor / hotspot、access 不限流、constant PHY），且能量化其有向 ISL 利用率量级 |

## 3. `T1-TIME-LEDGER-PASS` 设计

### 3.1 现状（实测，`8a30409`）

- `_decide` 位于 `CODE/leo_sim/kernel.py:3274-3437`，**区间内 `yield` 计数为 0**：观察、选择、提交发生在同一个 `env.now`。
- 全仓 `decision_id` grep **0 命中**；decision sink 的行使仅以 `(t, pid, sat, kind)` 为键（`kernel.py:3168-3184`）。同一包的多次重决策（`_redecide_pending` `3439-3444`、`_redecide_cell_pending` `2762-2782`）在流中不可区分。

### 3.2 需要的 11 个时间戳

`t_measure`、`t_control_rx`、`t_decision_start`、`t_decision_commit`、`t_local_queue_enter`、`t_service_start`、`t_service_finish`、`t_peer_arrival`、`t_peer_redecision`、`t_peer_target_egress_enter`、`t_peer_target_egress_service_start`。

在 `T1-COMPUTE-DELAY-PASS` 落地前，`t_measure = t_decision_start = t_decision_commit` 三者相等；此三元组在三者相等时**必须仍然分别记录**，不得折叠成一个字段，否则该门禁落地后无法回填语义。

### 3.3 落位决策：只扩展 `info_audit`，不新增 config key

这是本设计最重要的一条，理由来自实测的身份绑定链：

1. `config_sha256` 是对**整份 merged config** 的 canonical JSON 求 SHA（`CODE/leo_sim/config.py:970-976`）。**任何新 config key（哪怕纯输出用）都会改变所有配置的 `config_sha256`**，从而让已编译的 planned-run 行与既有授权失效（`CODE/experiment_platform/authorize_experiment.py:338-339`）。
2. 只往 `info_audit` 里加字段则 `config_sha256` 与 trace identity 都不变；`code_sha256`（对 `CODE/leo_sim/*.py` 逐个求 SHA，`CODE/leo_sim/receipt.py:124-131`）必然改变——这是任何 kernel 改动的固有代价，且是正确的：代码确实变了。
3. decision log 工件**不在 receipt / ledger 信任链内**（不在 `receipt.RECEIPT_KEYS` 与 `LEDGER_KEYS` 中），没有行键白名单。

因此：**T1 时间账本一律实现为 `info_audit` 的纯输出扩展，不引入任何新 config key。**

### 3.4 不改动 `packet_events`

`self.packet_events` 参与 receipt 重算与 `_validate_v2_event_authority`；往事件里加键有破坏既有回执验证的风险。**本次不改 `packet_events`、不改 `congestion_metrics`、不改任何 ledger 顶层键**。里程碑只写入 caller-supplied 的 `decision_sink`。

### 3.5 `decision_id` 语义

- 单调递增整数，由 `Kernel` 持有；**仅在 `decision_sink is not None` 时分配**。
- 分配点在 `_decide` **入口**，使 hold / fail 等未提交尝试（以及随后的重决策）也可区分——这正是 `(pid, t, sat)` 现在做不到的事。
- 提交（forward 或 deliver）时把该 id 记到包上（`DataPacket` 新增 slot），供后续里程碑归属。
- 默认关闭时（`decision_sink is None`）**不分配、不写、不新增热路径分支**：`_record_decision` 的 `if self.decision_sink is None: return`（`kernel.py:3154-3155`）保持为第一道早退。

### 3.6 里程碑的落盘形式

decision sink 是 append-only 流（`_DecisionLogWriter` 逐行写 JSONL）。因此下游时间戳**不能**靠回填既有行，而应以独立里程碑行追加，每条携带 `decision_id`、`pid`、`milestone`、`at`、`sat`；由纯后处理函数折叠成每决策的 11 字段链。后处理放在独立模块，不进入 kernel 热路径。

### 3.7 等价性要求与验证方法

关闭时（默认）必须与旧行为**逐位等价**。按「先记录旧版数值、再在新版重跑同一检查」验证：

1. 改前在基线 `8a30409` 上固定 config + seed 跑一次，记录 receipt SHA、artifact manifest SHA、trace SHA、`fate_counts`、`totals`、`occupied`、`queue_area_bits_s`；
2. 改后用同一 config + seed 重跑，逐项比对上述全部值必须完全相同；
3. 既有 `tests/test_decision_snapshot.py::test_decision_sink_does_not_change_behavior`（断言 fates / totals / deliveries / occupied / queue_area_bits_s / access / service_log / events_processed 相同）必须继续通过；
4. 新增测试：sink **打开**时上述集合仍与 sink 关闭时相同，且里程碑行不改变任何 fate。

### 3.8 验收证据

- 新旧对照的逐项数值记录（写入 PR 正文，不写入库内文档）；
- 全量测试通过的真实计数；
- `decision_id` 唯一性与「同一包多次重决策可区分」的定向测试。

### 3.9 已知语义边界（消费方必读）

独立复核（2026-09-23）与自审后确认的边界，下游分析不得越过：

1. **`t_control_rx` 在非学习运行中是"可能已知"而非"实际使用"**。学习运行按 contract 裁剪 cache 条目；非学习运行没有 contract 可裁，因此记录该节点**全部有效 cache 条目**中最新的 `received_at`（`mapping_status` 仍为 `truth_audit_not_learner_tensor`）。对**从不读 cache 的确定性路由**而言这是一个反事实上界，**不得**读作"路由实际使用的那个值"。
2. **"每个 id 都有归属"这条不变量只在挂载 `timeline_sink` 时成立**。只开 `decision_sink` 时，hold/fail 消费的 id 不落任何记录，于是 sink 中的 id 是稀疏的（例如 14 个已分配 id 只有 2 个出现在决策行里）。需要完整 id 账目时必须同时挂 timeline。
3. **`t_local_queue_enter` 取该决策名下第一条 `queue_enter`**。提交型决策自身的入队必然早于其后任何重新入队，故该字段稳定；但**按 `decision_id` 索引的"全部入队"集合**只包含归属于该决策的那些——链路 stall/退休造成的重新入队不归属任何决策（`decision_id` 为 `None`），这是刻意的（见 R8-A8）。
4. **`t_measure` / `t_decision_start` / `t_decision_commit` 在默认（延迟为 0）下恒相等**。`T1-COMPUTE-DELAY-PASS` 落地后：`t_decision_start` 早于其余两者，`t_measure` 仍等于 `t_decision_commit`——**但这条只在已实现的 `refresh` 模式下成立**，因为决策体在计算落地时重读状态，`t_measure` 就是「选择实际依据的状态」的时刻。**2026-09-23 复核判定：这恰恰是缺陷**——`refresh` 模式下计算时延把状态陈旧**抹掉**而不是产生。`frozen` 模式（阶段 1d）下 `t_measure` 应等于 `t_decision_start`。见 4.3 与 R8-A10。

## 4. 其余门禁的设计要点

- **`T1-DOWNSTREAM-RESOURCE-PASS`**：`peer_egress_queue_bits`（`kernel.py:3218-3220`）是邻居全部方向 data+ctrl 求和，不代表包到达后实际进入的队列。真正对应的是同处的 `reverse_link_queue_bits`（`3212-3217`）。需要新增的是**候选动作级**、固定 downstream policy 下的具体 egress、到达前 workload、在服务剩余量与控制积压。保留旧字段以免破坏既有消费方，但必须在文档与 schema 中标注其语义边界。
- **`T1-COUNTERFACTUAL-REPLAY-PASS`**：从相同 immutable trace / config / seed 重放到同一 decision，比对 pre-branch state hash 后只改目标包这一次动作；第一版限定 deterministic router、GE off、learning off。**实现口径（2026-09-23）**：
  - 新增 `CODE/leo_sim/counterfactual.py`：**两跑一强制**。基线跑与强制跑各自完整执行同一 config/trace/seed；强制跑通过 `Kernel(forced_actions={decision_id: action})` 只在该决策处改一次动作（每个 id 至多生效一次，由 `_forced_applied` 保证），并写一条 `forced_action` 里程碑记录 original/forced，使替换**可审计而非隐形**。
  - **pre-branch 配对证明**：指纹覆盖「目标之前（按提交顺序）的**全部**决策行完整内容」+「目标行去掉 `chosen` 之后的 pre-commit 字段」。两跑指纹不等则**拒绝出结论**（抛 `CounterfactualError`）——从不同分支点算出来的不是反事实。
  - **fail-loud 边界**：强制动作在分支点必须合法，否则抛 `KernelError`；强制成基线本来就选的动作会被拒（测不出差异）；目标决策若是 deliver 会被拒（第一版只支持在 ISL forward 候选间强制）；`learning.algorithm != none` 被拒。
  - 不 deepcopy SimPy 环境；不从原轨迹读取未选候选的未来状态——强制跑是独立的完整重放，未选候选的后果只能由它在**强制跑里**实际发生的事件给出。
- **`T1-COMPUTE-DELAY-PASS`**：默认关闭的 `execution.compute_delay_s`（默认 `0.0`）；旧实验默认为 0 以保持语义兼容。

  **4.1 已实现模式 = `refresh`（延迟后重新观测决策）**

  - 不改 `_decide` 本体。它重读 `env.now`、重建候选集并重查几何/速率/队列余量，**所以「延迟后提交」在实现上就等于「提交时刻重新求解」**——动作是对计算落地时刻的状态做出的，而不是对计算开始时可见的状态。
  - 新增 `Kernel.decide_deferred` 生成器（`kernel.py:3544-3561`）；**只有 `compute_delay_s > 0` 时才被创建**。四个调用点按需分支：两个在生成器内（`_ingress_after_prop` / `_isl_arrive_after_prop`）用 `yield from`；两个在普通函数内（`_redecide_cell_pending` / `_redecide_pending`）用 `env.process`。延迟为 0 时仍走原来的同步调用，**逐位不变**。
  - 决策行新增 `t_decision_start`；折叠器据此填 `t_decision_start`，字段缺失时退化为等于 `t`。

  **4.2 已知后果（必须与重新编译、重新授权一并规划）**

  新增 config key 会改变**所有**配置的 `config_sha256`，因此既有 `EXPERIMENTS/EXP-*/run-manifest.json` 中记录的 `config_sha256` 与对应 `authorization.json` **不可再对新代码复用**；历史实验的授权不得重放。trace identity 不受影响（该哈希只覆盖 `scenario`/`endpoints`/`demand`/`execution.max_packets`）。

  **4.3 2026-09-23 复核判定：`refresh` 不能代表真实同步推理（R8-A10）**

  真实同步 DDQN 的时序是「在 `t0` 读取观测 `o(t0)` → 在 `t0~t1` 对 `o(t0)` 推理 → 在 `t1` 提交」。已实现的 `refresh` 是「在 `t0` 等待 → 在 `t1` 重新读状态并立即求出动作」。差别不是措辞：**`refresh` 会把「计算期间状态已经变化」这件事从决策里消掉**，而这正是 T1 要测的错位。用它做 compute-delay 实验，等于把自变量抹平后宣称该自变量无效应。

  实测证据（2×2 夹具，两变体各自 `delay=0` 与 `delay=2`，`t0=5.082`、`t1=7.082`，其余配置与 `t0` 状态逐位相同；唯一 ISL 方向的可达性在窗口内翻转）：

  | 变体 | ISL@t0 | ISL@t1 | delay=0 | delay=2 |
  |---|---|---|---|---|
  | V1 上→下（flip 6.0） | 通 | 断 | `forward` @5.082 → DELIVERED | **无决策行，一直 hold** → IN_SYSTEM |
  | V2 下→上（flip 6.0） | 断 | 通 | hold@5.082，6.100 `forward` → DELIVERED | **`forward` @7.082** → DELIVERED |

  - V1 中 `t0` 状态完全相同而延迟改变结果 ⇒ 动作**不是**对 `t0` 状态做出的。
  - V2 中 `t0` 时链路是断的，延迟版却在链路刚恢复的 `t1` 提交 `forward` ⇒ 决策使用了**计算开始时并不存在的信息**。
  - 附带发现：`delay` 会把一个在计算开始时合法的转发决策变成**无限 hold**（V1 delay=2 全程无决策行），这是 `refresh` 的语义副产品，不是链路问题。

  **4.4 `frozen` 模式（已实现，2026-09-23；R8-A10）**

  - 配置：`execution.decision_observation_mode ∈ {refresh, frozen}`，默认 `refresh`；未知值 fail-loud；`frozen` 要求 `compute_delay_s > 0`（没有计算时间就没有可冻结的区间，属配置错误而非静默 no-op）。
  - 语义：在 `decision_start` 取观测并据此推理（`_observe_preferred_action`），`timeout(delay)` 后**只**做合法性校验（`_deliver_legal_now` / `_forward_legal_now`：链路仍通？队列仍有余量？包未过期？）。
  - 被拒绝的动作**不得**被静默替换为「用最新状态重算的最优动作」——那正是 `refresh`。拒绝写 `commit_rejected` 里程碑（含 `inferred_at` / `action` / `reason`）并把包 park，包要为下一次推理**再付一次计算时间**。
  - 两种模式并存、互斥、由配置显式选择，**默认仍是延迟 0 的旧行为**：`_decide` 仅在传入观测时提前分支，延迟为 0 时 `decide_deferred` 根本不创建。
  - **v1 边界（刻意限制，不是遗漏）**：不与 learning arm 组合（学习需要自己的观测契约）、不与 `forced_actions` 组合（反事实 harness 在分支点强制动作，而 frozen 把分支点移到了观测时刻）；两者都 fail-loud 抛 `KernelError`。拒绝记录只走 timeline sink——**不新增 mechanism counter**，因为那会改变 receipt 的 `MECHANISM_COUNTER_KEYS` 键集。
  - 验收证据（`CODE/leo_sim/tests/test_frozen_observation.py`，10 个测试）：① 4.3 的 2×2 反例——`frozen` 下 V2 的 delay 版**不提交** `forward`，而 `refresh` 提交；② V1 下 `frozen` 记 `commit_rejected`，`refresh` 什么都不记；③ 状态不变时两种模式**逐条相同**（负对照）；④ 关闭新特性（默认 `refresh` + 延迟 0）与基线逐位等价（digest `NO_RESULT_DIFFERENCES`）。
  - **已知行为差异（必须随 claim 一起说明）**：某目的 cell 的**第一次**决策会因 endpoint 惰性创建而看到空服务集（`no_info`）——`refresh` 靠同一时刻的重新求解把它掩盖过去，`frozen` 会如实地把这个 `no_info` 当作观测结果并 hold 一个计算周期。这不是 frozen 的缺陷，而是 `refresh` 一直在掩盖的一个冷启动效应。
- **`T1-PRESSURE-WINDOW-PASS`**：现有 `EXP-20260829-GLOBAL-PRESSURE-BRACKET-R02` 在 10/20/40/80 Mbps 下无可饱和有向 ISL、无持续 hotspot（80 Mbps 的 1 s active-window p99 utilization 约 0.5%，最大约 1%），**不能充当 T1 主压力场景**；须另建固定 OD corridor / hotspot、access 不限流、constant PHY 的可解析场景。

## 5. 明确不做

- 不重写模拟器；不替换 SimPy。
- 不 deepcopy SimPy 环境做反事实；不从原轨迹读取未选候选的未来状态。
- 不加"转发效率"这类与物理带宽混淆的旋钮：`isl_rate_mbps` 已是链路带宽，若用同一参数表示"转发效率"，F2 与 F3 会退化成同一个实验。F2 必须定义为独立于 PHY 带宽的节点处理/调度开销。
- 不在本文授权任何运行。

## 6. 阶段 1 微观机制场景契约（2026-09-23 复核新增）

正式因果实验之前，必须先有**宏观星座上不可辩驳的微观证据**。以下四项构成阶段 1 的验收面，全部以可执行场景夹具（而非叙述）交付，且必须能在小星座（4–12 星）上复现：

- **M1 无排队负对照**：链路不拥塞时，候选动作排序必须与「先到先服务 + 物理时延」一致；任何"排序反转"都不允许出现。用于证明 M2/M3 的反转不是夹具噪声。
- **M2 单次 burst 导致候选动作排序反转**：一次突发必须能把某个候选动作从最优挤到非最优，并给出反转前后的排队量 / 在服务量证据。**若反复尝试都无法产生符合 FIFO 与物理时序、且可解释的反转，则按第 7 节停止**，不得改用大星座或复杂预测模型掩盖。
- **M3 后到包不得插入目标包 FIFO 前方**：在目标包已入队 / 已开始服务的时刻之后到达的包，**不得**出现在目标包之前的服务序列里。这条直接检验排队语义与 `t_local_queue_enter` 的时序归属。
- **M4 计算时延时间语义（`frozen` vs `refresh`）**：4.3 的 2×2 夹具必须同时覆盖两种模式；`frozen` 下延迟版**不得**使用 `t1` 才出现的信息（V2 的 delay 版不得提交 `forward`）。

M2/M3 的证据必须来自**不可变事件**（`packet_events` + `link_service_windows`），不接受从决策行反推。

## 7. 停止条件（2026-09-23 复核新增）

若 4–12 星微观夹具无法产生**符合 FIFO 与物理时序、且可解释**的候选动作排序反转，则：

- **不得**用加大星座、加大负载或上复杂预测模型来掩盖该缺失；
- T1 预测价值路线（四路真值比较 → 轻量模型 → 复杂模型）**不得启动**；
- 应把结论记录为「当前机制的候选动作排序对排队不敏感」，回到机制层重新设计，而不是继续堆平台功能。

