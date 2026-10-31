# ROUND3-RESULTS-20261031 — 响应强度 × 新鲜度 × 容量余量（进行中）

> 状态：**RUNNING / PARTIAL**。本文件按工作单元增量更新；最终冻结版在结束前重写并 commit。
> 协议：`ROUND3-PROTOCOL.md`（在任何 Round-3 运行前冻结）。
> 代码：`2354c0f`（P4/P5）；矩阵 release `2354c0f3…-c5d2dc9f…`；fork release
> `73154a8…-2820a6ae…`（CODE 与 Round-2 `c63c071` 逐字节相同）。
> 证据：`evidence/t1/sq5-*`（矩阵）、`fk3-*`（fork 扩展）。

## 1. 本轮新增（相对 Round-2）

| 项 | 内容 |
|---|---|
| P4 `queue_softmin` | J 与 greedy 相同；P(a) ∝ exp(−(J_a−J_min)/T)；T 网格 {2.5,10,40} ms，主值 **10 ms**（由 Round-2 margin p50 10.78 ms 冻结，证据 `round3-results/TEMPERATURE-FREEZE.json`） |
| P5 `queue_greedy_sticky` | J 与 greedy 相同；`(sat,src,dst)` OD-pair flowlet 方向保持 H；H 网格 {20,100,500} ms，主值 **100 ms** |
| same-continuation fork | n=12 → **n=36**（s7 12 + s8 12 + s9 12），7 层分层 |
| controlled bottleneck | 两 corridor 机制模型，slack {0.50,0.20,0.10,0.05,0.02} × τ {0,20,100,500} ms |

## 2. 主矩阵（6 policies × {1 s, 20 ms} × {A,B,C,D} × seeds 7/8/9）

> 矩阵仍在运行；以下由 `run_round3_pipeline.sh` 增量生成，完成后冻结。

（待填：`round3-results/matrix.md` 的表）

## 3. freshness–stability trade-off（1 s → 20 ms）

（待填）

## 4. 响应敏感性（行为代理）→ flip / switch / E2E / p99

（待填）

## 5. same-continuation causal regret（n=36）

（待填：`round3-results/fork-results-n36.md`）

## 6. 受控瓶颈：z=|δ|/ε 的 amplification 边界

（待填：`controlled-bottleneck/RESULTS.md`）

## 7. 结论（结束前冻结）

（待填）
