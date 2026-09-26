# 00e — 字段、时间与信息权限表（E0 交付物）

**对应 commit**：`98c858f642aeca8056d215224b20783b00354026`（分支 `chore/retire-dead-lines-q0-tiny`）

**clean 树 `code_sha256`**：`2a114890e8b11e429fec45a6b1efcb81c1d80a8e195a9c9407a250d218f16dbe`

> **冻结有效性声明**：取证期间有并发写入者（详见 `00-repository-and-source-audit.md` §2.3）；上述 `code_sha256` 只对该 commit 的 clean checkout 成立，随他人提交失效。2026-09-25T00:08:45 采样时 `code_sha256` 已为 `57e86d4471af7a94b584202cb8b2418a92f6d2a7850a46b7e6c4e0696e489b7c`。

**本文件用途**：把"决策时刻允许使用什么信息、禁止使用什么信息、每个时间戳从哪来"固化成可直接当规范用的字段级表格。所有条目来自源码，来源以 `路径:行号` 给出。

**表格结构**：表 A（允许使用的信息）、表 B（禁止使用的信息）、表 C（时间与时间戳，含 C-1 十一个时刻、C-2 timeline 里程碑、C-3 决策行 19 键）。表 C 拆成三张子表，因为三类对象的字段集合不同，合并会丢失一一对应关系。

**依据的源码文件**（本文件全部行号指向本仓）：`CODE/leo_sim/routing.py`、`CODE/leo_sim/control.py`、`CODE/leo_sim/kernel.py`、`CODE/leo_sim/decision_ledger.py`、`CODE/leo_sim/metrics.py`、`CODE/leo_sim/receipt.py`。

---

## 表 A — 决策时刻允许使用的信息

适用范围：确定性策略（`routing.policy` ∈ `hop` / `delay` / `capacity` / `info_queue` / `info_physical`）、学习策略的观测契约、以及 `frozen` 模式在决策起点冻结的观测。

| 编号 | 字段或来源 | 语义 | 约束 | 源码位置 |
|---|---|---|---|---|
| A1 | `own_queue_bits`（本星各 ISL 出口队列位数） | 决策卫星自己当前直接观测到的出口积压 | 只限本星；第一跳队列只能"直接观测"，不得从广告反推 | `kernel.py:4117`、`:3538-3539`；`routing.py:14` |
| A2 | `deliver` 合法性（本星对目的 cell 当前可见性 + 下行队列余量） | 是否可立即交付 | 只依据本星当前 GSL 可见性与下行队列；MCS 下还需下行速率大于 0 | `kernel.py:4059-4062`、`:3857-3869`；`routing.py:4-5` |
| A3 | 本地控制 cache 的有效条目集合 | 本星真正收到且未过期的控制广告 | 有效当且仅当 `generated_at <= received_at <= now <= generated_at + ttl_s`；未来到达与过期条目都无效 | `control.py:44-51`、`:70-77`；`kernel.py:3302-3323` |
| A4 | 广告载荷 `isl_queue_bits[direction]` | 邻居对其各出口积压的自我报告 | 必须锁定对端身份：记录中的 `peer` 必须等于当前拓扑该方向的 peer，否则丢弃（防重匹配后张冠李戴） | `kernel.py:3346-3358`、`:3427-3437` |
| A5 | 广告载荷 `isl_propagation_s` | 邻居报告的传播时延 | 远端边只能用已到达的广告值，不得查询全局当前几何 | `control.py:106`；`routing.py:15-17` |
| A6 | 广告载荷 `serve_cells` | 邻居报告的可见目的 cell 集合 | 与 A3 同一有效期约束 | `kernel.py:3366-3368`；`routing.py:6-8` |
| A7 | 静态星座拓扑（有向图） | 先验的邻接关系与方向 | 有向；构造时验证物理双向性，不存在的反向边拒绝伪造 | `routing.py:9-11`、`:70-93` |
| A8 | 第一跳当前几何、距离、可用性、推导速率 | 本跳到邻居的即时物理状态 | 只限第一跳；不得用于任何远端边 | `routing.py:15-17`、`:21-26`；`kernel.py:4163-4192` |
| A9 | 每条 cache 条目的 AoI | `now - generated_at`（从生成时刻计） | 是标量年龄，不表示该邻居任何其他字段的采样时刻 | `control.py:53-54`；`kernel.py:3364`、`:3473` |
| A10 | oracle 策略的全局当前知识 | 分析上界用；可含全局当前服务集与全局当前状态 | 显式标注 `analysis_upper_bound`，永不喂给学习；不得当作可部署方案 | `routing.py:18-19`；`kernel.py:3409-3417` |
| A11 | `frozen` 模式在决策起点的合法性校验输入 | 提交时刻只判断推断动作是否仍然合法（链路仍通、队列仍有余量、包未过期） | 只做合法性校验，不得用最新状态重新求最优（那属于 `refresh`） | `kernel.py:3857-3887`、`:3942-4035` |
| A12 | 决策身份字段 `decision_id`、`state_version` | 每次决策的唯一标识与状态版本号 | 纯输出；分配或不分配都不影响路由、学习、时序与命运 | `kernel.py:1497-1508`、`:3579-3580` |
| A13 | 决策行输出对象（`observation_at_start`、`estimate_at_start`） | 把"决策实际依据了什么"落盘 | 纯输出；记录本身不得回流为决策输入 | `kernel.py:3325-3384`、`:3386-3489`、`:3571-3599` |
| A14 | 学习观测向量（按契约裁剪后的张量） | 学习臂实际使用的观测 | 必须经契约裁剪（`C1` 固有单跳；其他契约按 `obs_hops`）；摘要以 `obs` 字段落盘 | `kernel.py:3524-3533`；`kernel.py:3312-3317`（`information_set`） |
---

## 表 B — 决策时刻禁止使用的信息（未来真值类）

适用范围：任何在线策略、任何"决策时刻"的预测、任何 arms 的观测函数。以下字段可以**记录与事后分析**，但**不得**进入决策输入。

| 编号 | 字段 | 为何禁止（含源码原文要点） | 源码位置 |
|---|---|---|---|
| B1 | `truth_at_commit`（`leo-sim-truth-at-commit/v1`） | commit 时刻的核内真值，等于原 `info_audit` 内容；docstring 明写 This is hindsight, not a deployable prediction | `kernel.py:3560-3570`；`decision_ledger.py:26-30` |
| B2 | `info_audit.candidate_truth[dir]` 全部分支 | 决策时刻的物理真值审计，`mapping_status = truth_audit_not_learner_tensor`，显式 never fed to a policy | `kernel.py:3711-3802`（`mapping_status` 在 `:3799`） |
| B3 | `candidate_truth[dir].downstream`（`peer_egress_data_bits`、`peer_egress_ctrl_bits`、`peer_in_service_bits`、`peer_in_service_remaining_bits`、`peer_in_service_phase`、`peer_in_service_remaining_method`） | 候选动作级**对端真实**出口积压、在服务量与剩余量；它读取对端真实队列与对端自己的 cache | `kernel.py:3653-3709`（字段在 `:3700-3708`） |
| B4 | `candidate_truth[dir]` 中的 `peer_egress_queue_bits` | 邻居**全部方向** data 加 ctrl 求和，不等于包到达后实际进入的队列；真正对应的是同处的 `reverse_link_queue_bits` | `kernel.py:3743-3745`、`:3751-3752` |
| B5 | `egress_snapshot`（到达快照） | 包**真正到达**邻居那一刻的每出口真值，到达前不存在 | `kernel.py:3627-3651`；折叠引用 `decision_ledger.py:243-245` |
| B6 | `truth_at_target` | 包真正竞争到的资源的已实现真值；MISSING while the target instant has not happened，结构上决策时刻不可得 | `decision_ledger.py:181-225`、`:305-314` |
| B7 | `t_peer_redecision`、`t_peer_target_egress_enter`、`t_peer_target_egress_service_start` | 由后继决策的时刻回填，属于事后折叠 | `decision_ledger.py:290-292`、`:305-311` |
| B8 | `score_downstream_predictions` 的任何读数 | 使用 commit 时刻真值评分；docstring 要求不得当作 t0 信念质量 | `decision_ledger.py:440-462` |
| B9 | 未选候选在**原轨迹**中的未来状态 | 反事实必须独立完整重放，不得从原轨迹读取未选候选的未来 | `ANALYSIS/T1-MEASUREMENT-PROTOCOL.md:99-103` |
| B10 | 未来星历与隐藏全局队列 | 模块级绑定声明：No policy reads future ephemeris or hidden global queues | `routing.py:28` |
| B11 | 尚未到达（`received_at > now`）或已过期（`now > generated_at + ttl_s`）的控制消息 | 未到达不算信息；过期不算有效状态 | `control.py:44-51` |
| B12 | 对端真实队列与对端自己的 cache（在构建可部署预测时） | `_estimate_at_start` 的 docstring 明确：读到它们就是把 hindsight 回填进可部署预测 | `kernel.py:3391-3398` |
| B13 | 目标包自身在共同未来时刻的入队 | 必须用标签包剔除或 before-arrival workload 协议，避免把目标包自己算进它面前的队列 | 任务书 v1.4 §4.3（第 250 行）；协议 §6 的 M3（第 156 行） |
| B14 | `t_control_rx` 的"实际使用"解读 | 非学习运行没有观测契约可裁剪，该字段记录节点**全部有效 cache 条目中最新**的 `received_at`，对从不读 cache 的确定性路由是反事实上界 | `decision_ledger.py:132-145`；`ANALYSIS/T1-MEASUREMENT-PROTOCOL.md:91` |
| B15 | F2 节点占用区间（`node_process_start` / `node_process_end`）当作决策计算时间 | 两者互不重叠（F2 在决策阶段之前结束）；把未覆盖区间读成决策计算时间会重复计时 | `kernel.py:1138`、`:4297-4302`；`ANALYSIS/CURRENT-EVENT-TIMELINE.md:153-159` |

---

## 表 C — 时间语义与时间戳

### 表 C-1 — 十一个时刻（`decision_ledger.TIMELINE_FIELDS`，生命周期序）

| 序号 | 字段 | 语义 | 折叠来源 | 缺失条件 |
|---|---|---|---|---|
| 1 | `t_measure` | 动作**实际依据**的信息时刻：`frozen` 取 `t_decision_start`，`refresh` 取 `t_decision_commit`；是每决策标量，不表示任何单邻居测量时刻 | `decision_ledger.py:159-168`、`:272-278` | 不缺失（由模式决定） |
| 2 | `t_control_rx` | 该决策可能已知的最新控制缓存到达时刻（全部有效条目中最新 `received_at`） | `decision_ledger.py:132-145`、`:279` | 契约未贡献任何条目时为 `MISSING`（如非学习且无 cache 的场景） |
| 3 | `t_decision_start` | 计算开始时刻；零计算延迟时等于 commit | `kernel.py:3846`、`:3576-3578`；`decision_ledger.py:280` | 不缺失（缺该键时退化为等于 `t`） |
| 4 | `t_decision_commit` | 动作提交时刻，等于 `env.now` | `kernel.py:3537`、`:3572`；`decision_ledger.py:281` | 不缺失 |
| 5 | `t_local_queue_enter` | 归属于该决策的第一条 `queue_enter` 时刻 | `kernel.py:1538-1562`；`decision_ledger.py:282-283` | 该决策名下无 `queue_enter` 时为 `MISSING` |
| 6 | `t_service_start` | 该决策导致的第一次服务开始 | `kernel.py:1572-1585`；`decision_ledger.py:284-285` | 无服务开始时为 `MISSING` |
| 7 | `t_service_finish` | 该决策导致的第一次服务结束 | `kernel.py:1587-1602`；`decision_ledger.py:286-287` | 无服务窗口结束记录时为 `MISSING` |
| 8 | `t_peer_arrival` | 包到达邻居的时刻（携带 `egress_snapshot`） | `kernel.py:1616-1635`；`decision_ledger.py:288-289` | 无到达里程碑时为 `MISSING` |
| 9 | `t_peer_redecision` | 邻居对该包重决策的信息时刻（后继决策的 `t_measure`） | `decision_ledger.py:290`、`:309` | 无后继重决策时恒为 `MISSING` |
| 10 | `t_peer_target_egress_enter` | 邻居侧目标出口入队时刻（后继决策的 `t_local_queue_enter`） | `decision_ledger.py:291`、`:310` | 无后继重决策时恒为 `MISSING` |
| 11 | `t_peer_target_egress_service_start` | 邻居侧目标出口服务开始时刻（后继决策的 `t_service_start`） | `decision_ledger.py:292`、`:311` | 无后继重决策时恒为 `MISSING` |

补充规则：`MISSING` 与 `None` 是两种不同事实——`MISSING` 表示该记录早于该字段存在，`None` 表示已记录但当时不存在合法预测（`decision_ledger.py:42`、`:171-178`）。三个时刻在默认零计算延迟下恒相等，但必须分别记录、不得折叠（`ANALYSIS/T1-MEASUREMENT-PROTOCOL.md:37`）。
### 表 C-2 — timeline 里程碑（13 种取值、15 处调用点）

事实来源：枚举 `CODE/leo_sim/kernel.py` 中全部 `_timeline(` 调用点。`redecision` 与 `commit_rejected` 各出现两次（`refresh` 路径与 `frozen` 路径），故 15 处调用点对应 13 种取值。所有里程碑行由 `_timeline()` 写入，行结构固定为 `{milestone, at, pid, decision_id}` 加该调用点附加的 `extra` 字段。

| 编号 | milestone | 产生位置 | 语义与归属 |
|---|---|---|---|
| M1 | `hold` | `kernel.py:1484` | 包被 park（等待）时的里程碑，携带决策 id 与 `obs_mode`；`frozen` 下用于记录被推断为 hold 的情况 |
| M2 | `queue_enter` | `kernel.py:1561` | 物理队列入队；`decision_id` 必须由调用方显式传入，链路 stall 或 retire 造成的重新入队传 `None` |
| M3 | `service_start` | `kernel.py:1584` | 服务开始；归属 `pkt.decision_id` |
| M4 | `service_finish` | `kernel.py:1600` | 服务窗口结束；归属 `pkt.decision_id` |
| M5 | `peer_arrival` | `kernel.py:1635` | 包到达邻居；携带 `egress_snapshot`，是 `truth_at_target` 的唯一来源 |
| M6 | `satellite_ingress` | `kernel.py:1648` | 上行到达卫星（仅上行路径产生） |
| M7 | `forced_action` | `kernel.py:3827` | 反事实强制替换记录，携带 `original` 与 `forced`，使替换可审计而非隐形 |
| M8 | `redecision`（refresh 路径） | `kernel.py:4050` | 该包此前已提交过至少一次，携带 `prev_decision_id` 与 `obs_mode`，是 `_successor` 链与 `t_peer_*` 回填的依据 |
| M9 | `redecision`（frozen 路径） | `kernel.py:3960` | 同上，产生于冻结提交路径 |
| M10 | `commit_rejected`（frozen） | `kernel.py:3996` | 冻结推断动作在提交时刻不合法（链路断、队列满、包过期），携带 `inferred_at`、`action`、`reason`；包被 park 并需为下次推理再付一次计算时间 |
| M11 | `commit_rejected`（frozen，第二产生点） | `kernel.py:4008` | 同上，另一处拒绝分支 |
| M12 | `frozen_inferred_hold` | `kernel.py:4020` | 冻结推断结果为 hold 且未产生提交 |
| M13 | `node_process_start` | `kernel.py:4297` | F2 节点处理占用开始（仅数据包；上行 ingress 与每次 ISL 到达） |
| M14 | `node_process_end` | `kernel.py:4300` | F2 节点处理占用结束；`node_process_end` 等于决策阶段起点，与计算时延互不重叠 |
| M15 | `fail` | `kernel.py:4366` | 失败命运记录，携带 `fate`；与 `_fail` 调用点一一对应 |

注意：M1 至 M15 是**调用点**编号（15 个），里程碑**取值**为 13 种：`hold`、`queue_enter`、`service_start`、`service_finish`、`peer_arrival`、`satellite_ingress`、`forced_action`、`redecision`、`commit_rejected`、`frozen_inferred_hold`、`node_process_start`、`node_process_end`、`fail`。

### 表 C-3 — 决策行 19 键（`decision_ledger.DECISION_ROW_KEYS`）

契约：键集固定、双向 fail-loud（未知键与缺失键都拒绝，`decision_ledger.py:90-110`）；流契约名 `decision-rows/v1`（`:83`）；行由 `_record_decision` 以单个无条件字典字面量写出（`kernel.py:3571-3599`），因此不存在条件字段。

| 序号 | 键 | 内容 | 写入位置 |
|---|---|---|---|
| 1 | `t` | 提交时刻，等于 `committed_at` | `kernel.py:3537`、`:3572` |
| 2 | `t_decision_start` | 计算开始时刻；无计算延迟时等于 `t` | `kernel.py:3576-3578` |
| 3 | `decision_id` | 本决策唯一 id；仅当存在 sink 时分配 | `kernel.py:1497-1508`、`:3579` |
| 4 | `state_version` | 内核状态版本号 | `kernel.py:3580` |
| 5 | `pid` | 包 id | `kernel.py:3581` |
| 6 | `src` | 源 cell | `kernel.py:3582` |
| 7 | `dst` | 目的 cell | `kernel.py:3583` |
| 8 | `sat` | 做决策的卫星 | `kernel.py:3584` |
| 9 | `kind` | 决策类型（`forward` / `deliver`） | `kernel.py:3585` |
| 10 | `policy` | 路由策略标签；学习臂为 `算法:契约` | `kernel.py:3586-3587` |
| 11 | `candidates` | 决策时刻的**合法**动作集 | `kernel.py:3588` |
| 12 | `chosen` | 实际提交的动作 | `kernel.py:3589` |
| 13 | `own_queue_bits` | 本星各 ISL 出口队列位数 | `kernel.py:3538-3539`、`:3590` |
| 14 | `obs` | 学习观测摘要（契约、维度、短哈希、L2 范数），非学习臂为 `null` | `kernel.py:3524-3533`、`:3591` |
| 15 | `info_audit` | 决策时刻物理真值审计（`candidate_truth` 与 `cache_entries`） | `kernel.py:3536`、`:3592` |
| 16 | `obs_mode` | `refresh` 或 `frozen`；未知值 fail-loud | `kernel.py:3540-3556`、`:3595`；`decision_ledger.py:148-156` |
| 17 | `observation_at_start` | 决策实际依据的观测（含每个邻居各自的测量时刻） | `kernel.py:3540-3556`、`:3596` |
| 18 | `estimate_at_start` | 该观测合法支持的唯一预测；无法产生时为 `null` 并附原因 | `kernel.py:3386-3489`、`:3597` |
| 19 | `truth_at_commit` | commit 时刻核内真值（事后视，禁止作为决策输入，见 B1） | `kernel.py:3560-3570`、`:3598` |

---

## 判读规则（三条硬规则）

1. **模式决定测量时刻**：`t_measure` 由 `obs_mode` 决定（`frozen` 取决策起点，`refresh` 取提交时刻），不得由数值反推模式；`obs_mode` 未知值必须 fail-loud（`decision_ledger.py:148-156`）。
2. **真值只在事后使用**：表 B 全部字段可写入工件、可用于事后评分与诊断，但只要进入在线决策路径即构成信息泄漏。判定方法：检查决策函数的输入集合是否与表 A 一致。
3. **缺失不等于零**：任何时刻字段缺失必须保持 `MISSING` 状态并给出原因，不得填 0、不得从 commit 时刻真值回填（`decision_ledger.py:12-13`、`:181-190`）。

**与已登记限制的接口**：`frozen` 的被拒提交只写 timeline 不写 decision 行（`ANALYSIS/PLATFORM-AUDIT-REPORT.md:93`，L7），因此 decision 流不是全部尝试的完备记录；只挂 `decision_sink` 时 id 稀疏，需要完整 id 账目必须同时挂 timeline（`ANALYSIS/T1-MEASUREMENT-PROTOCOL.md:92`）。