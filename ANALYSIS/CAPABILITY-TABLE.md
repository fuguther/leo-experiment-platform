# 能力表（2026-09-26）

**五级状态**，逐级更强，低级不得当作高级：

| 级别 | 含义 | 判定方式 |
|---|---|---|
| **实现存在** | 代码在仓库里 | `grep`/`read` 得到符号 |
| **命令可达** | 有 CLI 入口且能启动（无导入错、能给出拒绝理由） | 实跑 `--help` 或触发一次拒绝路径 |
| **本地实跑** | 在本机干净检出上真跑出产物 | 产物落盘且内容被读过 |
| **VM 实跑** | 在 `vm`（`/data/论文/leo-direct-sim`）上真跑出产物 | VM 上的产物文件 |
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
| 逐包比较驱动的 14 条测试（含三条拒绝路径） | `CODE/experiment_platform/tests/test_minimal_branch_compare.py` | 本地实跑 ✅ |
| 有界每星计算资源 + 计算排队的 6 条测试（含无界逐位相同负对照、服务器数单调性） | `CODE/leo_sim/tests/test_compute_delay.py` | 本地实跑 ✅ |
| 逐包比较产物 | `out/branch-compare.json`（`minimal-branch-compare/v1`） | 本地实跑 ✅ |
| 成本/压力探针产物 | `out/cost-pressure.json`（`cost-pressure-probe/v1`） | 本地实跑 ✅ |
| 无学习小场景 profile | `CODE/leo_sim/profiles/t1_frozen_branch_smoke.yaml` | 本地实跑 ✅ |
| **VM 实跑**（本轮） | VM `/data/论文/leo-direct-sim/ANALYSIS/DIAG-T1-FROZEN-BRANCH-20260926/`，拉回 `out/vm/`；部署 `leo-vmdeploy@f9a7464`，`code_sha256=ff8ba667…`，Python 3.11.15 / simpy 4.0.1 / numpy 1.24.3 | **VM 实跑 ✅** |
| VM 上的平台测试套件 | `3 failed, 1005 passed, 1 skipped`——3 条失败全部归因于部署形态（无 `.git`、父仓实验实例），非本轮引入 | VM 实跑 ✅（含失败） |
| 跨环境一致性 | 语义字段逐项相同；算力争用 `wait_total=17.234256 s` 两边相同；`branch_fingerprint` **不同**（6/1199 叶子为 ≤1e-9 的相对浮点差） | 见 `ANALYSIS/VM-RUN-20260926.md` §4 |

---

## D. 研究比较：现在能说什么、不能说什么

**能说**（有本地配对证据）：
- `frozen` 的分支点是**观测时刻**，且在 `compute_delay>0` 时严格早于提交；强制动作必须落在该观测记录的合法集内，否则 fail-loud。
- 一对分支的**分支前状态逐行相同**（决策指纹 + 分支时刻之前的全部 timeline 行），**分支后的第一个差异出现在分支时刻之后**。
- 在 `t1_frozen_branch_smoke` 上，`N` 与 `S` 两个候选的真实差异是：走 `N` 送达（45.13 s，路径 [0,1,2]），走 `S` 在对端拿不到路由信息、61 次 hold、地平线内未送达。**这是"机制可分辨"的证据，不是"某个路由策略更好"的证据。**

**不能说（剩余阻塞）**：
1. **本场景里"改选另一个合法候选"从未成功送达**。在 3 个负载档 × 4 个种子 × 3 个计算时延档下扫描，**没有找到"两个候选都送达"的梯度对照**；已定位原因：`routing.policy=hop` 下冻结推理规则取 `legal[0]`，其余合法候选在本地星座里是死端（对端 `no_info` → 反复 hold）。因此**当前还比较不了"代价大小"，只比较了"可行/不可行"**。
2. 因此 **`research comparison complete` 不成立**：要拿到梯度代价，需要一个候选之间真正可竞争的配置（换策略或换 OD/星座规模），这属研究选择，本轮未替你决定。
3. **main 系代码没有在 VM 上跑过**（VM 停在 R5 冻结版）。VM 实跑的结果见 §C 指向的文件；它跑的是本轮改动的诊断路径，**不是正式授权运行**（`research_eligible=false`）。
4. 十一时刻**字段**仍不进回执信任链；只有两条流的哈希进链。

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
