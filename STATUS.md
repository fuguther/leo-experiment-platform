# leo-experiment-platform 当前状态

最后对账：2026-11-23。topic 级研究现态仍见私有 Issue #3；本文件只记录平台当前事实、已知正确性缺口和下一步。

## 当前边界

- 所有仿真、压力测试、决策计时和产生科研数值的运行在 VM 执行；本机测试只证明代码路径和本地行为。
- 正式可执行路线是 `leo_sim_v2`。`legacy_gateway` 仅是外部旧平台兼容合同，本仓库不提供可独立完成的正式运行。
- 实验实例、历史工作包和一次性分析器不进入活动树。追溯旧内容时使用产生它的 Git commit/blob、run-id、manifest、receipt 和原始证据。
- 长期契约与最小夹具位于 `EXPERIMENTS/contracts/`；运行实例不得写回该目录。
- Canonical 执行身份只来自干净 `main`、GitHub `refs/heads/main` 与 VM `CURRENT.json` 指向的同一精确 commit；development release 和来源未知的历史 release 不能冒充 current。
- VM 不需要直连 GitHub；Mac 在 promotion 前生成 live-main proof，VM 校验 proof 身份/新鲜度和 installed release 后写入 CURRENT。
- 旧 `push-remote.sh` 与新 canonical 发布器共用 clean-main/live-GitHub 门；普通 `run_release()` 默认要求 CURRENT，历史复现必须显式覆盖。
- 测试、CI、维护检查和文件存在都不能建立科研结论；正式结论还要求 exact source、运行回执和对应分析。

## Canonical 发布合同

- 易变 Git/release 身份不复制进本文件；当前精确 source commit、release、artifact、CURRENT pointer 与 proof 以 VM `/data/论文/leo-t1-wt/CURRENT.json` 和 Issue #3 的固定字段为准。
- canonical promotion 已完成一次端到端验证：三端 commit 一致，VM `verify-current` 通过，临时 live-main proof 已清理。
- VM 不要求能访问 GitHub；Mac 在 promotion 前生成自签名 live-main proof，VM 验证 proof 与 installed release 后写入 CURRENT。
- 普通 run 默认只能使用 CURRENT；历史 release 必须显式覆盖，development release 不能成为 current。

## 当前清理结果

- 活动树不再保留 `CODE/work/WP-*`、`EXPERIMENTS/EXP-*`、旧 scene 运行快照和已退役的一次性分析器。
- T1 主/开发契约、scene calibration、ISL 压力判定和测试直接引用的最小夹具已迁入长期契约区。
- 活动分析入口统一到 `outcome_metrics`、`time_alignment_compare`、`t1_stats` 等可复用实现；不再从目录名或历史报告推断口径。
- 历史 deadline 文件中的旧 source hash 保持原样，因为它们是冻结契约的证据身份，不表示对应旧脚本仍是活动代码。

## 活动研究边界

- TAP-DAR baseline suite 是独立活动工作流（engineering-governance Issue #10），不由本仓库的活动树清理结果推断其完成或失败。
- PCVR、TAP-DAR 训练实验、domain-map 中的 cohort/physics-residual 等候选属于算法或研究叶子；没有进入本平台主干，也不因分支存在而成为已选算法。
- 历史 research 分支只能通过固定 commit、tag、run-id 和原始证据追溯；不得把其预测器、场景或分析器恢复为默认入口。

## 已知时间语义缺口

以下问题在清理前已由代码和数据流审计指出，清理不改变其语义；在修复并有回归测试前，不应把相关边界结果当作可信科研数值。

1. **deadline 等号边界尚未统一。** outcome 汇总按 `delay <= D` 计入满足 deadline，当前内核的若干到期路径在 `now == D` 时已经写 `DATA_DEADLINE_EXPIRED`。同一时刻可能得到不同判定。
2. **测量窗可能超出实际停止时间。** `build_measurement_window` 的说明要求测量窗被自然停止时间封顶，但当前函数没有 `stop_time_s` 参数；调用层必须显式检查并记录截断。
3. **旧异步计算流的工作统计仍有兼容盲点。** 某些历史流只有 `compute_finish.started_at` 而没有独立 `compute_start`；部分统计能把等待/服务时间补出来，但 `start_events` 仍可能为 0，必须区分“事件缺失”和“开始时刻可由 finish 恢复”。
4. **拓扑 rematch 附近的排队归因仍需专项回归。** 现有分解器能拒绝重叠区间，但 rematch 前等待是否全部落入正确阶段，尚不能由现有一般性测试证明。

## 证据与验收

- 本机干净提交可做编译、单元/回归测试、维护检查、release 构造预检和已回传工件的只读复核。
- VM 的原始 manifest、receipt 和运行目录是运行真源；本机副本、GitHub 提交或绿 CI 都不能替代它们。
- `research_eligible=false` 的运行、嵌入旧 simulator 的 release、缺少远端 commit 的 source identity 和 `remote_backup_pending` 均不得产生科研 claim。
- 外部备份介质与依赖锁不由 `maintenance_check.py` 验证；其通过不表示平台已完成整体备份或依赖治理。

## 下一步

1. 为上述时间语义缺口逐项先写最小失败回归，再单独修复；不得在同一次改动中改变路由算法或历史证据。
2. 修复后从干净、已推送的 main 构造新 release；普通运行默认只能使用 CURRENT，历史复现必须显式覆盖。
3. VM 仍有一个来源未知且有历史 run 引用的 release；其精确 ID 记录在 Issue #3，保持只读、禁止作为 current，不能因不在 GitHub 就删除唯一证据。
4. 正式实验仍须编译、独立审阅、明确授权和 receipt 复核；当前 canonical release 不构成科研结论。
