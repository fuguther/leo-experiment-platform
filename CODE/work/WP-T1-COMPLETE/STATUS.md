# WP-T1-COMPLETE — 状态账本

> 唯一工作账本。计划书：`docs/superpowers/plans/2026-09-27-t1-complete-implementation.md`
> 最终交付：`REPORT.md`（含"返工 R1–R9"节）；判据：`criteria.json`；语义合同：`contract.yaml`。

## 0. 身份

| 项 | 值 |
|---|---|
| 仓库根 | `/Users/lge/Desktop/topic/leo-exp-main` |
| 分支 | `t1/frozen-branch-and-async-design` |
| 起始 HEAD | `0876b12dbb0e067a840743dcc57b804d4600e9b0` |
| 首轮交付 | `5bce0e4` / `6e9e885` / `dc2373a` / `53aeb30`（复审身份） |
| 返工提交 | `650cdff`（R1/R7/R8/R9）、`219f432`（R2/R3）、R4/R5/R6 见 git log 末尾 |
| 旧工作树 | `/Users/lge/Desktop/topic/leo-experiment-platform` 只读引用，未触碰 |

## 1. 阶段状态（含复审纠正）

| 阶段 | 状态 | 说明 |
|---|---|---|
| P0 基线/账本 | DONE | |
| P1 单位/身份/合同 | DONE | 身份链已在 R1 中改为整包发现 |
| P2 时间事件/有限算力/零成本冻结 | DONE | |
| P3 允许信息/预测/评分 | DONE（R8 修正 ETA 分项） | ETA 现含本地出口排队与请求时刻可知的计算等待 |
| P4 四组理想诊断 | **返工后 DONE** | R2 补齐 oracle_now/common/candidate 真值三组与 2×2 分解 |
| P5 四组可部署执行 | DONE | |
| P6 包粒度/基准/压力 | **返工后 DONE** | R4 分段计时 + 调用计数；R3 包长/请求率单元 |
| P7 异步更新器 | DONE | R3 增加分段窗口与版本分布证据 |
| P8 五执行模式/DDQN | **返工后 DONE（R6 真实检查点仍外部阻塞）** | R3 触发缓存命中与有限 N>0；R6 固定推理适配器 + 哈希校验 |
| P9 指标/统计/停止 | **返工后 DONE** | R5 冻结 D、观察窗口删失，统计接入真实 run 汇总 |
| P10 t1_suite/恢复 | **返工后 DONE** | R1 真校验；R3 行为谓词/dev 扫描/formal 待执行包 |
| P11 证据链/回归/待执行包 | DONE（正式/远端为外部条件） | 全平台回归 1241 passed, 1 skipped |
| P12 最终交付 | DONE | REPORT.md 含返工节与逐条证据 |

## 2. 返工后实测（原文末尾）

```
python3 -m pytest CODE/leo_sim/tests CODE/experiment_platform/tests CODE/tests ANALYSIS/tests -q
1241 passed, 1 skipped, 1 warning in 436.14s

t1_suite compile  -> 20 cells（acceptance 10 / dev 10 / formal 待执行）
t1_suite validate -> valid true
t1_suite run --tier acceptance -> {"ok": 10, "error": 0, "timeout": 0}
t1_suite run --tier dev        -> {"ok": 10, "error": 0, "timeout": 0}
t1_suite run --tier formal     -> SUITE REFUSED（PENDING PACKAGE）
report（acceptance/dev）        -> run_status ok；statistics blocks/bootstrap/样本量/退化警告
```

R1 反问例复核：结构保留改参数 → 拒绝；删除 result → report FAILED_CELLS + resume 重跑恢复；篡改 result 内容 → FAILED_CELLS。

## 3. 未做范围（非工程缺口）

1. FORMAL_RUN：确认性矩阵未执行（formal 包仅编译+校验，运行需授权）。
2. 真实 DDQN 检查点：本机无 tensorflow/检查点；适配器与小模型接口已验证，不作策略性能结论。
3. REMOTE_NOT_EXECUTED：未做远端覆盖部署。
4. 本次夹具的开发配对差恒为 0 → 样本量规划标 degenerate，不能用于估计确认样本量。
