# ASYNC-SCHEDULING-DESIGN — 研究线②异步流量调度的可审模型与最小执行设计

> 本文件给研究线②（异步流量调度）定六件契约：**调度变量 / 信息范围 / 更新触发 / 计算与安装 / 生效版本 / 旧方案持续转发规则**，并给出第一步可审证据的最小设计。
> 本文件不写算法，不评价策略优劣。**本平台当前没有任何异步流量调度的实现**（核验见 §0）。
>
> **⚠️ 定义未定，不要照本文件的 §1 实现（2026-09-26 晚补记）。** 本文件此前把调度对象写成三元组
> `(schedule_epoch, binding_scope, installed_action)`，那实际上是**路径/动作调度**——与研究线①
> 的`chosen`是同一个东西换了个名字。②到底要调度**路径 / 分流比例 / 发送速率**三者中的哪一个，
> 尚未裁定，见 **§0.4**。**在裁定之前，§1.4 的 D1--D5 与 §最小执行设计 一律不得实现**：
> 它们只对"路径"那一种读法成立。

| 字段 | 值 |
| --- | --- |
| 仓库 | `leo-exp-main`（= `leo-experiment-platform` origin/main `1fad58f`，detached HEAD） |
| 日期 | 2026-09-26 |
| 取证级别 | 机制与字段 **[READ]**（逐条指向 `文件:行`）；配置默认值 **[READ]**（`CODE/leo_sim/config.py`）；**无 [EXEC]** —— 本文件未运行任何仿真，所有行为性陈述都是设计意图，不是实测结果 |
| 运行前提 | `python3` = 3.14.2；必须在仓库根目录运行（`CODE` 是包，绝对导入 `from CODE.leo_sim import ...`） |
| 涉及文件 | `CODE/leo_sim/{kernel,config,decision_ledger,counterfactual,routing,receipt,q0}.py`；`CODE/experiment_platform/{fold_decision_ledger,replay_counterfactual,minimal_branch_compare}.py` |

**行号锚点是对 1fad58f 的读出；符号名是稳定锚点。** 全文引用一律写成 `符号名`（@1fad58f `文件.py:行`）的双写形式。`CODE/leo_sim/kernel.py` 与 `CODE/leo_sim/config.py` 在本文件写作之后被改动（新增 `execution.compute_servers_per_satellite` 与 timeline milestone `compute_wait`，见 §4.5），**这两个文件的行号一律以符号名为准**；其余被引文件（`decision_ledger.py`、`counterfactual.py`、`q0.py`、`receipt.py`、`routing.py`、`__main__.py`）本轮未改动，行号可直接核对。

---

## 0. 现状核验与术语边界

### 0.1 两条研究线的平台能力落差

| 研究线 | 平台能力 | 状态 |
| --- | --- | --- |
| ① 逐包分布式路由 | frozen/refresh 观测模式（`execution.decision_observation_mode`）；单次候选动作干预（`forced_actions` + `counterfactual.replay_with_forced_action` + CLI `replay_counterfactual`）；逐包分支比较（`minimal_branch_compare.py`）；时间链折叠（`decision_ledger.TIMELINE_FIELDS` + `build_ledger` + CLI `fold_decision_ledger`）；v6 流绑定（`RECEIPT_KEYS_V6`） | 已具备 |
| ② 异步流量调度 | 无。**零实现** | 需新增 |

研究线登记现状：`lines/README.md` 声明"每条研究线一个文件。本表是唯一索引"，而 `lines/` 目前只有 `TIME-SEMANTICS.md`（状态 ACTIVE，对应研究线①）。②要成为一条被承认的线，须先在 `lines/` 登记（见 §最小执行设计 前置条件）。**本文件不代替该登记。**

### 0.2 "零实现"的核验命令

```bash
cd /Users/lge/Desktop/topic/leo-exp-main
grep -rniE "\basync|asynchron" CODE --include=*.py | wc -l    # -> 0
grep -rniE "schedule_version|timeslot|slot_index" CODE --include=*.py | wc -l   # -> 0
grep -rniE "schedul" CODE --include=*.py | wc -l               # -> 33（含本轮新增的注释）
grep -rniE "compute_servers|compute_wait" CODE --include=*.py | wc -l  # -> 21 / 13
```

33 处 `schedul` 命中属于三类，**没有一处是流量调度**：

1. F2 节点处理/调度开销：`config.execution.node_process_delay_s`（@1fad58f config.py:221 声明）；F2 节点处理/调度开销：`kernel._node_process`（@1fad58f kernel.py:4261，"one receive/process/schedule occupancy"）、`config.execution.node_process_delay_s` 的 F2 说明注释 214-220、`test_f2_node_cost.py`；
2. SimPy 的离散事件调度与定时唤醒：`kernel._run`（@1fad58f kernel.py:658）、链路 retirement 唤醒注释（@1fad58f kernel.py:2631）、`kernel._schedule_pending_wake`（@1fad58f kernel.py:4242）。

**术语边界（必须守住，否则会与 F2 混淆）**：本文件中的"调度"= 决定**何时计算、一次计算覆盖哪些包、结果何时生效**。F2 的"调度开销"= 一个包占用节点 receive/process/schedule 元件的**时间代价**（`config.execution.node_process_delay_s`（@1fad58f config.py:221 声明）；F2 说明注释 214-220），单位是秒，与"何时重算"无关。二者共用一个英文词，但字段、时刻、代价项都不同。

### 0.3 三条禁止（本设计的硬边界）

1. **不得把"按流缓存"（per-flow caching / 流的路径复用）当作异步流量调度。** 反退化判据见 §1.4 —— 它是**结构性**的（计算与决策解耦、版本可归因、安装真的发生过、作用域不是单流、陈旧度有界），**不是"动作必须变化"**。旧版设计里的"动作必须变化"判据已删除，理由同样在 §1.4。
2. **不得重复开发既有能力**：frozen/refresh 观测、单次候选动作干预、逐包分支比较、十一时刻折叠、V6 流绑定，全部只引用、不重建。
3. **不得新增引擎语义之外的旁路**：任何新信息必须经 `decision_sink` / `timeline_sink` 两条既有输出通道之一落地。

### 0.4 定义待裁：②到底调度什么（本轮新增，先于任何实现）

"异步流量调度"在本平台可以被读成三件不同的事。它们的信息、状态、触发、代价项与可测对象**都不同**，
把它们混在一份契约里，写出来的字段既不能实现也不能证伪。

| 读法 | 调度对象 | 一次计算产出什么 | 与①的关系 | 需要的新状态 | 效果落在哪 |
| --- | --- | --- | --- | --- | --- |
| **P 路径调度** | 某个作用域内的**下一跳/路径** | 一个动作（取值域与现有 `chosen` 相同） | **待定，见下** | 动作空间可能不需要新增，也可能需要 | 每个包走哪条路 |
| **R 分流比例** | 某个作用域内**每个候选出口分走多少** | 一组权重 `w_d`（Σ=1），按权重把包分到多个出口 | ①的推广：①是 `w` 退化为 one-hot | 需要**按权重选出口**的决策原语；多出口并发占用；每条出口的到达/入队时刻都要记 | 链路上的负载分布、每条出口的排队 |
| **S 发送速率** | 某个作用域内的**发送/注入速率** | 一个速率（bps 或每包间隔） | **与①正交**：速率不改变下一跳，改变的是**包什么时候进入系统** | 需要**整形/限速**原语（包在入口或出口排队等待发送时机）；需要记录"被整形"的等待 | 输入业务的时间形状、突发与队列 |

三条读法的**共同点**只有一句：都必须回答"何时计算、结果何时生效"。这正是本文件 §3--§6 的价值所在，
也是它们唯一可以共享的部分。**其余全部不能共享。**

### 0.4.1 撤回一个判断：调度对象与机制差异是两个独立问题

本节早先的版本写过"P 路径调度**就是**①"。**那个判断撤回**，理由有两条：

1. **它把"调度什么"和"怎么调度"混成了一个问题。** ①研究的是**选哪条路**（信息与代价的因果），
   ②研究的是**计算与生效的时序**（何时算、算一次管多少、结果何时安装、算的时候旧的怎么办）。
   即使两者的**动作取值域完全相同**，它们问的仍是两个不同的问题：①问"选择对不对"，②问
   "在时间错位下这个选择还有效吗、代价是多少"。把②说成①，等于把②的问题**消掉**而不是回答它。
2. **同一个"机制差异"可以挂在不同的调度对象上。** "何时计算、何时安装、期间旧版本怎么办"
   这套机制，挂在**路径**上成立，挂在**分流比例**上成立，挂在**发送速率**上也成立——
   机制不决定对象，对象也不决定机制。

所以本节保留**两个独立问题**，不合并：

| 问题 | 内容 | 谁来定 |
|---|---|---|
| **Q1 调度对象** | 路径 / 分流比例 / 发送速率 | 研究者裁定（本轮不替裁） |
| **Q2 机制差异** | 何时触发计算、算一次覆盖多少、结果何时安装、计算期间旧方案如何继续转发、版本如何单调 | 本文件 §3--§6 给契约，与 Q1 的选择无关 |

**Q2 的三条已经可以确定**（不依赖 Q1）：计算完成与版本安装必须是**显式事件**且安装不回溯；
计算期间旧版本继续完整生效、版本号单调；任何新信息只走 `decision_sink` / `timeline_sink`。
**Q1 未定，所以 §1.4 的 D1--D5 与 §最小执行设计 仍是草案**：它们默认了"一次计算覆盖多个决策"，
而这个默认只有在 Q1 = 路径（或比例）时才自然成立，在 Q1 = 速率时根本不适用。

**为什么这一节必须先于实现**：

1. 若选 **P**，②与①**共用动作空间**，但问题不同：②要额外回答"重算与安装的时序代价值多少"。
   是否值得单开一条线，是 Q1 之后才有的问题；**不能由"动作空间相同"直接推出"不是一条线"**。
2. 若选 **R**，D1--D5 **原样不适用**：一套权重可以在一整个 epoch 内不变，而"计算与决策解耦"
   在 R 下的自然形式是"一次计算覆盖多少个包的**分配**"，不是"覆盖多少个决策"。而且必须新增
   **按权重选出口**的决策原语——那**是**引擎语义新增，直接触碰 §0.3 第 3 条边界。
3. 若选 **S**，D1--D5 **完全无关**：速率调度的输出不是动作、不参与候选集、不产生 `decision_id`，
   它作用于包的**发射时刻**。更要紧的是它会改动 `trace`：本平台的 trace 是**输入工件**
   （`trace.csv` 被 `trace_identity_sha256` 绑定、进 receipt），一个会改动发射时刻的调度器
   必须说明它与 trace 身份的关系——这是**先决问题**，不是实现细节。

**本轮不替研究者裁定。** 要求是：在 `lines/` 登记②并写下选中的那一个之前，
§1.4 的 D1--D5 与 §最小执行设计 **保持为草案**，不得落地成代码。已经可以确定、与三选一无关的，
只有下面三件：

- 计算完成与版本安装必须是**显式事件**，且安装不回溯（§4.2/§4.3/§6.2）；
- 计算期间旧版本继续完整生效，版本号单调（§6.3）；
- 任何新信息只走 `decision_sink` / `timeline_sink` 两条既有输出通道（§0.3 第 3 条）。

---

## 1. 调度变量（scheduling variables）

> **本节定义**（以及 §2 / §4.4 / §最小执行设计 里的字段与判据）**建立在"P 路径调度"这一读法之上**。
> 若 §0.4 裁定为 R 或 S，本节需要整体重写而不是修补。

### 1.1 被调度的对象是什么

**不是包，不是流，是"一次计算的时机 + 该计算结果的作用域"。** 动作空间不新增，沿用引擎已有的 `candidates`。

必须显式分开三层——现有代码里这三层是压在一起的，不分开就无法定义异步调度：

| 层 | 名称 | 内容 | 平台现状 |
| --- | --- | --- | --- |
| L1 | 动作层 | 一个包在某一跳选哪个方向（或 deliver / hold） | 已有：`routing.choose_next_hop`（@1fad58f routing.py:167） 返回 `(cands, status)`，`cands[0]` 是固定下游策略下的选择 |
| L2 | 时机层 | 这次计算什么时候开始、它的结果在多长窗口内被复用 | **需新增**。现状时机 = 每个包各自到达即算（`kernel._redecide_cell_pending` @1fad58f kernel.py:2916 调用点 2937；`kernel._ingress_after_prop` @1fad58f kernel.py:4304 调用点 4316） |
| L3 | 作用域层 | 这次计算的结果适用于哪些包 | **需新增**。现状作用域恒等于"该包自己这一次"（动作逐包分配 `decision_id`，`kernel._next_decision_id`（@1fad58f kernel.py:1497） |

**异步调度的定义（本文件采用）**：L1 保持不变，把 L2 与 L3 从"每包一份"解耦为"一次计算覆盖一组包"。变量是三元组 `(schedule_epoch, binding_scope, installed_action)`。

### 1.2 变量表（定义域 / 单位 / 与现有字段的对应）

| 变量 | 层 | 定义域 | 单位 | 与现有字段的对应 |
| --- | --- | --- | --- | --- |
| `decision_id` | L1 | ℕ，每 run 单调 | 个 | 行键 `decision_id`；分配于 `kernel._next_decision_id`（@1fad58f kernel.py:1497） |
| `pid` | L1 | ℕ | 个 | 行键 `pid` |
| `sat` | L1/L3 | `0..num_satellites-1` | id | 行键 `sat` |
| `candidates` | L1 | `{N,S,E,W,deliver}` 的子集，或 `["deliver"]`，或 `[]` | 方向枚举 | 行键 `candidates`；由 `choose_next_hop` 的合法集给出 |
| `chosen` | L1 | `candidates` ∪ `{deliver, hold}` | 方向枚举 | 行键 `chosen` |
| `own_queue_bits` | L1 | `{N,S,E,W} -> ℕ` | bit | 行键 `own_queue_bits`（`kernel._record_decision`（@1fad58f kernel.py:3491；own_queue_bits 3538） |
| `state_version` | — | ℕ 单调 | 事件步 | 行键 `state_version`；语义见 §5.2 |
| `t_decision_start`, `t` | L2 | float | s | 行键 `t_decision_start` / `t`（`kernel._record_decision`（@1fad58f kernel.py:3491；decision row 字面量） |
| `schedule_version` **新增** | L2 | ℕ 单调，每次 installation +1 | 版本号 | **无对应**。不得直接写进 decision row（硬约束见 §4.4） |
| `t_epoch_start`, `t_epoch_end` **新增** | L2 | float，`t_epoch_end > t_epoch_start` | s | 无对应 |
| `binding_scope` **新增** | L3 | `{sat}` 或 `{sat, dst_cell}` | 无 | 无对应；**不含 `src`，不含流 id**（§1.4） |
| `installed_action` **新增** | L1×L3 | `{N,S,E,W,deliver,hold}` | 方向枚举 | 必须是某次 `choose_next_hop` 输出候选的成员 |

### 1.3 L1 不扩张：调度层不能发明动作

`installed_action` 在 commit 时刻仍必须通过引擎自己的合法性检查：`kernel._forward_legal_now`（@1fad58f kernel.py:3871），检查几何、MCS 零速率、队列 room与 `kernel._deliver_legal_now`（@1fad58f kernel.py:3857）。不合法即被拒——frozen 模式下记 timeline `commit_rejected` 并 park（`kernel._decide_from_frozen_observation`（@1fad58f kernel.py:3942；两处 commit_rejected 在 3996 / 4008）。

**因此异步调度只能"在已有候选里选，或延后选"，不能新增动作。** 这一条把本线钉在"不新增引擎语义"内。

### 1.4 为什么把调度对象定义成"流"会退化成按流缓存

流的键是 `(src, dst)`。若一次计算的结果按流缓存，则同一流的后续包直接命中缓存：**不再计算、`t_decision_start` 不再前进、`chosen` 恒定**。此时"调度器"的输出与"每条流一条固定路径"在可观测层面**完全不可区分**，而且它根本不是异步的——计算只发生一次，之后全是查表。
更实质的代价：它破坏 CHARTER 不变式 4（可归因）。路径复用带来的收益无法归因到"调度"，只能归因到"路由结果被冻住了"。

**反退化判据（2026-09-26 重写；旧版已删除）**

旧版写的是："同一 `(src,dst)` 的连续两个 forward 决策，其 `chosen` 至少出现一次不同，否则该 run 不得作异步调度证据"。**这条判据是错的，已删除**，三条理由：

1. **它测的是输出，不是机制。** 被防的退化是"**没有再计算**"，不是"**动作没变**"。一个正确的异步调度器在低负载、拓扑稳定、或原调度本来就正确时，完全可以连续给出同一个动作——旧判据会把一个**正确实现判死**（假阴性）。反过来，动作变了也不能证明"做了调度"：逐包重算同样会让动作变化。
2. **它不是一条判据。** 窗口多大、样本多少、判定失败是作废还是降级，旧版自己都还列在"未决定"里。一个连判定规则都未定的条件不是判据，把它写进证据表只会让读者以为它已经被检查过。
3. **它把两种不同的东西混在一起。** "按流缓存"的可观测特征是**计算次数**与**版本身份**，不是动作序列。

**替换为结构性判据（D1--D5，全部可从现有两条流核验）**：

| 编号 | 判据 | 为什么它能区分"调度"与"缓存" |
| --- | --- | --- |
| **D1** | 计算与决策**解耦**：被覆盖的 forward 决策数 **严格大于** `schedule_install` 事件数 | "异步"的定义就是这个不等号；逐包重算时两者相等 |
| **D2** | **身份可归因**：每个被覆盖的决策都能指出它当时用的是哪个 `schedule_version` | 没有这一条，任何"收益"都无法归因到某一次计算 |
| **D3** | **安装真的发生过**：一次 run 内至少安装过两个不同的 `schedule_version`，且每次安装都带触发原因与时刻 | 算一次用到死 = 缓存，不是调度 |
| **D4** | **作用域不是单流**：某次安装的 `binding_scope` 覆盖了多于一条流的决策，**或**该次安装发生在被覆盖流没有新包的时刻（即由触发器驱动而非由包驱动） | 这是区分"调度一次、覆盖一片"与"每条流查一次表"的关键性质 |
| **D5** | **陈旧度有界**：每个决策记录它所用调度的年龄 `t_decision_start - t_install`，且该年龄被声明的 epoch/刷新策略约束 | 无界年龄正是"按流缓存"的失效模式 |

**明确不作为判据**：同一 `(src,dst)` 连续决策的 `chosen` 是否变化。它可以作为**描述性统计**报告（例如"本 run 中同流连续同向的比例"），但**不得**用来判定一个 run 是否有效，也不得用来否决证据。若要报告它，必须同时报告 D1--D5。

**避免退化的三条设计约束**（保留，它们防的是真问题）：

1. `binding_scope` 的键**不含** `src`，也不含任何流 id；只含 `sat` 与（可选）目的 cell。
2. 每个 binding 必须带**两个失效条件**：`schedule_version` 被新版本覆盖，**或** `t_epoch_end` 到期。epoch 一到结果即作废、必须重算，**哪怕观测一个字都没变**。这条是"异步"与"缓存"的分水岭。
3. 一个 epoch 覆盖的决策数有上限（见 §最小执行设计 的 `schedule_epoch_max_packets`），超出部分回落为逐包决策。

**本节小结** —— 已有：L1 全部（`candidates` / `chosen` / `choose_next_hop` / 合法性检查）。需新增：L2 的 `schedule_epoch` 与 `schedule_version`，L3 的 `binding_scope`，以及 §1.4 的 D1--D5 核验脚本（**不是**"动作是否变化"的核验脚本）。

---

## 2. 信息范围（information scope）

### 2.1 三个既有信息对象（对齐它们的真实字段名）

| 对象 | 载体 | 来源与语义 |
| --- | --- | --- |
| `observation_at_start` | decision row 键；schema `leo-sim-observation-at-start/v1`（`kernel._observation_at_start`（@1fad58f kernel.py:3325） | 这次决策**实际依据**的观测。frozen = `t_decision_start` 的快照；refresh = `t_decision_commit` 重读的状态。**模式靠标签区分，不靠数字推断** |
| `estimate_at_start` | decision row 键（`kernel._estimate_at_start`（@1fad58f kernel.py:3386） 起） | 该观测**合法支持**的预测。`prediction_method = "same_policy_on_advertised_peer_state"`；无预测时为 `None` + `estimate_unavailable_reason`，**永不从真值回填** |
| `truth_at_commit` / `truth_at_target` | 前者是 decision row 键（`kernel._record_decision`（@1fad58f kernel.py:3491；truth_at_commit 3563），后者是折叠产物（`decision_ledger.truth_at_target`（@1fad58f decision_ledger.py:181） | **事后真值**。`truth_at_commit.source = "kernel_state_at_commit"`；`truth_at_target` 在目标时刻发生前为 `MISSING`（带 reason），**结构上不可能作为决策输入** |

### 2.2 允许清单（在线调度策略可读）

| 允许读 | 字段路径 | 依据 |
| --- | --- | --- |
| 本星自身队列 | `observation_at_start.own_queue_bits`（`{N,S,E,W} -> bit`） | 直接观测，`kernel._record_decision`（@1fad58f kernel.py:3491；own_queue_bits 3538 |
| 已物理到达且未过期的控制广告 | `_observed_cache_entries(sat, now)` -> `caches[sat].valid_entries(now)` | `kernel._observed_cache_entries`（@1fad58f kernel.py:3302） / `3332`；合法性 = `received_at <= now <= generated_at + ttl_s`（`kernel.ControlPacket.valid_at`（@1fad58f kernel.py:280） |
| 每个邻居各自的测量时刻 | `observation_at_start.neighbours[str(origin)].received_at` / `.age_s` | `kernel._observation_at_start`（@1fad58f kernel.py:3325；received_at 3363 / age_s 3364；`age_s = entry.aoi(now)`（`kernel.ControlPacket.aoi`（@1fad58f kernel.py:283） |
| 广告内容（逐方向队列比特） | `.advertised_isl_queue_bits` | `kernel._observation_at_start`（@1fad58f kernel.py:3325；**必须通过 peer 绑定检查** `record["peer"] == topo[origin][direction]`，匹配不上即丢弃 |
| 广告内容（服务 cell） | `.advertised_serve_cells` | `kernel._observation_at_start`（@1fad58f kernel.py:3325；advertised_serve_cells 3367 |
| 邻居跳数 | `.hops` | `kernel._observation_at_start`（@1fad58f kernel.py:3325；hops 3365 |
| t0 合法预测 | `estimate_at_start` 全部字段：`peer_egress_direction` / `peer_egress_queue_bits_estimate` / `peer_egress_queue_bits_known` | `kernel._estimate_at_start`（@1fad58f kernel.py:3386；prediction_method 3488 |
| 本星视角拓扑与几何 | `self.topo` / `geometry.isl_available(sat, peer, now)` | `kernel._forward_legal_now`（@1fad58f kernel.py:3871；geometry.isl_available 3879 |
| 物理容量 | `links.isl_rate_mbps`（默认 1000.0，`config.links.isl_rate_mbps`（@1fad58f config.py:133 声明） / 345 默认 1000.0）、`links.downlink_rate_mbps`（默认 100.0，`config.links.downlink_rate_mbps`（@1fad58f config.py:112 声明） / 306 默认 100.0） | 配置常量 |
| 路由上限 | `routing.max_hops`（默认 16，`config.routing.max_hops`（@1fad58f config.py:156 声明） / 367 默认 16） | 配置常量 |
| 判定"候选方向"与"合法方向" | `observation_at_start.candidate_directions` / `.legal_directions` | `kernel._observation_at_start`（@1fad58f kernel.py:3325；candidate_directions 3379 / legal_directions 3380 |

### 2.3 禁止清单（在线调度策略不得读）

| 禁止读 | 位置 | 原因（原文依据） |
| --- | --- | --- |
| `truth_at_commit.candidate_truth` / `.cache_entries` | `kernel._record_decision`（@1fad58f kernel.py:3491；truth_at_commit 3563 起 | 来源 `kernel_state_at_commit`，是提交时刻的真值，**不是 t0 可部署预测**（模块 docstring 与 `score_downstream_predictions` docstring 均明写） |
| `truth_at_target` | `decision_ledger.truth_at_target`（@1fad58f decision_ledger.py:181） | docstring：**MISSING while the target instant has not happened**；只能事后评分 |
| `_peer_downstream_truth` | `kernel._peer_downstream_truth`（@1fad58f kernel.py:3653） | docstring：**"never fed to a policy"**，`mapping_status = truth_audit_not_learner_tensor` |
| `_egress_snapshot` | `kernel._egress_snapshot`（@1fad58f kernel.py:3627） | 到达时刻真值；仅供 `decision_ledger.score_downstream_predictions`（@1fad58f decision_ledger.py:440） 与 `decision_ledger.score_start_estimates`（@1fad58f decision_ledger.py:553） 事后评分 |
| `info_audit` | 行键 | 与 `truth_at_commit` 同源，同为事后真值 |
| 其它卫星的 `self.caches[other]` | — | 合法信息只有"物理到达本星的广告"；跨星直读即为越界 |
| 未来到达序列（trace / `demand.offered_mbps` 的生成脚本） | `CODE/leo_sim/trace.py` | 读它即为 oracle，不是分布式可部署策略 |
| 未来事件队列（`env.peek()` 等） | `kernel.Kernel.__init__`（@1fad58f kernel.py:1286） 注释 注释提到全局快照可含"已排定的超时到达" | 未来信息，且会绕过广告到达时延 |
| 全局快照 `snapshot_global()` | `kernel.snapshot_global`（@1fad58f kernel.py:1996） | **不是非法，但它属于集中式信息对象**（文档自称 Q0-A "global current information"）。一旦允许，② 就从分布式调度变成集中式调度 —— 是否允许是研究选择，见 §尚未确定 第 10 条 |

### 2.4 fail-closed 判据（需新增）

调度器读到的每个数字，都必须能在**该决策自己的** `observation_at_start` 或 `estimate_at_start` 里找到同名来源；找不到即越界。

**现状**：这条判据只存在于 `_observed_cache_entries` 的 docstring 纪律里（`kernel._observed_cache_entries`（@1fad58f kernel.py:3302 的 docstring） ："Two call sites that disagreed about the information boundary would silently break the very quantity the T1 ledger exists to measure"），**没有运行时强制**。需新增一个折叠期核验（读 decision row，不读引擎内部状态）。

**本节小结** —— 已有：三个信息对象全部落地且分字段、分时刻；`_observed_cache_entries` 是"什么能被知道"的唯一定义（含 peer 绑定检查与 AoI）。需新增：越界核验器；以及"是否允许集中式快照"的研究决定。

---

## 3. 更新触发（update triggers）

### 3.1 候选触发源，逐个说清时刻语义

| 触发源 | 时刻语义 | 与 `compute_delay_s` 的先后 | 现状 |
| --- | --- | --- | --- |
| 控制面广告到达 | 到达时刻，**不是 `t_measure`**：`ControlPacket.received_at` 只可写一次（`kernel.ControlPacket.received_at`（@1fad58f kernel.py:268），写 cache 的时刻 = 物理到达时刻 | 触发 → 新一次计算的 `t_decision_start` = 处理该触发的时刻；`compute_delay_s` 在**其后**流逝，`t` = `t_decision_start + compute_delay_s` | 引擎有到达事件（`kernel._ctrl_arrive_after_prop`（@1fad58f kernel.py:2990） 附近写入 cache），**无重算钩子** → 需新增 |
| 本地队列状态变化 | 入队/出队时刻；`_metric_queue_enter` 要求**显式**传 `decision_id`（`kernel._metric_queue_enter`（@1fad58f kernel.py:1538，R8-A8） | 计算在入队之后开始 → `t_decision_start > t_local_queue_enter` | 无订阅 → 需新增 |
| 定时器（ticker） | `_pending_ticker` 每 `scenario.time_step_s`（默认 0.1，`config.scenario.time_step_s`（@1fad58f config.py:63 声明） / 259 默认 0.1）醒一次（`kernel._pending_ticker`（@1fad58f kernel.py:2733）；唤醒时刻即候选 `t_decision_start` | ticker 唤醒在前，`compute_delay_s` 在后 | **已有 ticker**，但当前只服务 parked 包的 `kernel._redecide_pending`（@1fad58f kernel.py:4232） → 复用即可，不必新造事件源 |
| 邻居 AoI 超阈值 | 判定用**观测时刻**的 age：`age_s = entry.aoi(now)`（`kernel.ControlPacket.aoi`（@1fad58f kernel.py:283） | 阈值判定基于 `t_measure` 时刻的 age，**不能用预测值或真值** | 有 `age_s` 字段，**无阈值参数** → 需新增（形如 `schedule_aoi_max_s`） |
| 路径失效 / 拓扑变化 | `state_version += 1` 的三个点：拓扑重算（`kernel._recompute_topology`（@1fad58f kernel.py:1921；state_version bump 1981）、Q0 计划应用（`kernel.apply_joint_plan`（@1fad58f kernel.py:2352；state_version bump 2384）、每事件步（`kernel.Kernel.run`（@1fad58f kernel.py:4388；每事件步 bump 4405） | `state_version` 是"已安装结果是否仍合法"的**判据**，不是触发时刻 | 有计数器，**无订阅机制** → 需新增 |

### 3.2 为什么不能"每个包都重算"

**因为那正是现有机制，而且它的成本可以算出来。**

`execution.compute_delay_s > 0` 时，每个包各创建一个 `decide_deferred`（定义 `kernel.decide_deferred` @1fad58f kernel.py:3831；调用点 `kernel._redecide_cell_pending` @1fad58f kernel.py:2916 的 2937 与 `kernel._ingress_after_prop` @1fad58f kernel.py:4304 的 4316），各自 `yield self.env.timeout(self.compute_delay_s)`（`kernel.decide_deferred`（@1fad58f kernel.py:3831；timeout 在 3851）。
不可扩展是算术问题：

- 默认 `execution.max_packets = 200_000`（`config.execution.max_packets`（@1fad58f config.py:196 声明） / 404 默认 200_000）。
- 若 `compute_delay_s = 0.01 s`，200 000 包累计计算时延 = **2 000 仿真秒**。
- 而 `smoke` profile 的 `scenario.duration_s = 5.0`（`config.PROFILES` 的 `smoke`（@1fad58f config.py:422））。

后果不是"算得慢"，而是**每个包都在自己的 deadline 之后才生效**，测到的是"截止期批量超时"，不是调度效果。

**一条必须澄清的事实**：算力模型**由 `execution.compute_servers_per_satellite` 决定**（本轮新增，见 §4.5）。默认 `0`（无界）时每包各自 `yield timeout(compute_delay_s)`、各自独立 `env.process`（调用点 `kernel._redecide_cell_pending` @1fad58f kernel.py:2916 的 2937 与 `kernel._ingress_after_prop` @1fad58f kernel.py:4304 的 4316），彼此不争用——此时"每包重算"的代价就是"每个包各自晚 Δ 生效"。取 `> 0` 时每星是一个**确定性 N 服务器池**，决策要先等空闲服务器，等待计入 commit。所以"每包重算是否可扩展"取决于算力是否有界，这一点直接决定 §4.5。

### 3.3 为什么不能"永不重算"

已安装结果会活得比支撑它的信息久。平台**已经有**这条语义的检查：
`q0.validate_plan_version(plan, state_version)`（`q0.validate_plan_version`（@1fad58f q0.py:71） 在 `plan.version != state_version` 时 fail-closed 返回 `"stale plan version X != Y"`；调用点在 `kernel.validate_joint_plan`（@1fad58f kernel.py:2272）。而 `state_version` **每事件步 +1**（`kernel.Kernel.run`（@1fad58f kernel.py:4388；每事件步 bump 4405）。

所以"永不重算"在下一个事件步就被判 stale —— 它不是可选项，是立即失效。反过来也说明：`state_version` **不能**直接当调度版本号用（见 §5.2）。

**本节小结** —— 已有：ticker（`_pending_ticker`）、广告到达事件、`age_s`、`state_version` 与 `validate_plan_version`。需新增：重算钩子、AoI 阈值参数、`state_version` 的订阅（或等价的 epoch 失效条件）。

---

## 4. 计算与安装（computation and installation）

### 4.1 三个时刻必须分开

| 时刻 | 现有字段 | 语义 |
| --- | --- | --- |
| 计算开始 | `t_decision_start`（decision row 键，`kernel._record_decision`（@1fad58f kernel.py:3491） | 决策体开始运行。frozen 在此取观测并推理（`kernel.decide_deferred`（@1fad58f kernel.py:3831；观测在 3849） |
| 计算完成 | `t` 即 `t_decision_commit`（行键；`committed_at = float(self.env.now)`，`kernel._record_decision`（@1fad58f kernel.py:3491） | 决策体落地、动作被选出、若被拒则记 `commit_rejected` |
| 结果生效（installation） | **不存在** | 见 §4.2 |

### 4.2 现状：commit 即 installation，没有第三个时刻

`_decide` 选出动作后**立刻**入队：`self.downlinks[sat].put(pkt)`（`kernel._decide_from_frozen_observation`（@1fad58f kernel.py:3942；downlink put 3978）或 `self.isls[sat][action].put_data(pkt)`（`kernel._decide_from_frozen_observation`（@1fad58f kernel.py:3942，ISL put_data） 3989与 `kernel._decide`（@1fad58f kernel.py:4036，ISL put_data） 4214）。对**该包自己**而言，"动作被选出"与"动作开始支配流量"是同一瞬间。
但对**其它包**而言，现在**根本没有任何机制**：动作是逐包的，每个包到达时各自分配 `decision_id`（`kernel._next_decision_id`（@1fad58f kernel.py:1497）。

**所以异步调度的 installation 天然是一个新事件**：`schedule_version` 对 `binding_scope` 生效。它不是 commit 的别名，也不能用 commit 顶替。

### 4.3 install 落在十一个时刻的哪里：哪里都不落

`decision_ledger.TIMELINE_FIELDS`（@1fad58f decision_ledger.py:45） 是十一个：`t_measure` / `t_control_rx` / `t_decision_start` / `t_decision_commit` / `t_local_queue_enter` / `t_service_start` / `t_service_finish` / `t_peer_arrival` / `t_peer_redecision` / `t_peer_target_egress_enter` / `t_peer_target_egress_service_start`。
没有一个是"一次结果对一批包生效"。原因是**粒度不同**：十一个时刻是**决策级**时刻（每个 `decision_id` 一条账），install 是**版本级**事件（每次安装影响一批决策）。把版本级事件塞进决策级字段，语义会被压扁。

两条落地路径：

- **(A) 用时间线 milestone（最小步骤走这条）**。`_timeline(milestone, pkt, decision_id, **extra)` 的 `milestone` 是**自由字符串**，timeline 流由 `leo-sim-timeline-log/v1` sidecar 以 sha256 绑定（`__main__._write_timeline_manifest` @1fad58f __main__.py:166，schema 字面量在 181），**没有 milestone 白名单**。`commit_rejected`、`frozen_inferred_hold`、`node_process_start/end`、`forced_action` 都是这样加的。新增一条 `schedule_install` **不改变 receipt 键集**。
  - **同一条路径本轮已被复用一次，可作先例**：`kernel._compute_queue_wait`（本轮新增；当前工作树 `kernel.py:3925`）的 docstring 写明，等待之所以记 timeline 而不记 decision row，是因为 "the decision row has a frozen 19-key contract, and the wait happens before a decision id is allocated"。`schedule_install` 面对**同一个约束**，所以 (A) 不是权宜，而是本平台的既定做法。
  - **硬性注意**：不得把它写进 `ATTEMPT_VERDICT_PRECEDENCE = ("fail", "commit_rejected", "frozen_inferred_hold", "hold")`（`decision_ledger.ATTEMPT_VERDICT_PRECEDENCE` @1fad58f decision_ledger.py:116）。该元组里的 milestone 会被折成"决策尝试的判决"（`decision_ledger.build_attempts` @1fad58f decision_ledger.py:333，判决折叠在 363）；install 不是尝试判决，混进去会污染尝试账。
- **(B) 把十一时刻扩为十二**：在 `TIMELINE_FIELDS` 加 `t_install`。这会改动 T1 的测量契约与门禁（`test_decision_ledger.test_ledger_rebuilds_the_full_eleven_field_chain` @1fad58f test_decision_ledger.py:124，字段循环在 132，逐字段核验），属**另一次契约变更**，不是本线的第一步。

### 4.4 硬约束：新字段不能直接进 decision row

| 事实 | 位置 |
| --- | --- |
| `validate_decision_row` **双向**拒绝：未知键与缺失键都报 `DecisionRowError` | `decision_ledger.validate_decision_row`（@1fad58f decision_ledger.py:90） （"Fail loud in BOTH directions"） |
| 该校验**无条件**挂在决策日志写入器上 | `__main__._cmd_run`（@1fad58f __main__.py:436；validate 挂载在 495 |
| `DECISION_ROW_KEYS` 是 19 键，且有测试断言 `len(...) == 19` | `decision_ledger.DECISION_ROW_KEYS`（@1fad58f decision_ledger.py:75）；`test_receipt_v6_streams.test_the_decision_row_contract_matches_what_a_run_writes`（@1fad58f test_receipt_v6_streams.py:106；len==19 断言 115 |
| 决策流契约标识 | `DECISION_STREAM_CONTRACT = "decision-rows/v1"`（`decision_ledger.DECISION_STREAM_CONTRACT`（@1fad58f decision_ledger.py:83） |

**结论**：`schedule_version` **不能直接加进 decision row**，除非把 `decision-rows/v1` 升为 v2 并同步改 receipt 键集。最小路径是走 §4.3 的 (A)：包与版本的绑定关系写在 timeline 的 `schedule_install` 行里（extra 键 `schedule_version` / `t_epoch_start` / `t_epoch_end` / `binding_scope` / `installed_action` / `sat` / `decision_ids`），**decision row 的 19 键一个不动**。

### 4.5 计算在哪里排队：本轮起可建模（有界每星计算资源）

**已有（本轮新增）**：配置键 `execution.compute_servers_per_satellite`（本轮新增；当前工作树 `config.py:231` 声明 / `config.py:429` 默认 `0` / `config.py:940-950` 校验），实现为 `kernel._compute_queue_wait`（本轮新增；当前工作树 `kernel.py:3925`）。

- 取 `0`（默认）= **无界**：`_compute_queue_wait` 直接返回 `0.0`，不创建任何额外事件，**与历史行为逐位相同**；有测试钉住这一点（`test_compute_delay.py:162` 的 `test_an_unbounded_pool_is_bit_identical_to_the_historical_run`）。
- 取 `> 0` = 每星一个**确定性 N 服务器池**：`kernel._compute_free_at`（当前工作树 `kernel.py:1118`）为每星维护 N 个"空闲时刻"槽，决策取**最早空闲**的槽，并列时按槽序号决胜，因而分配可复现。
- **等待与服务分开记录**：`kernel.decide_deferred`（@1fad58f kernel.py:3831）先算 `wait`，再 `yield self.env.timeout(wait + self.compute_delay_s)`，于是 **commit = `t_decision_start` + `wait` + `compute_delay_s`**（当前工作树 `kernel.py:3920-3921`）。
- 等待记在 timeline milestone `compute_wait`（当前工作树 `kernel.py:3950`），字段 `wait_s` / `service_s` / `servers` / `servers_busy` / `queueing`，`decision_id` 为 `None` —— 等待发生在 `decision_id` 分配之前。

**结论：算力争用从本文件写作时的"不可观测"变成了可观测**，"省算力"这条收益现在有字段可依。两条边界必须同时记住：

1. **等待没有进 decision row。** `_compute_queue_wait` 的 docstring 给的理由是：决策行有冻结的 19 键契约，且等待发生在 `decision_id` 分配之前。后果是当 `compute_servers_per_satellite > 0` 时，`t - t_decision_start` **同时包含排队与服务**，只有 timeline 流能把两者分开；要逐决策核验排队，仍绕不开 §4.4 的契约取舍。
2. **它仍不是"调度"。** 它提供的是"算力有界"这一**代价项**（CHARTER 不变式 5），不产生任何跨包复用的调度结果；§1 的 L2/L3 依旧**需新增**。因此最小步骤**不必**再新造 Resource —— 有界池已由本轮提供，直接引用即可。

**本节小结** —— 已有：`t_decision_start` / `t` 两个时刻、自由 milestone 的 timeline 通道、双向行契约、sha256 流绑定；**本轮新增**：有界每星计算池（`execution.compute_servers_per_satellite` + `compute_wait`），使算力争用可观测，且 `0` 时逐位等同于历史行为。需新增：`schedule_install` milestone 及其 extra 键；(A)/(B) 二选一。

---

## 5. 生效版本（effective version）

### 5.1 方案

`schedule_version` **新增**，类型 ℕ，单调递增，**每次 installation +1**。

- 不是"每次计算 +1"：算完但没安装不占版本号。否则会出现"算完了、但从未生效的版本"，这种中间态无法归因。
- 不是"每事件步 +1"：那是 `state_version`（§5.2）。

**包与版本的绑定规则（提案）**：包在第 k 跳使用的版本 = **它自己 `t_decision_start` 时刻"当前生效"的版本**。

为什么不是到达时刻、也不是 commit 时刻：

- 用**到达时刻**：会把"在 holding 队列里排队等待"的时间算进版本归属，包的版本反映的是队列延迟，不是信息。
- 用 **commit 时刻**：会把 `compute_delay_s` 算进去，同一个 epoch 内开始的决策可能因计算时延落在不同版本，epoch 边界失效。
- 用 `t_decision_start`：它是 `t_measure` 的 frozen 分支（`decision_ledger.measure_instant`，`decision_ledger.measure_instant`（@1fad58f decision_ledger.py:159），即"这次决策实际依据的时刻"，正是版本归属该用的时刻。

### 5.2 与 `state_version` 的关系（二者不可互换）

| | `state_version` | `schedule_version`（新增） |
| --- | --- | --- |
| 递增事件 | 每事件步（`kernel.Kernel.run`（@1fad58f kernel.py:4388；每事件步 bump 4405）、拓扑重算（`1990`）、计划应用（`2393`） | 每次结果安装 |
| 递增频率 | 与事件数同阶 | 与调度周期同阶，远低于事件数 |
| 用途 | "已计算/已安装的结果是否仍合法"的 fail-closed 判据（`q0.validate_plan_version`（@1fad58f q0.py:71） | **身份**：同一批包是否共享同一次计算 |
| 能否当版本号 | **不能**：每事件步 +1，任何已安装结果在下一个事件步即 stale | 是版本号 |

一句话：**`state_version` 判过期，`schedule_version` 给身份。** 一个调度结果的身份是 `schedule_version`，它的合法性仍由 `state_version`（以及 `t_epoch_end`）判。两者都需要，缺一不可。

### 5.3 "旧版本结果仍在被使用时被新版本覆盖"的处理规则

1. **不撤销已 commit 的动作。** 包若已 commit（动作已进 `isls[sat][d]` 或 `downlinks[sat]`），它的 `chosen` 属于它自己的 `decision_id`，不因新版本安装而改变。
2. **不追溯重解释。** 新版本只对"安装时刻之后**开始**的决策"生效。历史 decision row 的 `chosen` 语义**永不改写**。
3. **parked 包立即改用新版本。** 在 `pending[sat]` 等待的包（`kernel._hold_packet`（@1fad58f kernel.py:1471））没有生效中的动作，所以在安装后的下一次 `_redecide_pending`（ticker `kernel._pending_ticker`（@1fad58f kernel.py:2733），或 `kernel._schedule_pending_wake`（@1fad58f kernel.py:4242）的证明性唤醒）用新版本重算。
4. **一个包可以跨版本，一个 decision 不能。** 包的 `path` 上各跳可以在不同 `schedule_version` 下做出（跨版本合法且**必须被记录**）；同一个 `decision_id` 内的动作只能有一个版本归属。

---

## 6. 旧方案持续转发规则（legacy continued-forwarding rule）

### 6.1 逐状态规则

| 包的状态 | 新版本安装时的处理 | 依据 |
| --- | --- | --- |
| 已 commit 且已进 ISL/downlink 队列 | **继续按旧版本转发到本跳结束**（按它自己的 `chosen` 服务、传输、到达下一跳） | 动作已物理入队并已计账：`_metric_queue_enter` 显式带 `decision_id`（`kernel._metric_queue_enter`（@1fad58f kernel.py:1538）；撤回会破坏 `conservation_ok`（receipt 中 `field_authority = "recomputed"`，`receipt.py` 头部信任模型） |
| 在 `pending[sat]` 中 park（含 frozen 被拒的 `commit_rejected` 之后） | 安装后的下一次重决策**采用新版本** | 无生效中动作；`_hold_packet` 只入 holding 队列（`kernel._hold_packet`（@1fad58f kernel.py:1471） |
| 传输中（`_in_flight`） | 不干预；到达后按到达星当时的版本重新决策 | 传输是链路物理过程，不属于任何调度结果 |
| "已决定但尚未执行"的动作 | —— | **现状无此状态**：commit 与入队同一瞬间（§4.2） |

### 6.2 切换时机（三条，缺一不可）

1. 切换**只发生在显式的 install 事件时刻** `t_install`。没有 install 事件就没有切换。
2. 切换粒度是 `binding_scope`（一个 `sat`，或一个 `sat + cell`），**不是全局**。
3. 切换**不回溯**：`t_install` 之前开始的决策一律按旧版本解释，**即使它的 commit 在 `t_install` 之后**。

### 6.3 计算期间旧方案如何继续转发（本轮补充）

一次重算从 `t_compute_start` 到 `t_compute_done` 之间有一个**在飞窗口**。规则必须写死，否则窗口内的包无版本可归：

| 时刻关系 | 该包用哪个版本 | 理由 |
| --- | --- | --- |
| 包的决策在 `t_compute_start` **之前**开始 | 旧版本（它启动时生效的那一版） | 结果不可追溯改写（§5.3 第 2 条） |
| 包的决策落在 `[t_compute_start, t_compute_done)` 内 | **仍然是旧版本** | 新版本此刻**还不存在**。旧版本在此期间**继续完整生效**——不是"降级"、不是"停用"，而是照常服务、照常提交、照常计账 |
| 包的决策在 `t_compute_done` **之后**开始，且该结果被安装 | 新版本 | 安装是显式事件（§4.2），只有安装之后才切换 |
| 计算完成时发现已有**更新的**版本被安装过 | 该计算结果**作废**，不安装 | 否则版本号会回退，D2 的身份可归因性被破坏。作废必须留痕（一条 `schedule_discarded` milestone，带 `reason="superseded_while_computing"`） |

三条推论：

1. **计算期间不阻塞转发。** 异步调度的意义就在这里：旧方案在算的时候照常工作，包不会因为"调度器正在想"而停等。若实现成"算完才转发"，那只是把逐包重算换了个位置，D1 会退化。
2. **在飞窗口必须可观测。** `t_compute_start` 与 `t_compute_done` 都要落 timeline（本轮已有 `compute_wait` 的粒度可比照：等待与服务分开记，`kernel._compute_queue_wait`），否则上面这张表无法核验。
3. **版本号必须单调。** 作废规则（第 4 行）是版本单调的唯一保证；没有它，"安装"就只是一个可以回退的标签。

### 6.4 为什么"立即全部切换"会造成不可归因的现象

**（一）严格配对比较被结构性拒绝。** `counterfactual.replay_with_forced_action`（@1fad58f counterfactual.py:75） 先算 `counterfactual.branch_fingerprint`（@1fad58f counterfactual.py:54），覆盖目标决策之前**全部** decision row（整行；分支点字段 `PRECOMMIT_FIELDS`，`counterfactual.PRECOMMIT_FIELDS`（@1fad58f counterfactual.py:34）。若切换会重写已在途包的动作，基线 run 与实验 run 会在**目标决策之前**就分叉，`branch_states_identical` 为 False，直接 `raise CounterfactualError("the replay did not reach the same branch point")`。

也就是说：全局立即切换会让**平台最核心的因果工具对该实验永久失效**，违反 CHARTER 不变式 3（可干预）。`minimal_branch_compare` 的 `pre_branch_identity.decision_fingerprints_identical` 同样会 False。

**（二）入队无法归属。** `_metric_queue_enter` 的 `decision_id` 必须**显式**传（`kernel._metric_queue_enter`（@1fad58f kernel.py:1538，R8-A8） ：链路 stall / retirement 造成的重入队传 `None`，因为"把它记到上一个决策头上是错的"。全局切换正是这种重入队——它会以 `decision_id=None` 进指标，于是**改变了结果、但在逐决策账本里不可见**。测到的差异归不到任何决策上。

**（三）两种解释无法分开。** 同一份差异既可能是"调度选得更好"，也可能是"已 commit 的动作被事后改写了"。混在一起，任何归因都是编的。

**规则因此是**：新版本**只向前生效**；已在途的按旧版本走完本跳；parked 的在新版本下重算。

---

## 尚未确定的研究选择

> 以下每一条**本文件都没有替你决定**。每条给出"取不同选项会改变实验测到的什么"。
> 全部标注为 **未决定**。§最小执行设计 中的具体取值是**示例参数**，不关闭本节任何一条。

**1. 调度周期：固定 Δ 还是事件驱动？** —— **未决定**
选项：固定周期（复用 `scenario.time_step_s` 或新增 `schedule_epoch_s`）／纯事件驱动（广告到达、队列变化即重算）／混合（周期兜底 + 事件加速）。
改变什么：固定 Δ 会把"调度延迟"与周期绑定，测到的收益里含"定期重算的固定代价"；事件驱动会把代价挪到触发器频率上，容易测成"事件多 = 重算多"，把触发频率的效果误读成调度效果。**最小执行设计临时固定 Δ，仅为让 epoch 边界可观测，不构成对本条的裁决。**

**2. 调度粒度：只到方向，还是"方向 + 时隙"？** —— **未决定**
改变什么：只到方向 → 结果无时间维度，必须靠 epoch 失效，粒度粗、更接近缓存；"方向 + 时隙" → 表达力强，但一个 epoch 内可能无包可用，测到大量空转安装。

**3. `binding_scope` 是否允许包含 `dst_cell`？** —— **未决定**
改变什么：含 `dst_cell` → 同星不同目的地的包拿到不同结果，动作更"正确"，但作用域更窄、更靠近"按目的地缓存"；不含 → 一个 sat 一个动作，作用域最宽。**判定依据是 §1.4 的 D4（作用域是否覆盖多于一条流），不是"动作是否变化"**——后者已被删除，不参与决策。

**4. 安装延迟是否建模？** —— **未决定**
选项：install 与 commit 同刻（免费）／install 消耗 `execution.node_process_delay_s`／新增 `schedule_install_delay_s`。
改变什么：不建模 → 调度看起来免费，代价项缺失，违反 CHARTER 不变式 5（代价可量化）；建模 → 必须再决定 install 与 F2 是否重叠（`kernel.Kernel.__init__` @1fad58f kernel.py:1138）注释明确 F2 与 `compute_delay_s` **互不重叠**，install 落在哪一侧是新的选择）。

**5. "算力受限下的调度收益"用什么对照来测？** —— **未决定**
现状：算力争用**已可观测**（`execution.compute_servers_per_satellite` + timeline `compute_wait`，§4.5；`0` 时逐位等同于历史行为）。仍缺的是**对照设计**：
选项：同一 profile 跑 `compute_servers_per_satellite = 0`（无界）与 `= N`（有界）的配对；或固定有界、只对照"是否复用计算结果"；或把 `compute_wait.wait_s` 直接计入成本指标而不做配对。
改变什么：不做配对 → 无法把"调度省下的计算"与"调度本身多花的计算"分开，收益与代价混在一个数里；做配对 → 必须再决定 `compute_servers_per_satellite` 取几台、以及它与 `compute_delay_s` 的相对量级（`N × service` 决定排队是否真的会发生）。**本条是最可能改变②结论性质的一条。**

**6. `schedule_version` 放哪里：升契约还是走 timeline？** —— **未决定**
选项：(A) timeline `schedule_install` extra 键（decision row 19 键不动）／(B) 扩 `TIMELINE_FIELDS` 为十二时刻／(C) 把 `decision-rows/v1` 升为 v2，在 decision row 里加键。
改变什么：直接决定"这条证据能否进 receipt/ledger 信任链"。走 (A) 时，`schedule_install` 只在 timeline 流里（有 sha256 绑定与 sidecar manifest，但**不在 decision row 契约内**）；走 (C) 才逐决策可核验，但要动 receipt 键集与两个测试断言。

**7. D1 与 D3 的量化门槛，以及 D5 的年龄上界？** —— **未决定**
D1 只要求严格不等号，但"解耦到什么程度才算异步"没有定：`#decisions / #installs` 要到多少（2 倍？10 倍？）？D3 要求至少两次安装，但一次 run 至少要有多少次安装才够？D5 的年龄上界等于 epoch 长度还是另有独立上界？改变什么：门槛松 → 一个几乎逐包重算的实现也能通过；门槛紧 → 正确的粗粒度调度被判死。**注意：这三条都是"要多少"的问题，不是"要不要"的问题——"动作必须变化"不在其中。**

**8. 触发器本身的时刻要不要留字段？** —— **未决定**
现状：`t_decision_start` 是"决策开始"，**触发器自己的时刻没有字段**。是否为触发器新增 `t_trigger`（并说明它与 `t_measure` 的差）？改变什么：不留字段 → "触发到重算的延迟"无法测量，"事件驱动 vs 定时"无法对照；留字段 → 又是一次契约变更。

**9. 调度器允许在 frozen 语义下工作吗？** —— **未决定**
改变什么：若允许基于过时观测调度，②与①的差别只剩"触发方式"；若强制 refresh，则 epoch 内复用观测这件事本身就不成立。**本条与 §1 的 L2 定义直接冲突或共存，必须先裁决。**

**10. 是否允许集中式信息对象 `snapshot_global()`？** —— **未决定**
改变什么：允许 → ② 从"分布式异步调度"变成"集中式周期调度"，信息范围整段重写（广告时延、AoI 都不再是约束），测到的收益含"全局信息"这一项，与①不可比；不允许 → 维持分布式信息边界。若允许，必须把 `Q0-B`（未来信息）排除在外（`kernel.snapshot_global`（@1fad58f kernel.py:1996；Q0-B 注释 2005 明确它尚无独立视图）。

**11. 是否为②在 `lines/` 注册研究线？** —— **未决定**
`lines/README.md`："每条研究线一个文件。本表是唯一索引。" 当前只有 `TIME-SEMANTICS.md`（ACTIVE）。②未登记则它不是一个被承认的线，本文件只是设计稿。是否新开文件、是否复用 TIME-SEMANTICS、还是与①合并，由研究者定。

---

## 最小执行设计（可审）

### 目标：第一条可审证据是什么

**证明"一次计算确实覆盖了多个决策，且该覆盖不是按流缓存"。**

具体形态：同一 `schedule_version` 覆盖的 N 个决策，其 `observation_at_start.neighbours[*]` 的 `received_at` 与 `age_s` **逐邻居完全相等**（证据 1，证明"共享同一次观测"），且满足 §1.4 的 **D1--D5**（证据 2，结构性反退化判据）。**证据 2 不是"`chosen` 至少一次不同"**——那条判据已删除，理由见 §1.4。
两条都是**布尔/计数断言**，可从已落盘的 decision row 与 timeline 两流程序化核验，不需要新工具链。

### 前置条件

`lines/` 登记（见 未确定 第 11 条），以及一个新建 profile（形如 `CODE/leo_sim/profiles/t2_async_epoch_smoke.yaml`）。**未核实**：该 profile 的最终命名与是否复用 `t1_frozen_branch_smoke.yaml`。

### 改动清单（全部为"新增"，不新增引擎语义）

| # | 新增项 | 形态 | 作用 |
| --- | --- | --- | --- |
| 1 | `execution.schedule_epoch_s` | config 键，float > 0 | 一个观测被复用的时间窗长度 |
| 2 | `execution.schedule_scope` | config 键，枚举 `sat` / `sat_cell` | `binding_scope` 的键 |
| 3 | `execution.schedule_epoch_max_packets` | config 键，int ≥ 1 | 一个 epoch 覆盖的决策数上限 |
| 4 | `execution.schedule_require_frozen` | config 键，bool | 为 true 时非 `frozen` 模式 fail-closed |
| 5 | timeline milestone `schedule_install` | 自由 milestone + extra 键 | 一次安装事件；不进 `ATTEMPT_VERDICT_PRECEDENCE` |
| 6 | 越界核验器 | 折叠期只读检查 | §2.4 的信息范围判据 |

**不新增**：decision row 键（19 键契约不动）、receipt 键（V6 已够）、引擎动作（复用 `candidates`）、CLI 子命令、旁路通道。

### 输入—处理—输出

| 阶段 | 输入 | 处理 | 输出 |
| --- | --- | --- | --- |
| 输入 | config（含 4 个新增键）+ trace + seed | `config.py` 校验，风格同现有 fail-closed：`schedule_epoch_s > 0`；`schedule_epoch_s >= compute_delay_s`（否则一个 epoch 内算不完，语义不成立）；`schedule_epoch_max_packets >= 1`；`schedule_require_frozen` 且 `decision_observation_mode != "frozen"` → 拒（与 `config._validate_semantics`（@1fad58f config.py:470；frozen 需 compute_delay>0 的断言在 920 的既有风格一致） | `resolved_config.json`（含新键，被 `config_sha256` 覆盖） |
| 处理（epoch 开启） | 该 `sat` 的第一个待决策包 | 在 `t_decision_start` 取**一次** `observation_at_start`（沿用 `kernel._observation_at_start`（@1fad58f kernel.py:3325）、生成一次 `estimate_at_start`；`schedule_version += 1`；写一条 `schedule_install` | timeline 1 行（extra：`schedule_version` / `t_epoch_start` / `t_epoch_end` / `binding_scope` / `installed_action` / `sat`） |
| 处理（epoch 内复用） | epoch 内后续包 | **不重新取观测**，复用该 epoch 的观测与估计；仍逐包走 `decide_deferred` → `kernel._decide_from_frozen_observation`（@1fad58f kernel.py:3942） → 各自的 `commit_rejected` 或 commit | 每包 1 条 **19 键** decision row（`obs_mode="frozen"`，键集不变） |
| 处理（epoch 到期） | `now >= t_epoch_end`，或 epoch 内决策数达上限 | 作废该 epoch；下一个包开启新 epoch、新 `schedule_version` | 新的 `schedule_install` 行 |
| 输出（证据 1） | decision rows + `schedule_install` | 同一 `schedule_version` 覆盖的 N 个决策，其 `observation_at_start.neighbours[*].received_at` / `.age_s` 逐邻居相等 | 布尔断言（可程序化核验） |
| 输出（证据 2） | decision rows + timeline | **D1** 被覆盖的 forward 决策数 > `schedule_install` 行数；**D2** 每个被覆盖决策可指出版本；**D3** 至少两个不同 `schedule_version` 被安装且各带触发原因；**D4** 某次安装覆盖多于一条流，或该次安装时被覆盖流没有新包；**D5** 每决策的调度年龄落在声明策略内。**不检查"`chosen` 是否变化"** | 结构性反退化判据（§1.4） |
| 输出（账本） | decision + timeline 两流 | `fold_decision_ledger`：十一时刻 + 四个分离对象照常折叠；`schedule_install` 作为额外 milestone 出现，不被折成尝试判决 | `ledger.json`（十一字段齐备） |
| 输出（成本） | 同一 config | `minimal_branch_compare --decision-id N --forced-action D`：给出该分支的对照与代价 | `branch.json`（`pre_branch_identity` / `post_branch_divergence` / `candidates` / `action_cost`） |
| 输出（回执） | 两个流 | receipt 升为 V6：`decision_stream_contract` / `decision_log_sha256` / `timeline_log_sha256` | `receipt.json` = `leo-sim-receipt/v6` |

### 为什么这是最小的

- **不破契约**：decision row 19 键不动（`validate_decision_row` 仍双向通过），receipt 已是 V6，无需新 schema。
- **不新增旁路**：新信息只走 `timeline_sink`（与 `commit_rejected` / `node_process_start` 同一通道）；决策仍走 `decision_sink`。
- **完全复用既有因果工具**：每个受益包仍走自己的 `decide_deferred` → 自己的 `t_decision_start` → 自己的 commit，所以 `counterfactual.replay_with_forced_action`（含 `branch_fingerprint` 配对证明）、`decision_ledger.build_ledger`、`receipt` **全部原样可用**，无需改动。这正是"复用而有证据"的关键：**共享观测，但不共享决策身份。**
- **可产出负面结果**：若证据 2 不成立（同流 `chosen` 恒定），该实验**证伪**了"这不是按流缓存"，这是有价值的可审结论，不是失败。

### YAML 片段（字段名与 `config.py` 现有键风格一致；新键标注"新增"）

```yaml
scenario:
  duration_s: 60.0
  num_satellites: 24
  num_planes: 6
  time_step_s: 0.1

demand:
  offered_mbps: 2.0          # 既有键：输入业务
  packet_bits: 8000          # 既有键

links:
  isl_rate_mbps: 1000.0      # 既有键：物理容量
  downlink_rate_mbps: 100.0  # 既有键

execution:
  compute_delay_s: 0.05              # 既有键；frozen 要求 > 0（`config._validate_semantics`（@1fad58f config.py:470；frozen 需 compute_delay>0 的断言在 920）
  decision_observation_mode: frozen   # 既有键；本设计依赖它的"观测冻结 + 提交只校验合法性"
  node_process_delay_s: 0.0          # 既有键；本步不叠加 F2，保持代价可分
  # --- 以下四项为新增 ---
  schedule_epoch_s: 0.5              # 新增：一个观测被复用的时间窗（s），须 > 0 且 >= compute_delay_s
  schedule_scope: sat                # 新增：binding_scope 的键，取值 sat | sat_cell
  schedule_epoch_max_packets: 8      # 新增：一个 epoch 最多覆盖的决策数，须 >= 1
  schedule_require_frozen: true      # 新增：为 true 时非 frozen 模式 fail-closed

outputs:
  out_dir: out/sched-min
  trace_path: null
  plotting: false
```

### 核验命令（必须 cd 到仓库根；`python3` = 3.14.2）

```bash
cd /Users/lge/Desktop/topic/leo-exp-main

# 1) 跑出两个流；两者齐全才升 V6
python3 -m CODE.leo_sim run \
  --config CODE/leo_sim/profiles/t2_async_epoch_smoke.yaml \
  --out out/sched-min \
  --decision-log out/sched-min/decisions.jsonl \
  --timeline-log out/sched-min/timeline.jsonl

# 2) 折叠十一时刻 + 四个分离对象（既有 CLI，不新增）
python3 -m CODE.experiment_platform.fold_decision_ledger \
  --decision-log out/sched-min/decisions.jsonl \
  --timeline-log out/sched-min/timeline.jsonl \
  --out out/sched-min/ledger.json

# 3) 单次候选动作干预的逐包分支比较（既有 CLI，不新增）
python3 -m CODE.experiment_platform.minimal_branch_compare \
  --config CODE/leo_sim/profiles/t2_async_epoch_smoke.yaml \
  --decision-id <N> --forced-action E \
  --out out/sched-min/branch.json

# 4) 证据 1/2 与契约不变性
python3 - <<'PY'
import json
from CODE.leo_sim import decision_ledger as d
rows = [json.loads(l) for l in open('out/sched-min/decisions.jsonl') if l.strip()]
assert rows, "decision stream empty"
for r in rows:
    d.validate_decision_row(r)            # 19 键契约必须仍然成立
assert len(d.DECISION_ROW_KEYS) == 19
print("rows", len(rows), "key sets", len({frozenset(r) for r in rows}))
PY
```

### 本设计未核实的事项

1. 具体 profile 文件名与参数取值未定；`24 / 6 / 2.0 Mbps / 0.5 s` 均为占位示例，不是标定结果。
2. `receipt verify` 对 V6 的完整校验路径**未逐行核验**（本文件只核到 `receipt.RECEIPT_KEYS_V6`（@1fad58f receipt.py:61） 与 `receipt.STREAM_FILES`（@1fad58f receipt.py:78）。
3. `schedule_epoch_s >= compute_delay_s` 这条约束的取值边界未做数值实验，是按语义推出的必要条件。
4. 本文件未运行任何仿真；§最小执行设计的全部输出都是**设计意图**，不是实测结果。任何据此声称的收益均为**未核实**。
5. 20 Mbps 以上的压力窗口限制仍然生效（`lines/TIME-SEMANTICS.md` 引 `T1-PRESSURE-WINDOW-PASS`，finding R8-A9：access 在约 20 Mbps 以上未解除限流，利用率平台在 0.56）。若在更高速率下取示例参数，测得的是限流平台，不是调度效果。
