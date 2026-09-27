# WP-T1-COMPLETE 最终交付报告

> 计划书：`docs/superpowers/plans/2026-09-27-t1-complete-implementation.md`
> 账本：`CODE/work/WP-T1-COMPLETE/STATUS.md`、`criteria.json`、`contract.yaml`
> 结论分层：IMPLEMENTED / TESTED / DIAGNOSTIC_RUN / FORMAL_RUN。**本报告不含任何 FORMAL_RUN。**

## 1. 用户问题

在同样可收到的本地与邻居历史信息下，把候选资源的预计使用时刻对齐，能否改善选择；
计入计算与生效方式成本后，改善能否保留？

两个层次：
1. **状态时间语义的价值**：先用隔离的理想信息估计价值空间，再用可在线实现的预测检验能实现多少；
2. **计算与生效方式**：固定信息权限、预测器、评分器与动作空间，比较逐包计算与后台更新/结果复用。

## 2. 实现（IMPLEMENTED）

| 计划书文件 | 状态 | 说明 |
|---|---|---|
| `CODE/leo_sim/time_alignment.py` | 新增 | 不可变快照、ResourceKey/StateSample/ResourcePrediction/TruthSample、hold_last/bounded_linear、ETA 分项、统一评分、schedule 表与只查表 lookup。纯函数，不读内核真值 |
| `CODE/leo_sim/async_routing.py` | 新增 | scope 状态机 UNINITIALIZED→COMPUTING→INSTALL_PENDING→ACTIVE、单 pending 合并、完成才安装、版本单调、窗口从实际安装起算 |
| `CODE/leo_sim/kernel.py` | 扩展 | 每星 FIFO 计算池 + `compute_request/start/finish`（稳定 `compute_job_id`）、零成本 frozen、观察记录新增 `candidate_resources` 与 `time_alignment` 审计、五执行模式在线接线、周期异步更新器 |
| `CODE/leo_sim/control.py` | 扩展 | LocalCache 保留有界「实际到达」广告历史（仅输出用，供 bounded_linear） |
| `CODE/leo_sim/config.py` | 扩展 | `time_alignment` / `async_routing` 命名空间、白名单、默认值、矛盾即拒绝；接受 JSON 指数浮点（编译配置 JSON 写 YAML 读回） |
| `CODE/experiment_platform/time_alignment_compare.py` | 新增 | 每个合法候选独立闭环重放、四组离线评分、每候选命运/损失/regret、resource_mismatch、只读 oracle 轨迹、oracle 仅评估器 |
| `CODE/experiment_platform/execution_compare.py` | 新增 | 五执行模式同一 trace/seed/臂/预测器/计算预算、调用与查询成本分开、DDQN 可用性/阻塞 |
| `CODE/experiment_platform/benchmark_decision.py` | 新增 | 完整决策路径计时 + 空调用基线 + 有限池 N=0/1/2/4 压力 |
| `CODE/experiment_platform/t1_suite.py` | 新增 | compile/validate/run/resume/report、预算、身份校验、旧证据不覆盖 |
| `CODE/experiment_platform/t1_stats.py` | 新增 | 归一化损失、配对差、固定种子 bootstrap、样本量规划、精度核查、主对比 |
| `CODE/experiment_platform/artifact_identity.py` | 新增 | Git commit/dirty/diff + 源文件哈希链 + runtime |

## 3. 验证（TESTED）

实际命令与输出（均为本机实跑原文末尾）：

```
python3 -m pytest CODE/leo_sim/tests/test_time_alignment.py -q                 -> 28 passed
python3 -m pytest CODE/leo_sim/tests/test_async_routing.py -q                 -> 16 passed
python3 -m pytest CODE/leo_sim/tests/test_compute_pool_fifo.py -q             -> 7 passed
python3 -m pytest CODE/leo_sim/tests/test_time_alignment_online.py -q         -> 8 passed
python3 -m pytest CODE/leo_sim/tests/test_async_kernel.py -q                  -> 7 passed
python3 -m pytest CODE/experiment_platform/tests/test_time_alignment_compare.py -q -> 15 passed
python3 -m pytest CODE/experiment_platform/tests/test_execution_compare.py -q -> 10 passed
python3 -m pytest CODE/experiment_platform/tests/test_benchmark_decision.py -q -> 8 passed
python3 -m pytest CODE/experiment_platform/tests/test_t1_stats.py -q          -> 32 passed
python3 -m pytest CODE/experiment_platform/tests/test_t1_suite.py -q          -> 11 passed
python3 -m pytest CODE/experiment_platform/tests/test_control_reach_units.py -q -> 3 passed
```

端到端流水线实跑（acceptance 层）：
```
t1_suite compile  -> 7 cells, contract_sha256 b5cdeb41...
t1_suite validate -> valid true, 7 cells
t1_suite run --tier acceptance -> {"ok": 7, "error": 0, "timeout": 0}
t1_suite report   -> run_status ok, 7/7 cells, REPORT.md 生成
resume 实跑：只重试失败 cell；bundle 身份变更后旧输出标 invalidated 并重跑；旧 result 被改名保留
validate 篡改拒绝：删除某 cell 的 driver 后 -> "bundle validation failed: ... missing driver"
```

全平台回归见 `STATUS.md` 第 2 节（含一次真实回归失败的根因与修复）。

## 4. 有界诊断结果（DIAGNOSTIC_RUN）

可从 `out/t1/` 复现，命令见第 7 节。**均为单分支/单 trace 诊断，不构成统计结论。**

| 诊断 | 结果 | 解释 |
|---|---|---|
| 等长空队列（reachability，decision 3） | 两候选损失各 0.5，regret 全 0 | 机制在无差别场景退化，符合预期 |
| 确定性竞争（contention，decision 4） | baseline 选 E 损失 0.5105；W 损失 0.3013；oracle 选 W | 每候选闭环重放产生真实代价差 0.209 |
| 四组在线评分（同一 contention 分支） | 四组均选 W，regret 0 | **负结果**：本分支中状态时间对齐没有增量价值，因为简单评分器已从广告中读到目标出口积压 |
| 四组改变排序（合成夹具 `test_the_query_instant_is_the_only_arm_difference`） | stale 选 W；now/common/candidate 选 E | 机制本身可判别，只是当前场景不激活 |
| 恒状态退化 | 四组动作一致 | 恒队列时预测外推退化一致 |
| 计算池手算夹具 | 1 服务台 starts [0,0.1] / finishes [0.1,0.2]；2 服务台 finishes [0.1,0.1] | 与计划书手算一致 |
| 异步跨版本时间例 | 请求 1 s、服务 2 s、安装 0.5 s → 1.5/3.2 s 查 v1，3.5 s 安装后查 v2 | 与计划书时间例一致 |
| 决策路径计时 | full p50 ≈ 34 µs、p99 ≈ 50 µs；空调用基线 ≈ 0.018 µs | 主机/VM 实测，非星载 |
| 五执行模式复用 | per_flow 命中缓存后计算请求低于 per_packet；async 模式查询表、按窗口安装 | 机制计数与事件对齐 |

## 5. 能支持的判断

- 平台现在能从一个明确命令复现：四组状态时间比较、每候选闭环代价、有限算力压力、异步复用诊断，输出完整命运/成本/证据。
- 时间语义、信息权限、候选资源映射、统一评分、异步安装时序、五执行模式公平性均有可判定测试；关闭新功能时旧路径逐位不变（回归实证）。
- 主对比的统计设计（独立区组、配对、bootstrap、样本量规划、预声明阈值 0.01/0.005/0.02）已冻结在 `contract.yaml`，可在确认运行中直接使用。

## 6. 不能支持的判断

- **没有 FORMAL_RUN**：确认性种子 1001+ 的大规模矩阵未执行；本报告任何数字都不能当作确认性结果。
- **没有 DDQN 结果**：本机无有效固定推理检查点，`ddqn_status` 返回 `EXTERNAL_BLOCKER`（精确原因与恢复方法见工件）；不把确定性评分器结果当 DDQN 结果。
- **没有星载实测**：计时全部是主机/VM；配置服务时长是诊断情景输入，不是标定值。
- **没有远端覆盖部署**：标 `REMOTE_NOT_EXECUTED`；未触碰用户正在使用的 VM。
- 不声称状态时间对齐有收益或没有收益——当前单分支诊断在 contention 上显示零增量，但这是「场景未激活机制」，不是算法结论。

## 7. 复现 / 恢复命令

```sh
cd /Users/lge/Desktop/topic/leo-exp-main

# 四组离线比较（单分支）
python3 -m CODE.experiment_platform.time_alignment_compare \
  --scenario contention --decision-id 4 --out out/t1/ta-contention.json
python3 -m CODE.experiment_platform.time_alignment_compare \
  --config CODE/leo_sim/profiles/t1_frozen_branch_smoke.yaml \
  --decision-id first_forward --out out/t1/ta-smoke.json

# 五执行模式
python3 -m CODE.experiment_platform.execution_compare \
  --scenario contention --out out/t1/exec-contention.json

# 决策计时与有限池
python3 -m CODE.experiment_platform.benchmark_decision \
  --scenario reachability --iterations 200 --rounds 2 --warmup 20 \
  --pool-sweep 0,1,2,4 --out out/t1/bench-reach.json

# 端到端流水线（新身份；目标目录必须不存在）
mkdir -p out/t1/suite
python3 -m CODE.experiment_platform.t1_suite compile \
  --contract CODE/work/WP-T1-COMPLETE/contract.yaml --out out/t1/suite/compiled
python3 -m CODE.experiment_platform.t1_suite validate --bundle out/t1/suite/compiled
python3 -m CODE.experiment_platform.t1_suite run \
  --bundle out/t1/suite/compiled --tier acceptance --out out/t1/suite/acceptance
python3 -m CODE.experiment_platform.t1_suite resume --run-dir out/t1/suite/acceptance
python3 -m CODE.experiment_platform.t1_suite report --run-dir out/t1/suite/acceptance
```

## 8. 证据链

- 源码与测试：本仓库提交（见 `STATUS.md` 身份表；`git log`）。
- 运行工件：`out/t1/`（gitignored，未提交；由上述命令可逐条重建）。
- 身份：每个新工件内嵌 `identity`（Git commit/dirty/diff_sha256 + 逐文件哈希 + runtime）。
- 旧证据：`out/` 既有文件未改写；新运行拒绝覆盖旧目录。
