# 研究线：状态时间错位

**状态：ACTIVE** · 起始：2026-09-23
能力与证据标准：[T1-MEASUREMENT-PROTOCOL.md](../ANALYSIS/T1-MEASUREMENT-PROTOCOL.md)、[CURRENT-EVENT-TIMELINE.md](../ANALYSIS/CURRENT-EVENT-TIMELINE.md)。工作区现态只从 `STATUS.md` 和 Issue #3 读取。

## 问题

分布式逐跳路由决策面对状态传播、计算、生效和数据到达之间的时间错位时，状态应对应哪个时刻；不同时间语义是否会改变下一跳选择，并在计入计算、查询和控制资源后保留 cohort 收益。

## 当前已实现

- `refresh` 与 `frozen` 观测；冻结模式在计算前保存观测，提交时重新校验合法性；
- 每决策唯一 `decision_id`、可回放 timeline，以及 eleven-milestone 折叠入口；
- 单决策强制动作与严格配对反事实回放；
- 逐包、按流缓存、预计算、异步点查询和异步窗口等执行模式；
- 共享计算池、查询服务、版本安装和 fallback 审计；
- GAT/MPNN 图编码、DDQN 与冻结推理接口。

这些是平台能力，不是算法收益结论。

## 当前证据边界

- 尚无经正式实验证明的时间语义或预测收益；
- 训练检查点性能、跨场景泛化能力和上线价值仍未验证；
- computation delay、query delay、模拟世界时间与程序实际运行墙钟必须分别报告；
- 单场景、单元测试或 CI 通过不能替代固定 cohort 的 E2E、p99、PDR、deadline loss 和资源净成本。

## 下一动作

先把时间、队列、计算和指标语义的明确缺陷修复并建立边界回归，再在 VM 上用固定 trace、config、seed 和 release 做最小机制诊断。任何 formal run 仍需独立授权。
