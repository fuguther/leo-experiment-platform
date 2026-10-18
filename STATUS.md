# 本工作树状态：场景与负载校准 + 2026-10-19 夜间场景族

范围：仅 `worktrees/.t1-scene-calibration` / `codex/20261018-scene-calibration`；工作区级入口仍是 `topic/STATUS.md`。

## 2026-10-19 夜间轮（最新）

**第七轮补充**：
- 产出 5 张图（PNG+PDF）到 `review-20261017/figures/fig_scene_*`：ISL 利用率曲线、
  时延分解、注入-时延、跳数分布、队列面积构成。全部由 VM 上 `result.json.gz` 重算的 CSV 生成。
- **十类图逐类声明**：已出 2、3 完整，1、8 部分；4、5、6、7、9、10 未出并写明原因（缺闭环/预测器数据，
  或只有 2 个负载点）。**未填任何示意数据。**
- 关键机制数字（全窗）：trunk 低/中载 e2e 77–79 ms、holding ≈0；corridor 494.6→948.8 Mbps 时
  e2e **246→333 ms**，holding 面积 **29→79 Mbit·s**（ISL 数据的 18–22 倍）。
- 边界：图绑的是整段运行统计，**未按后填充窗重算**，F2/F3 绝对值偏大（含填充瞬态）。

**第六轮补充**：
- 新增两个**声明式**预测器并带测试（共 4 个）：`zero`（按名字返回 0，刻意放在 history gate 之前——
  "空队列"这个断言不需要历史；放在 gate 后会变成 no_received_history 而悄悄换了实验）、
  `analytic_drain`（按观测速率排空、且**永远不能预测增长**，无速率时回落 hold_last 并记录）。
- **corridor 稳态决定性结果**：ρ=0.95 的 stale 与 truth（ideal_current）**逐位相同**
  （capped 0.08326034302586038，holding/isl/uplink 面积全同）⇒ 真值状态在此场景没有改变任何决策。
- 机制：corridor 主导等待是 **holding（78.7e6 bit·s，ISL 数据的 18 倍）**，等的是"目的小区何时被服务"，
  不是 ISL 出口积压。与 §12.13（瞬态下 O 优于 S）合并得到：**陈旧状态的代价只在瞬态成立，稳态不成立**。
- 预算：真实内核启动 **17/60**，余 43。

**第五轮补充**：
- **S/O 首个配对结果**（同 trace、同 seed，只差 state_source）：advertised 0.038116 / 未按期 1.2573%
  → ideal_current **0.027094 / 0.0972%**（capped loss −28.9%，未按期 −92.3%）。
- **方法陷阱**：用 `hold_last` 时 S = P0 = F = A **逐位相同**（目标时刻不进计算）。
  四臂不能分离 F/A；要分离必须换 `bounded_linear`。此前若报"F/A 无增量"须按此限定。
- **corridor 场景成立**：承载链路 175 → **27**，后填充 ISL 排队 0.0038 ms → **3.04 ms**
  （≈253 包），满足事先判据。主导等待是 **holding（29.2e6 bit·s，ISL 数据的 22 倍）**。
- **自我更正**：`utilization` 分母是整个 12 s 运行；发射窗口径要 ×duration/emission。
  corridor 发射窗 **48.7%** ≈ 标称 0.50；先前"实测 1%"是分母用错，低估 34 倍。
- 预算：真实内核启动 **14/60**，余 46。

**第四轮补充（方法结论 + 两条负面结果）**：
- **后填充窗规则**：同负载两格限制到同一子窗后一致到 0.01 ms（ISL 排队）。
  先前的"0.35 s 比 0.70 s 高 2 倍"是**伪差**（填充瞬态摊薄差异）。规则：统计从 emission_start+0.20 s 起；
  跨格必须同窗；最小可用发射窗 0.35 s。**因此 0.25 s 的条件格只能当瞬态证据**（已如实记录）。
- **场景矩阵（S1/S3 低载、S1/S2 中载）**：后填充窗 ISL 排队 **0.001–0.042 ms**，
  e2e **66.0–66.7 ms 在低/中载几乎不变** ⇒ 系统处在**负载不敏感区**，
  与实测最高利用率 1–2% 一致。**当前场景不给 S/O/F/A 留余地**，不是方法失败。
- **负面结果**：`policy=delay` 39 min CPU、decisions 0 字节——边代价取自控制面缓存而 `vis_k=2`
  只覆盖 2 跳，缓存外一律 inf ⇒ 全判不可达。两格协作终止、日志保留、计入预算。**不再重试同一做法。**

**第三轮补充**：
- 窗口敏感性拿到配对两点（同负载 741–751 Mbps）：0.35 s ⇒ ISL 排队 2.82 ms / e2e 92.1 ms；
  0.70 s ⇒ 1.39 ms / 78.9 ms；两者 **ISL 队列面积几乎相同**（7.32e5 vs 7.31e5 bit·s）。
  ⇒ 窗长改变的是"启动填充瞬态被摊薄的程度"，**0.70 s 不能直接宣称稳态**，跨窗数字不可相减。
- 实测成本**超线性**：17.7k 包 0.124 s/包、21.6k 0.144、43.8k **0.210**。
  按此外推 4 臂 × 15.4k 包 ≈ 3.6 h/格 ⇒ 多 seed 矩阵不可行，必须先修。
- 拿到第一个信息条件点 O（`sc-cond-r050-truth-s111`，ideal_current）：D4 capped loss 0.027094、
  未按期 0.097%。**但同格 S 还在跑，故不做比较**。
- 预算：真实内核启动 **7/60**，余 53。

**第二轮补充（同一夜，继续推进）**：
- 已发射 S/O/F/A 条件对比（`scc-20261019-05`）：1 格 4 臂（stale/now/common/candidate，
  state_source=advertised ⇒ S、P0、F、A）+ 1 格真值臂（now + ideal_current ⇒ O），r050、0.25 s 窗。
  核实 `state_source` 合法值只有 `advertised | ideal_current`（不是我先前写的 kernel_truth）。
- **决定性离线核查**：S1 中载短窗格 175 114 次决策 × 4 方向 = 700 456 候选槽中，
  **仅 3.17% 有排队**，仅 **12.60% 的决策**至少一个候选非空；非空槽 p50=5.82 µs、max=10.54 µs，
  折合 1 Gbps 下 **727 B / 1 317 B**——一个 1500 B 包占 12 µs，所以非空槽也装不下一个整包。
  结论：本场景族里 S/O/F/A 的差别注定接近零，**可定价的状态量本身几乎不存在**，
  下一步方向应是让场景真的在共享瓶颈排队（或修参考/执行路由对齐），不是加预测器。
  详见 INDEPENDENT-REVIEW.md §12.7。
- 预算：真实内核启动 **7/60**，余 53。

- 用户点出的五项校准问题：逐星 CPU、逐网关、静态↔实际一致、窗口敏感性、控制占用两种口径 →
  前四项（第 4 项部分）+ 第 5 项已处置，证据见 INDEPENDENT-REVIEW.md §12.1。
  静态 spec 与运行 resolved_config **13 项逐字段全等**。
- 新增声明式 demand 程序（S1 平稳 / S2 OD 独立突发 / S3 相关热点）与诊断流开关（timeline 可关，
  单格产物 700 MB → 230 MB）。release 链：`2de8cdb` → `65a6f9f`（wave1 合同）→ `e81890c`（原子写 + 稳定种子）。
- 首轮实测 4 格完成（S1/S2/S3 低载 + S1 中载短窗），ISL 排队 0.29–2.82 ms，尾丢 0，
  低载三场景无差别（有效零效应）。
- **未完成**：S/O/F/A 四主条件、10 seed、预测器比较、十类图。原因是单格 0.124 s/包
  且 `Kernel.run()` 停止后归约 >1 h 未定位。9 格仍在跑（中/高载、窗口 1.20 s、缓冲 5/100 ms）。
- 预算：本轮真实内核启动 **5/60**（8 格 fail-closed 且 kernel_call_count=0 不计入）。
- 失败：tcsv 交错写 + `tuple.__hash__` 随机种子（已修，见 §12.5 与 PLAN-20261019.md）。
- 证据：批量启动器发的诊断批次**无根回执**（与 rtE/rtS 同），未做 verified pull；
  派生表在 VM 只读生成。

## 2026-10-18 校准轮（历史）

范围：仅 `worktrees/.t1-scene-calibration` / `codex/20261018-scene-calibration`；工作区级入口仍是 `topic/STATUS.md`，本文件只记本工作树任务。

**任务**：为现有 LEO 星间路由实验算出一套物理一致、成本可承受的流量配置，并做有界诊断。不训练预测器，不启动正式矩阵，不按算法胜负选场景。结论与证据写在 `.diag-ta-20261002/study-design-20261009/review-20261017/INDEPENDENT-REVIEW.md` §11（本轮唯一结论入口，未新建竞争文档）。

**交付物**：`CODE/experiment_platform/scene_load_calibration.py`（参考路径→b_e/c_e→R(ρ) 计算器，含手算自检、逐星/逐网关容量、OD 口径对比、替代路径）、`CODE/experiment_platform/analyze_scene_calibration.py`、profile `t1_scene_trunk_v1.yaml` 与其突发变体、合同 `scene_calibration_{stationary,burst}_20261018.json`、`CODE/work/WP-T1-SCENE-CAL/`。

**版本身份**：`dac3387`（v1 静态标定与 release）→ `8267451`（scope 对比/分析器）→ `8992128`（逐星容量纠正）→ `3cfdd8f`（计算面改为 1 µs 服务时间）。各版 release 见 review §11.1/§11.7。分支已推送 origin。

**已验收**：计算面纠正（决策排队 83.8%→2.6%，等待 p99 238 ms→0.67 µs；同格 12 s 仿真跑完）。

**未完成/未验证**：六档×三 seed 与平稳/突发矩阵**未运行**（用户指示暂停追加；只完成一个探针）；参考路由与执行路由不一致已量化但未纠正；接入与缓冲未做仿真敏感性；探针的 `summary.json` 在本轮结束时仍在结果归约阶段（>1 h），故**回传（verified pull）未完成**。

**预算**：真实内核启动 19/24，余 5。被平台 fail-closed、`kernel_call_count=0` 的 1 次不计入。批 A 的 17 个 run 目录保留未删、无结果、run-id 不复用。

**维护检查**：`python3 -B CODE/scripts/maintenance_check.py --repo .` → 0 error / 1 warning（外置备份与依赖锁未核验，属预期）。

---

## 分支基底历史（不代表本工作树当前结果）

# 本工作树状态：长业务与50ms邻居状态对照

范围：仅 .t1-fast-observation / codex/20261016-fast-observation，工作区入口仍是topic/STATUS.md。基于3a3bc0a创建，原负载校准工作树保持不变。

本轮适配有界诊断合同至300秒、最多100000包；新合同使用160秒、30000包上限，120秒发包、50ms邻居状态广播，6seed×4臂，bounded_linear不变。服务集合固定known_serving，属于受控队列信息实验，不是部署收益结论。40项mock测试通过，科学运行仅VM。执行前先烟测，再检查候选覆盖与资源成本。主指标是端到端时延和P95，交付率配套。

以下为分支基底历史记录，不代表本工作树当前新实验结果。

# 本工作树状态：负载校准与时间处理验证

范围：仅本 checkout，分支 `codex/20261013-load-calibration`；工作区级状态仍以工作区根 STATUS 为准。更新：2026-10-16。

## 2026-10-16 队列状态年龄实验（rtA，最新，本轮唯一任务）

- HEAD `00f756829ed62bff299990d310b097348b11fcaf`，工作树干净。改动：
  `CODE/leo_sim/config.py`（`min_observation_age_s` 字段与默认 None）、
  `CODE/leo_sim/kernel.py`（快照构建时按 `measured_at <= t0 - a` 扣留样本；
  `observation_age` 审计块用预测器自身的 `order_history`/`_latest_by_resource` 构造，
  并整体包在 try/except 里——诊断错误绝不改变决策）、
  `CODE/experiment_platform/load_calibration.py`（`time_alignment` 覆盖组新增该字段）、
  `CODE/leo_sim/tests/test_routing.py`（3 个新测试：扣留改变读入值、配置默认与解析、
  审计在无映射方向上不崩）。`test_routing.py` + `test_config.py` 58 passed。
- 合同：`CODE/work/WP-T1-COMPLETE/queue_age_main_20261016.json`（18 格，看结果前冻结：
  seeds 81–86 × a ∈ {0, 2, 6} s，服务信息固定 `known_serving`，预测器 `hold_last`，arm `stale`）。
- 结果：`a=2−a=0` = +3.50 pp [+0.58, +6.42]，`a=6−a=0` = +8.62 pp [+6.70, +10.55]，均 6/6 同向。
- **损失性质（按 rtA 独立审查更正）**：D4 未达标 7.15% → 10.66% → 15.79%，而**总体交付率几乎不变**，
  为 **96.36% / 96.21% / 96.05%**（未交付 3.64% / 3.79% / 3.95%）；新增未达标几乎全是"送达但超期"。
  上一版写的 92.85%/89.31%/84.21% 是 1−D4 的近似按期率，不是总体交付率。
- **a 是"源测量年龄下限门槛"，不是实际年龄**：a=0 的臂照样有采样/传播陈旧（匹配子集实际年龄中位约 2.09 s），
  不能称为零年龄真值，也不能写成"从 0 延迟增到 6 秒"。
- **机制核验分两层**：固定冻结历史的纯函数测试证明门槛改变读入值（9000 → 5000 bit，样本缺席而非被改写）；
  闭环配对（70.3% 读入值不同 / 86.8% 评分不同 / 64.4% 动作不同）是**已分岔轨迹**的描述，
  不是冻结同状态的机制归因。
- **裁决收窄**：限制较新队列观测的可用性明显增加超期，可归因于**队列观测可用性退化**
  （含旧值与缺观测两部分，未分离纯旧值贡献）；这支持研究状态更新与预测，
  但**不专门证明候选使用时刻对齐值得做**。
- **预算对账（独立审查指出旧账 127/134 错误）**：内核启动 **153/160**（余 7），
  全部尝试 **157/160**（余 3）；组成 rt1 30 + rt2 9 + rt3 30（失败关闭、目录已清理）+ rt4 30
  + rt7 34 + rt9 2 + rtA 18，另有 4 次入口拒绝未启内核。**对账前不再发射。**
- 两次失败根因已修复：rt8 是合同未进 release（校验拒绝、未启内核）；
  rt9 是 `resource_for(d)` 为 None 时审计调用 `.as_tuple()`（内核已启动，计入预算），
  已加"审计错误永不改变决策"的测试。

## 2026-10-16 第二批（rt7）：初始化证伪、可部署事件通告、队列年龄 pilot

- HEAD 本轮终态 `831562c6233572ea0c1d183f42236b8aeaca26a4`，工作树干净；本轮改动：
  `CODE/leo_sim/kernel.py`（advertiser 独立日程、`_serving_changed`/事件通告、计数器）、
  `CODE/leo_sim/config.py`（`event_advert_enabled` / `event_advert_min_interval_s`）、
  `CODE/experiment_platform/load_calibration.py`（允许 `time_alignment` 覆盖组）、
  `CODE/leo_sim/tests/test_routing.py`（断言周期栅格为 0/2/4、事件通告计数）。
- **周期栅格不再被初始报文平移**：`initial_sync_burst_s` 现在走独立日程，测试断言
  plain=[0,2,4] 且 burst 同时包含 0/2/4 与 0.5。上一版把它平移成 0.5/2.5/4.5。
- **事件触发通告**：`_serving_changed(sat)` 在关联变为 active 或链路 release 时调用，
  经 `event_advert_min_interval_s` 限频、窗内变更合并为下一条；计数器
  `serving_changes_seen` / `event_adverts_emitted` 已暴露。seed 71 实测 167 次变更 → 67 条报文。
- 合同：`CODE/work/WP-T1-COMPLETE/init_event_queueage_20261016.json`（33 格）。
- 烟测纪律：两次单格烟测分别被"未知顶层字段"和"未支持的覆盖组"以 fail-closed 拦下，
  均在发射批次之前，零无效内核调用；对应校验缺口已补。
- 结果与边界见工作区 `汇报材料/状态时间错位_20261016早间汇报.md` §七。

## 2026-10-16 夜间实验的实现与证据（上一批）

- HEAD `aed0dfefee6d2d3dfd7bdf2685678c32b13fc336`，工作树干净。本轮改动全部在
  `CODE/leo_sim/{routing,config,kernel}.py` 与 `CODE/leo_sim/tests/test_routing.py`。
- **干预范围固定**：新增 `_serving_source()` 作为唯一在服集合入口，由**有效策略**切换；
  四个 `choose_next_hop` 调用点、`_candidate_resource_map`、`_estimate_at_start` 全部改用它。
  修正前 known 臂 3.07% 决策全候选 fallback，修正后 1.32%。
- **新增实验面**：`control_plane.initial_sync_burst_s`（等成本初始同步，真实控制报文并计费）、
  `routing.staged_policy` + `staged_policy_at_s`（共同前缀后切换策略）。
- **新增策略** `known_serving`（统一版）；`hop_known_serving` 保留为已发布混合版身份。
- 测试：`test_routing.py` 25 项（含"每个策略端到端跑一遍"的回归，用于防止调用点 NameError 再次漏网）、
  `test_config.py` 30 项全过。
- 合同：`CODE/work/WP-T1-COMPLETE/night_startup_vs_live_20261016.json`（30 格）。
  结果与边界见工作区 `汇报材料/状态时间错位_20261016早间汇报.md`。
- 失败记录：release `fe90f3cc…` 上 30 格因 `_candidate_resource_map` 内 `NameError` 全部
  FAILED_CLOSED（内核已启动，计入调用预算）；修复后以 release `aed0dfef…` 重跑 30 格全部完成。

## 已完成

- 四臂压力诊断与三个 seed 的广播周期扫描已完成；广播周期 0.5 / 2 / 4 秒下，D4 未达标的合并计数为 53 / 221 / 691，分母均为 4541。同 seed 复用 trace，跨 seed 不同。
- 多 server 不支持的时间对齐配置现在被显式拒绝，编译默认值为单 server。
- 理想状态测试快照补齐 `local_egress_in_service_s=0`，表达本地服务器空闲；评分器与原动作断言不变。本机复核：`test_ideal_state_time.py` 14 passed / 1 skipped；`test_frozen_ledger.py` + `test_s3_review_counterexamples.py` 22 passed；`test_bp_heuristic.py` 9 passed；`test_config.py` 30 passed；`experiment_platform/tests` + `tests` 合计 **782 项可无错收集**，此前的收集失败已修。
- 多 server 与时间对齐的矛盾组合由 `resolve_config` 的 fail-loud 守卫拒绝，已直接触发验证。`t1_suite compile` 本机复现：59 格，35 份配置全部 `enabled=true, servers=1`，0 处矛盾。
- `max_duration_s` 由硬校验 12.0 改为声明值、限定 [12, 60]（两端有测试）；该硬上限正是旧曲线跑不出预热窗口的结构性原因。
- `four_direction_audit` 补回 `DECISION_ROW_KEYS`（19 → 20）。此前每条 `CODE.leo_sim run` 都以 `DecisionRowError` 失败关闭，fold/replay 入口完全不可用。
- `CODE` 全套 1948 passed / 19 failed / 8 skipped。19 个失败全部既有：所在 6 个测试文件均不引用本轮改动符号，失败原因分别是 `benchmark_decision` 输出字段缺失、`t1_suite` 运行编排、旧版本回执重验。
- 维护检查验证 158 条索引、122 份本地回执，0 errors、37 warnings。警告涉及旧证据缺失和外部备份/依赖锁未核验。

## 研究状态

旧 12 秒曲线的"周期越慢损失越大"是**冷启动**，不是状态陈旧。逐决策核查：收到第一条广告前节点没有任何状态，数据包被 `hold`（`no_downstream_isl_resource_for_hold`），等首条广告到达后 `redecision`；首次 forward 决策在 0.655 / 2.175 / 4.166 秒，正对应 0.5 / 2 / 4 秒广告周期。无状态窗口里包不丢，只是被压住错过 4 秒期限（4 秒周期 [0,4)：781 发包 / 197 未达标 25.2% / 平均时延 1.638 s；[4,8)：739 / 10 / 1.4% / 0.420 s）。

预声明预热窗口（28 秒运行、业务 [0,24)、预热 [0,12)、评估 [12,24)、三 seed）：

| 窗口 | 0.5 s | 2 s | 4 s | 4 s ÷ 0.5 s |
|---|---:|---:|---:|---:|
| 预热 [0,12) 均值 | 0.959% | 3.504% | 10.710% | **11.17×**（三个 seed 全单调） |
| 评估 [12,24) 均值 | 1.507% | 1.140% | 1.506% | **1.00×**（排序不一致） |

四臂（4 秒周期、同一 seed、同一预声明窗口）：**6489 个同观测可比决策、18829 个方向对，0 次偏好反转**，四臂选择 0 次不同。四臂的 `resource_work_s` 确实互不相同（不是静默塌缩），但修正量中位 0、p90 6.5 ms、最大 110 ms，而名次间隔中位 35 ms。

机制是活的：25%（137658 / 549357）候选槽位 `resource_work_s` > 0；isl_data 队列面积 45.4 / 42.0 / 96.6 Mbit·s（0.5 / 2 / 4 秒周期）；评估窗口候选完整率 94–98%。差异小不是因为没有拥塞，而是队列项改变不了排序。

结论：R1 须改口径为"控制面恢复期损失"；R2 在当前场景为零。要谈候选使用时刻的价值，先要造出"陈旧会造成排序翻转"的场景，判据应是 `修正量 / 名次间隔` 的分位数，而不是继续扫广播周期。

这些诊断中有 `full_replay=false` 的输出，正式入口、回放和相关门禁需按拟用路径验收。

## 下一步

1. 把名次间隔压小、队列变化率拉大：找出使 `修正量 / 名次间隔` 分位数上升的负载与拓扑条件，判据跑前定死。
2. 三个 seed 的四臂对照（本轮四臂只有 seed 7）。
3. 离线真值上界臂，把损失写成绝对量。
4. 正式路径 `t1_suite` 按拟用路径整体验收；本轮全部是 diagnostic。

此前状态的原始字节保存在工作区私有审计归档，历史代码状态也可经 Git 查询。研究主文档与运行存储盘点由工作区入口导航，不复制进本公开代码仓库。
