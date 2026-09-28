# TIME-SEMANTICS-QUARTET — 四组时间语义的共同契约

| 项 | 值 |
| --- | --- |
| 仓库 | `leo-exp-main`（`origin/main` = `1fad58f` 检出，detached HEAD，工作树含未提交改动） |
| 本文性质 | **契约层补充**。定义"四组时间语义各是什么、要能互比还缺什么、谁允许看什么"，不定义引擎行为 |
| 上游契约 | `98c858f` 交付集 `00e-field-time-and-information-permission-table.md`（表 A/B/C + 判读规则）——**本文不替代它** |
| 引用规则 | 引 00e 用它自己的编号（`A1..A14` / `B1..B15` / `C-1..C-3` / `M1..M15`）；引本仓代码**必须标身份**，因为行号随身份变化（见下节） |
| 纪律 | 本文不新增引擎语义；凡"需要新增"的标 **待实现** 并给最小形态；无法核实的标 **未核实** |

本文只回答一个问题：**让四组时间语义可比较，最少必须共同记录什么、共同禁止什么。**
四组中两组已实现、两组缺失；缺的两组（候选到达时刻 / 共同未来时刻）共享同一种缺失能力——**前向投影**。同一个缺口被两次需要，不是一个缺口两种说法。

> **📌 状态更新（2026-09-28，实现方补记；不改写本文研究契约）**
> - 上表基线 1fad58f（含"工作树含未提交改动"）是本文写作时的身份，**不是现行身份**。
>   现行证据身份 8a31496，实验只在 VM 上跑，见 CODE/work/WP-T1-COMPLETE/STATUS.md。
> - 本文标「**待实现**」的前向投影能力，已落地为 CODE/experiment_platform/time_alignment_compare.py
>   的 resource_work_ahead：按内核事件序重建目标包前方的真实工作量，区分**排队数据 / 在服务剩余 /
>   控制优先 / 目标自身 / FIFO 后方**，判不出时保持"未知"而不填 0。这与第 145–146 行对 rate_model=mcs 下
>   in_service_remaining_bits 为 null 的处置姿态一致：宁可未知，不给错数。
> - 第 12 行"四组中两组已实现、两组缺失"的**能力**判断仍成立；但两组是否产生**收益**仍未验证——
>   当前单分支诊断场景未激活机制（contention 上四组 regret 全 0）。

---

## 与既有交付集的关系

`/Users/lge/Desktop/topic/leo-experiment-platform/ANALYSIS/ARRIVAL-TIME-T1-20260923/`（未跟踪、未提交，只读引用）已经交付了一套完整的字段级权限规范，核心是 `00e-field-time-and-information-permission-table.md`：

| 00e 的部件 | 它已经固定了什么 | 本文怎么办 |
| --- | --- | --- |
| **表 A**（A1..A14） | 决策时刻**允许**使用的信息，逐条给出语义与源码位置 | **不重写**。本文只补一层：把 A1..A14 按四组语义重新归类，并指出哪些条目在缺失语义下根本没有对应物 |
| **表 B**（B1..B15） | 决策时刻**禁止**使用的信息（未来真值类），含"可以记录但不得进入决策输入" | **不重写**。本文补的是 B 类对象在语义 3/4 下的**角色变化**（从"禁止使用"变成"要被预测"） |
| **表 C-1**（十一个时刻） | 每个时刻的语义、折叠来源、缺失条件 | **不重写**。本文补的是"每个时刻属于哪组语义"以及"语义 4 需要的时刻在 C-1 里没有位置" |
| **表 C-2**（13 种里程碑 / 15 个调用点） | 全部 timeline 里程碑及其归属 | 不在本文范围内 |
| **表 C-3**（决策行 19 键） | 键集契约与每个键的写入位置 | **不重写**。本文补的是嵌套层（R-C4）与语义标签的作用 |
| **判读规则（三条硬规则）** | 模式决定测量时刻 / 真值只在事后使用 / 缺失不等于零 | 见"规则（可检查）"：本文引用它们为**强版本**，只写它们**没有**覆盖的差异 |

本文补的那一层，是三者之间缺失的接口：**00e 回答"某个字段能不能用"，本文回答"在四组语义的哪一组下、在哪个时刻、它是否存在"**。

### 代码身份（本次实测）

| 身份 | clean `code_sha256` | 核实方式 |
| --- | --- | --- |
| `98c858f`（00e / 07 冻结的身份） | `2a114890e8b11e429fec45a6b1efcb81c1d80a8e195a9c9407a250d218f16dbe` | 从**两个**仓的 git 对象按 `receipt.code_sha256()` 的算法重算，两仓一致 ✔ |
| `1fad58f`（= `origin/main`） | `57cca0eda33994844cf072c91914545708b53837957d829af248ab9ad1a8b011` | 同上，两仓一致 ✔ |
| `leo-exp-main` **工作树**（含未提交改动） | `7f111281e4c4af947b9aee98af48ba0198beda634957de59150d02936c292b34` | 直接对工作树 `CODE/leo_sim/*.py` 重算（本文引用的行号即此身份） |

**关键结论（决定了本文可以引用 00e 的哪些行号）**：

- `CODE/leo_sim/kernel.py` 与 `CODE/leo_sim/decision_ledger.py` 在 `98c858f` ↔ `1fad58f` 之间**逐字节相同** → 00e 对这两个文件的行号**在 main 的提交态仍然成立**。
- `config.py` 在两者之间**有改动且 AST 不同**；但 `VALID_OBSERVATION_MODES` 在两处**恰好都是 `config.py:247`**。`frozen` 需要 `compute_delay_s > 0` 的校验在 `98c858f` 是 `config.py:869-872`，在当前工作树是 `config.py:910-921`。
- `RECEIPT_KEYS_V6` / `decision_stream_contract` 在 `98c858f` **已经存在**（`receipt.py:61-62`）。v6 流绑定不是 main 才有的能力。
- **一个易踩的陷阱**：`leo-experiment-platform` 的 HEAD 是 `98c858f`，但它的**工作树文件内容当前等于 `1fad58f`**（本次实测该工作树的 `code_sha256` = `57cca0ed…` = `1fad58f` 的 clean 值；即以 `98c858f` 为 HEAD 却带着等于下一版的未提交改动）。所以「在 00e 的仓里看一眼」读到的可能不是 00e 冻结的那份代码。本文凡引 00e 均以 00e 自己声明的 `98c858f` / `2a114890…` 为准。
- **本文行号漂移的唯一来源是 `leo-exp-main` 工作树的未提交改动**：`kernel.py` 相对 `1fad58f` 有 134 行量级的未提交改动，`config.py` 25 行。`ANALYSIS/CURRENT-EVENT-TIMELINE.md` 的行号基于更早的 4615 行版本，与上述三者都不通用。

### 更正一条口头说明（未复现）

收到的说明称："`98c858f` 与 `1fad58f` 在 `CODE/leo_sim` 上只差 `trace.py`/`__main__.py` 的声明文本 + 两个测试文件，AST 归一化后相同。"

**实测未复现。** 在 `CODE/leo_sim`（含 `tests/`）内，`98c858f → 1fad58f`：

- **9 个文件字节不同且 AST 级不同**：`__main__.py`、`config.py`、`matrix.py`、`metrics_independent.py`、`receipt.py`、`trace.py`、`tests/test_config.py`、`tests/test_pairing_trace_identity.py`、`tests/test_review_residuals.py`；
- **7 个新文件**：`pullback.py`、`recompute.py`、`tests/test_burst_transform.py`、`tests/test_formal_stream_gate.py`、`tests/test_pre_experiment_trust.py`、`tests/test_pre_experiment_trust_round2.py`、`tests/test_pullback_authority.py`；
- 全仓 `*.py` 共 **20 条路径**变化（12 增 8 改）。

所以"只差声明文本、AST 相同"不成立。**这不影响 00e 的结论有效性**（`kernel.py` 与 `decision_ledger.py` 确实未变，而 00e 的表 A/B/C 全部锚在这两个文件上），但它废掉了"跨身份可直接沿用行号"的做法——本文按实测写，逐条标身份。**未核实**：`98c858f` 交付集里的实验证据能否在 `1fad58f` 上逐位复现（07 §3 只对**诊断结论与 F2 记账**声明过逐位一致，本次未重跑）。

---

## 四组语义的定义

| # | 语义 | 观测时刻 | 动作决定时刻 | 生效时刻 | 平台现状 | 对应字段 |
| --- | --- | --- | --- | --- | --- | --- |
| 1 | 原始状态 | `t_decision_start` | `t_decision_start`（观测与推断同一瞬间） | `t_decision_commit` | 已实现 | `obs_mode="frozen"`；`t_measure=t_decision_start`；`observation_at_start`（`source="frozen_snapshot_before_compute"`）；`estimate_at_start` |
| 2 | 延迟重观测（提交时刻重读） | `t_decision_commit` | `t_decision_commit` | `t_decision_commit` | 已实现（默认） | `obs_mode="refresh"`；`t_measure=t_decision_commit`；`observation_at_start`（`source="commit_time_state"`）；`estimate_at_start` |
| 3 | 候选到达时刻 | **不存在**（要求投影到"该候选被选中后到达对端"的时刻） | `t_decision_start`（本应在同一决策内） | `t_peer_arrival`（实际只在事后得知） | **缺失** | 只有事后真值 `truth_at_target`（`t_arrival` / `contended_direction` / `egress`）；决策输入侧字段**待实现** |
| 4 | 共同未来时刻 | **不存在**（要求把各候选投影到同一未来时刻 `t_common`） | `t_decision_start` | `t_common`（无定义） | **完全缺失** | 无。平台**全部**既有预测都取决策时刻值（见下） |

**1. 原始状态（`frozen`）**（工作树 `kernel.py`）：观测在 `t_decision_start` 取得并在同一瞬间推断出动作（`_observe_preferred_action`，`:3945`），提交时刻只做**合法性校验**（`_decide_from_frozen_observation`，`:3998`）。校验失败不是"重解一次"，而是记 `commit_rejected`（`:4059,4071`）并 park（`:4095-4097`）；观测本身推出 hold 的记 `frozen_inferred_hold`（`:4083`）。被拒/被 hold 的尝试**不写 decision 行**，只存在于 timeline 流。配置侧：`VALID_OBSERVATION_MODES`（`config.py:247`，两身份同号）；`frozen` 要求 `compute_delay_s > 0`（工作树 `config.py:910-921`，`98c858f` 为 `:869-872`；无计算区间就无可冻结之物，报配置错误而非静默降级）；`frozen` 不允许与 learning arm 同时出现（工作树 `kernel.py:1140-1143`）。

**2. 延迟重观测（`refresh`）**：默认模式（`config.py:413`）。实现是"延迟重观测"：计算落地后重新读 `env.now` 与实时状态，再选动作（`_decide` → `_record_decision`，工作树 `:3550-3561,4273`）。因此观测时刻**就是**提交时刻。

判别性实测夹具（不是论证）：`tests/test_frozen_observation.py:225-234` 让唯一 ISL 在决策开始时 down、提交时 up，`refresh` 提交了那次 forward（断言原文 "the ISL was down at 5.082, so this forward is not knowable then"）；`frozen` 在同一夹具下不会（`:237-251`）。反向夹具 `:254-273`：ISL 在 t0 up、提交时 down，`frozen` 留下 `commit_rejected`，而 `refresh` **根本没有 `commit_rejected` 这个概念**——它直接重解。

**3. 候选到达时刻（缺失）**：平台**只有事后真值** `decision_ledger.truth_at_target`（`decision_ledger.py:181-225`，该文件两身份逐字节相同），从 `peer_arrival` 携带的 `egress_snapshot` 折叠，字段 `t_arrival` / `contended_direction` / `egress`，MISSING 时给五种具名原因（`no_arrival_recorded`、`arrival_carries_no_egress_snapshot`、`contended_direction_unresolved`、`peer_delivered_the_packet`、`contended_direction_absent_from_snapshot`）。作为**决策输入**它不存在——不是字段缺失，而是**没有任何机制能生成它**。

**4. 共同未来时刻（完全缺失）**：平台全部预测都在**决策时刻**取值，`prediction_method` 已写死：

| 对象 | `prediction_method` | 出处（工作树） |
| --- | --- | --- |
| `estimate_at_start` | `same_policy_on_advertised_peer_state` | `kernel.py:3498` |
| 候选级审计预测 `_peer_downstream_truth` | `same_policy_full_cache_at_decision_time` | `kernel.py:3706` |

两个方法名里的 `at_decision_time` 就是结论：**没有任何前向投影机制**。语义 3 与 4 都要这一种能力，只差"投影到哪个时刻"这一个参数。

---

## 共同字段

**不重复 00e 的表 C-1 / C-2 / C-3。** 本节只做 00e 没做的两件事：给每个时刻**归属语义**，并列出四组语义要互比时还缺的字段。

### 十一时刻归属哪组语义（对 00e 表 C-1 的补充）

| 00e C-1 序号 | 字段 | 语义归属 | 00e 已登记的缺失条件 | 本文补充 |
| --- | --- | --- | --- | --- |
| 1 | `t_measure` | **由语义决定**：1→`t_decision_start`，2→`t_decision_commit`，3/4→**取值待定** | 不缺失 | 语义 3/4 下取投影时刻还是仍取 t0，是**研究选择**（见末节第 5 条），方向相反 |
| 2 | `t_control_rx` | 1/2/3/4 共用 | 无 cache 条目时 MISSING | 00e B14 已把它标为"对不读 cache 的确定性路由是反事实上界"——四组语义下都必须带这个标签，否则会把它读成"决策用了这么新的信息" |
| 3 | `t_decision_start` | 四组共用的起点 | 不缺失 | — |
| 4 | `t_decision_commit` | 语义 2 的观测时刻；四组共用的提交点 | 不缺失 | — |
| 5-7 | `t_local_queue_enter` / `t_service_start` / `t_service_finish` | 四组共用的本地生效链 | 无对应里程碑时 MISSING | 语义 1 下**提交时刻的合法性失败**会让这三项恒 MISSING（被拒尝试不写 decision 行） |
| 8 | `t_peer_arrival` | **语义 3 的目标时刻** | 无到达里程碑时 MISSING | 语义 3 要做的是把这个事后时刻**提前到决策时刻**预测；今天只有事后值 |
| 9-11 | `t_peer_redecision` / `t_peer_target_egress_enter` / `t_peer_target_egress_service_start` | **语义 3 的对端侧时刻** | "无后继重决策时**恒为** MISSING" | **在 `frozen` 下"无后继重决策"不是边缘情况而是常见结果**：后继尝试被 hold/拒绝就不写 decision 行。因此语义 3 依赖的三个时刻在语义 1 下系统性缺失 |
| — | **`t_common`** | 语义 4 唯一需要的时刻 | — | **不在 C-1 里，也没有位置**。这是结构事实：十一时刻是"已发生事件链"，语义 4 需要一个"未发生的约定时刻" |

### `t_measure` 在四组语义下取值不同

```
语义1 frozen    t_measure = t_decision_start    （measure_instant: decision_start if mode=="frozen"）
语义2 refresh   t_measure = t_decision_commit   （else 分支）
语义3 候选到达   t_measure = ???                （投影到该候选到达对端的时刻；待定）
语义4 共同未来   t_measure = t_common            （四候选共用；待定）
```

两点必须同时进契约：(a) **`t_measure` 是每决策一个标量，且不说明任何单个邻居何时被测到**（`decision_ledger.py:35-38` 原文）——邻居级时刻只能读 `observation_at_start.neighbours[*].received_at`；(b) 计算时延为 0 时语义 1/2 重合，**区分失效但字段仍分别记录**，否则计算时延落地后无法回填语义。

### 四组语义必须共同记录、而现在不存在的字段（**待实现**）

| 字段 | 语义指向 | 现有来源 | 状态 |
| --- | --- | --- | --- |
| **`resource_scope`** | 争的是本地出口 / 对端出口 / 下行 / F2 / 计算 / 控制面 | 无（只隐含在字段名前缀里） | 需新增 |
| **目标资源标识（决策时刻版）** | "若选该候选，目标包将争哪个出口" | 仅事后版 `truth_at_target.contended_direction`（`decision_ledger.py:196-198`） | 需新增；无法确定时写 `null` + 原因，**不得猜测** |
| **`work_ahead_bits` / `work_ahead_s`** | 目标包面前的工作量（bits / 秒） | bits 已有（见"资源"）；秒**无任何来源** | 需新增 |
| **投影声明 `projection_model` / `projection_horizon_s` / `t_common`** | 语义 3/4 的语义由它定义 | 无 | 需新增；**语义须先由研究者定** |

**为什么"不共同记录就无法比较"**：四组要比较的是"同一决策、同一批候选、同一个被争资源"上的差异。(a) 缺 `resource_scope` 与目标资源标识时，差异可能来自"争的不是同一个队列"；(b) 缺 `obs_mode`/`t_measure` 时，"哪一组依据了更旧的信息"无法从数字读出——`decision_ledger` 对此有硬要求：**区分靠标签，不靠数字**（`:22`）；(c) 缺候选动作集时，"差异"可能是可选集差异；(d) 只有 bits 没有秒时，跨语义比较混用了"排队长度"与"还要等多久"两种量纲。

---

## 资源

| 被竞争的资源 | 现可度量字段 | 目标包面前的工作量 | 缺失 |
| --- | --- | --- | --- |
| ISL 出口队列（本地） | `own_queue_bits`（`kernel.py:3548-3549`，A1） | bits：`own_queue_bits[d]` | 无秒 |
| ISL 出口队列（对端，到达时） | `egress_snapshot`（`kernel.py:3637-3661`；00e B5） | `data_bits`+`ctrl_bits`（`minimal_branch_compare` 命名为 `queued_bits_ahead`）、`in_service_remaining_bits`、`ctrl_packets` | 决策时刻不可得；无秒 |
| ISL 出口队列（对端，审计） | `_peer_downstream_truth`（`kernel.py:3663-3719`；00e B3/B4） | `peer_egress_data_bits`+`peer_egress_ctrl_bits` | 审计专用；无秒 |
| **下行队列** | 无快照覆盖——`_egress_snapshot` 只遍历 `self.isls[sat]`（`kernel.py:3647`） | 无 | **覆盖范围缺口**：语义 3/4 若含下行需扩展 |
| **节点处理 F2** | `execution.node_process_delay_s`（`kernel.py:1154`）；`node_process_start`/`node_process_end`（M13/M14） | 与计算**互不重叠**（`kernel.py:1145-1153`） | 无（已可归因） |
| **计算** | `execution.compute_delay_s`（`kernel.py:1105`）；区间 `t_decision_start`→`t_decision_commit` | 每决策固定时延，不属任何队列 | 无 |
| **控制面缓存** | `info_audit.cache_entries[*]` 的 `generated_at/received_at/age_s/hops`（`kernel.py:3796-3805`；A3/A9） | 控制包与数据包**共用同一条 ISL 队列**，可见代理是 `ctrl_bits`/`ctrl_packets` | 控制面自身无独立排队度量 |

### 现有可能只有 bits —— 缺"还差多少秒"

- 两套 bits 口径都只回答"前面还有多少 bit"（`data_bits+ctrl_bits` 与 `in_service_remaining_bits`），**没有一个字段回答"目标包还要等多少秒才能开始服务"**。固定速率下可换算，变速率下不可换算。
- `in_service_remaining_bits` 在 `rate_model=mcs` 下**为 null**，方法标签写明原因 `unavailable_varying_rate`（`kernel.py:3611-3635`，原文"under MCS the rate is distance-dependent, so no number is produced rather than a wrong one"）。`minimal_branch_compare.py:428-429` 已把这条列为输出的显式限制。
- 对端估计侧更窄：广告不携带在传工作量，`estimate_at_start` 只能写 `peer_in_service_remaining_bits_estimate: None` 并把该字段列入 `not_advertised`（`kernel.py:3496-3497`）。**语义 3/4 若要"还差多少秒"，今天的字段全都不够。**

**最小形态（待实现）**：不加任何调度语义，只在既有快照/审计字典里增加两个只读字段——`work_ahead_bits`（＝今天 `data_bits+ctrl_bits` 的显式命名）与 `work_ahead_s`（＝`work_ahead_bits / rate_bps`，**仅在速率确实已知且不中途变化时写入，否则 `null` + 方法标签**，与 `_in_service_remaining` 的既有姿态一致）。速率来源只能是决策时刻可得的量。

> 本文不把"按流缓存"当作异步流量调度的替代方案。缓存是信息可见性问题，调度是服务顺序问题；本文件不为它保留字段。

---

## 评分器

**00e 表 B 已把 B8（`score_downstream_predictions` 的任何读数）列为禁止进入决策输入。本节补的是两个评分器的分工、入口缺失，以及语义 3/4 需要的第三个评分器。**

| 评分器 | 输入字段 | 真值来源 | 在线 / 离线 | 回答什么问题 | 现状入口 |
| --- | --- | --- | --- | --- | --- |
| `decision_ledger.score_downstream_predictions`（`decision_ledger.py:440`；00e **B8**） | `info_audit.candidate_truth[chosen].downstream`（B2/B3） + `peer_arrival.egress_snapshot`（B5） + 后继 `chosen` | **事后真值**（`summary["source"]="info_audit_truth_at_commit"`，`:548`） | **仅离线** | 提交时刻审计预测与到达真值差多少 = **预测精度上界** | 只有测试调用（`tests/test_downstream_truth.py:177`、`tests/test_frozen_ledger.py:460`）；**无生产入口** |
| `decision_ledger.score_start_estimates`（`:553`） | `estimate_at_start`（含 `information_source`）+ `peer_arrival.egress_snapshot` + 后继 `chosen` | 已实现真值（到达快照），**输入是 t0 合法信息**；输出固定 `truth_used: False`（`:613,649`） | **可部署**（在线口径） | 决策时刻信念的质量 = 陈旧邻居状态错位本身 | 同上；**无生产入口** |
| **`score_projected_estimate`** | 语义 3/4 的投影输出 + `peer_arrival.egress_snapshot` | 已实现真值 | 离线评分 / 输入可部署 | 前向投影比"不动"好多少 | **需新增** |
| **`score_work_ahead_seconds`** | `work_ahead_s` + 实际 `t_service_start` | 已实现时间链 | 仅离线 | bits 口径与秒口径是否会给出不同的候选排序 | **需新增** |
| **`score_refresh_staleness`** | 语义 2 的 `observation_at_start` 与语义 1 的同一决策快照**配对** | 两个已实现对象 | 仅离线 | 计算区间内状态变了多少（= refresh 抹掉了多少陈旧性） | **需新增** |

**口径分工（硬性）**：离线真值诊断 → `score_downstream_predictions`（`decision_ledger.py:440-449` 明写它是 "an upper bound on predictive accuracy and must never be quoted as the stale-neighbour misalignment of a t0 belief"）；在线可部署 → `score_start_estimates`。任何"决策时刻信息质量"的结论只能引用后者；**两者之差就是事后性带来的虚高**（`:558-561`）。

**必读警告**：两个评分器**都没有生产调用者**（全仓 grep 只命中测试与自身模块）。也就是说，00e 判读规则 2 的执行方式（"检查决策函数的输入集合是否与表 A 一致"）目前**没有任何流水线在替它做**——标签写在返回值里，但没有机制阻止引用者转述时不带标签。

---

## 信息权限矩阵

**本节不重写 00e 的表 A / 表 B。** 它做的是把 A1..A14 与 B1..B15 按四组语义**重新归类**，并标出哪些条目在缺失语义下**根本没有对应物**。

### 矩阵（00e 的 7 个关键对象 × 四组语义 × 在线策略）

图例：`允许`=该语义下作为决策输入合法且可得；`仅离线`=可记录、可评分，禁止进入决策路径；`禁止`=同上但连语义内也不产生；`不存在`=该语义下这个对象没有对应物。

| 信息对象 | 00e 编号 | 语义1 frozen | 语义2 refresh | 语义3 候选到达 | 语义4 共同未来 | 在线策略 |
| --- | --- | --- | --- | --- | --- | --- |
| `observation_at_start` | **A13** | 允许（t0 快照） | 允许（提交时刻重读） | 允许（t0 快照） | 允许（t0 快照） | 允许 |
| `estimate_at_start` | **A13** | 允许（t0 合法预测） | 允许（t0=提交） | 允许（投影的**基线**） | 允许（投影的**基线**） | 允许 |
| `truth_at_commit` | **B1** | 仅离线 | 仅离线 | 仅离线 | 仅离线 | **禁止** |
| `truth_at_target` | **B6** | 仅离线 | 仅离线 | 仅离线**且是预测目标** | 仅离线**且是预测目标** | **禁止** |
| `info_audit.candidate_truth` | **B2/B3/B4** | 仅离线 | 仅离线 | 仅离线 | 仅离线 | **禁止** |
| `egress_snapshot` | **B5** | 仅离线 | 仅离线 | 仅离线（评分基准） | 仅离线（评分基准） | **禁止** |
| 控制面广告 | **A3..A6, A9** | 允许（受 age 限制） | 允许（受 age 限制） | 允许（**投影的输入来源**） | 允许（**投影的输入来源**） | 允许 |

每行理由（一句话）：`observation_at_start` 是"决策实际依据了什么"的唯一记录（`:3335-3394`），四组语义都必须记录它，变的是 `t_observed` 与 `source`；`estimate_at_start` 的文档明写只有 obs-time 真正被告知的东西可以进入（`:3401-3403`），因此它是四组共用的唯一合法预测对象，语义 3/4 把它当基线而非替代品；`truth_at_commit` 在**提交之后**于 `_record_decision` 内构造（`:3570-3580`），四组语义下都只能离线；`truth_at_target` 的文档写明"a target instant that has not happened yet is MISSING with the reason why"（`decision_ledger.py:188-190`），决策时刻**结构上不可得**；`candidate_truth` 每个字段自带 `field_sources[*].source="direct_kernel_state"`、`age_s=0.0`（`:3770-3777`）且 `mapping_status="truth_audit_not_learner_tensor"`（`:3809`）；`egress_snapshot` 的文档写明它是"the ground truth a decision-time prediction can be scored against"（`:3638-3643`）——**定义上就是评分基准**；控制面广告是唯一被设计的在线信息通道，年龄必须随值一起传递（`generated_at/received_at/age_s`，`:3372-3374`）。

### A/B 条目按语义重新归类

| 00e 条目 | 语义1 frozen | 语义2 refresh | 语义3 候选到达 | 语义4 共同未来 |
| --- | --- | --- | --- | --- |
| A1 `own_queue_bits` | 允许 | 允许 | 允许（但只描述"现在"） | 允许（同上） |
| A2 `deliver` 合法性 | 允许 | 允许 | 允许 | 允许 |
| A3 本地 cache 有效条目 | 允许 | 允许 | 允许（**唯一**可投影来源） | 允许（同上） |
| A4 广告 `isl_queue_bits` | 允许 | 允许 | 允许 | 允许 |
| A5 广告 `isl_propagation_s` | 允许 | 允许 | 允许 | 允许 |
| A6 广告 `serve_cells` | 允许 | 允许 | 允许 | 允许 |
| A7 静态拓扑 | 允许 | 允许 | 允许 | 允许 |
| A8 第一跳几何/速率 | 允许 | 允许 | 允许 | 允许 |
| A9 cache AoI | 允许 | 允许 | 允许（**陈旧性的唯一显式字段**） | 允许 |
| A10 oracle 全局当前知识 | 允许（`analysis_upper_bound`） | 允许（同左） | 允许（同左） | 允许（同左） |
| A11 frozen 合法性校验输入 | 允许（**仅**校验合法性） | 不存在（该模式无此概念） | 允许（三组共用同一合法性路径） | 允许（同左） |
| A12 决策身份字段 | 允许 | 允许 | 允许 | 允许 |
| A13 决策行输出对象 | 允许（**纯输出**） | 允许（纯输出） | 允许（纯输出） | 允许（纯输出） |
| **A14 学习观测向量** | **不存在** | 允许 | 允许 | 允许 |
| B1..B5, B8..B12 | 仅离线 | 仅离线 | 仅离线 | 仅离线 |
| **B6 `truth_at_target`** | 仅离线 | 仅离线 | **仅离线＋预测目标**（角色改变） | 仅离线＋预测目标 |
| B7 三个 `t_peer_*` | 仅离线，**且系统性 MISSING** | 仅离线 | 仅离线（语义 3 要预测的量） | 仅离线（同上） |
| **B13 目标包自身入队** | 不存在（无语义 4 时刻） | 不存在 | 需适用（**待定**） | **已登记的唯一语义 4 条目** |
| B14 `t_control_rx` 的反事实解读 | 仅离线（且是反事实上界） | 仅离线（同左） | 仅离线（同左） | 仅离线（同左） |
| B15 F2 区间不算计算时间 | 适用 | 适用 | 适用 | 适用 |

### 在缺失语义下**根本没有对应物**的条目（本节的核心产出）

1. **语义 1 下 `A14`（学习观测向量）不存在**：`frozen` 与 learning arm 互斥（`kernel.py:1140-1143`）。学习臂的观测契约在冻结语义下**没有实现**——这不是"禁止"，是"没有这个东西"。
2. **语义 3/4 下 `A1/A4/A5/A6/A8/A9` 全部存在，但全部不够**：它们都描述"现在"，其中没有一条描述"候选到达时"。把状态推进到 `t_peer_arrival` 需要投影，而 A 表中**没有任何条目对应"未来"**。
3. **语义 4 在 A 表中零对应物**：`t_common` 不是任何已有字段的函数；`A` 表里唯一与"共同"沾边的是 A4/A6（邻居自我报告的、按 `advertise_interval_s` 周期刷新的量），但它们的时刻由邻居决定，不是实验者选择的。语义 4 在 A/B 两表中的**唯一**已登记条目是 `B13`（一条禁止条目）。
4. **语义 3 下 `B6` 发生角色变化**：`truth_at_target` 从"禁止使用的真值"变成"**要被预测的对象**"。00e 表 B 没有覆盖这一层——同一个量必须同时以"离线真值"和"预测目标"两种角色存在，且**必须用不同字段区分**，否则评分会自我实现（预测值即真值）。
5. **投影的输入权限必须 ⊆ 表 A**（本节新增的硬约束）：投影若读 `B12` 禁止的对端真实队列，它就不是投影，而是 `truth_at_commit` 换了个名字。这是语义 3/4 唯一能防止"预测作弊"的约束。

---

## 规则（可检查）

**00e 的"判读规则（三条硬规则）"（00e:134-138）已经是本文三条纪律的强版本，本节不平行重复，只写差异。** 映射如下：

| 本文的纪律 | 00e 判读规则中的强版本 | 00e 已给出的检查方式 | 本文补的差异 |
| --- | --- | --- | --- |
| A 区分离线真值诊断与在线预测 | 规则 2「真值只在事后使用」（+ 表 B） | "检查决策函数的输入集合是否与表 A 一致" | 00e 管**字段级**许可；本文补**评分器级**许可：两个评分器输出哪个能进结论、以及**都没有生产入口**这一事实 |
| B 不得把 refresh 当作预测补偿 | 规则 1「模式决定测量时刻」 | `obs_mode` 未知值 fail-loud（`decision_ledger.py:148-156`） | 00e 管"时刻由模式决定"；本文补 **`refresh` 与前向投影在语义上不同**这一条——00e 的规则 1 不涉及投影 |
| C 不得把事后未来真值注入在线策略 | 规则 2（同 A）+ 表 B 全部 | 同左 | 00e 管"禁止使用"；本文补**角色变化**（语义 3 下 B6 是预测目标）与投影输入权限 ⊆ 表 A |

> 另注：`07-caliber-decisions.md` §5 的"三条纪律"是**另一根轴**（口径标签：packet_bits / 链路 / 代码身份 / 证据级别），与本文 A/B/C 不重叠，也不互相替代。

### A 的可检查形式

| 规则 | 检查形式 | 现状 | 证据 |
| --- | --- | --- | --- |
| **R-A1** 任何预测质量数字必须带来源标签 ∈ {`estimate_at_start`, `info_audit_truth_at_commit`} | 断言 `summary["source"]` 非空且在该集合内 | **硬**（对两个既有评分器） | `decision_ledger.py:514,548,650` |
| **R-A2** 事后评分器不得省略"这是事后"的自述 | `score_downstream_predictions` 的 `summary["source"]` 与每条 `scores[*]["source"]` 都等于 `info_audit_truth_at_commit` | **硬** | 同上；`truth_used` 只出现在可部署评分器上（`:613,649`），两者键集不同即无法误认 |
| **R-A3** 引用"决策时刻信息质量"时不得引用事后评分器 | 结论文档中的数字能追溯到 `score_start_estimates` 的输出 | **仅约定** | 两个评分器**均无生产入口**（全仓 grep 只命中测试）；无任何工具在执行引用时的口径校验 |
| **R-A4** 两个评分器必须能被同时产出并配对 | 同一 run 的两次评分共用同一 `(decision_rows, timeline_rows)`，差值可算 | **仅约定（入口缺失）** | 需新增一个聚合入口；差值的解释目前只写在 `decision_ledger.py:558-561` 的注释里 |

### B 的可检查形式

| 规则 | 检查形式 | 现状 | 证据 |
| --- | --- | --- | --- |
| **R-B1** `refresh` 行的观测时刻必须等于提交时刻 | `obs_mode=="refresh" ⇒ t_measure == t_decision_commit == observation_at_start.t_observed` | **硬**（构造事实） | 工作树 `kernel.py:3551-3559` 用 `committed_at` 构造 `source="commit_time_state"`；`measure_instant`（`decision_ledger.py:159-168`）走 `else` |
| **R-B2** `refresh` 行不得声称携带"计算期间变陈旧"的状态 | 不存在任何表示"观测发生在决策开始时刻"的字段取值 | **硬**（字段不存在即不可表达） | `observation_at_start.mode` 只有两个合法值；`obs_mode_of` 对未知模式 fail-loud（`decision_ledger.py:148-156`） |
| **R-B3** 不得用 `refresh` 与 `frozen` 的差异冒充"前向投影的收益" | 该对照必须标注为"**同一分支点**的两种观测语义"，不得推断任何未来状态 | **仅约定** | 夹具证明两者分支点不同（`tests/test_frozen_observation.py:225-273`）；`minimal_branch_compare.py:244-254` 直接拒绝在 `refresh` 上做分支对照，理由原文 "a refresh run has a DIFFERENT branch point" |
| **R-B4** `refresh` 不得被称为"把状态推进到未来" | 文档/结论中不得出现该等价表述 | **仅约定** | 全部依据是 `kernel.py:1106-1110` 的注释："a *delayed re-observation* decision: it CANNOT represent a state that went stale during the computation, **because the staleness is erased by the re-read**"。没有任何 receipt/ledger 键承载这条语义 |

**为什么两者语义不同**（契约语言）：`refresh` 移动的是**观测时刻**（`t_decision_start` → `t_decision_commit`），描述的是一台**更慢但更准**的机器；它**抹掉了陈旧性本身**——陈旧性恰恰是"信息在计算区间内变旧"，重读把这件事从记录里删除。前向投影做的是相反方向：把 t0 信息**外推**到未来，并**保留**"它是外推的"这一属性。前者是时间轴上的平移，后者是延伸；平移永远得不到延伸的结果（同一夹具下的 hold 与 forward 是两个不同答案：`:237-251` vs `:225-234`）。

### C 的可检查形式

| 规则 | 检查形式 | 现状 | 证据 |
| --- | --- | --- | --- |
| **R-C1** `truth_at_target` / `truth_at_commit` / `info_audit.candidate_truth` 不得进入 policy 输入（00e B1/B2/B3/B6） | 静态：策略与学习路径零引用；动态：审计对象在动作选定之后才构造 | **硬**（调用顺序） | `info_audit = self._decision_info_audit(...)` 在 `_record_decision` 内（工作树 `kernel.py:3546`），而 `_record_decision` 只在动作已选定后被调用（`:4036,4047,4172,4273`）；`grep truth_at_commit\|truth_at_target\|candidate_truth\|info_audit CODE/leo_sim/learning.py` → **零命中**；`frozen` 与 learning arm 互斥（`:1140-1143`） |
| **R-C2** 标注 `mapping_status` 后必须有人读它（00e B2） | 存在消费者，读 `mapping_status` 并在不等于 `truth_audit_not_learner_tensor` 时失败 | **仅约定（比约定更弱：目前无人读）** | `mapping_status` 在工作树 `kernel.py:3809` 有唯一写入者；全仓 grep 的**其余命中全是写侧透传或测试断言**，**零个生产 reader** |
| **R-C3** `truth_at_commit.t_observed` 这个键名不得当作"观测时刻" | 消费者用 `obs_mode`/`t_measure` 判时刻，不读该键 | **仅约定** | 工作树 `kernel.py:3572` 把它写成 `committed_at`——名字是 observed，值是 commit |
| **R-C4** decision 行的**嵌套对象**不得携带未声明内容 | 行契约覆盖嵌套键，或显式声明"嵌套不受契约保护" | **仅约定（目前敞开）** | `DECISION_ROW_KEYS` 只冻结顶层 19 键（`decision_ledger.py:75-80`），`validate_decision_row` 只比顶层键集（`:90-110`）。评审记录已实测：顶层 `rogue` 被拒，**嵌套进 `truth_at_commit` 的键被接受**（`CODE/work/WP-LEO-V2-T1-TRUST-CHAIN-V6/round1/review-adversarial.json`） |
| **R-C5** 投影的输入权限 ⊆ 00e 表 A | 投影函数的入参集合是表 A 的子集；不得读对端真实队列或对端 cache（00e B12） | **待实现** | 今天最接近的先例是 `receipt.FIELD_AUTHORITY`（`receipt.py:140-157`），给 17 个 run-level 字段标了 `recomputed`/`ledger_consistency`/`diagnostic` 并随 `field_authority` 进入每次运行（`:590`）。**该表不覆盖决策时刻对象，也不含任何时间语义标签**——可作形态先例，不是已有实现 |
| **R-C6** 任何进入 policy 输入的字段必须被标为 t0 可得 | 存在 capability 标签集合，policy 输入向量每个分量都有标签 | **待实现** | 同 R-C5；**未核实**：是否已在别处以其他名字存在（本文只找到 `FIELD_AUTHORITY` 一个近似物） |

### 硬保证与约定的分界

**硬保证（构造或代码路径不可违反）**：R-A1、R-A2、R-B1、R-B2、R-C1；加上 00e 已登记的 `frozen` 必须有计算区间（工作树 `config.py:910-921`）、F2 非零必须有 timeline（`kernel.py:1155-1162`、`__main__.py:460-469`）、顶层 19 键契约在**每次**发布 decision 流的运行上强制（`__main__.py:493-495`，注释原文 "enforced on EVERY run that publishes a decision stream"）、V6 回执按哈希绑定两个流（`receipt.py:61-63,78-81,1412-1428`；`RECEIPT_KEYS_V6` 与 `decision_stream_contract` 在 `98c858f` 已存在，见上节）。

**仅约定（靠文档与人）**：R-A3、R-A4、R-B3、R-B4、R-C2、R-C3、R-C4。共同点：**这些规则约束"怎么读、怎么引用、怎么命名"，而平台没有为这类约束保留检查位置。** 其中 R-C2/R-C4 最值得注意——一个有写了却没人读的标签（`mapping_status`），一个只覆盖第一层的契约（`DECISION_ROW_KEYS`）。

**未核实**：仓库之外是否存在评审流程或 CI 步骤在检查引用口径（本文只核实了仓库内代码与测试）。

---

## 与已有资产的关系

本文件**不重复**以下能力，只在其上补语义。

| 已有资产 | 它已解决什么 | 本文件补什么 |
| --- | --- | --- |
| **00e** 表 A/B/C + 判读规则 | 字段级许可、时刻语义、行键契约、三条硬规则 | 四组语义的**归类**：哪些 A 条目在哪组语义下存在、哪些不存在、哪组语义在 A/B 两表里零对应物 |
| 时间链整理：`TIMELINE_FIELDS`（`decision_ledger.py:45-57`）+ `build_ledger`（`:228`）+ `fold_decision_ledger` CLI + `ANALYSIS/CURRENT-EVENT-TIMELINE.md` | 时刻**有哪些**、从哪两个流折叠、怎么发布 | 每个时刻**属于哪组语义**；语义 4 需要的 `t_common` 在 C-1 里**没有位置** |
| 反事实命令入口：`counterfactual.replay_with_forced_action` + `experiment_platform/replay_counterfactual.py` | 同一 trace/config/seed 重放到同一决策、证明分支前状态相同、只改一个动作（`counterfactual.py:54-72,121-128`） | 被干预的"分支点"在四组语义下**分别落在哪一刻**（frozen = 观测时刻，refresh = 提交时刻，语义 3/4 = 需先定义） |
| v6 流绑定：`RECEIPT_KEYS_V6`（`receipt.py:61-63`）+ `DECISION_ROW_KEYS` 19 键契约 | 决策流与时间线流**可被回执校验**，行契约双向 fail-loud | 契约**内容的语义**：`obs_mode` 是语义标签而非普通键；嵌套层未受保护（R-C4）必须显式化 |
| 逐包分支比较：`experiment_platform/minimal_branch_compare.py` | 输出 `pre_branch_identity` / `post_branch_divergence` / `candidates` / `action_cost`，逐候选给到达与争用时刻、目标包面前工作量 | 这些量**分别属于哪组语义**、哪些是事后真值；以及"缺秒"这一度量缺口 |

**本文件补的那一层是**：把"字段已存在"与"字段在某组语义下是否合法/是否存在"分开。已有资产回答"记录了什么"，本文件回答"它可以被谁读、在哪一刻读、四组语义要能互比还缺什么"。

**补充（未核实）**：`minimal_branch_compare.py` 在 `CODE/experiment_platform/tests/` 下**没有对应测试文件**（该目录 11 个测试文件均不引用它）。

---

## 待实现（最小形态）

均为"待实现"，且都**不改变引擎调度行为**。

| 项 | 最小形态 | 依赖 |
| --- | --- | --- |
| `resource_scope` | decision 行/审计字典里一个字符串标签：`local_isl_egress` / `peer_isl_egress` / `downlink_egress` / `node_process_f2` / `compute` / `control_cache` | 无 |
| 目标资源标识（决策时刻版） | 每候选一个键，指名"若选它将争哪个出口"；无法确定时 `null` + 原因，**不得猜测** | 语义 3 投影 |
| `work_ahead_bits` / `work_ahead_s` | 显式命名 + 秒换算；速率未知或变化时 `null` + 方法标签 | 无（bits 部分今天即可） |
| 投影声明 | 三个只读字段：`projection_model`、`projection_horizon_s`、`t_common`（语义 3 写 null） | **研究者先定语义** |
| `score_projected_estimate` / `score_work_ahead_seconds` / `score_refresh_staleness` | 各一个纯函数 + 一个聚合 CLI（复刻 `fold_decision_ledger.py` 的原子发布与"拒绝覆盖"姿态） | 前两项 |
| capability 标签（R-C6） | `{字段名: "t0_available" \| "post_hoc_truth" \| "audit_only"}` 映射，随决策行/评分类产物发布；先声明，检查器可后补 | 无 |
| 读 `mapping_status` 的检查（R-C2） | 决策行消费者里 fail loud：对象声明为审计专用而调用方在策略路径上则拒绝 | 无 |
| 嵌套键契约（R-C4） | 二选一：冻结 `truth_at_commit` 等嵌套对象键集；或在 `DECISION_ROW_KEYS` 文档里显式声明"嵌套不受契约保护"，让缺口可见 | 无 |

---

## 未确定的研究选择（需研究者拍板，不得由平台默认）

**本节单列。以下每一项都有多种合理选择，平台不得用默认值假装已决定。**

1. **投影模型的形式**：零阶保持（假设届时同本刻）／一阶外推（按当前服务速率线性推进排队）／按 `advertise_interval_s` 节奏周期性重估。三者会测到不同的东西。
2. **`t_common` 取什么**：固定前瞻 Δ？各候选到达时刻的最大值？决策周期边界？`minimal_branch_compare` 的既有输出已表明各候选到达时刻本身就不同（`candidates[*].realized[*].t_peer_arrival`），所以"共同"必然是**设计选择**而非可推导量。
3. **时刻是参数还是模式**：语义 3/4 做成第三个 `decision_observation_mode` 取值，还是做成与观测模式正交的前瞻参数？前者简单但把两种正交语义耦合，后者需要新的组合合法性规则与拒绝表。
4. **语义 3 与 4 是否共用一个机制**：本文主张共享（同一种投影，只差一个时刻参数），**机制上只实现一次**。若研究者要求两者能分别对照，则需两个独立参数，并须声明二者差异不得被解释为"两种机制"的差异。
5. **语义 3/4 下 `t_measure` 取什么**：投影的目标时刻，还是仍取 `t_decision_start`（因为信息基础仍是 t0）？两种取值对"信息有多新"的结论方向相反。
6. **是否允许事后真值参与离线策略迭代**：本文规则 C 主张**一律禁止**（含离线训练），因为一旦进过训练就无法从最终策略里剔除。若要放宽到"只允许进诊断、不进任何回报函数"，需单列更细规则并给出检查形式。
7. **`work_ahead` 的度量单位**：统一 bits、统一秒、还是并存但指定"排序用哪个"？比特数在不同速率出口间不可直接比较。
8. **`rate_model=mcs` 下 `in_service_remaining_bits` 为 null 的处置**：禁止在 mcs 下引用秒口径／改用 `rate_model=constant` 跑时间语义实验／新增"服务开始时刻预测"字段。这决定时间语义实验能否在 mcs 下进行。
9. **下行队列是否算被争资源**：`_egress_snapshot` 今天只覆盖 `self.isls[sat]`（工作树 `kernel.py:3647`）。若语义 3/4 要覆盖下行，需扩展范围并明确它与 ISL 出口是否同一资源域。
10. **`B13`（目标包自身入队）在语义 3/4 下是否必须适用**：00e 只把它登记为语义 4 的禁止条目；语义 3 的"候选到达时该候选出口面前的工作量"是否也要剔除目标包自身，需研究者定。

---

## 一句话结论

四组语义中两组已实现且语义已被 `obs_mode` / `t_measure` 固定，两组缺失且共享同一种前向投影能力；**在投影语义被研究者确定之前，平台只能提供机制与时刻参数，不能自行发明语义**——否则四路对照测到的不是同一个东西。本文是 00e 之上的契约层补充，不替代 00e。
