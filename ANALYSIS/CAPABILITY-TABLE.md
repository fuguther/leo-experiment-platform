# 能力表（2026-09-26）

**五级状态**，逐级更强，低级不得当作高级：

| 级别 | 含义 | 判定方式 |
|---|---|---|
| **实现存在** | 代码在仓库里 | `grep`/`read` 得到符号 |
| **命令可达** | 有 CLI 入口且能启动（无导入错、能给出拒绝理由） | 实跑 `--help` 或触发一次拒绝路径 |
| **本地实跑** | 在本机干净检出上真跑出产物 | 产物落盘且内容被读过 |
| **VM 实跑** | 在 `vm`（`旧正式部署根`）上真跑出产物 | VM 上的产物文件 |
| **研究比较完成** | 对照已跑、差异可归因、有可复现证据 | 有配对证据 + 口径声明 |

**基线身份**：`origin/main = 1fad58f`（clean `code_sha256 = 57cca0ed…`）；本轮改动后工作树 `code_sha256` 见 `ANALYSIS/VERSION-EVIDENCE-RECONCILIATION.md` §5。
**这不是一份"全绿"的表**：表里"研究比较完成"一列绝大部分是空。

---

## A. 路线① 逐包分布式路由

| 能力 | 实现存在 | 命令可达 | 本地实跑 | VM 实跑 | 研究比较完成 |
|---|---|---|---|---|---|
| 时间链整理（十一时刻 + 折叠账本） | ✅ `decision_ledger.TIMELINE_FIELDS` / `build_ledger` | ✅ `-m CODE.experiment_platform.fold_decision_ledger` | ✅ 产出 `decision-ledger-fold/v1` | ❌ 未在 VM 上跑过该 CLI | ❌ |
| 反溯/可重放（`config_sha256`/`code_sha256`/receipt） | ✅ `receipt.py` | ✅ `-m CODE.leo_sim receipt verify` | ✅ | ✅ R1–R5 十格回执（v5/v6） | ❌ |
| frozen 观测模式 | ✅ `kernel._observe_preferred_action` / `_decide_from_frozen_observation` | ✅ 配置键 `execution.decision_observation_mode` | ✅ | ⚠️ 仅 E1 校验（未跟踪交付集），**main 未上 VM** | ❌ |
| refresh 观测模式（默认） | ✅ | ✅ | ✅ | ✅ | ❌ |
| 单次候选动作干预（forced action） | ✅ `kernel._apply_forced_action` + `counterfactual.py` | ✅ `-m CODE.experiment_platform.replay_counterfactual` | ✅ | ❌ | ❌ |
| **frozen × 单次候选动作干预**（本轮新增） | ✅ `kernel._forced_action_at_observation` | ✅ 同上 + `minimal_branch_compare` | ✅ | ❌ 见 §C | ❌ |
| **逐包最小机制比较**（本轮新增） | ✅ `experiment_platform/minimal_branch_compare.py` | ✅ | ✅ 产出 pre_branch_identity / post_branch_divergence / candidates / action_cost | ❌ 见 §C | ⚠️ 见 §D |
| 候选实际到达 / 竞争资源时刻 | ✅ `peer_arrival` + `queue_enter` + `truth_at_target` | ✅ 由上面两个 CLI 输出 | ✅ | ❌ | ❌ |
| 目标包面前工作量（本星出口 + 对端出口） | ✅ `own_queue_bits` / `_egress_snapshot` | ✅ | ✅ | ❌ | ❌ |
| 真实动作代价（逐包） | ✅ `action_cost`（送达时刻/命运/路径/尝试次数） | ✅ | ✅ | ❌ | ❌ |
| 四组时间语义契约 | ✅ `ANALYSIS/TIME-SEMANTICS-QUARTET.md`（契约层） | — | — | — | ❌ 后两组仍缺前向投影 |
| 下游资源真值评分器 | ✅ `_peer_downstream_truth` / `score_downstream_predictions` / `score_start_estimates` | ⚠️ 三个评分器**无生产 CLI**（仅测试调用） | ⚠️ 仅经 Python API | ❌ | ❌ |
| v6 流绑定（decision/timeline 两流进信任链） | ✅ `RECEIPT_KEYS_V6` | ✅ `receipt verify --decision-log/--timeline-log`、`verify-pulled` | ✅ | ✅ R1–R5 的 treatment 回执为 v6 | ❌ |
| 十一时刻**字段**进回执/账本信任链 | ❌ `RECEIPT_KEYS_V5`/`LEDGER_KEYS` 不含 | — | — | — | ❌ |
| 候选到达时刻（作为决策输入） | ❌ | — | — | — | ❌ |
| 共同未来时刻（前向投影） | ❌ | — | — | — | ❌ |

---

## B. 路线② 异步流量调度

**本轮之前：整条路线零覆盖**（未跟踪交付集里 grep「异步」0 命中，无调度变量/信息范围/触发/安装/版本/转发规则的任何设计）。
本轮新增的是**设计与字段契约**，不是实现。

| 能力 | 实现存在 | 命令可达 | 本地实跑 | VM 实跑 | 研究比较完成 |
|---|---|---|---|---|---|
| 可审模型与最小执行设计（六要素） | ✅ `ANALYSIS/ASYNC-SCHEDULING-DESIGN.md` | — | — | — | ❌ |
| 调度变量定义（`(schedule_epoch, binding_scope, installed_action)`） | ⚠️ 仅契约，无字段 | ❌ | ❌ | ❌ | ❌ |
| 信息范围（允许/禁止清单） | ⚠️ 复用 `00e` 表 A/B，未落到新字段 | ❌ | ❌ | ❌ | ❌ |
| 更新触发（事件驱动的重算时机） | ❌ | ❌ | ❌ | ❌ | ❌ |
| 计算与安装（`schedule_install` 事件） | ❌ 计划走 timeline milestone | ❌ | ❌ | ❌ | ❌ |
| 生效版本（`schedule_version`） | ❌ | ❌ | ❌ | ❌ | ❌ |
| 旧方案持续转发规则 | ❌ | ❌ | ❌ | ❌ | ❌ |
| 反退化判据（防止退化成按流缓存） | ⚠️ 判据已写，标准未定 | ❌ | ❌ | ❌ | ❌ |

---

## C. 本轮新增能力的可核验证据

| 证据 | 位置 | 级别 |
|---|---|---|
| frozen × forced 组合的生成侧测试（4 条：接受/非法/持有分支点/零扰动负对照） | `CODE/leo_sim/tests/test_frozen_observation.py` | 本地实跑 ✅ |
| 逐包比较驱动的 17 条测试（含两条脚本化场景、两时刻工作量检查、三条拒绝路径） | `CODE/experiment_platform/tests/test_minimal_branch_compare.py` | 本地实跑 ✅ |
| 有界每星计算资源 + 计算排队的 6 条测试（含无界逐位相同负对照、服务器数单调性） | `CODE/leo_sim/tests/test_compute_delay.py` | 本地实跑 ✅ |
| 逐包比较产物（no_info 失败诊断） | `out/br-noinfo.json`（`minimal-branch-compare/v2`） | 本地实跑 ✅ |
| 逐包比较产物（**两个候选都送达**，零负载） | `out/br-reach.json`：两分支 `delivered_at` 完全相同，Δ = 0.000000000 s | 本地实跑 ✅ + **VM 实跑 ✅** |
| 逐包比较产物（**可手算的出口竞争**） | `out/br-cont.json`：入队前工作量 1,566,997.9231432169 bit → 实测等待 1.614997923 s；送达差 = −1.614997923 s，与实测等待在实现容差（1e-9）内一致 | 本地实跑 ✅ + **VM 实跑 ✅** |
| 脚本化场景定义（参数先声明） | `CODE/experiment_platform/scripted_scenarios.py` | 本地实跑 ✅ |
| **真实星座 no_info 原因定位**（四因互斥分类，只用观测自身） | `CODE/experiment_platform/no_info_diagnosis.py` + `ANALYSIS/REAL-CONSTELLATION-SCAN-20260926.md` §1 | 本地实跑 ✅（诊断） |
| **真实星座双可达人口扫描**（选点规则预先声明） | `CODE/experiment_platform/dual_reachable_scan.py` + 同上 §3；实测 **0/12** | 本地实跑 ✅（诊断） |
| 成本/压力探针产物 | `out/cost-pressure.json`（`cost-pressure-probe/v1`） | 本地实跑 ✅ |
| 无学习小场景 profile | `CODE/leo_sim/profiles/t1_frozen_branch_smoke.yaml` | 本地实跑 ✅ |
| **VM 实跑**（本轮，与提交对齐） | VM `隔离 VM 诊断目录`；部署 `leo-vmdeploy@ab15b8f`，`code_sha256 = 2c17718d…` = 提交 `a49ce82`，Python 3.11.15 / simpy 4.0.1 / numpy 1.24.3。VM 上重算的握手量与本机**在实现容差内一致** | **VM 实跑 ✅**（诊断，非正式授权） |
| VM 上的平台测试套件 | `3 failed, 1005 passed, 1 skipped`——3 条失败全部归因于部署形态（无 `.git`、父仓实验实例），非本轮引入 | VM 实跑 ✅（含失败） |
| 跨环境一致性 | 语义字段逐项相同；算力争用 `wait_total=17.234256 s` 两边相同；`branch_fingerprint` **不同**（6/1199 叶子为 ≤1e-9 的相对浮点差） | 见 `ANALYSIS/VM-RUN-20260926.md` §4 |

---

## D. 研究比较：现在能说什么、不能说什么

**能说**（有本地配对证据）：
- `frozen` 的分支点是**观测时刻**，且在 `compute_delay>0` 时严格早于提交；强制动作必须落在该观测记录的合法集内，否则 fail-loud。
- 一对分支的**分支前状态逐行相同**（决策指纹 + 分支时刻之前的全部 timeline 行），**分支后的第一个差异出现在分支时刻之后**。
- 在脚本化 `reachability` 场景（无负载、两条路径几何相同）上，两个候选**都送达且送达时刻完全相同**，Δ = 0.000000000 s。这是**如实报告的"没有差异"**：该场景只证明可达性与配对正确，不证明任何策略优劣。
- 在脚本化 `contention` 场景（额外一个 4 Mbit 竞争包、链路 1 Mbps）上，两条路径的**真实代价可以手算并被实测在实现容差内验证**：基线走 E 在对端出口 `isl:1:3` 入队前有 1,566,997.9231432169 bit 在前面，实测等待 1.614997923 s；强制走 W 的 `isl:2:3` 工作量为 0、等待为 0；两个分支的送达差 = −1.614997923 s，**与实测等待在实现容差内一致**。
- 两时刻工作量检查在该场景为 `True`：对端到达时的快照比入队前多出 84,000 bit —— 若直接引用到达快照，就会把**取错快照**当成机制差异。
- 在 `t1_frozen_branch_smoke`（真实星座）上，`N` 与 `S` 的差异是：走 `N` 送达（45.13 s，路径 [0,1,2]），走 `S` 在对端拿不到路由信息、61 次 hold、地平线内未送达。**这是保留的失败诊断（可行性对照），不是代价对照。**

**不能说（剩余阻塞）**：
1. **真实星座场景（no_info）里"改选另一个合法候选"从未成功送达**：3 个负载档 × 4 个种子 × 3 个计算时延档全部如此。原因已定位：`routing.policy=hop` 下冻结推理规则取 `legal[0]`，其余合法候选在本地星座里是死端（对端 `no_info` → 反复 hold）。该场景**保留为失败诊断**。
2. **真实星座上不存在可比较的代价差异**（本轮实测）：预先声明的选点规则下，13 个候选点里执行了 12 个，**双可达点 0 / 12**——被强制的备选方向一个都不送达，原因与 no_info 相同（控制面 2 跳可达范围）。所以代价对照目前**只在脚本化场景成立**，它证明的是机制与度量正确，不是真实星座上的路由结论。
3. 因此 **`research comparison complete` 仍不成立**：把它推到真实星座需要一个候选之间真正可竞争的配置（换策略、换 OD 或换星座规模），这属研究选择，本轮未替你决定。
4. **VM 上跑的是诊断，不是正式授权运行**（`research_eligible` 为假、无回执、不在信任链内）。正式运行要重新 compile + authorize。
5. 十一时刻**字段**仍不进回执信任链；只有两条流的哈希进链。

---

## E. 本轮明确不重复开发的既有资产

| 资产 | 位置 | 本轮处理 |
|---|---|---|
| 时间链整理 | `decision_ledger.py` + `ANALYSIS/CURRENT-EVENT-TIMELINE.md` | 只引用，未改语义 |
| 反事实命令入口 | `CODE/experiment_platform/replay_counterfactual.py` | 只补了一处错误面（`KernelError` → `REPLAY REFUSED` exit 2），未改机制 |
| v6 流绑定 | `RECEIPT_KEYS_V6` + `verify_receipt_streams` | 未改 |
| E0–E3 交付集（**未跟踪**） | `ANALYSIS/ARRIVAL-TIME-T1-20260923/`（61 文件，冻结于 `98c858f`，`code_sha256=2a114890…`） | **未复制、未改写**；已备份并固化为本地 ref `refs/heads/wip/protect-arrival-time-t1` |
| E2-F 三因素单因素设计 | `ANALYSIS/ARRIVAL-TIME-T1-20260923/02b-…` | 未重做；本轮的 `cost_pressure_probe` 只补它没有的**每星算力上界**这一杠杆，并在 docstring 里写明关系 |
| 信息权限表（表 A/B/C） | `ANALYSIS/ARRIVAL-TIME-T1-20260923/00e-…` | 未重写；`TIME-SEMANTICS-QUARTET.md` 只补它与四组语义之间的那一层 |


---

## F. T1-COMPLETE 新增能力（P1–P12，含两轮复审返工）

基准身份：起始 `0876b12`（`t1/frozen-branch-and-async-design`）；现行证据身份 `8a31496`。
执行规则：**实验只在 VM 上跑**（`AGENTS.md` §1），本机只做代码/测试/只读复核。
状态口径同 A–E；**没有 FORMAL_RUN**，没有研究比较完成。

> 第三轮独立只读验收正在进行（4 路：S1–S3 / S4–S5 / S6–S7 / S8+证据链）。
> 结论回齐之前，下表的 ✅ 表示「实现方声明 + 已有可查证据」，**不代表已获独立确认**。

| 能力 | 实现存在 | 命令可达 | 本机测试 | VM 实跑 | 研究比较完成 |
|---|---|---|---|---|---|
| 单位修正（occupied 秒 / 控制比特分离） | ✅ `control_reach_probe._control_overhead` + `UNITS` | ✅ 同名 CLI（schema v2） | ✅ 3 测试 | ✅ | ❌ |
| 执行链身份（commit/dirty/diff + 逐文件哈希） | ✅ `artifact_identity.py`（57 文件链） | ✅ 各驱动内嵌 `identity` | ✅ | ✅ 链 `96b3fbaf` 本机==VM 重算 | ❌ |
| runner 清单身份（S8：暂存移出工作区，消除 dirty 假阳性） | ✅ `t1-vm.sh` + `CODE/tests/test_t1_vm_launch_manifest.py` | ✅ `t1-vm.sh sync\|experiment` | ✅ 3 测试 | ✅ `dirty=false`、拉回 52 文件逐字节一致 | ❌ |
| 每星 FIFO 计算池 + compute_request/start/finish | ✅ `kernel._deferred_enabled` / `decide_deferred` | ✅ 配置键 + timeline | ✅ 手算夹具 | ✅ 有限池 N=1/2 排队实测 | ❌ |
| 零成本 frozen 诊断 | ✅ `_deferred_enabled` | ✅ `decision_observation_mode=frozen` | ✅ | ✅ | ❌ |
| 不可变快照 / 预测器 / ETA 分项 / 统一评分 | ✅ `time_alignment.py` | ✅ 经内核与 `time_alignment_compare` | ✅ | ✅ | ❌ |
| 理想队列真值（S1：排队/在服务剩余/控制优先/目标自身/FIFO 后方） | ✅ `time_alignment_compare.resource_work_ahead` | ✅ 三组理想臂 + 2×2 | ✅ 2500 bit 反例 + 内核对齐 | ✅ | ❌ |
| 包长与广告队列分离（S2：`advertised_bits`） | ✅ `kernel._build_ta_snapshot` | ✅ 经内核在线/离线构造 | ✅ 取值/顺序不变性 | ✅ | ❌ |
| 四组离线比较 + 每候选闭环代价 + oracle 隔离 | ✅ `time_alignment_compare.py` | ✅ `-m …time_alignment_compare` | ✅ | ✅ 四臂 targets/ranking 全对齐 | ❌ |
| 四组在线执行 + 可回放审计 | ✅ `kernel._time_aligned_order` | ✅ `time_alignment.enabled=true` | ✅ | ✅ | ❌ |
| 共用决策入口（S5：在线/离线重放/计时同走 `plan_decision`） | ✅ `plan_decision` / `build_predictions` / `resolve_common_horizon` | ✅ 内核与基准共用 | ✅ 调用计数测试 | ✅ 真实在线路径 p50 526–529 µs | ❌ |
| 异步更新器（scope 状态机 / 完成才安装 / 版本单调） | ✅ `async_routing.py` | ✅ `async_routing.enabled=true` | ✅ | ✅ 逐包 0 计算请求 + 32 次安装 | ❌ |
| 查表模式不付逐包完整计算（S3：`_packet_compute_required`） | ✅ `kernel.decide_deferred` | ✅ precomputed / async_* 模式 | ✅ 正成本与零成本查询 | ✅ 逐包 0 次计算请求 | ❌ |
| 五执行模式公平矩阵 | ✅ `execution_compare.py` | ✅ `-m …execution_compare` | ✅ | ✅ 缓存命中 7 次、有限 N>0 | ❌ |
| 决策路径计时（分相 + 调用计数） | ✅ `benchmark_decision.py` | ✅ `-m …benchmark_decision` | ✅ | ✅ 观测构造 398–399 µs / 仅推理 12.07 µs | ❌ |
| 统一统计（配对/bootstrap/样本量/主对比） | ✅ `t1_stats.py` | ✅ 被驱动与报告引用 | ✅ | ✅ `common_strong.frozen=false`（诚实标注） | ❌ |
| 端到端流水线 compile/validate/run/resume/report | ✅ `t1_suite.py` | ✅ `-m …t1_suite` + `t1-vm.sh experiment` | ✅ 含整轮成败反例 | ✅ acceptance/dev 各 10/10 | ❌ |
| 行为谓词驱动整轮成败（S4：非 ok 即整轮失败、CLI 退出码 3） | ✅ `t1_suite.py` | ✅ CLI 退出码 | ✅ run→report→resume 反例 | ✅ `predicate_failed=0`、`not_ok=0` | ❌ |
| 正式包语义冻结与指纹（S7：阈值/种子/D/依赖文件入哈希） | ✅ `formal_package` / `formal_design` | ✅ compile / validate | ✅ 改阈值 999 被拒 | ✅ formal 可编译校验、拒绝未授权执行 | ❌ |
| DDQN 固定推理接口（S6 + 检查点硬门槛） | ✅ `inference.py` + `kernel.inference_policy` + `counterfactual` 透传 | ✅ 冻结快照 / 分支重演 | ✅ 固定小模型接线、掩码/参数不变 | ⚠️ 无真实训练检查点 → 外部阻塞 | ❌ |

**F 节能说**：机制本身已在本机测试与 VM 有界诊断两级实跑并可判定；关闭新功能时旧路径回归通过（**1278 passed, 2 skipped**）；acceptance 与 dev 两层各 10/10，且逐格行为谓词与结果哈希核验通过。
**F 节不能说**：状态时间对齐的收益；异步的性能收益；DDQN 策略性能；任何确认性结论。当前单分支诊断在 contention 上四组 regret 全 0（场景未激活机制），在合成夹具中四组可改变排序。
