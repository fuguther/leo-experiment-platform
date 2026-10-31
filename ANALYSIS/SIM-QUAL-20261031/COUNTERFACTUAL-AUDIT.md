# COUNTERFACTUAL-AUDIT — same-continuation forced-action fork（2026-10-31）

> 性质：**只读审计**（本机；未跑 VM、未改代码、未 commit）。对象是 Round-2 `n≥12` causal-regret
> 实验要用到的 same-continuation 反事实 harness：`CODE/leo_sim/counterfactual.py`、
> `kernel.run_simulation(forced_actions=…)` 路径、branch fingerprint、`decision_ledger` 的角色、
> `CODE/leo_sim/tests/test_counterfactual.py` + `test_frozen_observation.py`、
> `CODE/experiment_platform/minimal_branch_compare.py`、`CODE/scripts/counterfactual_one_decision.py`，
> 以及历史 C5 证据（`ANALYSIS/TIME-ALIGNMENT-20261030/FINDINGS-20261030.md` §4b/§8、
> `RUNBOOK-20261031.md` §2.2/§7、`c5_summary.json`）。
>
> 行号口径：worktree `.wt-qual-20261031`。**审计开始时** HEAD=`9eec9f6`（`kernel.py` 脏：未提交的
> 控制面可用性修复）；**审计结束前** Lead 已把该修复提交为 HEAD=`fb90c01`（`kernel.py` blob
> `bb58bbd→aed28fe`）。`file:line` 在 `fb90c01` 上复核过；与配对语义相关的文件
> （`counterfactual.py` / `decision_ledger.py` / `load_calibration.py` / `minimal_branch_compare.py` /
> `counterfactual_one_decision.py`）在两个 commit 之间**未变**；只有 F8 / §6.3 的代码身份门受该提交影响，
> 已按最终 HEAD 更新。

---

## 0. TL;DR（6 个问题的直接答案）

| # | 问题 | 结论（一句话） | 证据锚点 |
|---|---|---|---|
| 1 | 一次 fork 固定了什么 | **两次从 t=0 的完整重放 + 决策日志前缀指纹**；指纹只覆盖「目标决策之前的全部决策行 + 目标自己的 14 个 pre-commit 字段」，**不覆盖**包/队列/链路/RNG/事件队列/在飞/在服务的状态哈希 | `counterfactual.py:34-72`；`test_counterfactual.py:200-281` |
| 2 | RNG / 外生事件是否真正耦合 | **不是"共享 RNG 状态"**：真正成立的是 ①两分支拿到**同一个已物化 trace 列表**（同一次 `compile_trace` 产物）；②这些配置（GE off / learning none / 无 inference policy / 无 async）**运行期零 RNG 抽样**。若将来打开 GE/学习/采样策略，**harness 没有任何守卫**，分支后随机序列会静默错位 | `rng.py:32-47`；`outage.py:38-52`；`kernel.py:957,2868`；`counterfactual.py:91-96` |
| 3 | forced action 注入与合法性 | 在 commit 前用 `{decision_id: direction}` 覆盖，合法性由**同一 branch point 的 legal set** 强制（非法即 `KernelError`）；两分支 continuation **规则同一、历史不同**（每条路径各自演化策略记忆与缓存）；frozen 模式下若 commit 时不再合法则**park 而不落决策行**，指纹随即拒绝（fail loud） | `kernel.py:6522-6592,7176-7247,7327-7331,7471-7473` |
| 4 | 一次 fork 的成本 | **2 次 kernel run**（无快照/拷贝，均从 t=0 重放）；Round-1 同 cell 单次实测 wall **157.7–183.0 s**（high）/ 58.7–61.0 s（low）/ 429.2–492.0 s（p2-sync）⇒ high cell 单 fork 约 **3–6 min**，近容量 cell 约 4–8 min；并发硬上限 **≤2**（8 并发实测 OOM SIGKILL）；`n≥12` 分层规则见 §5 | `counterfactual.py:98-110`；`load_calibration.py:1080,1091`；`RUNBOOK-20261031.md:591,615` |
| 5 | 可照抄入口 + 需要的 cell | 现成可用入口只有 `counterfactual_one_decision.py`（`minimal_branch_compare` 只支持 frozen）；用「证据 run 的 `arms/now/config.json` 的 resolved_config」可**逐位复现** config sha 与 trace sha（本机已实测）；`n≥12` 的 near-capacity 分层需要**新 cell** `m2-c-greedy-s7`（load_c≈160 Mbps、seed 7、queue_greedy、adv 1.0 s） | §6；本地验证见附录 A |
| 6 | 允许的表述边界 | 只能说「**两分支到达同一 branch point，其见证是决策日志前缀**（+ `minimal_branch_compare` 时的 timeline 前缀）」；**不能**说「完整 SimPy state 相同 / bit-identical / 我们 fork 了仿真」。见 §7 的句子模板与禁句表 | `counterfactual.py:1-18`；`minimal_branch_compare.py:80-86`；`test_counterfactual.py:200-215` |

一句话结论：**harness 的配对证明是"决策日志见证"级，不是"状态哈希"级；在当前的确定性配置类别里，
它的实际强度高于它自己声称的（因为运行期无随机抽样），但这个强度是配置属性、不是 harness 保证，
且没有任何机制阻止将来用坏。**

---

## 1. 审计对象、方法与证据边界

### 1.1 对象清单

| 对象 | 位置 | 作用 |
|---|---|---|
| 配对 harness | `CODE/leo_sim/counterfactual.py`（157 行） | `replay_with_forced_action`：两次完整重放 + 指纹 + 拒绝规则 + outcome delta |
| 注入点 | `CODE/leo_sim/kernel.py`：`_apply_forced_action`(6522)、`_forced_action_at_observation`(6549)、两处调用点(7176/7471)、deliver 守卫(7327) | refresh / frozen 两种分支点 |
| 决策行 | `kernel.py:6091-6240`（`_record_decision`） | 指纹的输入 |
| 决策账本 | `CODE/leo_sim/decision_ledger.py` | **不含任何 branch fingerprint**；`build_ledger`(236) 只是把决策行+timeline 折叠成逐决策时间账本，`minimal_branch_compare` 用它取 branch 侧事实 |
| 驱动 A（可用） | `CODE/scripts/counterfactual_one_decision.py`（262 行） | compact 输出：verification + tagged packet + cohort |
| 驱动 B（仅 frozen） | `CODE/experiment_platform/minimal_branch_compare.py`（680 行） | 额外做 **timeline 前缀**比较 + per-packet 工作量/等待分解 |
| 驱动 C（全量） | `CODE/experiment_platform/replay_counterfactual.py` | 序列化两个分支的完整 result（体积不可用于 140-sat） |
| 驱动 D（多候选） | `CODE/experiment_platform/time_alignment_compare.py:1129` | 对**每个候选**各做一次 fork（k 个候选 ⇒ 1+2(k−1) 次 run），不是本轮所需 |
| 测试 | `CODE/leo_sim/tests/test_counterfactual.py`（281 行）、`test_frozen_observation.py`、`test_fixed_inference.py:242` | 正例/负例/拒绝/指纹覆盖边界/负对照 |
| 历史 C5 | `FINDINGS-20261030.md` §4b、`c5_summary.json`、`RUNBOOK-20261031.md` §2.2/§7 | n=3 的 same-continuation fork 先例 |

### 1.2 本机只读验证（附录 A 有原始输出）

1. `python3 -m pytest CODE/leo_sim/tests/test_counterfactual.py CODE/leo_sim/tests/test_frozen_observation.py -q` → **24 passed in 2.96s**（本机 python3，非 VM run 解释器）。
2. 用 Round-1 证据 run `sq-p1-high-greedy-20261031-01` 的 `arms/now/config.json` 的 `resolved_config`
   回灌 `config_mod.load_config_file` → **sha256 逐位相等** `da7e4d2a…e11385`。
3. 用该 resolved config 跑 `trace.compile_trace` → trace sha **`64f505df72cd…f022e`**，与证据
   `trace-manifest.json.source_trace_sha256` **逐位相等**，rows=1255（= 决策流里 unique pid 数）。
4. 用 `sq_policy_analyze.py` 的同一套定义在证据 cell 上复算：flip 0.2778（4,098/14,752）、
   W p50 43.53 ms / p90 303.65 ms / 748 段 / 16 censored、switch 0.3315（14,707 对）、
   Γ 中位 0.0744 —— 与 `RESULTS-20261031.md` §2.1/§3/§6 一致（说明我复用的是产出 RESULTS 的同一口径）。
5. 由上述数据现场算出的分层可得性（§5.4 表）：用于证明 `n≥12` 在现有 cell 上**除 near-capacity 外**都可达。

### 1.3 证据边界

- 本审计**没有** VM 上 `cf2-20261030-*` 的 `cf.json`、`run-manifest.json`、receipt（证据已外置到
  `/data/论文/leo-t1-wt/...`，本机只有 `FINDINGS`/`RUNBOOK` 的二手记录）。凡引用 C5 数字的地方均标注为
  **[二手证据]**。
- 本审计**没有**重跑 140-sat 仿真（规则不允许在本机产生科研数值）；成本数字来自证据里已经记录的
  `execution_wall_s` 与外推，外推口径写在 §5.2。
- 未验证项集中在 §8。

---

## 2. Q1 — 一次 forced-action fork 到底固定了什么

### 2.1 机制：不是 fork，是「两次独立重放」

`counterfactual.replay_with_forced_action`（`counterfactual.py:75-157`）：

1. **第 1 次 run**：`kernel.run_simulation(resolved, rows, …, decision_sink=base_sink, timeline_sink=base_timeline)`（100-103）。
2. **第 2 次 run**：同一 `resolved`、同一 `rows`、同一 `geometry`，只多传 `forced_actions={target_decision_id: forced_action}`（106-110）。
3. 对两次的 decision sink 各算一次 `branch_fingerprint`（112-113），不相等即
   `CounterfactualError`（128-131）。
4. 若 baseline 本来就是这个动作（132-135）→ 拒绝；否则比较 `OUTCOME_KEYS`（137-148）。

module docstring 明确禁止 deep-copy SimPy env、禁止从原轨迹读未选候选的未来（`counterfactual.py:1-18`）。
⇒ **"fork"这个词在这里是误导性的**：没有状态快照、没有 copy-on-write，只有"同一输入重放到同一点"。

### 2.2 指纹口径（`branch_states_identical` 的唯一依据）

```python
# counterfactual.py:34-38
PRECOMMIT_FIELDS = ("t", "t_decision_start", "decision_id", "state_version",
                    "pid", "src", "dst", "sat", "kind", "policy",
                    "candidates", "own_queue_bits", "obs", "info_audit")
```

`branch_fingerprint(decision_rows, target_decision_id)`（54-72）：
- 取**追加顺序**上排在目标之前的**全部决策行，整行**（含 `chosen`、`observation_at_start`、
  `truth_at_commit`、`four_direction_audit`、`estimate_at_start`…）→ `earlier`；
- 取目标行的 14 个 pre-commit 字段（**不含 `chosen`**）→ `target_precommit`；
- 对 `{"earlier": …, "target_precommit": …}` 做 canonical JSON 的 sha256。

`branch_states_identical = (base_fp == cf_fp)`（118-127），不等即拒绝。测试把这条边界钉死了：
`chosen` 被排除（240）、14 个字段逐个 mutable 检查（250-255）、目标之后的行**故意不覆盖**（266-271）、
显式断言"没有 rng/link/queue/topology/packet state"（279-281）。

### 2.3 覆盖表：哪些量被固定、哪些没有

| 状态类别 | 被固定？ | 具体到哪一层 |
|---|---|---|
| **时钟** | ✅ | 目标行 `t` / `t_decision_start`（refresh 下相等）；更早的每个决策时刻都整行入哈希 |
| **决策历史（动作）** | ✅ | 目标之前**每一行整行**（含 `chosen`）⇒ 任何已提交的动作分歧都会被抓到 |
| **候选/合法集** | ✅ | 目标行 `candidates`（= 该分支点 legal set，`kernel.py:7474` 传入 `legal`） |
| **本星出口队列** | ✅（部分） | `own_queue_bits`：该 sat 的**全部** outgoing ISL 的 `data_bits+ctrl_bits`（`kernel.py:6148,6227`） |
| **候选侧真值** | ✅（部分） | `info_audit.candidate_truth`：每候选 `distance_km/rate_bps/available/peer_egress_queue_bits/reverse_link_queue_bits/topology_available/downstream`（`kernel.py:6429-6474`） |
| **控制缓存** | ✅（部分） | `info_audit.cache_entries`：该 sat 在该时刻**可见的** cache entry 的 `generated_at/received_at/age_s/hops/payload_field_age_s`（`kernel.py:6480-6520`）；**payload 内容**不覆盖 |
| **事件步计数** | ✅（弱） | `state_version` = **每处理一个事件 +1 的计数器**（`kernel.py:7714-7721`），不是状态哈希；相等只说明"处理了同样多的事件" |
| **几何/拓扑** | 🟡 间接 | 通过 `candidate_truth.distance_km/available` 与 `state_version`（`topo_recompute` 会 +1，2625）间接体现；没有几何轨迹/拓扑哈希 |
| **在服务（in-service）** | ❌ | 只计入 `own_queue_bits` 的**总 bits**；`in_service_remaining_bits` 和 finish 时刻不在指纹里 |
| **在飞（in-flight）** | ❌ | 部分发送的包的剩余 bits / 剩余传播时间 / 别的包的 `path`、`deadline`、`birthed_at` 都不覆盖 |
| **其他 139 颗星的队列/缓存** | ❌ | 只有"本 sat 出口 + 候选对端出口总和"，其他卫星的队列/缓存不在指纹里 |
| **SimPy 事件队列** | ❌ | 待触发的 timeout/wake/process 集合、触发时刻、顺序都不覆盖 |
| **RNG 状态** | ❌ | 没有任何 generator 状态入哈希（见 §3） |
| **目标自身的 observation 记录** | ❌ | `observation_at_start`（含 `time_alignment.used_state`、`candidate_resources`）**不在** `PRECOMMIT_FIELDS`；`obs_mode`/`hop_index` 也不在 |
| **packet 状态** | 🟡 部分 | 目标包的 `pid/src/dst/sat` 覆盖；目标包已走的 `path`、`deadline`、到达时刻不覆盖（只能从 `earlier` 的 `info_audit`/`own_queue_bits` 间接推） |

**判定依据一句话**：`branch_states_identical=true` 只证明「两份**决策日志前缀** ＋ 目标 14 个字段」
逐位相同；它**不等于**「两个 SimPy 进程处于同一状态」。

### 2.4 指纹的两处实现陷阱（必须写进操作规范）

**F1（高）持久化 decisions 流被投影过，直接拿来算指纹会静默退化。**
`load_calibration._JsonlSink._project_decision`（`load_calibration.py:566-589`）只保留
`("t","pid","sat","decision_id","kind","candidates","chosen","t_decision_start","hop_index")`
+ `observation_at_start` 的 **3 个子键** + `truth_at_commit` + `estimate_at_start`。
⇒ 对持久化行算指纹时，14 个 pre-commit 字段里 **7 个恒为 None**：
`state_version, src, dst, policy, own_queue_bits, obs, info_audit`。
而 `_precommit` 用的是 `row.get(name)`（`counterfactual.py:50-51`）——**缺字段不报错，就是 None**，
两侧都 None ⇒ 比较仍"通过"，但证明强度掉一半以上。**规则：指纹只能用进程内 sink 行算，禁止用
`decisions.jsonl.gz` 复算。**

**F2（中）目标自身的 observation 不在 pre-commit 集。**
Frozen 模式的分支点是 `obs["t_observe"]`、合法集是 `obs["legal"]`（`kernel.py:6549-6592`），
但指纹不含 `observation_at_start`/`obs_mode`。若未来改 `_observation_at_start` 的内容，
配对证明不会跟着变强/变弱。**规则：不要声称"指纹覆盖了分支点观测"。**

---

## 3. Q2 — RNG 与外生事件：是不是真正的 common random numbers

### 3.1 结论（分三层，别混写）

| 层 | 是否成立 | 依据 |
|---|---|---|
| **L1 外生到达流相同** | ✅ **强成立（构造性）** | 两分支用的是**同一个 `rows` 列表对象**（`counterfactual.py:100,106` 传同一个 `rows`）；trace 由 `compile_trace` 一次物化，emitter 只读行（`kernel.py:3346-3395`，不改行）。不是"同 seed 各跑一遍"，而是"同一份已物化 trace" |
| **L2 运行期随机抽样** | ✅ **在本轮配置类别里为零** | 命名流只在 **trace 编译**时抽（`rng.py:32-47`；`trace.py:1318,1396,1415`）；kernel 里唯一的运行期随机是 GE 的 exponential，且 `enabled=False` 时**构造都不抽**（`outage.py:41-44,52`）；`learning.algorithm=none`（harness 硬拒绝非 none，`counterfactual.py:91-96`）；无 `inference_policy`；`execution.monitor=false`；`time_alignment.execution_mode=per_packet`（非 async） |
| **L3 共享 RNG 状态 / CRN 证明** | ❌ **不成立且不必要** | 没有任何 RNG 状态入指纹；harness 也没有"RNG 对齐"检查。若将来打开 GE/学习/采样推理策略，"先分支后抽样的顺序不同 ⇒ 随机序列错位"，**没有任何守卫会发现** |

⇒ 因此：`FINDINGS-20261030.md` §4b 的「common random numbers 由确定性仿真天然满足」**在当前配置下
事实上成立，但推理写错了**：它不是"确定性仿真自然满足 CRN"，而是"①同一物化 trace + ②该配置类别
运行期无随机抽样"。作为**表述**，必须写成 §7 的条件句，不能写成 harness 的能力。

### 3.2 具体核对（file:line）

- `rng.streams(seed)` 的 8 条命名流：`demand/ge_gsl/ge_isl/association/routing/control/monitor/nested_filter`
  （`rng.py:16-38`）。全仓 grep：只有 `trace.py:1318` 用 `streams()`；`kernel.py` 只用 `link_stream`
  （957、2868），且仅被 GE 通道持有。
- `GilbertElliott.__init__`：`self._next_flip = …exponential(…) if enabled else math.inf`（`outage.py:48-49`）
  ⇒ `ge_enabled=false` 时**零 draw**。矩阵 cell 的 resolved config：`links.ge_enabled=false`（已核）。
- `learning`: `algorithm=none`；`fixed_inference=false`；`inference.py:145` 的 `default_rng(seed)`
  只在显式传 `inference_policy` 时生效（`counterfactual.py:106-110` 支持传入并被
  `test_fixed_inference.py:242` 覆盖，但本轮矩阵不用）。
- 因此对 `sq-p1-*`/`sq-p2-*`/`m2-*` 这类 cell：**t=0 之后没有任何 RNG draw**，两分支的差别
  **只能**来自那一个被强制的动作。这是比 CRN 更强的性质（"零随机"），但它**不是 harness 保证的**。

### 3.3 风险清单（将来会踩）

| 触发条件 | 后果 | 现有守卫 |
|---|---|---|
| `links.ge_enabled=true` | GE 的 exponential 在事件顺序里抽 ⇒ 分支后两条流错位 ⇒ 差值混入随机差异 | **无** |
| `learning.algorithm≠none` | 直接拒绝（`counterfactual.py:91-96`） | ✅ 有 |
| `inference_policy` 带随机/状态 | 两分支各建 generator，draw 顺序随事件序变 | **无**（harness 反而允许） |
| `time_alignment.execution_mode=async*` | async 决策路径引入额外事件序 | **无**（未检查） |
| `monitor=true` / 任何按事件打点的旁路 RNG | 同上 | **无** |

**建议（不属本审计写作用域，交给 Lead 决定是否提 issue）**：在 `replay_with_forced_action`
入口加三条 fail-closed 断言：`links.ge_enabled is False`、`inference_policy is None`（或显式声明
"可复现抽样"）、`execution.async*` 关闭；并把这些写进 fork 的 pre-registration 检查表。

---

## 4. Q3 — forced action 的注入、合法性、两个分支的 continuation policy

### 4.1 两种分支点、两条注入路径

| 模式 | 分支点 | 注入函数 | 合法集 | 说明 |
|---|---|---|---|---|
| `refresh`（本轮 12 个 Round-1/2 cell 全部是 refresh） | commit 瞬间 | `_apply_forced_action`（`kernel.py:6522-6548`），调用点 7471-7473 | 本次决策现算的 `legal` 列表（= 决策行 `candidates`） | 覆盖发生在策略已选出 `action` 之后、`_record_decision` 之前 |
| `frozen` | observation 瞬间 `obs["t_observe"]` | `_forced_action_at_observation`（6549-6592），调用点 7176-7179 | `obs["legal"]`（observation 时记录的集） | commit 时只**重验合法性**（`_forward_legal_now`），不重解最优（7191-7196） |

### 4.2 合法性：fail loud，不猜

- refresh：`if forced not in legal: raise KernelError("forced action … is not legal at the branch point …")`（6541-6543）。
- frozen：非 `forward` 决策 → `KernelError`（6572-6575）；不在 `obs["legal"]` → `KernelError`（6576-6585）。
- deliver 决策被指名 → `KernelError`（7327-7331）。
- 每个 decision_id 最多应用一次（`_forced_applied`，1555 + 6538/6586）。
- 时间线写 `forced_action` 里程碑（6545-6547 / 6587-6591）：`original`、`forced`、
  frozen 时还有 `t_observe` 与 `legal_at_branch_point`。**审计可追，不是隐形替换。**

`verification.legal_at_branch_point`（`counterfactual.py:126`）是**事后**用 cf 行的 `candidates`
重算的**报告值**，不是执行时的守卫；真正的守卫在 kernel。

### 4.3 两个分支的 continuation policy 是否同一

**规则同一（✅）、历史不同（✅ 且必须如此）。**

- 同一：`resolved`（config 逐字节相同）、`rows`（同一个 trace 列表）、`geometry`、`seed`、
  代码（同一进程同一版本）。唯一差异是 `forced_actions` 字典里的一个 entry。
- 无副作用：没有 forced 时两条新路径都立即返回（6533-6534、6569-6570）；负对照测试
  `test_no_override_leaves_a_frozen_run_bit_identical`（`test_frozen_observation.py:228-241`）
  证明 `forced_actions={}` 与不传时 fates/totals/decision 行/timeline 行**逐字节相同**；
  `test_the_baseline_is_untouched_by_the_forced_replay`（`test_counterfactual.py:109-125`）
  证明 harness 内的 baseline 等于一次普通 run。
- 历史不同：分支后，策略记忆 `_state_policy_last[(sat,dst)]`（`kernel.py:6130-6131`，
  在 `decision_sink is None` 早退**之前**更新，所以有无 sink 都一样）、链路 EWMA、控制缓存
  各自演化 ⇒ 后续决策的**动作集**不同（这是反事实的定义，不是 policy 不一致）。
- ⇒ 报告里必须写：**"两分支后验走同一路由规则，但各自在不同网络状态上走；这不是同一条轨迹的两份副本。"**

### 4.4 失败模式（frozen 特有，本轮不用但要知道）

frozen 下 forced 动作在 observation 合法、commit 时不再合法：
`_decide_from_frozen_observation` 走 reject 分支 → 写 `commit_rejected`/`frozen_inferred_hold`
timeline → **park 包，不落 decision 行**（7201-7247）。
后果：forced run 里**找不到该 decision_id** ⇒ `branch_fingerprint` 抛
`CounterfactualError("decision … never occurred")` ⇒ 整对拒绝。**安全**（不会把 hold 当成
"另一个动作"来比），但会烧掉一个配额，所以 §5 的替换规则必须存在。

### 4.5 注入能力边界

`forced_actions` 只作用于**数据面 forward 决策**。控制面广告、access 上下行、compute 服务
都没有强制入口 ⇒ 任何"强制改变控制面节奏"的反事实**当前做不到**（要做只能改 config 另开 cell）。

---

## 5. Q4 — 一次 fork 的成本，以及 `n≥12` 的预注册抽样规则

### 5.1 一次 fork = 2 次完整 kernel run

`counterfactual.py:98-110`：baseline + forced，两次都从 t=0 跑满 `duration_s`（本轮 20 s / 140 sat）。
**没有**状态快照、没有"从 branch point 续跑"的入口。所以：

```
cost(fork) = 2 × kernel_run(cell) + 采样/合并开销
```

`counterfactual_one_decision.py` 另外做一次 `compile_trace`（112-124）+ 两个分支的 compact 化
（126-177），但真正贵的是两次 run。

### 5.2 实测锚点（Round-1 12 个 140-sat cell，`summary.json.execution_wall_s`）

| cell 组 | 包数 | 单 run `execution_wall_s` | manifest start→finish | 备注 |
|---|---|---|---|---|
| `p1-high-*`（120 Mbps + pulse） | 1,255 | geom 157.7 / greedy 167.6 / hyst 183.0 / ewma 165.0 | 164–187 s | 含 decisions.jsonl.gz(21 MB) + target_probe(67 MB) 的流式 gzip |
| `p1-low-*`（40 Mbps） | 427 | 58.7 / 61.0 / 60.5 / 59.4 | 62–64 s | |
| `p2-sync-*`（adv 0.1 s，控制事件 ×10） | 1,255 | 429.2 / 481.5 / 492.0 / 474.6 | 432–496 s | 控制面事件主导 |

**外推（fork）**：fork 的 run **不写** 87 MB 诊断产物，但 harness **无条件**把 timeline 收进内存
（`counterfactual.py:98-110` 总是传 `timeline_sink=[]`，没有开关）⇒ 单 run 取上表 wall 的
**0.6–1.0×**：

| 分支来源 cell | 单 fork 估计 | 12–14 forks（2 并发，6–7 波） |
|---|---|---|
| `p1-high-greedy`（已有证据） | **3.2–6.1 min** | **22–43 min** |
| near-capacity `m2-c-greedy-s7`（≈1,670 包 / ≈32k 决策，×1.3） | 4.2–8.0 min | 30–56 min |
| `p2-sync-greedy`（不建议做 branch 来源） | 14–16 min | 1.7–1.9 h |

### 5.3 内存与输出（决定并发上限）

- **峰值内存**：两个分支的**完整 result**（`packet_events`/`link_service_windows`/`caches`/
  `deliveries`/`service_log`，`kernel.py:7862-7902`）+ 两份**全字段决策行**（含 `info_audit`/
  `observation_at_start`/`truth_at_commit`）+ 两份 timeline 行，全部同时在内存里。
- **实测红线**：`RUNBOOK-20261031.md:615` 记录 8 个 full-network fork 并发 → 容器
  `memory.oom_kill=118`、watchdog 56 GiB TERM、`cf2-20261030-d89662` 在 1619 s 后被 SIGKILL(−9)。
  ⇒ **硬约束：≤2 个 full-network run 并发**（`RUNBOOK:591`）。注意：**C5 的 OOM 是并发导致，不是单 fork 太大**。
- **单 fork 输出**：`cf.json` 只含 verification + tagged packet（事件）+ cohort（逐包 delivery delta）
  ⇒ 矩阵 cell（1,255 包）约 **0.3–0.6 MB**；C5 的 Stage C（8,860 common delivered）也只有 MB 量级 [二手证据]。
  不是 87 MB 的 cell 产物。
- **磁盘**：14 forks ≈ 5–10 MB 工件，可忽略；真正占盘的是 branch 来源 cell 的 87 MB/run。

### 5.4 `n≥12` 预注册抽样规则（字段与判定条件具体化）

**前提**：写在 fork 运行之前的 `PROTOCOL` 冻结段；本文件只给可执行定义，阈值冻结由 Lead 拍板。
下表的"可得性"是**本机在 `sq-p1-high-greedy-20261031-01` 证据上现场复算**（附录 A.4），
证明这套规则能从现有 cell 抽到足够样本。

#### (a) 候选宇宙 U（逐条判定）

从 branch 来源 cell 的 `arms/now/decisions.jsonl.gz` + `target_probe.jsonl` + `trace.jsonl.gz` 抽取，
必须**同时**满足：

| 条件 | 字段 | 阈值/判定 | 依据 |
|---|---|---|---|
| u1 forward | `kind` | `=="forward"` | 只有 forward 可强制（`kernel.py:6572-6575`、7327） |
| u2 多候选 | `candidates` | `len ≥ 2` | 否则无对比 |
| u3 有真值 | target_probe 同 `decision_id` 的 ≥2 个 direction 行，`identity_source=="used_state"`、`service_rate_bps>0`、`truth.work_ahead_bits` 非空 | 必须有 `J_truth` | `sq_policy_analyze.py:72-137,183-192` |
| u4 有已用状态 | `observation_at_start.time_alignment.state_policy.per_direction[d]` 的 `j_used_s`、`resource_work_used_s`、`service_rate_bps` 均非空（≥2 个候选） | — | 同上 |
| u5 有反差 | `chosen != fresh_action` | `fresh_action` 按 §5.4(b) 计算 | 否则 harness 拒绝（`counterfactual.py:132-135`） |
| u6 可强制 | `fresh_action ∈ candidates` | 必须在记录下的 legal set 里 | `kernel.py:6541` |
| u7 主口径 | branch 来源 cell 的 `state_policy == "queue_greedy"` | 主分层只用 greedy | hysteresis 的"fresh"是 **rule-relative**（`RESULTS-20261031.md:56-59`），不进主分层 |

#### (b) 逐决策派生量（复用 `sq_policy_analyze.py` 的既有定义，不另造）

| 量 | 定义（与 RESULTS 同口径） | 实现锚点 |
|---|---|---|
| `m_used` | `j_used` 的 top1−top2 | `sq_policy_analyze.py:246-249` |
| `m_truth` | `J_truth` 的 top1−top2 | 同上 |
| `R_proxy` | `J_truth(chosen) − min_a J_truth(a)`（**same-decision truth-substituted，不是因果**） | `:244-245` |
| `Γ` | `abs(slope_a − slope_b)·τ/m_used`，`τ=t−measured_at`，slope 由 target_probe 同 `resource_id` 的 `truth.work_ahead_bits/service_rate_bps` 在 `eval_target_s ±0.25 s` 局部线性拟合 | `:160-181,250-266` |
| `flip` | `chosen != fresh_action`（greedy/ewma 用 truth argmin；hyst 用 last_direction+η） | `:193-207` |
| `W 段` | 同一 `(sat,dst)` 决策流里连续 flip 的时长；**dst 必须从 `trace.jsonl.gz` 的 `packet_id→dst_grid_id` join 回来**（见 F3） | `:210-292` |
| `c_bits` | `max_cand truth.queued_bits`（按候选取 max） | target_probe `truth.queued_bits` |
| `c_work` | `max_cand truth.work_ahead_bits / service_rate_bps`（秒） | 同上 |

#### (c) 分层与配额（总数 14，下界 12）

| 层 | 判定条件 | 配额 | 排序（选谁先） | 现有 cell 可得性 |
|---|---|---|---|---|
| **S1 large Γ** | `Γ ≥ 5.0` | 3 | Γ 降序 | **484 条**（401 pid / 19 sat） |
| **S2 small margin** | `m_truth ≤ 0.002 s` | 2 | `m_truth` 升序 | **642 条**（537 pid / 27 sat） |
| **S3 large proxy regret** | `R_proxy ≥ 0.010 s` | 2 | `R_proxy` 降序 | **432 条**（405 pid / 17 sat） |
| **S4 near-capacity** | `c_bits ≥ 0.25×isl_queue_bits`（16 Mbit）**或** `c_work ≥ 0.25 s` | 2 | `c_bits` 降序 | **0 条**（现有 cell 最大 `queued_bits` 3.0 Mbit、最大 `c_work` 39.7 ms）→ **必须新 cell** |
| **S5 sustained same-direction** | 处于时长 `≥0.100 s` 的 W 段内 | 2 | 段时长降序 | **2,972 条**（257 段 / 25 sat） |
| **S6 random control** | `U` 内均匀随机（固定种子，见 (d)） | 3 | — | 4,098 条候选 |

**去重约束**（对全 14 个 fork）：≤1 个/`pid`；≤2 个/`sat`；≤1 个/`(sat,dst)` 流；整批 ≥8 个不同 sat。
违反则在同一层内**顺延取下一条**（不是换层）。
**替换规则**：运行期被拒（`KernelError` 非法 / `did not change the action` / 决策 id 不存在）时，
在**同一层**取排序下一条替补；**禁止**跨层补、禁止换 cell 救数；每次拒绝连同原因记入报告
（拒绝是数据，不得静默丢弃）。若某层连续拒绝 ≥2 次，该层标记 underpowered。

#### (d) 随机对照的可复现种子

```
seed_hex = sha256(f"{contract_sha256}:sq-fork-sampling-v1").hexdigest()[:16]
rng = random.Random(int(seed_hex, 16))
```
抽样时先按 `(decision_id)` 升序排 `U` 再 `rng.sample`，保证同 contract 同批可复现。

#### (e) 估计量与报告口径（预注册）

- **主估计量（逐 fork）**：`Δe2e = e2e(forced=fresh) − e2e(baseline=stale)`（s），
  `e2e = delivered_at − emitted_at`（tagged pid）。**符号约定：>0 ⇒ stale 动作更慢（该点是正的因果 regret）。**
- **次估计量**：`Δdelivered_at`、fate 是否改变、cohort 外部性（多少包变快/变慢/改命、`max|Δ|`）。
- **报告**：逐层给 `n / median / IQR / #pos,#neg,#0 / min,max`；**给全部 14 个 fork 的散点**（含零与负）。
- **不做**：p 值/置信区间/显著性（每个 fork 是确定性配对，无抽样噪声；问题只在代表性）、
  总体平均外推、跨 cell 合并平均。
- **允许的因果表述**：仅限"在这 14 个 branch point 上，把 stale 动作换成该点的 fresh 动作，
  后续沿同一规则续跑，tagged 包 E2E 变化为 X"。

#### (f) 可行性判定（预注册门槛）

`n_useful ≥ 12` 且至少 4 个层各 ≥2 个成功 fork → 该批可用于回答"causal regret 是否可测"；
否则报告 `UNDERPOWERED`，不得用 truth-substituted `R_proxy` 冒充因果 regret。

### 5.5 F3（高）— 现有分析器的 `(sat,dst)` 流实际退化成 `per-sat`

`load_decisions` 里 `"dst": str(decision.get("dst"))`（`sq_policy_analyze.py:124`），但
`decisions.jsonl.gz` 的投影**丢掉了 `dst`**（`load_calibration.py:567-574`）。本机复算：
14,752 行的 `dst` **全部是字符串 `'None'`** ⇒ `decision_stats` 的 `streams[(sat, dst)]`
（`:224`）实际是 **per-satellite** 聚合。因此：

- `RESULTS-20261031.md` 里 `W p50/p90`（43.5/303.7 ms）与 `switch`（0.3315）的真实口径是
  **"同一卫星上跨目的地的决策序列"**，不是文档写的"同一 `(sat,dst)` 决策流"（`RESULTS §1`、`PROTOCOL §7`）。
- 对本轮 `n≥12` 的影响：**S5 层必须**先用 `trace.jsonl.gz` 的 `packet_id → dst_grid_id`
  把 `dst` join 回来，再算 W 段；否则 S5 抽样会重复 Round-1 的口径缺陷。
- 这是**报告口径错误**（不影响 simulator 数值），但会改变 §5.4(c) 里 S5 的段数和 p50。

---

## 6. Q5 — 可照抄的执行入口，以及需要哪个新 cell

### 6.1 三个入口的可用性判定

| 入口 | 可用于 Round-2 fork？ | 原因 |
|---|---|---|
| `CODE/scripts/counterfactual_one_decision.py` | ✅ **主入口** | 无 mode 限制（refresh 可）；compact 输出；记录 `config_sha256`/`trace_sha256` |
| `CODE/experiment_platform/minimal_branch_compare.py` | ❌ | **硬要求 `decision_observation_mode=frozen`**（`minimal_branch_compare.py:562-567`），而 Round-1/2 matrix cell 全是 `refresh` |
| `CODE/experiment_platform/replay_counterfactual.py` | ❌ | 序列化两个分支完整 result，140-sat 体积不可用 |
| `CODE/experiment_platform/time_alignment_compare.py` | 🟡 仅当需要"每个候选都 fork" | k 候选 ⇒ 1+2(k−1) 次 run，成本更高 |
| `CODE/experiment_platform/no_info_diagnosis.py`/`control_reach_probe.py` | ❌ | 单点诊断 driver，不是 n≥12 批量入口 |

### 6.2 已在本机验证的"逐位复现分支来源"三步（可直接照抄）

```bash
cd /Users/lge/Desktop/topic/<某个 checkout，代码必须与分支来源 release 同一 commit>
E=/Users/lge/Desktop/topic/三端文件与同步治理/evidence/t1/sq-p1-high-greedy-20261031-01/p1-high-greedy

# 1) 导出该 cell 的 arm-resolved config（这就是分支来源 run 真正跑的 config）
python3 - "$E" <<'PY'
import json, sys, hashlib, pathlib
cell = pathlib.Path(sys.argv[1])
doc  = json.loads((cell / "arms/now/config.json").read_text())
out  = pathlib.Path("/tmp/sq-branch-source.yaml")
out.write_text(json.dumps(doc["resolved_config"]))
print("recorded sha256:", doc["sha256"])
print("bytes:", out.stat().st_size)
PY
# 期望：sha256 == da7e4d2a1d3328bec7c37934b7401ee94fc847759ccb32203f2a2f39e2e11385（本机已验证）

# 2) 校验 config 与 trace 的身份（必须逐位相等，否则 decision_id 指向别的决策）
python3 - /tmp/sq-branch-source.yaml "$E/trace-manifest.json" <<'PY'
import json, sys
from CODE.leo_sim import config as config_mod, trace as trace_mod
resolved = config_mod.load_config_file(sys.argv[1])
man = json.loads(open(sys.argv[2]).read())
work = "/tmp/sq-trace-check"; import shutil; shutil.rmtree(work, ignore_errors=True)
m = trace_mod.compile_trace(resolved, work)
print("config sha256 :", resolved["sha256"])
print("trace  sha256 :", m.get("__trace_sha256") or m.get("trace_sha256"),
      "| evidence:", man["source_trace_sha256"])
PY
# 期望：trace sha256 == 64f505df72cd9c0f4f44c6bd9669f105990cf759c096a0924432ce82cd2f022e（本机已验证）

# 3) 一次 fork（branch A = 默认 stale 动作，branch B = 强制成该点的 fresh 动作）
E=/Users/lge/Desktop/topic/三端文件与同步治理/evidence/t1/sq-p1-high-greedy-20261031-01/p1-high-greedy
python3 CODE/scripts/counterfactual_one_decision.py \
  --config /tmp/sq-branch-source.yaml \
  --decision-id <DECISION_ID> --forced-action <FRESH_DIRECTION> \
  --root /tmp --out /data/论文/leo-t1-wt/runs/<RUN_ID>/cf-<DECISION_ID>.json
# 不要传 --arm：resolved config 已经把 arm 固化成 now
```

**逐 fork 必须核对的 5 个 identity（缺一不可）**：

| 检查 | 期望 |
|---|---|
| `verification.branch_states_identical` | `true` |
| `verification.action_changed` | `true` |
| `verification.legal_at_branch_point` | `true` |
| `verification.baseline_action` | == `decisions.jsonl.gz` 里该 `decision_id` 的 `chosen` |
| `target.pid` / `target.hop_index` / `target.candidates` | == 该决策行同名字段（**这是"反事实落在被分析的那个分支点"的唯一硬证据**；C5 只做了 `baseline_action` 一项 [二手证据]） |

### 6.3 代码身份门（F8，高）——审计期间 HEAD 已前移

- **审计开始时**：HEAD=`9eec9f6`，`HEAD:CODE/leo_sim/kernel.py` blob=`bb58bbd…`，与 Round-1 release
  commit `76427851…` 的 kernel.py blob **相同** ⇒ 当时"用 Round-1 cell 当分支来源"是安全的。
- **审计结束前**：Lead 已提交 `fb90c01`（控制面可用性修复，`kernel.py` +45 行，跳过几何不可达方向的
  广告注入）⇒ `HEAD:kernel.py` blob=`aed28fe…`，**不再等于** `76427851` 的 `bb58bbd…`。
- 该修复会改变控制包注入数与全局事件顺序，**decision_id 是逐 run 计数器** ⇒ Round-1 证据里的
  `decision_id` 在 HEAD 上**不保证**指向同一决策（branch 来源随之失效）。
- ⇒ **规则：fork 的 (branch 来源 run, fork run) 必须来自同一个 release/commit**。三条可行路径：

| 路径 | 做法 | 代价 | 身份风险 |
|---|---|---|---|
| **P1（最快，复用已有证据）** | 从 `76427851` 拉一个**只加 fork driver、不改 kernel** 的分支 → publish → 用已有的 Round-1 verified 证据 cell（`sq-p1-high-greedy-20261031-01`）当分支来源做 12 个 fork | 1 次 publish + 12 runs ≈ 40–70 min | 最低：kernel blob 与来源 release 相同；本机已验证 config sha `da7e4d2a…` 与 trace sha `64f505df…` 可逐位复现 |
| **P2（单 release，最省心）** | 在 HEAD（`fb90c01`）上提交 cell-aware driver → publish → **在同一个 release 上重跑** branch 来源 cell（1 个 cell ≈ 3 min）→ 再 fork | 1 次 publish + 1 cell run + 12 forks | 同 commit/同 kernel，自查项最少；代价是**不能直接用** Round-1 证据 |
| **P3（并入 Round-2）** | driver 与 Round-2 contract 一起提交 → 等 calib/matrix 回传后直接对 `m2-*` cell fork | 等 Round-2 日程 | 最干净；`n≥12` 被 Round-2 阻塞 |

- 若坚持在**现有** Round-1 release（`76427851e32fc735da3fd45f4faa1ad54e49b308-9c5df15ae56789920e1c4a7dad8a9d6ff5a12f1c2d56c244db3f2d417e305ce2`，
  `remote_backup=verified`）上跑而不新 publish：实查该 commit **已含**
  `CODE/scripts/counterfactual_one_decision.py`、`CODE/experiment_platform/load_calibration.py`、
  `CODE/experiment_platform/t1_tasks.py`、`EXPERIMENTS/contracts/sq-policy-matrix-20261031.json`
  （`git cat-file -e` 全部 present）⇒ 可用 `-- python3 -c "<内联 40–60 行 cell-aware driver>"`
  作为 **stopgap**（读 runner 替换后的 contract 快照 → `_read_contract`+`t1_tasks.design` →
  `replay_with_forced_action` → 写 compact JSON）。**不推荐长期使用**：内联脚本不进 release 树、
  不可审计，且 `--config` 的路径替换语义容易踩坑（`RUNBOOK §7` 教训 #3）。

### 6.4 需要哪个新 cell 作为 branch 来源

| 分层 | 需要的 cell | 状态 | 说明 |
|---|---|---|---|
| S1/S2/S3/S5/S6 | **已有** `sq-p1-high-greedy-20261031-01`（120 Mbps + 8.0–8.5 s ×1.6 pulse，seed 7） | ✅ 证据 verified | 数量足够（§5.4）；是当前唯一能立刻支撑 n≥12 的 cell |
| **S4 near-capacity** | **新 cell `m2-c-greedy-s7`**：`total_mbps = load_c ≈ 160`、`seed=7`、无 pulse、`queue_greedy`、`advertise=1.0 s`、`ctrl=8000 bit`、`vis_k=3`、`duration=20 s`、`emission 4–14 s`、`packet 1 Mbit`、`ISL 100 Mbps/64 Mbit` | ⚠️ **未冻结**：`make_round2_contracts.py:matrix_contract` 已定义，但 `EXPERIMENTS/contracts/sq-matrix2-20261031.json` 尚不存在（Phase B，等 calib 的 ρ/slack 选 load） | 选它的原因：现有 cell 的 `queued_bits` 最大 3.0 Mbit（4.7% of 64 Mbit）、`c_work` 最大 39.7 ms ⇒ **现有 cell 物理上到不了 near-capacity 分层** |
| S4 备选（超临界瞬态） | `m2-d-greedy-s7`：`load_d=140` + 8.0 s 起 0.5 s ×1.6 burst | ⚠️ 同上未冻结 | 若 calib 显示 160 Mbps 直接溢出（丢包主导）而不是"排队变深"，则改用 D 层并**改名分层**为 over-critical transient，不得叫 near-capacity |

**建议顺序**：先走 §6.3 的 **P1**（`76427851` + driver 的独立 release），只用现有
`sq-p1-high-greedy` cell 跑 S1/S2/S3/S5/S6（共 12 个 fork，约 40–70 min，2 并发），
拿到"causal regret 量级与符号"的第一批 n=12；S4 的 2 个 fork
等 Round-2 near-capacity cell 回传后补。这样 `n≥12` 不被合同冻结阻塞，且 near-capacity
单独作为"是否放大"的对照批。

### 6.5 如果要走 release→run→receipt 流程（推荐，但需要新 driver）

现状：`counterfactual_one_decision.py` 只吃 `--config <profile>`，不认识 contract/cell；而
`run-release-remote.sh` 的 `--config` 只做输入快照与路径替换（`AGENTS.md:18`），不会补 driver 的
位置参数。因此需要一个**新的 cell-aware driver**（建议 `CODE/experiment_platform/fork_from_cell.py`），
骨架（写进 issue 给 Lead，不在本审计写作用域内改代码）：

```python
# 伪代码骨架（复用既有 API，不复制逻辑）
raw, contract, base, cells = load_calibration._read_contract(contract_path, repo_root)
resolved, rows, geometry, source = t1_tasks.design(
    config_path=str(repo_root / contract["base_profile"]),
    overrides=cells[cell_id]["cell"]["design_overrides"])
assert resolved["sha256"] == cells[cell_id]["resolved"]["sha256"]          # 身份门 1
# 身份门 2：trace digest == 分支来源 run 的 trace-manifest.source_trace_sha256
# 身份门 3：branch 来源 run 的 manifest.source_git_commit 的 kernel.py blob == 当前 blob
replay = counterfactual.replay_with_forced_action(
    resolved, rows, geometry=geometry,
    target_decision_id=args.decision_id, forced_action=args.forced_action)
# 身份门 4：replay["baseline"] 的目标行 (pid,hop_index,sat,t,candidates,chosen)
#           == decisions.jsonl.gz 的同一 decision_id 行；否则 REFUSED
# 输出：compact（复用 counterfactual_one_decision 的 _compact/cohort 口径）
```

调用方式（run-release-remote）：

```bash
CODE/scripts/remote/run-release-remote.sh --release-id <RID> --run-id sq2-fork-s1-2026110X-01 \
  --mode diagnostic --config EXPERIMENTS/contracts/sq-policy-matrix-20261031.json \
  --timeout-seconds 3600 \
  -- python3 -m CODE.experiment_platform.fork_from_cell \
       --contract EXPERIMENTS/contracts/sq-policy-matrix-20261031.json \
       --cell p1-high-greedy --decision-id <ID> --forced-action <DIR> \
       --out /data/论文/leo-t1-wt/runs/sq2-fork-s1-2026110X-01/cf-<ID>.json
```

- **这个 driver 必须与 branch 来源 run 在同一个 release 里**（§6.3）：因此 P1/P2 都要先 publish 新 release；
  选 P3 则与 Round-2 contract 同批 publish，一次到位。
- 一次 run 一个 fork（`kernel_calls=2` 会被 manifest 记两次 → 需要 runner/ledger 接受；
  若 runner 断言 `kernel_calls==1`，则**必须**改成"一个 run 内多 fork"，并在 manifest 里显式声明）。
- `--timeout-seconds` 至少 1800（高负载单 fork 3–6 min，但 runner 默认 1800 只是下限；
  含 trace compile 与启动，建议 1800–3600，别用 900——`RUNBOOK §7` 教训 #1）。
- 输出**绝对路径**（`RUNBOOK §7` 教训 #5）。

---

## 7. Q6 — 允许的表述边界

### 7.1 两个概念的定义差

| 说法 | 实际含义 | 由谁保证 |
|---|---|---|
| **decision-log prefix 一致** | 目标决策**之前**的每个决策行整行相同（含它选的动作、它看到的观测、它记录的候选真值/缓存年龄），且目标行的 14 个 pre-commit 字段相同 | `branch_fingerprint`（`counterfactual.py:54-72`）+ `branch_states_identical` |
| **完整 SimPy state 相同** | 两个仿真进程的**全部**对象状态相同：所有卫星队列、链路在服务/在飞剩余、事件堆、待触发 timeout、RNG、其他卫星缓存 | **没有任何机制保证**：没有 state_hash（测试显式断言 `rng_state/link_state/queue_state/topology_hash/packet_state` 都不在覆盖集，`test_counterfactual.py:279-281`） |
| （增强）**timeline 前缀一致** | 目标分支时刻**之前已触发**的每个物理里程碑行相同 | 仅 `minimal_branch_compare`（`:106,400-410,508`），且它**只支持 frozen** |

**关键的不对称**：decision-log prefix 能排除"**已经表现到日志里**的分歧"；它不能排除
"尚未触发、但在分支后会触发的差异"（例如两条待触发 timeout 差 1 µs）。所以：
**回放的可信度来自"同输入 + 确定性、运行期无随机抽样"这一构造论证，指纹是它的经验检查，
而不是它的证明。**（这也是为什么 §3.3 的"将来打开 GE"必须 fail-closed。）

### 7.2 可以直接照抄的句子（中/英）

**中文（报告正文）**：
> 采用 same-continuation 强制动作配对：两分支使用同一 trace、同一 config、同一几何与同一代码，
> 分别从 t=0 完整重放到同一决策；以**决策日志前缀**（目标前的全部决策行 + 目标 14 个 pre-commit 字段）
> 逐位相同作为"到达同一 branch point"的**见证**，然后只把该决策的动作替换为
> 该点真值排名第一的候选，其余续跑规则不变。两分支的唯一注入差异是这一个动作。

**英文（caption/limitation）**：
> Both branches replay the same immutable trace/config/seed to the same decision and are verified
> to reach the same branch point **as witnessed by the decision log** (every decision row committed
> before the target, plus the target's pre-commit fields). This is *not* a claim that the two
> simulations were in bit-identical states: packet, link, queue, event-queue and RNG state are not
> hashed. The counterfactual differs by exactly one forced action, chosen from the legal set recorded
> at that branch point; the continuation rule is identical, the continuation history is not.

### 7.3 禁止的句子（出现即视为越界）

| 禁句 | 为什么 |
|---|---|
| "两分支**完整 state 相同**/bit-identical/same SimPy state" | 无状态哈希；测试显式排除 |
| "我们 **fork/复制** 了仿真，从分支点继续" | 实际是两次从 t=0 重放（`counterfactual.py:1-18`） |
| "**common random numbers** 由 harness 保证" | harness 无此保证；只在当前配置类别下因"零运行期随机抽样"而成立（§3） |
| "该反事实给出该动作的**真实 regret / Q(s,a) 偏差**" | 只给"同一历史下换一个动作的后续差"；不同决策不可平均、不可当 RL 目标 |
| "**n=3**（历史 C5）证明 stale state 平均造成 52–235 ms 损失" | C5 是 3 个同一干线上的点，非抽样；`CONCLUSION U3` 已明确 |
| "统计显著 / p<0.05 / 置信区间" | 确定性配对无抽样噪声；只有代表性/覆盖问题 |
| "可外推到真实 Starlink / formal 结论" | DIAGNOSTIC；`PROTOCOL §8` 与 `CONCLUSION U4/U1` |

### 7.4 与既有结论文件的一致性

- `CONCLUSION-20261031.md` S6 已写明「flip 是 truth-substituted same-decision re-ranking，不是
  re-simulated counterfactual；因果 regret 只能由 `counterfactual.replay_with_forced_action` /
  `minimal_branch_compare` 给出」——**本审计确认 S6 正确**，并补两点：
  ① 当前 matrix cell 用不了 `minimal_branch_compare`（frozen-only）；② 该 harness 的证明是
  decision-log 级，不是 state 级。
- `CONCLUSION U3` 的「n≥12 的 fork 尚未做」——本文件 §5 给出可执行的 n≥12 预注册方案；
  做完后 U3 只能升级为"**某 cell 上 14 个 branch point 的因果差分布**"，**不得**升级为
  "population 平均 regret"。

---

## 8. 最关键的限制（Top-3）与其余风险

### Top-3（必须随任何 fork 结果一起读）

1. **配对证明是"决策日志见证"，不是"状态相同"**（`counterfactual.py:34-72`）。它排除已落日志的分歧，
   不排除未触发的 timeout/在飞/在服务差异；可信度真正来自"同输入 + 确定性 + 该配置零运行期抽样"。
2. **这份可信度是配置属性，harness 不守卫**（`counterfactual.py:91-96` 只查 learning）。
   一旦 `ge_enabled=true`、带随机 `inference_policy`、或 async 执行，分支后随机序列会静默错位，
   产物仍然"看起来配对成功"。
3. **成本与并发**：1 fork = 2 次 t=0 全量重放（高负载 3–6 min/个，近容量 4–8 min），
   峰值内存同时持有两份完整 result + 两份全字段决策行 + 两份 timeline；**并发硬上限 2**
   （8 并发已实测 OOM SIGKILL）。n=14 约 30–60 min（2 并发）。

### 其余已确认风险

| ID | 级别 | 风险 | 影响 |
|---|---|---|---|
| F1 | 高 | 持久化 decisions 被投影，14 个指纹字段里 7 个恒 None；`row.get()` 静默吞掉 | 用 `decisions.jsonl.gz` 复算指纹会静默削弱证明 |
| F2 | 中 | 目标自身 `observation_at_start`/`obs_mode`/`hop_index` 不在 pre-commit | 不能声称"指纹覆盖分支点观测"；改观测代码不会自动加固 |
| F3 | 高 | `dst` 被投影丢弃 ⇒ 分析器 `(sat,dst)` 退化为 per-sat ⇒ RESULTS 的 W/switch 口径与文档不符 | S5 分层必须从 trace join `dst`；RESULTS 的 W p50/p90 需加脚注 |
| F4 | 中 | harness 无条件收集两分支 timeline，无开关 | 内存/时间被放大；大 cell 有 OOM 风险 |
| F5 | 中 | `minimal_branch_compare` 只支持 frozen | Round-1/2 refresh cell 用不了它（也就拿不到 timeline 前缀增强） |
| F6 | 中 | `counterfactual_one_decision.py` 没有 contract/cell 身份校验，只靠 `--arm` | 跑错 config/trace 时会静默得到"另一个 decision_id"，只能靠事后 5 项 identity 检查兜底 |
| F7 | 中 | C5 的 identity 证据只有 `baseline_action == ledger chosen` [二手] | config sha 从未与 ledger1 记录交叉核对；重做时必须补 |
| F8 | 高 | 控制面修复已在审计期间提交（`fb90c01`，kernel blob `bb58bbd→aed28fe`） | HEAD 的物理行为不再等于 Round-1 证据（`76427851`），Round-1 的 `decision_id` 在 HEAD 上不保证复现 ⇒ fork release 必须**钉 commit**，或重跑 branch 来源 cell（§6.3 三条路径） |
| F9 | 中 | 没有 cell-aware fork driver | 走 release/run/receipt 流程前必须先加（§6.5） |
| F10 | 低 | harness 没有 `kernel_calls==1` 语义对齐 | 一个 run 两次 kernel call，需要 runner/ledger 明确接受 |

---

## 9. 未核验项（Unknown，不得当结论）

1. VM 上 C5 的 4 个 `cf.json`、`cf2-*` 的 manifest/receipt 本机不可见；C5 的 wall/内存细节只来自
   `FINDINGS`/`RUNBOOK` 二手记录。
2. 本审计未验证"140-sat cell 的跨进程重放是否逐位复现 decision_id"——只验证了 **config sha 与
   trace sha 逐位复现**（附录 A.2/A.3）。跨进程复现还依赖 kernel 内部 `set` 迭代顺序（本轮实查多为
   membership-only，order-sensitive 处有 `sorted(set(...))`），**没有实测**；而且 HEAD 已从 `76427851`
   前移到 `fb90c01`（kernel 变化），所以"跨 commit 复现"本身已不成立，必须按 §6.3 钉同一 release。
   因此 §6.2 用 5 项 identity 检查兜底。
3. 未测 fork 的真实 wall/峰值内存（需要 VM）；§5.2 是外推，误差 ±40%。
4. 未验证 near-capacity cell（`m2-c-*`/`m2-d-*`）是否真能产生 `c_bits ≥ 16 Mbit` 的决策；
   合同尚未冻结，load 由 calib 的实测 ρ/slack 决定。
5. 未审 `time_alignment_compare` 的多候选 fork 语义（本轮不用）；它的成本模型是
   1+2(k−1) 次 run，需另审。

---

## 10. Round-3 expansion（2026-11-01，n=36 预注册分层批次）

> 状态：**样本已冻结、尚未跑**（24 个新 fork 待 VM；12 个 s7 fork 已完成并回传）。
> 本节是 §5.4 抽样方案的扩批执行记录；§1–§9 的审计结论与表述边界**不变**（§10.7 重申）。

### 10.1 批次构成

| 项 | 值 |
|---|---|
| 目标 | n ≥ 30（目标 36）：把 same-continuation fork 从 C5 的 n=3 / Round-2 的 n=12 扩到 36 |
| 合并样本 | `ANALYSIS/SIM-QUAL-20261031/fork-samples/round3-expansion.json`（schema `sq-fork-sample-round3-merged/v1`，36 条） |
| 已完成 | 12 × `m2-c2-greedy-s7`（Round-2；证据 run `fk-c-<decision_id>-20261031-01`，release `de632b3e…-76a4127e…`） |
| 待跑 | 24（`m2-c2-greedy-s8` 12 + `m2-c2-greedy-s9` 12） |
| 采样器 | `ANALYSIS/SIM-QUAL-20261031/make_fork_sample_r3.py`（复用 `sq_policy_analyze.py` 的 Γ/margin/proxy-regret/truth-substituted fresh、`sq_persistence_analyze.py` 的 ε_r/z、`make_fork_sample.py` 的 `_fresh`/`_gamma_and_margin`） |
| 单 cell 样本 | `fork-samples/r3-m2-c2-greedy-s8.json`、`-s9.json`（schema `sq-fork-sample-r3/v1`，带自描述 `selection_rules`） |

### 10.2 分层与配额（合并 = 36，`strata_ok=true`）

| stratum | 判定（确定性） | 合并配额 | s7（已完成） | s8 | s9 |
|---|---|---|---|---|---|
| `large_gamma` | Γ ≥ p75，Γ 降序 | 6 | 4 | 1 | 1 |
| `small_margin` | `margin_s` ≤ p10，升序 | 5 | 3 | 1 | 1 |
| `large_proxy_R` | truth-substituted proxy regret ≥ p90，降序 | 5 | 3 | 1 | 1 |
| `long_persistence` | 同 `(sat,dst)` 流内同向连续 flip，`run_len ≥ 2`；run_len 降序、**每 run ≤1** | 5 | 1（Round-2 名为 `sustained`） | 2 | 2 |
| `high_z` | 候选资源 ε_r 的 **valid 子集**（ε ≥ max(1 bps, 1%·μ) = 1 Mbps），`z_bits = bits/ε_r ≥ p90`；**每 resource ≤1、每 sat ≤1** | 5 | 0 | 3 | 2 |
| `random_control` | 全部 eligible 按 `sha256(seed:decision_id)` 排序取前 | 5 | 1 | 2 | 2 |
| `low_gamma` | Γ ≤ p25（低漂移**负对照**）；每 run ≤1 | 5 | 0 | 2 | 3 |

- 两个 cell 的 `quota_filled` 与请求一致、`shortfall=0`、`n_cap_relaxed=0`（没有靠放宽 cap 凑数）。
- 12 个 s7 条目在合并文件中为 `status="completed"` 且带 `completed_verification`（`branch_states_identical`
  / `action_changed` / `legal_at_branch_point` 全 true，`baseline_action`/`forced_action` 与样本逐一相符）；
  **不要重跑**这 12 个 run-id。

### 10.3 采样宇宙与阈值（每 cell 自描述，以下为实数）

| 项 | s8 | s9 |
|---|---|---|
| 评估窗内决策（4.0–14.0 s） | 24,338 | 24,301 |
| eligible flips（chosen≠fresh、fresh∈候选、chosen 有真值） | 6,245 | 6,250 |
| `chosen_without_truth`（丢弃） | 41 | 41 |
| Γ p25 / p75 | 0.175 / 2.332 | 0.193 / 2.162 |
| margin p10 | 0.555 ms | 0.738 ms |
| proxy regret p90 | 11.84 ms | 12.77 ms |
| `z_bits` p90（valid 子集） | 0.1434（n=6,245） | 0.1425（n=6,191） |
| ε floor 排除 | 0 | **59** flips（ε<1 Mbps，饱和链路） |
| 随机种子 / 排除 id | `20261101` / s7 的 12 个 | 同左 |

池大小（large_gamma / small_margin / large_proxy_R / long_persistence / high_z / random_control / low_gamma）：
s8 = 1,469 / 624 / 625 / 5,293 / 632 / 6,241 / 1,470；s9 = 1,472 / 626 / 626 / 5,315 / 651 / 6,244 / 1,470。
⇒ 七层配额均有充足候选，没有任何一层是靠"降级判定"填满的。

### 10.4 新 config 与身份核对（本机只读，逐位相等）

| cell | fork config | config sha256 == 证据 `arms/now/config.json` | trace sha256 == 证据 `trace-manifest.source_trace_sha256` | rows |
|---|---|---|---|---|
| s7（Round-2 已有） | `EXPERIMENTS/contracts/fork-configs/m2-c2-greedy-s7.json` | ✅ `db54d247…f0728` | ✅ `2b27aac1…c667c` | 2,047 |
| s8 | `EXPERIMENTS/contracts/fork-configs/m2-c2-greedy-s8.json` | ✅ `3619bd9c…ea43f` | ✅ `6ac591e2…1441` | 1,998 |
| s9 | `EXPERIMENTS/contracts/fork-configs/m2-c2-greedy-s9.json` | ✅ `f4fcbc83…f8565` | ✅ `555ef133…beb16` | 1,985 |

- 复核方法 = §6.2 的两个 identity gate：`load_config_file(pinned)` 重算 sha256 等于证据 sha；
  `compile_trace(pinned)` 重算 trace sha 与 rows 等于证据 `trace-manifest`。
- **代码身份链（本机实查，2026-11-01）**：s7/s8/s9 三个 cell 的 run manifest 均记
  `source_git_commit=038fb16e…`（release 树 `b8eb375c…`）；Round-2 的 12 个 s7 fork 用 `de632b3e…`；
  `git diff 038fb16 de632b3 -- CODE/` **为空** ⇒ 两者物理代码相同，所以 s7 fork 的 baseline 能复现。
  当前 HEAD `c63c071` 与 `038fb16` 的 `CODE/` 差异也**为空** ⇒ **从 HEAD + "只加新 config/样本/脚本"的提交
  发布，物理代码与 cell run 一致，`decision_id` 可复现**。
- **但工作树现在有并行任务未提交的 CODE 改动**（`kernel.py +140`、`state_policy.py +330`、`config.py +44`、
  `load_calibration.py +5`、`test_state_policy.py +514`）⇒ **这些若进入 fork release，会改变物理/策略语义，
  使 s7/s8/s9 的 `decision_id` 失效**（per-fork identity gate 会 fail loud，但会烧样本）。
  发布前必须确认 release 的 `CODE/` 与 `038fb16` 相同，或重跑 branch 来源 cell。
- **VM trace 缓存是硬依赖**：三个 cell 的 `/tmp/t1-trace-programs/<cell>-<digest>.csv` 在 VM 上均存在
  （s7 116,046 B / s8 113,212 B / s9 112,524 B；行数 = rows+1），且与 Mac `/tmp` 副本 **sha256 逐位相同**
  （`75094b25…` / `238b9d9f…` / `776d2279…`）。`demand.mode=csv` 时 `compile_trace` 缺文件会 **fail loud**
  （`CODE/leo_sim/trace.py:1190-1194`：`csv input not found`），不会静默换 trace；若 `/tmp` 被清理，
  先按 `trace-manifest.source_trace_sha256` 重新生成并比对，再发射。

### 10.5 执行模板（`RUN_FORK_PREFIX=fk3-`）

前置：**需要一次新的 publish**——release 必须包含两个新 fork config 与样本文件；当前 HEAD `c63c071`
**尚未**包含它们（提交/推送由 Lead 执行，本审计不 commit）。Round-2 的 12 个 fork 用的是
`de632b3e5495074a90e69a6daa60e7525315217d-76a4127eefc94e8d5074912e7307e6635cb052091d428bc05c6bc562c581c9a3`。

```bash
cd /Users/lge/Desktop/topic/.wt-qual-20261031
# A) s8：12 个 fork（run_id = fk3-s8-<decision_id>-20261031-01）
RUN_FORK_PREFIX=fk3-s8- ANALYSIS/SIM-QUAL-20261031/run_forks.sh <RELEASE_ID> \
  EXPERIMENTS/contracts/fork-configs/m2-c2-greedy-s8.json \
  ANALYSIS/SIM-QUAL-20261031/fork-samples/r3-m2-c2-greedy-s8.json
# B) s9：12 个 fork（run_id = fk3-s9-<decision_id>-20261031-01）
RUN_FORK_PREFIX=fk3-s9- ANALYSIS/SIM-QUAL-20261031/run_forks.sh <RELEASE_ID> \
  EXPERIMENTS/contracts/fork-configs/m2-c2-greedy-s9.json \
  ANALYSIS/SIM-QUAL-20261031/fork-samples/r3-m2-c2-greedy-s9.json
# C) 回传（逐 run）
CODE/scripts/remote/pull-release-results-remote.sh --run-id <RUN_ID> \
  --release-id <RELEASE_ID> --evidence-root /Users/lge/Desktop/topic/三端文件与同步治理/evidence/t1
```

- `run_forks.sh` 默认 `CONCURRENCY=2`（VM 64 GiB cgroup 实测上限）、`TIMEOUT_S=5400`；Round-2 实测单 fork
  **≈650 s**（`fk-c-976` 15:57:55→16:08:45）⇒ 24 个新 fork × 2 并发 ≈ **2.2 h**。
- **run-id 必须带 cell 标记**（`fk3-s8-` / `fk3-s9-`）：两个 cell 的 `decision_id` 空间独立、会撞号，
  裸 `fk3-<id>` 会触发 `run ID already exists`。

### 10.6 合并样本字段与符号约定

`decisions[*]` 字段：`cell`、`config`、`stratum`（+`original_stratum`）、`decision_id`、`chosen`、`fresh`、
`forced`（=fresh）、`candidates`（记录下的 legal set）、`pid/sat/t/hop_index`、`gamma`、`margin_s`、
`proxy_regret_s`、`z_bits/z_delta/eps_bps/z_basis`、`run_len/chosen_resource/bits`、`status`、`run_id`、
`evidence_run_id`；s7 条目另有 `completed_verification` 与 `tagged_delta_s`。

- **符号**：`delta = forced(fresh) − baseline(stale)`；**>0 ⇒ fresh 更慢（stale 在该 branch point 更好）**，
  <0 ⇒ fresh 更快。
- 已完成的 12 个 `tagged_delta_s` 仅作参考写入（范围 −0.487…+0.307 s；6 负 / 3 正 / 3 零）——
  **这不是本轮结论**，只是 n=36 中已回传部分的事实记录；分析仍按 `fork_analyze.py` 的 tagged/cohort 双口径。
- 每个新 fork 必须过 `round3-expansion.json.verification_gate` 的 7 条（§6.2 的 5 项 + config/trace sha 两项）
  才可进入结果表；未过者记为 refusal 并保留原因。

### 10.7 表述边界（不变，对 n=36 逐条适用）

- 仍只是 **decision-log prefix 见证**：`branch_states_identical=true` ≠ 完整 SimPy state 相同；
  §7.3 的禁句表（"完整 state 相同 / fork 了仿真 / CRN 由 harness 保证 / 统计显著 / 可外推"）**逐条适用**。
- n=36 **不是概率样本**：7 层是按度量做的确定性配额（不是均匀抽样），24 个新 fork 只来自 2 个新 seed、
  同一 trace 程序族、同一策略（`queue_greedy`）。允许的表述上限：
  "**在这 36 个 branch point 上**，把 stale 动作换成该点的 fresh 动作后的后续差分布（分层 + 随机对照）"。
  不得写成 population 平均 regret、不得给 p 值/置信区间、不得外推到真实 Starlink 或 formal 结论。

### 10.8 扩批的已知限制（R1–R6）

| # | 限制 | 处置 |
|---|---|---|
| R1 | 两个新 fork config 未提交 ⇒ **现在 publish 不出来** | Lead 提交 `EXPERIMENTS/contracts/fork-configs/m2-c2-greedy-{s8,s9}.json` + `fork-samples/` 后再 publish |
| R2 | `high_z` 只对 valid 子集有定义（s9 排除 59/6,250 个 ε<1 Mbps 的 flip） | 报告 `z_basis`；被排除的饱和链路决策不得算进 high_z 分子/分母 |
| R3 | 36 fork 仅 3 个 cell / 3 个 seed / 1 个 trace 程序族 / 单一策略；配额非均匀 | 只做分层描述与随机对照，不做 population 推断 |
| R4 | `long_persistence` 按 run_len 降序取最长 run ⇒ 集中在内环干线少数 sat（134/126/44/24） | 报告 sat/dst 与 run_len；视作"持续同向错误"的**极端子集**，不是平均值 |
| R5 | driver `--arm now` 会重解析 pinned config | Round-2 的 12 个 fork 已证明 `baseline_action` 与样本 `chosen` 逐一相符；Round-3 每个 fork 仍须过 `verification_gate` |
| R6 | VM `/tmp` trace CSV 是硬依赖（缺失 fail loud，不静默） | 发射前 ssh 只读检查；需要时按 trace sha 重新生成并比对（§10.4） |
| R7 | **并行任务的未提交 CODE 改动**（kernel/state_policy/config/load_calibration ≈ +1,120 行）若进入 fork release，会改变物理与策略语义 ⇒ s7/s8/s9 的 `decision_id` 失效 | 发布前核对 `git diff <release-tree> 038fb16 -- CODE/` 为空；否则先重跑三个 branch 来源 cell 再 fork |

---

## 附录 A — 本机只读复算记录

```text
A.1 pytest
$ python3 -m pytest CODE/leo_sim/tests/test_counterfactual.py \
      CODE/leo_sim/tests/test_frozen_observation.py -q
........................                                                 [100%]
24 passed in 2.96s

A.2 resolved-config 回灌（证据 run: sq-p1-high-greedy-20261031-01/p1-high-greedy/arms/now/config.json）
recorded resolved sha: da7e4d2a1d3328bec7c37934b7401ee94fc847759ccb32203f2a2f39e2e11385
reloaded sha:         da7e4d2a1d3328bec7c37934b7401ee94fc847759ccb32203f2a2f39e2e11385
equal: True

A.3 trace 逐位复现
trace_sha256: 64f505df72cd9c0f4f44c6bd9669f105990cf759c096a0924432ce82cd2f022e
rows=1255      # == decisions.jsonl.gz 的 unique pid 数
evidence trace-manifest.source_trace_sha256: 64f505df72cd9c0f4f44c6bd9669f105990cf759c096a0924432ce82cd2f022e
evidence trace-manifest.rows: 1255 · jsonl_sha256: 2fedd0bf…5a84

A.4 用产出 RESULTS 的同一分析器复算（greedy / high）
decisions rows=24,279 · forward=23,025 · multi=14,752 · unique pid=1,255 · avg 18.35 decisions/pkt
flip rate=0.2778 (n=4,098)          # RESULTS §2.1: 0.2778 ✓
margin_used p50=11.49 ms · margin_truth p50=9.77 ms   # RESULTS 11.49 ✓
regret mean=1.658 ms / p90=7.104 / p99=13.409        # RESULTS 1.66/7.10/13.32 ✓
Γ median=0.0744                     # RESULTS 0.0744 ✓
W p50=43.53 / p90=303.65 ms · 748 段 / 16 censored   # RESULTS 43.5 ✓
switch=0.3315 (14,707 pairs)                        # RESULTS 0.332 ✓
dst 取值分布: {'None': 14752}        # ← F3：投影丢了 dst，流分组退化为 per-sat

A.5 n≥12 分层可得性（同一 cell，U = 4,098 条 usable contrast decisions）
Γ≥5          : 484 条 / 401 pid / 19 sat
Γ≥2          : 1,037 条
m_truth≤2 ms : 642 条 / 537 pid / 27 sat
R_proxy≥10 ms: 432 条 / 405 pid / 17 sat
W 段≥100 ms  : 257 段 / 2,972 条决策 / 25 sat
c_work 分位  : p50 10 ms / p90 10 ms / p99 17.5 ms / max 39.7 ms
c_bits 分位  : p50 0 / p90 0.46 Mbit / p99 1.0 Mbit / max 3.0 Mbit（cap 64 Mbit = 4.7%）
⇒ near-capacity 层在本 cell **不可得**，必须新 cell。

A.6 12 个 Round-1 cell 的实测单 run wall
p1-high : 157.7 / 167.6 / 183.0 / 165.0 s   （geom / greedy / hyst / ewma）
p1-low  :  58.7 /  61.0 /  60.5 /  59.4 s
p2-sync : 429.2 / 481.5 / 492.0 / 474.6 s
（= summary.json.execution_wall_s，kernel_call_count=1；manifest start→finish 略高 6–11 s）
```

代码身份实查（只读）：

```text
审计开始 HEAD                                  = 9eec9f6aebaf266d412b976fa1d42b04afc9fc49
审计开始 HEAD:CODE/leo_sim/kernel.py blob      = bb58bbddf41181808bdae74fc5ce75163c0dd290
76427851:CODE/leo_sim/kernel.py blob           = bb58bbddf41181808bdae74fc5ce75163c0dd290   # 当时相同
审计结束 HEAD                                  = fb90c01ef79e4804f617f70ce9fcd6fe31583069   # 控制面修复已提交
审计结束 HEAD:CODE/leo_sim/kernel.py blob      = aed28fe3bf6e12cf3460587eed0b6e041d744906   # ≠ 76427851（F8）
两 commit 之间未变：counterfactual.py / decision_ledger.py / load_calibration.py /
                    minimal_branch_compare.py / counterfactual_one_decision.py
76427851 中已存在的入口（git cat-file -e 实查，全部 present）：
   CODE/scripts/counterfactual_one_decision.py
   CODE/experiment_platform/load_calibration.py · t1_tasks.py · minimal_branch_compare.py
   EXPERIMENTS/contracts/sq-policy-matrix-20261031.json
git status --short（本 worktree；这些不是本次审计写入）：
   M ANALYSIS/SIM-QUAL-20261031/sq_persistence_analyze.py
   ?? ANALYSIS/SIM-QUAL-20261031/calibration/ · run_cells.sh
   本次审计只新增 ANALYSIS/SIM-QUAL-20261031/COUNTERFACTUAL-AUDIT.md
```

---

## 附录 B — 给 Lead 的实施顺序建议（不含写作用域外的改动）

1. **冻结 fork 协议**：把 §5.4 的分层/配额/替换/估计量写进 `PROTOCOL-…-rev2`（新文件，不改已冻结的 PROTOCOL）。
2. **先在现有 cell 上做 12 个 fork**（S1/S2/S3/S5/S6）：按 §6.3 选 **P1**（从 `76427851` 拉 driver-only
   分支 publish，kernel 与 Round-1 相同）或 **P2**（HEAD 上 publish 并在同 release 重跑来源 cell）；
   用 §6.2 的三步复现 + 5 项 identity 检查；2 并发；预计 40–70 min。
   **不要**在没有 driver 的旧 release 上直接用 HEAD 重放 Round-1 的 `decision_id`（F8）。
3. **补 S4**：等 Round-2 calib 选 load → 冻结 `sq-matrix2-*.json` → 跑 `m2-c-greedy-s7`（或 `m2-d-greedy-s7`）
   → 回传 verified → 再抽 2 个 near-capacity fork。
4. **（可选，建议）加 harness 守卫**：GE/inference/async 三条 fail-closed 检查 + `kernel_calls` 语义；
   并给 `counterfactual_one_decision.py` 加 `--contract/--cell` 身份门（F6/F9）。
5. **修报告口径**：在 RESULTS/PROTOCOL 给 W/switch 加"W 与 switch 是 per-sat 口径、`dst` 已被投影丢弃"的
   脚注（F3）；S5 抽样前先从 `trace.jsonl.gz` 把 `dst` join 回来。
