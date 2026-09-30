# WP-T1-COMPLETE — 状态账本

> 计划书：`docs/superpowers/plans/2026-09-27-t1-complete-implementation.md`
> 交付：`REPORT.md`（含"第二轮复审返工 S1–S7"节）；判据：`criteria.json`；语义合同：`contract.yaml`。
> **执行位置硬规则（AGENTS.md）**：所有实验只在 VM 上跑；本机只做代码/测试/只读复核。

## 0. 身份

| 项 | 值 |
|---|---|
| 仓库根 | `独立研究工作树（路径由执行环境管理）` |
| 分支 | `t1/frozen-branch-and-async-design` |
| 起始 HEAD | `0876b12dbb0e067a840743dcc57b804d4600e9b0` |
| 复审轮次 | 53aeb30（R1–R9）→ 2330701（S1–S7）→ a124bba（第三轮：S1/S3/S5/S6/S8 返工）
  → b1f44af（第四轮：S1 B1/B2 回修）→ **06572b6 / f9f6c5c（第五轮：B1/B2 CONFIRMED_FIXED，S1_OPEN 关闭）** |
| 独立验收状态 | S1 已关闭；S3/S5 第三轮确认；**真实 DDQN 模型读取器未实现（内部缺口）**；**P9 PARTIAL（研究设计未就绪）** |
| 最新证据 | run-id `t1-final-06572b6`；HEAD `06572b6`；`identity.git.dirty=false`；执行链 `b7641418…` |
| 第三轮复审 | 四路只读验收回齐；S2/S7/S8(1)/证据链已确认；S1(A/D)、S4(d)、S5-R2、S6、S8(3a/i) 已返工；S1(B/C)、S3、S5 非 DDQN 标记仍未修（见 REPORT 末节） |
| VM | `ssh vm` = cuda-liguang13；隔离实验根 `T1 隔离工作根`；正式部署 `旧正式部署根` **从未写入** |
| VM runner | `CODE/scripts/remote/t1-vm.sh sync|run|pull|experiment` |
| 链一致性 | VM 链 `b7641418…` == 本机重算（57 个执行链文件；执行链已排除 ._ 元数据并远端剪枝） |

## 1. 阶段状态（含两轮复审纠正）

| 阶段 | 状态 | 说明 |
|---|---|---|
| P0–P3 | DONE | 基线/账本、单位与身份、时间事件与有限算力、快照/预测/评分 |
> 状态口径：**DONE = 代码已修 + 本机测试通过 + 在新身份上 VM 复跑通过，但第三轮独立复核尚未覆盖新身份**。
> 任一条目的历史身份：53aeb30（R1–R9）→ 2330701（S1–S7）→ a124bba（第三轮裁决）→ `b1f44af`（第三轮返工 + 新身份）。

| P4 四组理想诊断 | DONE（S1；第三轮返工：截断服务窗回退内核观测、未知不折数、控制包时间线） | 队列真值按内核事件语义重建 + 控制流量事件 |
| P5 四组可部署执行 | DONE | |
| P6 包粒度/基准/压力 | DONE（S5；第三轮返工：四臂对齐入门禁、模型来源标记） | 计时走真实在线入口；工件声明非 DDQN、非星载 |
| P7 异步更新器 | DONE（S3；第三轮返工：逐流缓存改请求时冻结） | 查表不付逐包计算；命中不因查询等待失效而变成未付费的完整评分 |
| P8 五执行模式/DDQN | DONE（S6 第三轮返工：推理边界改成"声明+探针"、策略统一委托 adapter.act、检查点 metadata 硬门槛） | 真实检查点：**模型读取器未实现**（内部缺口），不是仅缺 tensorflow/检查点 |
| P9 指标/统计/停止 | **PARTIAL（研究设计未就绪）** | 冻结 D、观察窗口删失已实现；但开发块配对差恒为 0 → `common_strong` 未冻结、**样本量无法估计**。这是**研究设计尚未就绪**，不等于"只差正式授权" |
| P10 t1_suite/恢复 | DONE（S4 第三轮返工：完整性失败也登记进 excluded） | 谓词失败即整轮失败；CLI 退出码 3 |
| P11 证据链/回归/待执行包 | DONE（S7 已确认修复） | acceptance/dev 在新身份跑通；formal 包可编译校验、拒绝未授权执行 |
| P12 最终交付 | DONE | REPORT 重写现状并标注历史；账本与 `still_open` 已对齐 |

## 2. 实测（本机=测试；实验=VM）

```
# 本机（允许：单元/回归测试）
python3 -m pytest CODE/leo_sim/tests CODE/experiment_platform/tests CODE/tests ANALYSIS/tests -q
1313 passed, 2 skipped, 1 warning in 483.15s
  （含三轮返工新增的反例文件：S1/S4/S5/S6 反例 + runner 清单反例）

# VM（实验唯一位置），隔离根 T1 隔离工作根
run-id: t1-final-06572b6   pulled -> out/vm/t1-final-06572b6/
t1_suite compile  -> 20 cells
t1_suite validate -> valid true（bundle_fingerprint 334dbbd2…）
t1_suite run --tier acceptance -> ok 10/10（谓词全过，not_ok=0，result_sha256 逐格核对）
t1_suite run --tier dev        -> ok 10/10
report -> run_status ok；common_strong.frozen = False（诚实：开发块配对差恒为 0）
身份一致性: commit=06572b6…, dirty=false, status_short=[]；VM 链 == 本机重算（b7641418…）；拉回 52 文件逐字节一致；平台 Linux-…aarch64
四臂对齐: targets_match/ranking_match 全 True
真实在线路径 p50 = 509.8 µs —— **确定性评分器的 VM 主机计时，非 DDQN、非星载**
  （工件 `model_provenance.trained_checkpoint_used=false` / `ddqn=false`；benchmark 谓词已强制该标记）
有限池: N=1 41 请求 23 排队 | N=2 47 请求 25 排队 | N=0 无界 0 排队
五模式: per_packet 41 计算请求；per_flow 34 请求 + 7 缓存命中；precomputed/async_point/async_window 各 0
```

## 3. 未做范围（非工程缺口）

1. FORMAL_RUN：确认性矩阵未执行（formal 包可生成/校验，运行需授权）。
2. 真实 DDQN 检查点：本机与 VM 均无 tensorflow 训练产物；接口与硬门槛已验证，不作策略结论。
3. 未写入 `旧正式部署根`，未做正式远端验收部署。
4. **研究设计尚未就绪（不只是"缺授权"）**：开发块配对差恒为 0 → `common_strong` 保持未冻结，
   正式样本量无法估计，formal 包因此标 `PENDING_DEV_SELECTION`。需先补有判别力的开发场景，
   才谈得上确认性运行；把它概括成"只差正式授权"是不准确的。
