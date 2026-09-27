# WP-T1-COMPLETE — 状态账本

> 唯一工作账本。计划书：`docs/superpowers/plans/2026-09-27-t1-complete-implementation.md`
> 最终交付：`REPORT.md`；判据：`criteria.json`；语义合同：`contract.yaml`。

## 0. 身份

| 项 | 值 |
|---|---|
| 仓库根 | `/Users/lge/Desktop/topic/leo-exp-main` |
| 分支 | `t1/frozen-branch-and-async-design` |
| 起始 HEAD | `0876b12dbb0e067a840743dcc57b804d4600e9b0`（与计划书 §0.3 一致） |
| 本轮提交 | `5bce0e4`（P1–P4/P7 核心）、`6e9e885`（P5/P7 内核）、`P6–P12`（见 git log 末尾） |
| 旧工作树 | `/Users/lge/Desktop/topic/leo-experiment-platform` 只读引用，未触碰 |
| 环境 | Python 3.14.2；simpy 4.1.1；numpy 2.3.5；pyyaml 6.0.3 |

基线（P0）：三文件 46 passed，与历史参考一致。

## 1. 阶段状态

| 阶段 | 状态 | 证据 |
|---|---|---|
| P0 基线/账本 | DONE | 本文件、`criteria.json`、46 passed |
| P1 单位/身份/合同 | DONE | `test_control_reach_units.py` 3 passed；`artifact_identity.py`；4 份文档最小编辑；`contract.yaml` |
| P2 时间事件/有限算力/零成本冻结 | DONE | `test_compute_pool_fifo.py` 7 passed；compute/frozen 47 passed |
| P3 允许信息/预测/评分/配置 | DONE | `test_time_alignment.py` 28 passed |
| P4 四组理想诊断/分支代价 | DONE | `test_time_alignment_compare.py` 15 passed |
| P5 四组可部署执行 | DONE | `test_time_alignment_online.py` 8 passed；观察记录含 `time_alignment` 审计 |
| P6 包粒度/基准/压力 | DONE | `test_benchmark_decision.py` 8 passed；N=0/1/2/4 扫描 |
| P7 异步更新器 | DONE | `test_async_routing.py` 16 + `test_async_kernel.py` 7 passed |
| P8 五执行模式/DDQN | DONE（K13 缺检查点，列外部阻塞） | `test_execution_compare.py` 10 passed |
| P9 指标/统计/停止 | DONE | `test_t1_stats.py` 32 passed；阈值预声明在 `contract.yaml` |
| P10 t1_suite/恢复 | DONE | `test_t1_suite.py` 11 passed；acceptance 7/7 ok |
| P11 证据链/回归/待执行包 | DONE（正式/远端为外部条件） | 全平台回归 1181 passed, 1 skipped；身份链内嵌 |
| P12 最终交付 | DONE | `REPORT.md`、`CAPABILITY-TABLE.md` §F、`criteria.json` |

## 2. 实测命令与结果（原文末尾）

```
# P0 基线
python3 -m pytest CODE/leo_sim/tests/test_compute_delay.py CODE/leo_sim/tests/test_frozen_observation.py CODE/experiment_platform/tests/test_minimal_branch_compare.py -q
46 passed

# P1
python3 -m pytest CODE/experiment_platform/tests/test_control_reach_units.py -q
3 passed

# P2
python3 -m pytest CODE/leo_sim/tests/test_compute_pool_fifo.py -q
7 passed

# P3（含 P3.1 配置）
python3 -m pytest CODE/leo_sim/tests/test_time_alignment.py -q
28 passed

# P4
python3 -m pytest CODE/experiment_platform/tests/test_time_alignment_compare.py -q
15 passed

# P5
python3 -m pytest CODE/leo_sim/tests/test_time_alignment_online.py -q
8 passed

# P6
python3 -m pytest CODE/experiment_platform/tests/test_benchmark_decision.py -q
8 passed

# P7
python3 -m pytest CODE/leo_sim/tests/test_async_routing.py -q
16 passed
python3 -m pytest CODE/leo_sim/tests/test_async_kernel.py -q
7 passed

# P8
python3 -m pytest CODE/experiment_platform/tests/test_execution_compare.py -q
10 passed

# P9
python3 -m pytest CODE/experiment_platform/tests/test_t1_stats.py -q
32 passed

# P10
python3 -m pytest CODE/experiment_platform/tests/test_t1_suite.py -q
11 passed

# P11 全平台回归
python3 -m pytest CODE/leo_sim/tests CODE/experiment_platform/tests CODE/tests ANALYSIS/tests -q
1181 passed, 1 skipped, 1 warning in 386.14s
```

第一次全平台回归（8 failed, 13 errors）的根因与修复：编译产出的配置以 JSON 写入再以 YAML 读回时，
`1e-06` 被 YAML 1.1 解析成字符串 → `config._UniqueKeyLoader` 增加 JSON 指数浮点解析。
修复后相关 58 tests 全绿，全平台回归 1181 passed。

## 3. 端到端流水线实跑

```
t1_suite compile  -> 7 cells, contract_sha256 b5cdeb41...
t1_suite validate -> valid true, 7 cells
t1_suite run --tier acceptance -> {"ok": 7, "error": 0, "timeout": 0}
t1_suite report   -> run_status ok, 7/7 cells
resume            -> 只重试失败 cell；bundle 身份变更 -> invalidated + 重跑；旧 result 改名保留
validate 篡改     -> 删除 cell.driver 后拒绝（fail loud）
```

## 4. 研究判断（诊断，非结论）

- reachability：等长空队列两候选损失 0.5、regret 0（机制退化）。
- contention：baseline E 损失 0.5105、W 损失 0.3013、oracle 选 W；四组在线评分均选 W、regret 0
  → **本分支中状态时间对齐无增量价值**（简单评分器已读到目标出口积压）。诚实负结果。
- 合成夹具中四组确实改变排序（`test_the_query_instant_is_the_only_arm_difference`），说明机制可判别、当前场景未激活。
- 无 DDQN 检查点 → `EXTERNAL_BLOCKER`；无正式/远端执行 → `FORMAL_NOT_EXECUTED` / `REMOTE_NOT_EXECUTED`。

## 5. 阻塞与待执行（不阻塞工程交付）

1. 正式确认性矩阵（种子 1001+、样本量按 `t1_stats.plan_sample_size` 冻结）未执行 —— 属外部授权/预算条件。
2. DDQN 固定推理检查点缺失 —— 需提供 checkpoint + sha256 + sibling metadata sha256 才能验证接口。
3. 远端覆盖部署未执行 —— 未触碰用户正在使用的 VM；命令与输入包已在 REPORT 第 7 节给出。
