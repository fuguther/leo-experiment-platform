# WP-T1-COMPLETE — 状态账本

> 唯一工作账本。每阶段完成即更新本文件。一个任务一个状态。
> 计划书：`docs/superpowers/plans/2026-09-27-t1-complete-implementation.md`

## 0. 身份

| 项 | 值 |
|---|---|
| 仓库根 | `/Users/lge/Desktop/topic/leo-exp-main` |
| 分支 | `t1/frozen-branch-and-async-design` |
| 起始 HEAD | `0876b12dbb0e067a840743dcc57b804d4600e9b0`（与计划书 §0.3 一致，未推进） |
| 旧工作树 | `/Users/lge/Desktop/topic/leo-experiment-platform` 只读引用，未触碰 |
| Python | 3.14.2；simpy 4.1.1；numpy 2.3.5；pyyaml 6.0.3 |

基线测试（P0）：三文件 46 passed（与历史参考一致）。

## 1. 阶段状态

| 阶段 | 状态 | 证据 |
|---|---|---|
| P0 基线/账本 | DONE | 本文件、`criteria.json`、46 passed 基线 |
| P1 单位/身份/合同 | DONE | `test_control_reach_units.py` 3 passed；`artifact_identity.py`；4 份文档最小编辑；`contract.yaml` |
| P2 时间事件/有限算力/零成本冻结 | DONE | `test_compute_pool_fifo.py` 7 passed；`test_compute_delay.py`+`test_frozen_observation.py` 47 passed |
| P3 允许信息/预测/评分 | DONE | `test_time_alignment.py` 28 passed；`time_alignment.py`；config 命名空间 |
| P4 四组理想诊断/分支代价 | DONE | `test_time_alignment_compare.py` 15 passed；`time_alignment_compare.py` |
| P5 四组可部署执行 | IN_PROGRESS | 内核在线接线未完成 |
| P6 包粒度/基准/压力 | PENDING | |
| P7 异步更新器 | IN_PROGRESS | `async_routing.py` 状态机 + `test_async_routing.py` 16 passed；内核在线接线未完成 |
| P8 五执行模式/DDQN | PENDING | |
| P9 指标/统计/停止 | IN_PROGRESS | `t1_stats.py` 32 passed；指标汇总/停止规则未接 |
| P10 t1_suite/恢复 | PENDING | |
| P11 证据链/回归/待执行包 | PENDING | |
| P12 最终交付 | PENDING | |

## 2. 已通过的实测命令

```
python3 -m pytest CODE/leo_sim/tests/test_compute_delay.py CODE/leo_sim/tests/test_frozen_observation.py CODE/experiment_platform/tests/test_minimal_branch_compare.py -q
46 passed                                  # P0 基线

python3 -m pytest CODE/leo_sim/tests/test_time_alignment.py -q
28 passed                                  # P3 + P3.1 config

python3 -m pytest CODE/leo_sim/tests/test_async_routing.py -q
16 passed                                  # P7 状态机

python3 -m pytest CODE/experiment_platform/tests/test_time_alignment_compare.py -q
15 passed                                  # P4

python3 -m pytest CODE/experiment_platform/tests/test_t1_stats.py -q
32 passed                                  # P9 统计

python3 -m pytest CODE/leo_sim/tests/test_compute_pool_fifo.py -q
7 passed                                   # P2 手算夹具

python3 -m pytest CODE/experiment_platform/tests/test_control_reach_units.py -q
3 passed                                   # P1 单位回归

# P11 全平台回归（第一次）：8 failed, 13 errors —— 根因：编译产出的配置以 JSON 写入
# 再以 YAML 读回时，1e-06 被 YAML 1.1 解析成字符串。已在 config._UniqueKeyLoader
# 增加 JSON 指数浮点解析（带证据：json.dumps({'query_delay_s':1e-06}) 现在读回 float）。
# 修复后：test_matrix_contract/test_governance/test_authorize_experiment
# test_formal_stream_gate/test_pullback_authority 共 58 passed。
```

## 3. 本阶段实测诊断（非结论、可复现）

- `--scenario reachability --decision-id 3`：两候选均为空队列、等代价 → 损失各 0.5、
  regret 全 0（"等长空队列零差异"）。
- `--scenario contention --decision-id 4`：baseline 选 E 损失 0.5105，W 损失 0.3013，
  oracle 选 W；四组均选 W，regret 0 → 本例中状态时间对齐无增量价值（诚实负结果）。
- 四组在"资源队列随时间增长"的合成夹具中确实改变排序（`test_the_query_instant_is_the_only_arm_difference`）。

## 4. 下一步

P5：把四组评分与异步查询接入内核在线转发路径（记录快照哈希/来源/查询时刻/候选排序），
并完成 P7 的"旧表转发中、完成才安装"内核级时间例。

## 5. 阻塞 / 待执行

- 正式确认性大规模运行、昂贵训练、远端覆盖部署：属计划书 §0.1 外部条件；标
  `FORMAL_NOT_EXECUTED` / `REMOTE_NOT_EXECUTED`，不阻塞其余阶段。
- DDQN 检查点：本机无有效训练检查点时按 K13 记录精确外部阻塞。
