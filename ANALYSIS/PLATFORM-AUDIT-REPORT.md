# PLATFORM-AUDIT-REPORT — 平台科研可用性审查

| 字段 | 值 |
| --- | --- |
| MAIN_SHA（基线） | `549acd8b7cb7a1bb573e7146653f55273999086d` |
| 审计分支 | `agent/20260924-platform-audit` |
| 日期 | 2026-09-24 |
| 平台根 | `CODE/leo_sim`（kernel 4615 行）、`CODE/experiment_platform`、`CODE/scripts/remote` |
| 方法 | 逐行读码 + 实跑取证。每条结论带 `file:line` 或真实命令输出。 |
| 证据分级 | **[EXEC]** 已执行验证 / **[READ]** 读码所得 / **UNDETERMINED** 未定 |

> **本报告的目的**：找出「实现存在但实验解释链不闭合」的位置，并**修复**或**明确限制**。
> 不新增算法、不优化性能、不重构架构、不改变既有论文结论、不降低 fail-loud 条件。

**审计期间产出并已验证的修复**（分支上 6 个提交，每个独立可解释）：

| commit | 主题 |
| --- | --- |
| `f4579b6` | docs: 平台事件链事实基线 `ANALYSIS/CURRENT-EVENT-TIMELINE.md` |
| `03aecfb` | fix: 打通 F2 经正式 CLI 的实验链（`--timeline-log`） |
| `8e69d79` | fix: 独立指标重算显式区分 F2 节点处理与决策计算 |
| `fb6b1a6` | test: F2 时延分离 / 单因素合法性 / 正式入口可达（Q1/Q2/Q3） |
| `a993374` | test: 钉死利用率分母语义 |
| `81f7d5f` | docs: 流量模型与 M-Lab 数据声明边界 `ANALYSIS/TRAFFIC-MODEL-SPEC.md` |
| `f2b8897` | test: 钉死反事实与 frozen 两条链的声明边界 |

---

## 0. 基线事实：main 是否绿

**[EXEC]** 在干净 worktree（`git worktree add`，仅检出 `549acd8b`）运行 CI 等价命令：

```bash
python3 -m pytest CODE/leo_sim/tests CODE/experiment_platform/tests \
                  CODE/tests ANALYSIS/tests PAPER/tests -q
```

结果：**1 failed, 966 passed, 2 skipped**。失败项
`ANALYSIS/tests/test_document_governance.py::test_repository_document_governance_is_clean`。

**定位（取证，非猜测）**：该测试在**干净检出上并不因平台缺陷失败**，而是因为
`scripts/check_document_governance.py` 的 `current_fact_sync.source_glob`
（`ANALYSIS/EXP-*/**/claim-gate.json`）按**文件系统**扫描，而主 worktree 里存在
**未跟踪的 R06 证据目录**（`ANALYSIS/EXP-20260923-T1-STEP5-{MICRO,PRESSURE}-R06/`）。
这些目录属于 **OPEN PR #218**（`agent/20260923-t1-step5-r06-evidence`）的产物，尚未并入 main。

- 主 worktree 报错：`CURRENT_FACT_SOURCE_OUTDATED … latest tracked source is …PRESSURE-R06/…`
- 我的审计 worktree（无这些目录）不含该错误；只在我新增未登记文档后出现 `UNCLASSIFIED_DOCUMENT`
  （已通过登记 `DOCUMENT-STATUS.json` 解决，见 §5）。

**结论**：**main 在干净检出上是绿的**；本地红是 worktree 卫生问题 + 脚本把"文件系统"说成"tracked"的措辞缺陷。
两条都记录为工程问题，**不修改门禁标准**（AGENTS.md 规则 18）。

---

## 1. Confirmed capabilities（已确认的平台能力，带证据）

| # | 能力 | 结论 | 证据 |
| --- | --- | --- | --- |
| C1 | 九事件时间链完整、可归因 | **PASS** | `ANALYSIS/CURRENT-EVENT-TIMELINE.md` §1；`kernel.py:1492-1655`（`_timeline`/`_metric_*`）；`pytest test_kernel.py test_decision_ledger.py` |
| C2 | F2 可经**正式 CLI** 运行 | **PASS（本次修复）** | `test_f2_runs_through_the_official_cli`：`python3 -m CODE.leo_sim run --timeline-log …` 退出 0，产出 receipt + timeline + metrics，`receipt verify` 通过 |
| C3 | F2 与决策计算时间**不混淆** | **PASS（本次修复）** | `test_f2_delay_is_not_decision_compute`：带 span 时 `decision_compute_s` 与「同 run 但 F2=0」完全相等；不带 span 时恰等于两者之和（陷阱被正向钉死） |
| C4 | F2 单因素实验成立 | **PASS** | `test_f2_single_factor_sweep_moves_only_the_node_term`：只扫 `node_process_delay_s` 时 `decision_compute_s` 不变、`node_process_s` 恰为 3×2×f2、queue/holding/tx/prop 均不变、e2e 增量恰为节点增量 |
| C5 | F2 默认 0 时历史 run 逐位不变 | **PASS** | `test_a_zero_cost_matches_the_unset_default_for_default_bit_for_bit`（test_f2_node_cost.py:292-300）；`kernel.py:4293-4295` 不碰时钟直接返回 |
| C6 | F2 不进入 `packet_events` 白名单、不新增机制计数器 | **PASS** | `test_the_node_cost_adds_no_mechanism_counter_and_no_new_event_kind`；`metrics.py:98-218` 封闭白名单 + `217-218` unknown kind 抛错 |
| C7 | frozen：提交只用 `o(t_0)`，绝不重解最优 | **PASS** | `test_frozen_records_a_rejected_action_instead_of_resolving_again`（**既有**，test_frozen_observation.py:168-187）：t0 链路可用 / t1 不可用 → 仍尝试原动作 `E`、`reason=action_no_longer_legal`、包被 park、`IN_SYSTEM_AT_STOP`；对照 refresh 不产生 decision 行 |
| C8 | frozen：不会使用计算期间**新出现**的信息 | **PASS** | `test_frozen_refuses_to_use_information_that_appeared_during_compute`；`kernel.py:3849-3853`（观测在 timeout **之前**取）；`_decide_from_frozen_observation` 只做合法性校验（`3972`/`3982`），失败即 park（`4032-4034`） |
| C9 | frozen 拒绝的可审计性边界已知 | **PASS（本次补测）** | `test_frozen_refusals_are_visible_only_through_the_timeline_sink`：无 timeline 时拒绝完全不可见、行为不变；有 timeline 时拒绝记录完整（含 `t_observed` 与 `observation_at_start`） |
| C10 | 反事实：同分支点被证明、只改一个动作 | **PASS** | `test_branch_states_are_proven_identical_and_the_action_changes`；`counterfactual.py:54-72`、`121-128`（不一致即 REFUSE 而不是报告） |
| C11 | 反事实证明的**覆盖边界**已知 | **PASS（本次补测）** | `test_branch_fingerprint_covers_the_decision_log_and_nothing_else`：`PRECOMMIT_FIELDS` 逐字段生效；覆盖按 **append order** 而非 decision_id 顺序；目标之后入账的行不覆盖（正向断言） |
| C12 | idle link 在**可用容量**分母内 | **PASS** | `kernel.py:1815`「Idle physical links remain in the denominator.」；`test_idle_link_denominator`：空闲链路 `available_capacity_bits>0`、`utilization=0.0`；有服务链路 `400>100` |
| C13 | outage **不是**容量 | **PASS** | `test_idle_capacity_is_absent_when_the_link_is_in_outage`：4 星环只有 12 条链路有容量而非 16，几何不可见卫星的 GSL 链路**根本不出现**；`kernel.py:1802-1803` |
| C14 | 退役/排空链路的容量按 drain 时刻截断 | **PASS** | `kernel.py:1833-1855`、`1867-1896`；`test_capacity_metric_includes_retired_isl_generation`、`test_capacity_metric_cuts_a_retired_generation_at_drain_time` |
| C15 | 独立第二实现是**真**独立代码路径 | **PASS** | `metrics_independent.py` 仅标准库；import 时 AST 守卫禁止引用 `metrics`（`:785-810` HEAD）；`[EXEC]` 48→50 测试全通过 |
| C16 | 同种子逐字节可复现 | **PASS** | **[EXEC]** 同 config 跑两次 `diff -r` 全部产物逐字节相同；需钉死 seed/identity 载荷/输入字节/code sha/numpy 版本 |
| C17 | trace 内容寻址、三重身份绑定 | **PASS** | `trace.py:834`（`trace_sha256`）、`config.py:909-917`（`trace_identity_sha256`）、`config.py:1024-1025`（resolved sha） |
| C18 | trace/config 路径 fail-loud 无静默回退 | **PASS** | **[EXEC]** 无覆盖 mlab / 缺文件 / 缺 csv / 未知字段 / 非法 mode 全部抛错；`trace.py:520-525`、`790-797`；`receipt.py:334-340` 校验 M-Lab 免责 |
| C19 | 编译产物默认不可执行 | **PASS** | **[EXEC]** `compile-report.json` → `status=COMPILED_REVIEW_REQUIRED`、`errors=[]`、`execution_authorized=false` |
| C20 | 授权器可产出 `status=AUTHORIZED` 并绑定逐 run 配置 | **PASS** | 见 §6 最小实验；`authorize_experiment.py:404-544` |

---

## 2. Confirmed limitations（已确认限制，禁止扩大声明）

| # | 限制 | 事实 | 因此**不能**声明 |
| --- | --- | --- | --- |
| L1 | M-Lab 是**逐小时城市对聚合**，不是逐包 | 44 929 行、9 列全为小时聚合；18 765 client 坐标 : **75** server 坐标；`trace.py:34` 硬编码路径；仓库内**无** pcap/parquet/h5 | 「真实逐包时间序列」、端到端 OD 需求矩阵、实测 burst/diurnal、已校准 offered load |
| L2 | 无 `flow_id`、无流对象 | `grep -rn "flow_id\|flowid" CODE/` = **0**；`DataPacket` 全字段 per-packet（`kernel.py:67-97`）；ledger 按 `pid` | 任何 flow-level / 每流结果 |
| L3 | `offered_mbps` ≠ 实际 offered load（burst/diurnal 开启时） | **[EXEC]** `mlab_multiod_burst_t0`：目标 50.0 Mbps，实测 **64.95** Mbps | 「offered load 等于配置值」 |
| L4 | RNG 子流声明**过度** | `rng.py:15-29` 声明 8 流；实际只消耗 `demand`（`trace.py:694`）与 `nested_filter`（`trace.py:703`）；GE 走 `link_stream`（`kernel.py:789/2223`）；另 6 流全库零引用 | 「每机制独立流、开关互不扰动」（对那 6 个流不成立） |
| L5 | **没有**任何状态哈希 | `grep -rn "state_hash" CODE/` = **0**；反事实证明是 decision-row 指纹（`counterfactual.py:34-72`） | 「两次 run full state identical」；**只能**声明「经决策日志见证到达同一分支点」 |
| L6 | 默认配置下 `utilization` **退化为恒 1.0** | `metrics.py:334-340`：无可用容量账本时 `denominator = capacity_bits`（= 服务窗口容量），而 `available_capacity_interval_s` 默认为 `None`（`config.py:409`） | 默认配置下的任何「链路利用率」数字 |
| L7 | frozen 的被拒提交只存在于 timeline | `kernel.py:3991-4005`；`_record_decision` 仅在 commit 时写（`3571`） | 「decision 行完整覆盖 frozen 的全部尝试」 |
| L8 | F2 **不是** receipt 的一等项 | 只写 timeline sink（`kernel.py:4297/4300`）；提升为 receipt 术语需改 `metrics.py` 白名单与 receipt 键集（冻结契约，另一任务） | 「receipt 自身含 F2 分解」 |
| L9 | F2 无法经 **canonical remote runner** 执行 | F2 硬要求 timeline sink（`kernel.py:1145-1152`）；`remote_job.py:304-309` 只传 5 个固定参数、从无 timeline；`run-remote.sh:44` 显式拒绝 `--`（"arbitrary commands are forbidden"） | 「F2 已在正式 VM 链路上跑通」 |
| L10 | GE 随机停机时间**计入** available capacity | `kernel.py:1738/1747` 只查几何可用性，从不查 `GilbertElliott.is_down`；与 `:1706-1708` 的措辞存在张力；**无任何测试固定该定义** | 「available capacity 排除了随机停机」——该定义尚未作出 |
| L11 | 生产 receipt 的指标护栏仍是**自比较** | `receipt.py:791-801` 仍调用 `metrics.summarize` 与自身比对；`metrics_independent` 只在测试与 step5 harness 中被引用 | 「receipt 校验由独立第二实现完成」 |
| L12 | 正式链的「审阅执行」阶段是**纯 prose** | 无任何工具执行审阅；`CODE/work/finalize_decision.py:71` 只对三份 JSON 做角色集/PASS/hash 的**算术重算** | 「平台机械保证了审阅真的发生过」 |
| L13 | 三份审阅回执来自**同族 agent 会话**，非人工评审 | 见 `CODE/work/WP-LEO-V2-PLATFORM-AUDIT-F2/R01/brief.json` 的 REVIEWER PROVENANCE 段 | 「已通过独立机构/人工评审」 |
| L14 | 本地正式执行 ≠ canonical 远端执行 | `__main__.py:208-233` 接受与远端相同的正式绑定参数并写 `formal_run.json`（`:445-450`），但不带 VM provenance | 「本地 formal-flag run 等于 VM 正式 run」 |
| L15 | **Fano 因子是负载相关的** | 同一 burst 形状下负载 ×16 使 Fano ×11（3.37→37.25），而 CV² 基本恒定（1.713–1.747） | 跨不同负载水平的 arm 比较 Fano；把 Fano 当"突发度"会让 Mode B 显得更突发。**必须用 CV²**（见 `ANALYSIS/BURST-DESIGN.md` §3.2） |
| L16 | **不存在 cv²/scv 一维旋钮** | `grep -rniE "burstiness\|cv2\|scv\|hurst\|pareto\|on-off" CODE/` = 0 | 「用一个旋钮固定均值改突发度」；Mode A 必须联动两条路径 |
| L17 | **burst 窗可落在 `emission_end_s` 之外（声明了但从未观测）** | **[EXEC]** `emission_end_s=20`、窗 `[30,40)` → 实测 0.9800（无突发），而 manifest **仍记录** `traffic_transform.burst`；`config.py:585-589` 只比对 `scenario.duration_s` | 「配置了 burst 就等于施加了 burst」；必须报告实测负载与有效窗占比 |
| L18 | **burst 旋钮在 `mode=csv/uniform` 下静默失效** | `trace.py:303` 只在 `mode ∈ {burst, mlab}` 应用；**[EXEC]** 126 份 resolved config 中 **55 份**携带 `burst_multiplier` 而其 mode 永不生效 | 「resolved config 里有 `burst_multiplier` 就说明该实验是 burst 实验」 |
| L19 | **`execution_authorized=false` 字段名误导** | 该字段只描述"编译产物尚未被授权"，**不表示"没有授权就不能运行"**（见 B1） | 把该字段读作运行门禁 |
| L20 | **审阅独立性只有 session-id 语义** | `finalize_decision.py:175-183` 只校验两个 `const:true` 标志与 `(reviewer_id, reviewer_session_id)` 不重复，**没有任何模型/族/机构语义** | 「receipt 里的 independence=true 等价于独立评审」（见 L13） |

---

## 3. Blocking issues（阻塞项）

| # | 问题 | 影响 | 处理 |
| --- | --- | --- | --- |
| **B1** | 模拟器**不强制授权**：`__main__.py:209-211` `if not any([auth,nonce,run_id]): return None`；`--authorization` 默认 `None` | **[EXEC]** 未经编译/审阅/授权/部署的 `smoke.yaml` 仍 `exit 0` + `receipt verify = verified`。真实不变量是「无授权不能被**分析**」，不是「不能**运行**」 | **限制声明**（改 `__main__.py` 授权判定属授权链承重改动，须独立冷启动复核）。已在 §2 L14 与请求 `claim_boundary` 写明 |
| **B2** | **入库的 20 份 `authorization.json` 在严格门下全部失败**（`ok=0 fail=20`），主因 `code_sha256` 漂移（`matrix.py:961-962`） | `remote_job`/`v2_serial_gate` 用严格函数 → **今天无法正式启动任何已入库实验**；`v2_analysis` 只能靠 `auto`/`bound_posterior` 回退（19/20） | **限制声明 + 修复方向**：任何 `CODE/leo_sim/*.py` 改动都会作废全部回执与授权，需要事前可机械检测的爆炸半径声明（R06 brief 已独立提出同一问题） |
| **B3** | 串行前置门对**多数**实验是**空操作**：`v2_serial_gate.py:52-55` `policy = request.get("execution_policy"); if policy is None: return []` 在**任何授权校验之前**返回 | **[EXEC] 本审计已亲自复现**：33 份入库实验中只有 **4** 份声明 `execution_policy`（`{mode: serial_fail_closed}`），另 **29** 份为 `null`。对一个 policy 为 null 的实验（R03）传入**根本不存在的** `authorization.json`，`verify_predecessors` **返回 `[]`** —— 门是空操作；对照组（`GLOBAL-PRESSURE-BRACKET-R02`，声明了 policy）则真的校验并抛 `AuthorizationError: invalid leo_sim V2 matrix: matrix compile report design accounting mismatch`（此处正是 B2 的 code_sha256 漂移在起作用）。`test_v2_serial_gate.py:60-65` 把静默通过**断言成预期行为** | **限制声明**（改门禁语义属授权链改动，须独立复核） |
| **B4** | 编译期与分析期**指标词表不相交且无交叉校验**：`matrix.py:181-183` 只要求 `primary_metric` 是**非空字符串**；`v2_analysis.py` 的分发只认自己的白名单；`metric-catalog.json` 被 V2 路径完全忽略 | **[EXEC] 本审计已亲自复现并量化**（数字经 rev3 对抗复核更正）：`metric-catalog.json` 列 **23** 个指标，`v2_analysis._metric_from_result` 只接受其中 **3** 个（`delivery_rate`、`access_admission_rate`、`network_delivery_rate_by_horizon`），另 **20** 个抛 `V2AnalysisError: unsupported V2 primary metric`（含 `p95_e2e_latency`、`cvar5_e2e_latency`、`packet_loss_rate`、`mean_e2e_latency` 等）。而 catalog 自己把其中 **10** 个标记为 `eligible_as_primary`。由于编译期只查"非空字符串"，这 20 个指标**都能顺利编译、跑完全部 run，然后在分析阶段才炸** | **限制声明 + 最小修复**：本审计的最小实验刻意选白名单内的指标，并在 §11 记录该缺陷；真正的修复（编译期交叉校验 catalog 与分发白名单）属授权链改动，须独立复核 |
| **B5** | 默认配置 `utilization ≡ 1.0`；且 `v2_analysis` 在该读数上**硬中止** | **[EXEC]** 默认 run → 4 条链路全部 `utilization = 1.0`、`available_samples = 0`、分母=服务窗口容量。**[EXEC]** `v2_analysis._run_diagnostics(ledgers)` 直接抛 `V2AnalysisError: windowed ISL pressure evidence is invalid: served bits without available capacity for isl:0:1 at 40.0`（`v2_analysis.py:302-306` → `isl_pressure.py:345`）—— **根本没有到达 `:337` 的 saturated 分支**。→ 后果不是"假饱和告警"，而是**未设 `available_capacity_interval_s` 的实验根本无法通过分析**；该要求**未在编译期强制**，只会在所有 run 跑完后才暴露（与 B4 同类）。 | **限制声明**（改 `metrics.py:334-340` 会改变历史 receipt 数值 → receipt 兼容性破坏，禁止）。已在 `test_idle_link_denominator` 正向钉死「该读数不是利用率」。**更正说明**：本行曾写成"标为 saturated"，那是**读码推断**；独立对抗复核实跑后证明是硬中止，此处按执行结果更正。 |
| **B6** | `metrics_independent` 在 `served==0` 且**无可用性覆盖**时可发布**伪造的 0.0**（`status=OK`、`available_capacity_bits=0.0`）；生产实现给出同样的 0.0，**交叉校验因此失明** | **[EXEC] 本审计已亲自复现**：构造「可用性窗口只覆盖 `isl:0:1`，而 `isl:1:2` 只有一条 stalled 服务窗口」的输入，该模块对 `isl:1:2` 返回 `status=OK`、`utilization=0.0`、`available_capacity_bits=0.0`、`available_samples=0` —— 分母**根本不存在**却发布了数值，而不是本模块自己定义的 `STATUS_DEGENERATE_DENOMINATOR`。恰好出现在「为抓伪造分母而建的模块」里，与模块自身契约矛盾；生产实现给出同样的 0.0，所以**交叉校验对这一情形是失明的** | **限制声明**（改判定会改变既有分析数值，须单独任务与冷启动复核） |
| **B7** | **F2 无法经 canonical remote runner 执行**（L9） | 本审计让 F2 在**本地**正式链上可跑通，但 VM 正式链路仍不可用 → F2 只能作为**工程能力**，不能作为**正式实验因子** | **本次部分修复**（新增 `--timeline-log`，commit `03aecfb`）+ **明确限制**（改 `remote_job.py` 属授权链承重改动且本环境无 VM 可验） |
| **B8** | `scene_check` 硬绑定业务模型：`scene_check.py:278-291` 要求 `provenance=="population_proxy"` 且 `temporal_model=="local_diurnal_cosine"`；`scope` 硬编码 `global_populated_land`（`:250-251`、`:919`） | 唯一入库的 scene-check lane 恰好满足该耦合 → **M-Lab / 合成业务模型无法通过场景合法性分类** | **限制声明**（改 scene_check 属指标/门禁契约） |
| **B9** | 主 worktree 携带 **OPEN PR #218** 的未跟踪 R06 证据目录 | 本地文档治理门禁变红；若被误读为 main 缺陷会浪费排查 | **限制声明 + 卫生建议**：合并或移除 PR #218 后自然消失；**本审计未删除任何未跟踪唯一文件**（AGENTS.md 规则 12 DIRTY-PROTECT） |
| **B10** | **分析腿在本地不可达**：`v2_analysis.py:410-415` 把 `governance_receipt.json` 列为**必需**文件，而该文件**只由** `remote_job.py` 写入（`build_v2_governance_receipt` `:132`/`:678`，落盘 `:700-703`）；v5 回执还强制 VM 外部启动证人（`:440-442`、`:494-497`、`:922`） | **[EXEC] 本审计已亲自复现**：本地正式旗标运行产出的结果目录只有 7 个文件 —— `ledgers.json / manifest.json / receipt.json / resolved_config.json / timeline.jsonl / timeline.jsonl.manifest.json / trace.csv` —— **没有** `governance_receipt.json`。全库非测试代码中，写该文件的只有 `remote_job.py:700` 一处。因此任何**本地**执行的实验（含本审计的最小实验）**无法**进入 canonical 分析；"实验链闭环"只能到 receipt + metrics + 独立重算为止 | **限制声明**（修复需要 VM 与授权链改动）。已在最小实验的 brief/PROCEDURE 中显式声明为 declared limitation，并把 deliverable 限定为可达成范围 |
| **B11** | **F2 的 e2e 可加性在有竞争时失效** | **[EXEC]** 8 星 / 1 面 / csv 微 trace 上，总 e2e 增量 **≠ 6×3×0.05 = 0.900 s**：**R02 设计（`compute_delay_s=0.05` 两臂共享）实测 0.849200279 s**；**R01 设计（`compute_delay_s=0`）实测 0.850000245 s**。两者是同一次吸收的两个设计下的读数，**不可互换**。pid 99 有 0.15 s 节点占用而 e2e 只增 0.099999964 s，其 `holding_wait_s` 减少 0.050000000 s；pid 2 另有 8.000e-04 s 转入 `queue_wait`。无竞争的 2 星装置则严格可加 | **限制声明**：**不得**声明「F2 只移动节点阶段」或「e2e 增量恰为 包数×访问数×时延」；只能声明「节点占用被独立记录且未计入决策计算时间」。已在 `CURRENT-EVENT-TIMELINE.md` §5.4 与最小实验 claim_boundary 中写明 |
| **B12** | **"恰一个变化因素"在唯一可正式执行的路径上没有机械门禁** | **[EXEC] 本审计已亲自复现并定位**：严格单因素门**确实存在**，但在 **legacy 编译器**里 —— `compile_experiment.py:462-468` `if design["one_change_policy"] == "strict": if len(factor_changed) != 1: 报错`。而本仓库唯一可正式执行的 `leo_sim_v2` 路线走 `matrix.py`，其请求的顶层键被白名单限定为 `{acceptance, analysis, arms, cells, claim_boundary, common_config, experiment_id, runtime_kind, schema, work_finalization}` —— **既没有 `design` 也没有 `one_change_policy` 字段**，`_expect_keys` 会直接拒绝未知字段，因此 V2 路线**连"声明策略"的地方都没有**。`matrix.py:294` 只强制 `intervention_paths` 等于该 arm 的实际 override 集合（**声明诚实性**），与"变化因素个数"是**两个不同的性质** | **限制声明 + 修复方向**：Mode A（固定均值变突发度，两条路径）走 V2 路线时不会触发任何"非严格"提示，"必须声明 exploratory_multi_factor 且不得声称单因素因果"目前**只是协议纪律**。修复（给 V2 请求加策略字段并在编译期计数）属授权链改动，须独立冷启动复核 |
| **B13** | **第二实现读不了持久化产物** | `verify_delay_decomposition(events, windows, json.load(ledgers.json))` 中 `deliveries` 键是**字符串**而事件 `pid` 是 **int** → declared 与 observed 交集为空，全部包被报成 `declared delivered but has no delivered event`，`checked_packets = 0` | **本次已修复**：新增 `_declared_pid`（接受 int 与规范十进制 JSON 键；`"07"`/`"7.0"`/`" 7"` 一律 fail-loud），测试 `test_the_persisted_ledger_mapping_is_accepted_after_a_json_round_trip` |

---

## 4. Non-issues（看似问题，实为设计选择）

| # | 现象 | 为什么**不是**缺陷 |
| --- | --- | --- |
| N1 | ~~formal run 拒绝 `--decision-log`（`__main__.py:332-335`）~~ **【2026-09-24 起不再成立】** | 当时成立的前提是决策流**没有行键契约**。现在 `decision_ledger.DECISION_ROW_KEYS`（19 键，双向 fail loud）已落地，正式运行可附 `--decision-log`；两条流齐备时回执升为 `leo-sim-receipt/v6`，其流身份与 `fold_decision_ledger` 的 source 哈希闭合。**N2 / N3 不受影响。** 证据见 `CODE/work/WP-LEO-V2-T1-TRUST-CHAIN-V6/` |
| N2 | `node_process_delay_s>0` 且无 timeline → `KernelError`（`kernel.py:1145-1152`） | 这正是 AGENTS.md 硬事实 4 要求的 fail-loud：不可归因的节点时间**不得**被静默计入 |
| N3 | `decision_compute_s` 用「未覆盖区间之和」定义 | 定义本身合理（模拟计算占用时间）。缺陷**不在定义**，而在 F2 的占用也是未覆盖区间却未被命名——已由 `node_spans` 修复（`8e69d79`） |
| N4 | 缺省 `demand.mode = "uniform"`（`config.py:278`） | 有文档的默认值，且计入 resolved config 哈希；属清单风险而非静默回退 |
| N5 | `offered/admitted` 比值为 0/0 时输出 `0.0`（`metrics.py:389-392`） | 有文档、有测试（`test_congestion_metrics.py:141-146`）、被 `metric-catalog.json:6-7` 认可，属**已声明**的非 fail-loud 例外 |
| N6 | `"validation": {"ok": True}` 是常量（`metrics.py:397/404`） | 因为所有违规都**抛异常**而非置位状态；常量 True 只表示"走到了这里" |
| N7 | frozen 不允许与 learning 臂或 `forced_actions` 组合（`kernel.py:1124-1133`） | 二者都会把分支点移动到别处，与 frozen 语义直接冲突；显式拒绝而非静默降级 |
| N8 | 目的地下行终点不做 F2（`_deliver_after_prop`） | F2 的作用域被**显式**限定为「卫星节点接收/处理/调度」，地面端与终结点不是卫星节点；注释与测试都写明（`kernel.py:4283-4287`） |
| N9 | `_transmit` 的「链路降级前的等待」不计入服务时间 | 这是 K2 不变式：只有全部可用性检查通过后才翻 `_svc_phase`（`kernel.py:2567-2575`），避免把等待记成传输 |
| N10 | 三份审阅回执由不同冷启动 agent 会话给出 | 平台既有实践（R06 用 GLM / Muse 两个非 DeepSeek 家族）；本审计沿用并在 brief 中**如实披露**其性质与边界（L13） |

---

## 5. 文档治理与测试状态

- **[EXEC]** `python3 scripts/check_document_governance.py --mode all` → `0 selected errors, 0 warnings`。
- 新增文档已在 `ANALYSIS/DOCUMENT-STATUS.json` 登记：
  `CURRENT-EVENT-TIMELINE.md`（EVIDENCE-SNAPSHOT）、`TRAFFIC-MODEL-SPEC.md`（CURRENT-CONTRACT），
  以及本文件（CURRENT-VOLATILE）与 `BURST-DESIGN.md`（CURRENT-CONTRACT）。
- **未删除任何测试、未放宽任何 tolerance、未降低任何 fail-loud 条件。**
- **新增测试函数 11 个**（`git diff main -- CODE/leo_sim/tests/ | grep -c "^+def test"` = 11），
  分布在 6 个测试文件、净增 599 行：
  - F2 归因族（3）：`test_f2_delay_is_not_decision_compute`、
    `test_f2_single_factor_sweep_moves_only_the_node_term`、`test_f2_runs_through_the_official_cli`
  - CLI timeline 族（3）：`test_node_process_delay_without_a_timeline_log_is_refused`、
    `test_timeline_log_rejects_an_existing_target`、`test_the_timeline_log_is_output_only`
  - 分母族（2）：`test_idle_link_denominator`、`test_idle_capacity_is_absent_when_the_link_is_in_outage`
  - 声明边界族（2）：`test_branch_fingerprint_covers_the_decision_log_and_nothing_else`、
    `test_frozen_refusals_are_visible_only_through_the_timeline_sink`
  - 第二实现读取族（1）：`test_the_persisted_ledger_mapping_is_accepted_after_a_json_round_trip`
- 全量测试：**基线 `main` 本地 966 passed / 1 failed（局部污染）→ 本分支 982 passed / 2 skipped / 0 failed**。

---

## 6. 最小实验：实验链闭环

见 §11「最小实验证据」。

---

## 7. 研究声明分级（最终结论）

> 任务书要求明确：哪些**可以用于论文 claim**、哪些**只能作为工程能力**、哪些**当前不能使用**。

### 7.1 可以用于论文 claim ✅

| # | 可声明内容 | 必须同时满足的前提 | 证据 |
| --- | --- | --- | --- |
| P1 | 包级端到端时延可分解为 queue / holding / tx / propagation / node_process / decision_compute 六个**命名且互不重叠**的项，闭合残差 ≤ 1e-9 | 启用 F2 时必须把 timeline span 交给分析器 | `metrics_independent.verify_delay_decomposition`；`test_f2_delay_is_not_decision_compute`；`test_f2_single_factor_sweep_moves_only_the_node_term` |
| P2 | 链路**可用容量**的定义：几何/速率决定、独立于服务采样；空闲链路在分母内（utilization=0），几何不可见链路不在分母内 | **必须**设置 `execution.available_capacity_interval_s` 非 null | `metrics.py:334-340`；`kernel.py:1815`；`test_idle_link_denominator`；`test_idle_capacity_is_absent_when_the_link_is_in_outage` |
| P3 | `utilization` 是 **goodput 利用率**（bits/bits），**不是**时间占用率；二者在服务失败时显著不同 | 报告时须同时给出定义与 `available_time_s` | `test_idle_link_denominator`（stalled 链路 util=0.0 而占用率=1.0） |
| P4 | F2 节点处理是与 PHY 传输（F3）、决策计算（compute_delay）**互不混淆**的独立可归因阶段 | 仅限机制层面的可归因性，不含任何性能效应 | `test_f2_node_cost.py` 21 项全通过；`test_sweeping_the_phy_rate_changes_tx_and_never_the_node_reading` |
| P5 | frozen 观测语义：提交只依据 `t_0` 的观测、动作不再重解；refresh 会在 commit 时重读状态 | — | `test_frozen_observation.py`（含拒绝场景与静态对照） |
| P6 | 反事实严格配对：同 config/trace/seed 重放到**同一分支点**，只强制一个动作；分支点不一致时**拒绝报告** | 只能声明「经**决策日志**见证的分支点同一性」 | `counterfactual.py:121-128`；`test_branch_fingerprint_covers_the_decision_log_and_nothing_else` |
| P7 | 合成逐包 trace 是**合成、不可变、内容寻址、逐字节可复现**的；逐包时间性是**模型输出** | 必须引用 `measurement_proxy` 与 `not_calibrated_user_demand` 标签 | `trace.py:827-834`、`945-950`；`receipt.py:334-340`；**[EXEC]** `diff -r` 相同 |
| P8 | 编译产物与授权的哈希绑定、`execution_authorized=false` 的默认不可执行 | — | `governance.py:366-369`；**[EXEC]** `COMPILED_REVIEW_REQUIRED` / `errors=[]` |

### 7.2 只能作为工程能力 ⚠️（不得作为科学 claim）

| # | 能力 | 为什么只是工程能力 |
| --- | --- | --- |
| E1 | 同种子逐字节复现 | 说明**流水线确定性**，不说明模型被真实数据校准 |
| E2 | M-Lab → OD 权重先验的数据管道 | 数据是逐小时聚合，只提供先验权重 |
| E3 | csv 逐包回放通道 | 通道忠实，但库内唯一被引用的回放文件是自造微场景 trace |
| E4 | F2 经正式 CLI 运行（本次新增 `--timeline-log`） | 本地/入口层可用；**VM 正式链路仍不可用**（B7） |
| E5 | 文档治理门禁 + receipt 自校验 | 保证工程可审计性，不保证科学结论 |
| E6 | 独立第二实现的存在 | `metrics_independent` 是**真**独立代码路径（C15），但**不在 receipt 路径上**（L11），因此只是分析侧能力 |

### 7.3 当前不能使用 ❌

| # | 不能声明/使用 | 原因 |
| --- | --- | --- |
| X1 | 「真实逐包时间序列」 | L1：库内无任何逐包记录；`README.md:22-23` 明文否认 |
| X2 | 端到端 OD 需求矩阵 | L1：18 765 : 75 的 client→server 不对称，是 Speedtest 式测量 |
| X3 | 「实测 burst / diurnal profile」 | L1 + `README.md:14-17`：是 reproducible stress transform，非实测 |
| X4 | flow-level / 每流结果 | L2：无 `flow_id` |
| X5 | 默认配置下的任何「链路利用率」数字 | L6 / B5：`utilization ≡ 1.0`（`available_samples=0`，分母=服务窗口容量）；且 `v2_analysis` 在该读数上**硬中止**（`V2AnalysisError`，`v2_analysis.py:302-306`），**并非**「标为饱和」——该更正由独立对抗复核实跑得出 |
| X6 | 「两次 run full state identical」 | L5：无任何状态哈希，只有 decision-row 指纹 |
| X7 | F2 作为**正式 VM 实验因子** | B7 / L9：`remote_job.py` 不传 timeline stream，`run-remote.sh` 无 passthrough |
| X8 | 「已入库实验今天可正式启动」 | B2：20/20 授权在严格门下失败（code_sha256 漂移） |
| X9 | 「无授权不能运行」 | B1：**[EXEC]** 无授权 run 仍 exit 0 + `receipt verify = verified`；真实不变量是「无授权不能被分析」 |
| X10 | 「串行前置门覆盖全部实验」 | B3：26/30 实验的 `execution_policy` 为 null → 门是空操作 |
| X11 | 「M-Lab / 合成业务模型可经 scene-check 分类」 | B8：`scene_check` 硬绑定 `population_proxy` + `local_diurnal_cosine` + 硬编码 scope |
| X12 | 「receipt 指标由独立第二实现校验」 | L11：`receipt.py:791-801` 仍是自比较 |
| X13 | 「available capacity 已排除随机 GE 停机」 | L10：定义未作出，且无测试固定 |
| X14 | 「平台机械保证了审阅真的发生过」 | L12：无工具执行审阅，只做算术重算 |
| X15 | 「已通过人工/机构独立评审」 | L13：三份回执来自同族 agent 冷启动会话 |
| X16 | 跨负载水平比较 Fano / 把 Fano 当突发度 | L15：Fano 负载相关（×11） |
| X17 | 「配置 burst 就等于施加 burst」 | L17：窗可落在 `emission_end_s` 之外且 manifest 仍记录 |
| X18 | 用 `burst_multiplier` 判断一个实验是 burst 实验 | L18：55/126 resolved config 携带但永不生效 |
| X19 | 「本地执行的实验可进入 canonical 分析」 | B10：`governance_receipt.json` 只由 `remote_job.py` 写入 |
| X20 | 「F2 的 e2e 增量恰为 包数×访问数×时延」 | B11：有竞争时实测 **R02 设计 0.849200279 s ≠ 0.900 s**（R01 设计 0.850000245 s；两个数字不可互换） |
| X21 | 「严格设计规则由机械门禁保证」 | B12：`matrix.py` 无 one-change 校验 |

---

## 8. 交付物清单

| 交付物 | 路径 | 状态 |
| --- | --- | --- |
| 事件链事实基线 | `ANALYSIS/CURRENT-EVENT-TIMELINE.md` | ✅ |
| 流量模型与 M-Lab 声明边界 | `ANALYSIS/TRAFFIC-MODEL-SPEC.md` | ✅ |
| burst 实验设计（即 `burst_experiment_design.md`） | `ANALYSIS/BURST-DESIGN.md` | ✅ |
| 本审查报告 | `ANALYSIS/PLATFORM-AUDIT-REPORT.md` | ✅ |
| 最小实验（编译/审阅/授权/运行/回执/指标） | `EXPERIMENTS/EXP-20260924-PLATFORM-AUDIT-F2-R01/` + `CODE/work/WP-LEO-V2-PLATFORM-AUDIT-F2/` | 见 §6 |
| F2 CLI 通道修复 + 测试 | `CODE/leo_sim/__main__.py`、`tests/test_cli.py` | ✅ commit `03aecfb` |
| F2 / decision_compute 分离 | `CODE/leo_sim/metrics_independent.py`、`tests/test_f2_node_cost.py` | ✅ commit `8e69d79`、`fb6b1a6` |
| 分母语义契约测试 | `tests/test_congestion_metrics.py` | ✅ commit `a993374` |
| 反事实/frozen 声明边界测试 | `tests/test_counterfactual.py`、`tests/test_frozen_observation.py` | ✅ commit `f2b8897` |

---

## 9. 明确**未**做的事（范围自律）

1. **未新增任何算法、机制或研究方向。**
2. **未重构模拟器架构**：`kernel.py`、`receipt.py`、`governance.py`、`learning.py`、`experiment_platform/` 在本次审计中**零改动**（核对命令见 §10）。
3. **未修改任何论文指标定义来适配代码**；相反，把「`utilization` = goodput 利用率」写成了契约测试。
4. **未删除任何测试、未放宽任何 tolerance、未降低任何 fail-loud 条件。**
5. **未删除、移动或覆盖任何已跟踪路径**；主 worktree 的未跟踪 R06 目录按 DIRTY-PROTECT 原样保留。
6. **未修复**需要独立冷启动复核的承重改动（B1/B2/B3/B6/B8/L10/L11/L12）——只给出**精确定位、影响与修复方向**，符合 AGENTS.md 规则 20 与任务书第十一节。

---

## 10. 复现与核对

```bash
cd <repo>                       # MAIN_SHA 549acd8b7cb7a1bb573e7146653f55273999086d
git checkout agent/20260924-platform-audit

# 门禁
python3 scripts/check_document_governance.py --mode all        # -> 0 errors
python3 -m pytest CODE/leo_sim/tests CODE/experiment_platform/tests \
                  CODE/tests ANALYSIS/tests PAPER/tests -q

# 承重文件零改动核对（输出应为空）
git diff --stat 549acd8b -- CODE/leo_sim/kernel.py CODE/leo_sim/receipt.py \
    CODE/leo_sim/governance.py CODE/leo_sim/learning.py CODE/experiment_platform/

# F2 经正式 CLI（node_process_delay_s > 0 的配置）
python3 -m CODE.leo_sim run --config <cfg> --out <out> --timeline-log <new path>

# burst 混淆复现
python3 /Users/lge/Desktop/topic/leo-runs/probe_burst.py
```

---

## 11. 最小实验证据：编译 → 审阅 → 授权 → 运行 → 回执 → 指标

### 11.1 设计

实验 `EXP-20260924-PLATFORM-AUDIT-F2-R02`（`leo_sim_v2`），**严格 2 cell 配对单因素**：

| 项 | 值 |
| --- | --- |
| 唯一变化因子 | `execution.node_process_delay_s`：0.0（control） vs 0.05（f2） |
| **两臂共享** | `execution.compute_delay_s = 0.05`（**不是因子**） |
| 其他共享 | `links.isl_rate_mbps=10.0`、`routing.policy=hop`、`demand.mode=csv` + `CODE/data/traffic/t1_step5_micro_ab.csv`、`scenario` seed 7 / 8 星 / 1 面 / 40 s、`available_capacity_interval_s=0.1` |
| 编译结果 | `COMPILED_REVIEW_REQUIRED`，`errors=[]`，`execution_authorized=false` |
| 单因素核对 | **[EXEC]** 两份 resolved config `diff` 恰有**一处**差异：`"node_process_delay_s": 0` vs `0.05`；`compute_delay_s` 两臂同为 `0.05` |

`compute_delay_s` 两臂共享是**故意的**：它让每条决策都消耗模拟时间，
从而 `decision_compute_s` 非零、"标注/未标注"的对照**具有判别力**，而不是 `0 == 0`。

### 11.2 ★ 关键实测：F2 会（且只会在缺少 timeline span 时）被读成决策计算时间

**[EXEC]** 两臂各 6/6 `DELIVERED`、`natural_end=true`、`conservation_ok=true`：

| 量 | control | f2 |
| --- | --- | --- |
| `node_process_start` / `node_process_end` 里程碑数 | **0 / 0** | **18 / 18** |
| `total_node_process_s`（标注） | 0.0 | **0.8999999999999979** |
| `total_decision_compute_s`（**未**标注，即"未覆盖区间之和"） | 0.899999999999997 | **1.7999999999999958** |
| `total_decision_compute_s`（**已**标注） | 0.899999999999997 | **0.8999999999999979** |
| 未标注 − 标注 | 0.0 | **0.8999999999999979** = 节点总量 |
| 闭合残差 | 4.4e-16 | 8.9e-16 |

**读法**：

1. f2 臂的**未标注**读数 **1.800 s = 0.900 s 真实决策计算 + 0.900 s 节点处理** —— 
   即"启用 F2 后，第二实现会把卫星节点处理时间发布为决策计算时间"这一缺陷被**量化**了，且差值恰等于节点总量。
2. **已标注**读数在两臂**完全一致**（各 0.900 s，恰为 18 次决策 × 0.05 s），
   即给分析器 timeline span 之后，F2 完全不移动决策计算读数。
3. control 臂 timeline 的 `node_process` 里程碑数为 **0**，是设计的负对照。

### 11.3 被**证伪**的可加性声明（不得写进论文）

**[EXEC] 本审计在 R02 设计（`compute_delay_s=0.05` 两臂共享）上亲自复算**：总 e2e 增量
**0.849200279 s ≠ 6×3×0.05 = 0.900 s**，差额 **−0.050799721 s**。逐包：

| pid | f2 节点占用 | Δe2e | Δholding_wait | Δprop | Δqueue |
| --- | --- | --- | --- | --- | --- |
| 1 | 0.150000 | 0.149999992 | 0.000000000 | −7.7e−09 | 0 |
| 2 | 0.150000 | 0.149200028 | 0.000000000 | 2.8e−08 | **−8.000e−04** |
| 3 | 0.150000 | 0.150000063 | 0.000000000 | 6.3e−08 | 0 |
| 4 | 0.150000 | 0.150000098 | 0.000000000 | 9.8e−08 | 0 |
| 5 | 0.150000 | 0.150000134 | 0.000000000 | 1.3e−07 | 0 |
| 99 | 0.150000 | **0.099999964** | **−0.050000000** | −3.6e−08 | 0 |

pid 99 与 pid 2 说明吸收发生在两处：pid 99 有 **0.050000000 s** 的节点占用被既有
`holding_wait` 抵消，pid 2 另有 **8.000e−04 s** 转入 `queue_wait`。
即**并非**"每次访问都恰好增加 0.05 s"。

> 该结论最早由**独立对抗复核实跑发现**（在 **R01** 设计上测得总增量 0.850000245 s、
> pid 99 的 `holding_wait_s` 减少 0.050000000000000266 s；R01 的 `compute_delay_s=0`，
> 故绝对值与本表的 R02 数字不同，**两者都证明同一次吸收**）。
> rev 1 的 brief 曾错误声明"只移动节点处理阶段"，该声明已被删除。
> 可加性只在**无竞争**的单元装置上成立（`test_f2_single_factor_sweep_moves_only_the_node_term`）。
> 现已在 brief 的 `cannot_claim` 与 `CURRENT-EVENT-TIMELINE.md` §5.4 中明确排除。

### 11.4 审阅门是**有效的**（这本身就是审计证据）

rev 1 的三个独立冷启动角色给出 **1 PASS / 2 BLOCK**，共 10 条 blocking findings。
它们**全部成立**，其中 3 条直接推翻了生产者自己的声明：

| 角色 | 判定 | 关键发现（全部经复现） |
| --- | --- | --- |
| cold_start | **PASS** | 11 个哈希全部复核一致；在影子根重新编译逐字节相同；确认单因素设计真实；确认 N1 触发且非空洞（对照 `--dry-run` 退出 0） |
| satellite_drl | **BLOCK** | brief 声称"修复后的链已闭环"但 `v2_analysis` **根本不 import** `metrics_independent`；承诺的独立分解**没有任何 procedure 步骤产生**；`compute_delay_s=0` 使验收项退化为 `0==0`；验收门只存在于**用不了的**远端 runner |
| adversarial | **BLOCK** | **证伪单因素声明**（0.850 ≠ 0.900）；**分析腿本地不可达**（`governance_receipt.json` 只由 `remote_job.py` 写）；我报告里"v2_analysis 标为 saturated"是**读码推断错误**，实跑是**硬中止**；`CURRENT-EVENT-TIMELINE.md` §5 已过期；审阅独立性只有 session-id 语义 |

**生产者对每条 blocking finding 的响应**（细节见 `CODE/work/WP-LEO-V2-PLATFORM-AUDIT-F2/R02/brief.json` 的 `revision_reason`）：

1. 删除"链已闭环"的完整性声明，把**分析腿缺口**与远端缺口并列声明；
2. 新增 **PROCEDURE §5b + `R02/attribute_f2.py`**，让承诺的独立分解**真的被产生**，并带 6 条 sentinel；
3. `compute_delay_s` 改为**两臂共享 0.05**，使判别性成立；
4. 声明远端验收门在本地路由**不生效**，并说明 6 个包共享**同一** OD 对、6/6 是**一个**几何路由属性；
5. 声明审阅独立性 = **session-id 不同**，无模型/机构语义；
6. 刷新 `CURRENT-EVENT-TIMELINE.md` §5；修正本报告 B5 的机制描述。

> **这证明该平台的审阅门不是装饰**：它对生产者自己的声明做了独立证伪，并强制收窄了可声明范围。
> 同时也暴露一个**真实局限**：`finalize_decision.py:175-183` 只校验 session-id 不重复，
> "独立审阅"在此平台上**没有模型/机构语义**（L20）。

### 11.5 交付与未交付（诚实清单）

| 项 | 状态 |
| --- | --- |
| compile artifact（`request/compile-report/run-manifest/RUNBOOK/resolved/*`） | ✅ 已产生，`errors=[]` |
| 三份独立审阅回执 | ✅ rev 1 已产生（1 PASS / 2 BLOCK）；rev 2 回执见 `CODE/work/WP-LEO-V2-PLATFORM-AUDIT-F2/R02/` |
| `authorization.json`（`status=AUTHORIZED`） | 见 §11.6 |
| 两臂 receipt + `formal_run.json` | 见 §11.6 |
| f2 臂 timeline 流 + sha256 侧车 manifest | ✅ 已产生（R02 两臂均已跑通） |
| 独立 F2 归因报告（标注 / 未标注 / 泄漏恒等式 / 跨臂容量对照） | ✅ `attribute_f2.py` 已产出并 `ok=true`、`failed_checks=[]` |
| **canonical 远端执行（VM provenance）** | ❌ **不可达**（B7 / L9）：`remote_job.py` 不传 timeline stream，无 passthrough |
| **`v2_analysis` 分析腿** | ❌ **不可达**（B10）：需要只由 `remote_job.py` 写入的 `governance_receipt.json` + VM 外部启动证人 |

### 11.6 授权门：**可达但未达成**，且这是刻意的

**rev 2 的三个独立角色全部给出 BLOCK**（cold_start 4 条、satellite_drl 1 条、adversarial 4 条，
共 9 条，**全部成立**）。因此**没有** `authorization.json`，也**没有**正式运行。

这不是"没跑通"，而是**门禁按设计生效**：任务书明确禁止"为了跑通实验而修改测试标准 / 降低 fail-loud 条件"，
所以本审计**不接受**用削弱门禁的方式换取一份授权。9 条 findings 及其修复（revision 3 已全部落实）如下：

| 来源 | finding | 修复 |
| --- | --- | --- |
| cold_start F1（承重） | **被审的编译产物不是被审代码的产物**：R02 在 17:21 编译，而 `CODE/leo_sim/metrics_independent.py` 在 17:24 被改，manifest 绑定的是**改前**的 `code_sha256`（`ebd752af…` vs 现行 `ffcad9fc…`） → `matrix.verify_compiled_matrix` 抛 `matrix manifest cells do not derive from request`，**授权不可达** | 冻结 `CODE/leo_sim` 后**重新编译为 R03**，并核对 `manifest.code_sha256 == receipt.code_sha256()`；已实测 **R03 PASS**，R01/R02 仍正确地 FAIL |
| cold_start F2 | PROCEDURE §4b 按字面**不可执行**：`--timeline-log` 的父目录必须先存在且不得位于符号链接路径下（`/tmp` 在 macOS 是符号链接） | 补 `mkdir -p` 与符号链接说明 |
| cold_start F3 / adversarial B4 | brief 把 **R01** 的测量值（0.850000245 s）当成**本设计**的数字 | 全面区分 R01/R02 数字；R02 实测 0.849200279 s |
| cold_start F4 | "唯一的非测试调用者是 step5_recompute.py" **不成立** —— 本工作包自己的 `attribute_f2.py` 也 import 它 | 更正为两个调用者 |
| satellite_drl BF1 / adversarial B1 | **被证伪的 e2e 可加性声明仍留在 `can_claim` 里**，且 `request.json`/`analysis-request.json` 是**哈希绑定**的，`v2_analysis.py:955` 还会把它复制进 canonical 分析 manifest —— 假声明落在**授权路径**上 | 把该项**移出** `can_claim`、写入 `cannot_claim` 并附实测数字，然后**重新编译** |
| adversarial B2 | 我写的 `attribute_f2.py` **可被击败**：截断时间线 / 空时间线 / 空账本都会 `ok=true`；`--compute-delay-s` 未与配置绑定 | 加固：延迟从**每臂自己的** `resolved_config.json` 读取（flag 只用于交叉校验）、校验 timeline 侧车（`row_count`/`log_sha256`）与 receipt 绑定、要求 f2 臂占用数为正且 control 为 0、拒绝空投递集，并新增 `--self-test` 覆盖**七个**拒绝路径（已实测全部触发） |
| adversarial B3 | 报告 X5 仍写 `v2_analysis` "误报全部 ISL 饱和"，与本报告 B5 的"硬中止"自相矛盾 | X5/X20 已更正；并记录该结论系**实跑**所得 |

### 11.7 ★ 第一手证实阻塞项 B2（本批次被它直接拦下）

**[EXEC]** 在冻结代码后对三个矩阵逐一重算身份：

```
EXP-20260924-PLATFORM-AUDIT-F2-R01: verify_compiled_matrix FAIL -> matrix manifest cells do not derive from request
EXP-20260924-PLATFORM-AUDIT-F2-R02: verify_compiled_matrix FAIL -> matrix manifest cells do not derive from request
EXP-20260924-PLATFORM-AUDIT-F2-R03: verify_compiled_matrix PASS  <-- authorization precondition reachable
```

即：**任何对 `CODE/leo_sim/*.py` 的改动都会作废该 checkout 上全部已编译矩阵与已发授权**，
而 `receipt.code_sha256()` 覆盖 `CODE/leo_sim/*.py`（`receipt.py:124-131`）。
这把 §3 的 **B2 从"读码推断"升级为"本审计亲自踩中"**：

> **唯一可行的顺序是：冻结 `CODE/leo_sim` → 编译 → 审阅 → 授权 → 运行。**
> 审阅后重新编译会使三份回执全部失效，因此"改了再补审"与"审了再改"在哈希绑定下互斥。
> 该结论已写入 R03 brief 的 context（`FREEZE-COMPILE-REVIEW ORDERING`）。


### 11.8 授权之后的全链机制已**预演通过**（只剩授权这一步）

**[EXEC]** 在 R03 上用与正式运行**完全相同**的配置跑通了两臂，并按 `attribute_f2.py` 要求的产物
布局（`<results-root>/<run-id>/`，即 `CODE/Results/<run-id>/` 的形状）验证了后续每一步：

```
control exit=0   files: ledgers.json manifest.json receipt.json resolved_config.json
                        timeline.jsonl timeline.jsonl.manifest.json trace.csv
f2      exit=0   files: (同上)
      两臂均 natural_end=True, conservation_ok=True, DELIVERED=6

attribute_f2.py -> status "ok", failed_checks []
  control: node_process_s 0.0                labelled_dc 0.8999999999999973
  f2     : node_process_s 0.8999999999999979 labelled_dc 0.8999999999999979
           unlabelled_dc  1.7999999999999958  (差值 = 节点总量)
  cross_arm_differing_links 1 (报告不裁决)
```

**即：授权一旦签发，编译 → 运行 → 回执校验 → 独立归因 → 指标 的每一步都已被证明可执行**，
不存在"拿到授权后才发现某步跑不通"的隐藏风险。剩余的阻塞**只有**独立审阅门本身。

> 附带记录：该预演中 `attribute_f2.py` 第一次**正确地拒绝**了一份布局错误的输入
> （把 `EXPERIMENTS/` 前缀也带进了 results-root），报 `missing ledgers.json` 而不是静默通过 ——
> 这正是 rev2 审阅要求加固后应当出现的行为。
### 11.9 本阶段结论（三轮回合后的诚实状态）

| 验收项 | 状态 |
| --- | --- |
| compile artifact | ✅ R01 / R02 / R03 三份；R03 绑定冻结代码身份并通过 `verify_compiled_matrix` |
| authorization | ❌ **未产生** —— **三轮**独立审阅共 9+9+9 条 findings，全部成立；且**拒绝以削弱门禁换取授权** |
| receipt | ✅ 本地执行产出 `receipt.json` + `ledgers.json`；**`formal_run.json` 未产生**（它要求通过 `verify_authorization_for_leo_sim_v2_config` 的正式授权，而授权不存在）。**未**声明为 VM 正式执行 |
| metrics | ✅ `ledgers.json` + 独立 F2 归因报告（标注/未标注/泄漏恒等式/跨臂对照），`ok=true`、`failed_checks=[]` |
| 分析腿 | ❌ 不可达（B10）：本地结果目录缺只由 `remote_job.py` 写入的 `governance_receipt.json` |

**任务书验收标准中"至少一个最小实验完整产生 authorization"一项未能达成。**
原因是独立的审阅门三轮均判定 BLOCK，而任务书同时禁止为跑通实验而降低门禁。
本审计选择如实记录 BLOCK，而不是削弱门禁。

### 11.10 第三轮审阅（rev 3）的 9 条 findings 与修复

| 来源 | finding | 修复 |
| --- | --- | --- |
| 三角色一致 | **F3 未真正修复**：哈希绑定的 `R03/PROCEDURE.md`、`R03/brief.json` 与 `CURRENT-EVENT-TIMELINE.md` 仍把 **R01 设计**的 0.850000245 s 说成"本装置"的测量 | 三处全部改为本设计的 **0.849200279 s**（并明确 R01/R02 两个数字不可互换） |
| cold_start | "唯一非测试调用者"说法仍不成立（`R02/attribute_f2.py` 是第三个）；brief 把 §5b 指向 R02 而 PROCEDURE 指向 R03 | 更正为三个调用者；brief 指向 `R03/attribute_f2.py` |
| **三角色一致** | **契约违例（生产者自己造成）**：修 R03 的提交 `cbff06f` **改写了已被 R2 回执哈希绑定的 R02 工件**（`PROCEDURE.md` / `attribute_f2.py` / `brief.json` / 请求源），却未重签 `R02/artifact-set.json` → 该文件声明的哈希与磁盘不符，**九条 R2 findings 无法再被复核** | **已把四个文件还原为被审字节**（`git checkout a069309`），`R02/artifact-set.json` 重新 11/11 通过；唯一无法还原的是 `ANALYSIS/CURRENT-EVENT-TIMELINE.md`（**R2 的对抗审阅自己要求**更正它），已在 `R03/PROCEDURE.md` 显式声明 |
| adversarial | **加固后的工具仍被击败三次**：(a) 删掉一整对 `node_process` 并重签侧车 → `ok=true`；(b) 从 ledger 与 timeline 一致地删掉一个包 → `ok=true`，而 `receipt.json` 仍写 `DELIVERED=6`；(c) 在真实决策计算空隙里注入伪造 span → `ok=true` | **新增三个独立锚点**：饱和度起点必须与 `packet_events` 推出的卫星访问时刻逐一吻合（= `satellite_ingress` + 每个 `stage=="isl"` 跳的到达时刻）；`receipt.ledgers_sha256` 必须等于实际读到 ledger 的哈希；`receipt.fate_counts.DELIVERED` 必须等于 ledger 中 `delivered` 事件数。三类攻击现全部被拒；`--self-test` 从 7 条扩到 **10 条**拒绝控制，全部触发 |
| adversarial | 报告 §11.9 声称本地运行产出 `formal_run.json` —— **不存在任何 `formal_run.json`**，也没有授权 | 本节已更正（见上表 receipt 行） |
| adversarial | 报告 B4 的新 `[EXEC]` 计数错误：实测 **3** 个被接受（`delivery_rate` / `access_admission_rate` / `network_delivery_rate_by_horizon`）、**20** 个 unsupported，且 catalog 标记 **10** 个 `eligible_as_primary` | B4 已按实测更正 |
| adversarial | `--pairing-key` 的 help 声称"从请求读取"，实际从不读请求 | 改为**真的校验**：从 `run-manifest.json` 读取已知 run id，key 选出的 id 不在其中即拒绝（help 文案同步更正） |
| cold_start | `brief.json:19` 仍以过去时声称 R03 审阅已执行 | 改为"为 revision 3 执行" |
| satellite_drl | 未披露"本装置上 labelled `decision_compute_s` 与 f2 节点总量**数值相等**"（都是 18×0.05 s），因此分离靠跨臂不变性而非总量不等 | 已在 brief 与本节 §11.2 显式披露 |

**R2/R3 的六份回执已入库**（`CODE/work/WP-LEO-V2-PLATFORM-AUDIT-F2/{R02,R03}/review-*.json`），
因此 §11.5 中"R02 回执不在库内"的悬空指针已消除，九条 R2 findings 也可按文件复核。

