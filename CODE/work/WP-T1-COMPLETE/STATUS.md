# WP-T1-COMPLETE — 状态账本

> 计划书：`docs/superpowers/plans/2026-09-27-t1-complete-implementation.md`
> 交付：`REPORT.md`（含"第二轮复审返工 S1–S7"节）；判据：`criteria.json`；语义合同：`contract.yaml`。
> **执行位置硬规则（AGENTS.md）**：所有实验只在 VM 上跑；本机只做代码/测试/只读复核。

## 0. 身份

| 项 | 值 |
|---|---|
| 仓库根 | `/Users/lge/Desktop/topic/leo-exp-main` |
| 分支 | `t1/frozen-branch-and-async-design` |
| 起始 HEAD | `0876b12dbb0e067a840743dcc57b804d4600e9b0` |
| 复审轮次 | 53aeb30（R1–R9，已修）→ 2330701（S1–S7，已修）→ 本轮 S8（runner 身份假阳性，已修） |
| 最新证据 | run-id `t1-final-8a31496`；HEAD `8a31496`；`identity.git.dirty=false`；执行链 `e8537aed…` |
| VM | `ssh vm` = cuda-liguang13；隔离实验根 `/data/论文/leo-t1-wt`；正式部署 `/data/论文/leo-direct-sim` **从未写入** |
| VM runner | `CODE/scripts/remote/t1-vm.sh sync|run|pull|experiment` |
| 链一致性 | VM 链 `e8537aed…` == 本机链 `e8537aed…`（执行链已排除 ._ 元数据并远端剪枝） |

## 1. 阶段状态（含两轮复审纠正）

| 阶段 | 状态 | 说明 |
|---|---|---|
| P0–P3 | DONE | 基线/账本、单位与身份、时间事件与有限算力、快照/预测/评分 |
| P4 四组理想诊断 | DONE（S1 修正真值语义） | 队列真值按内核事件语义重建，三组理想臂 + 2×2 分解 |
| P5 四组可部署执行 | DONE | |
| P6 包粒度/基准/压力 | DONE（S5 重做计时） | 计时走真实在线入口并逐臂对齐；包长/请求率/有限池 |
| P7 异步更新器 | DONE（S3 修正成本） | 查表不付逐包计算；后台任务付真实计算 |
| P8 五执行模式/DDQN | DONE（S6 接入固定推理；真实检查点外部阻塞） | 缓存命中/有限 N>0/分段窗口已激活 |
| P9 指标/统计/停止 | DONE（S7 冻结与来源） | 冻结 D、观察窗口删失、common_strong 未冻结时诚实标注 |
| P10 t1_suite/恢复 | DONE（S4 整轮成败） | 谓词失败即整轮失败；CLI 退出码 3 |
| P11 证据链/回归/待执行包 | DONE（S7 正式包真实冻结） | acceptance/dev 在 VM 跑通；formal 包可编译校验、拒绝未授权执行 |
| P12 最终交付 | DONE | REPORT 重写现状并标注历史 |

## 2. 实测（本机=测试；实验=VM）

```
# 本机（允许：单元/回归测试）
python3 -m pytest CODE/leo_sim/tests CODE/experiment_platform/tests CODE/tests ANALYSIS/tests -q
1278 passed, 2 skipped, 1 warning in 454.63s
  （1275 + 本轮新增 CODE/tests/test_t1_vm_launch_manifest.py 的 3 项）

# VM（实验唯一位置），隔离根 /data/论文/leo-t1-wt
run-id: t1-final-8a31496   pulled -> out/vm/t1-final-8a31496/
t1_suite compile  -> 20 cells
t1_suite validate -> valid true（bundle_fingerprint 334dbbd2…）
t1_suite run --tier acceptance -> ok 10/10（谓词全过，not_ok=0，result_sha256 逐格核对）
t1_suite run --tier dev        -> ok 10/10
report -> run_status ok；common_strong.frozen = False（诚实：开发块配对差恒为 0）
身份一致性: commit=8a31496…, dirty=false, status_short=[]；VM 链 == 本机重算（e8537aed…）；平台 Linux-…aarch64
四臂对齐: targets_match/ranking_match 全 True
真实在线路径 p50 = 526–529 µs（分相：观测构造 398–399 µs｜仅推理 12.07 µs｜调用基线 46 ns）
有限池: N=1 41 请求 23 排队 | N=2 47 请求 25 排队 | N=0 无界 0 排队
五模式: per_packet 41 计算请求；per_flow 34 请求 + 7 缓存命中；precomputed/async_point/async_window 各 0
```

## 3. 未做范围（非工程缺口）

1. FORMAL_RUN：确认性矩阵未执行（formal 包可生成/校验，运行需授权）。
2. 真实 DDQN 检查点：本机与 VM 均无 tensorflow 训练产物；接口与硬门槛已验证，不作策略结论。
3. 未写入 `/data/论文/leo-direct-sim`，未做正式远端验收部署。
4. 开发块配对差恒为 0 → common_strong 保持未冻结，样本量不可由此估计。
