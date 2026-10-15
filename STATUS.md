# 本工作树状态：负载校准与时间处理验证

范围：仅本 checkout，分支 `codex/20261013-load-calibration`；工作区级状态仍以工作区根 STATUS 为准。更新：2026-10-16。

## 2026-10-16 夜间实验的实现与证据（本工作树）

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
