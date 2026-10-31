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

## 3. 受控瓶颈：z=|δ|/ε 的 amplification 边界（已完成）

模型与口径：两条对称 corridor、单服务队列 μ=100 pkt/s、λ_src=90 pkt/s、λ_bg 由目标 slack 解出、
backlog=queued+in-service、queue-greedy 决策、τ∈{0,20,100,500} ms、3 seeds、warm-up 60 s + 测量 120 s。
`selftest` 断言 τ=0 与 fresh **逐位相同**（flip=0）。**这是机制模型，不是 LEO 证据。**

| ε/μ | τ=0.02 s paired Δdelay | τ=0.1 s | τ=0.5 s | 窗口级队列斜率（stale） | 相对 fresh 超额 |
|---|---|---|---|---|---|
| 0.50 | +3.66 ms | +8.53 | +15.53 | ≤+0.002 pkt/s | ~0 |
| 0.20 | +3.98 | +19.23 | +94.37 | +0.003 | +0.004 |
| 0.10 | +3.92 | +21.47 | +133.01 | +0.014 | +0.008 |
| 0.05 | +3.76 | +22.52 | +146.37 | +0.040 | +0.011 |
| 0.02 | +4.21 | +20.80 | +147.23 | +0.043 | +0.002 |

**FACT（正）**：放大存在。τ>0 时配对延迟差随 slack 变小、τ 变大而单调增大；最小 slack + 最大 τ
下 stale 平均延迟 297 ms vs fresh 150 ms（ratio≈1.98），p95 459 vs 195 ms，89–90% 包变慢。
**FACT（负）**：预注册的「z>1 ⇒ 持续 queue growth」**不成立**：窗口级队列斜率最大 +0.043 pkt/s、
相对 fresh 超额 ≤0.011 pkt/s（≈1% 容量）；放大是 herding 换边的**有界振荡**，不是漂移。
**FACT（口径）**：只有**分箱（transient）口径**的 z 有判别力——配对延迟差与
`z_bin_p95` 相关 +0.931、与 `bin growth(超额侧)` +0.963；窗口级 `z_net` 仅 +0.113，
且全网格 z_net ≤0.832 却对应 +147 ms 损失。
**FACT（最关键）**：反事实 |δ| 与"stale 运行实际负载 − fresh 运行实际负载"不是同一个量：
本模型 ε/μ=0.02 时 |δ|=1.35 pkt/s 而 realized 负载差只有 0.018 pkt/s（**差 75×**）。
这解释了 140-sat 的 "z≥1 但 backlog growth≈0"——z 用反事实口径，可以在实际负载几乎不变时 ≥1。
**待验证**：140-sat 的 `z_realized`（同 trace 的 advertised vs ideal_current 配对的逐链路负载差）
正在附录 `sq-realized-20261031.json`（8 cells）中测量。

## 3b. 140-sat realized freshness counterfactual（已 5/8 对）

同 trace/seed 的 advertised vs `state_source=ideal_current`（oracle 当前状态，非可部署策略）配对：
逐链路 4–14 s served rate 差 + 逐 pid E2E 差。证据 `round3-results/realized-pairs.{json,md}`。

| policy (C 1 s unless noted) | Σ\|Δλ\| over ISLs | max \|Δλ\| | z_realized p50 / max | paired E2E (fresh−adv) | fresh faster/slower |
|---|---|---|---|---|---|
| c2-queue_greedy | 923.4 Mbps | 16.00 Mbps | 0.014 / **0.364** | **-1.07 ms** | 887/918 |
| c2-queue_greedy_ewma | 2020.1 Mbps | 32.00 Mbps | 0.029 / **0.682** | **+10.97 ms** | 835/934 |
| c2-queue_softmin | 386.7 Mbps | 4.92 Mbps | 0.009 / **0.083** | **-4.96 ms** | 697/638 |
| c2-queue_greedy_sticky | 1126.2 Mbps | 29.61 Mbps | 0.013 / **0.385** | **-21.21 ms** | 875/812 |
| b-queue_greedy | 695.3 Mbps | 19.60 Mbps | 0.007 / **0.384** | **+4.65 ms** | 692/732 |

**FACT（重要，且与受控瓶颈互补）**：在 140-sat 上，反事实 δ 并没有像两 corridor 机制模型那样被大幅高估——
把 advertised 换成 oracle 当前状态后，逐链路 realized 负载确实大幅改变
（C greedy Σ|Δλ|≈0.92 Gbps、单链路最多 16 Mbps；EWMA 2.0 Gbps / 32 Mbps；sticky 1.13 Gbps / 29.6 Mbps），
z_realized 最大 0.36 / 0.68 / 0.39（softmin 仅 0.08）。**但 E2E 没有系统性改善**：
greedy −1.1 ms、EWMA +11.0 ms、softmin −5.0 ms、sticky **−21.2 ms**、B greedy +4.7 ms；
符号随策略而异、量级远小于策略类间差（EWMA vs geometry ≈ −290 ms）。
⇒ 即使信息完全新鲜（oracle），在 C regime 也没有稳定收益；sticky 的 −21 ms 提示“保持型策略 + stale”耦合稍差，
但这不是主线量级。待剩余 3 对（P4/P5 20 ms、B EWMA）完成后冻结。

## 4. response-dynamics（已完成，Round-2 数据）

（`response-dynamics/README.md`；flip 语义经 selfcheck 与主分析器逐决策一致。）

| cell | flip | switch | entropy(bit) | dJ p50/p90 (ms) | P(cons\|flip) | persistent_bias 份额 |
|---|---|---|---|---|---|---|
| C greedy s7 | 0.2638 | 0.297 | 0.863 | 6.46 / 13.22 | 0.977 | 76.5% |
| C greedy s8/s9 | 0.2566/0.2570 | 0.255/0.273 | 0.797/0.810 | 6.13/6.30 / 11.84/12.77 | 0.971/0.976 | 76.5% |
| C EWMA s7/s8/s9 | 0.260/0.264/0.265 | 0.158/0.217/0.207 | 0.503/0.695/0.731 | 5.72–6.52 / 11.5–11.9 | 0.949–0.967 | 81.6% |
| C hysteresis s7 | 0.1887 | 0.191 | 0.805 | 8.07 / 17.54 | 0.990 | — |
| B greedy/EWMA s7 | 0.269/0.262 | 0.241/0.138 | 0.823/0.445 | 6.16/5.94 / 11.6/10.8 | 0.982/0.951 | 80.7%/81.8% |

**FACT**：flip 不是孤立抖动——**70.6–81.8% 的 flip 落在 ≥3 条同方向连续 flip run 内**，
仅 8.6–15.6% 在下一决策 quick remerge；argmin 策略的 `fresh` 就是 truth argmin，故
`P(harmful)=P(consequential)`（proxy），per-flip dJ p50 5.7–8.1 ms、p90 11.5–17.5 ms，
换算到 per-decision 与已发布的 regret 1.6–2.1 ms 自洽。hysteresis 是唯一有 `stale_better`
（3.6%）的策略，且 dJ 最重。
**限制**：这是 proxy 后果分类，不是 causal；没有 softmin 数据时 entropy 只能读"实际选择"的经验熵。

## 5. freshness–stability trade-off（1 s → 20 ms，矩阵运行中）

（待填）

## 6. 响应敏感性（行为代理）→ flip / switch / E2E / p99（矩阵运行中）

（待填）

## 7. same-continuation causal regret（n=36，已冻结）

证据：`fk-c-*`（12，s7）+ `fk3-s8-*`（12）+ `fk3-s9-*`（12）；样本 `fork-samples/round3-expansion.json`；
结果 `round3-results/fork-results-n36.{json,md}`。sign：Δ = fresh − stale（<0 = fresh 更快）。

- **`branch_states_identical` 36/36 true**（决策日志前缀见证，**不是** full SimPy state）。
- **cohort（全网受影响包）均值：mean −0.065 ms，median 0.000，bootstrap 95% CI [−0.747, +0.424]，
  15 fresh 更好 / 16 stale 更好** ⇒ 总体与 0 无法区分。
- **tagged packet 因果差：median 0，mean −11.2 ms，CI [−70.6, +43.2]，15/15 正负对半**
  ⇒ 也不支持“fresh 系统性更好/更差”。
- **分层异质**（说明符号取决于具体决策，而不是总体方向）：
  high_z（n=5）tagged 全为 **stale 更快**（mean +125.6 ms，CI [+68.7,+166.2]）；
  large_proxy_R（n=4）tagged 全为 **fresh 更快**（mean −343.7 ms，CI [−479.7,−206.5]）；
  long_persistence cohort mean −1.88 ms（CI [−5.63,−0.003]）——唯一略偏 fresh 的层。
- proxy regret 中位 6.28 ms，与 causal 不可互换（同一决策可 ±数百 ms）。

**答 E[ΔM_fresh−stale]**：**≈ 0**（网络 cohort 口径 CI 跨 0；tagged 口径 CI 跨 0）。
不能写成 “fresh 平均更好” 或 “stale 平均更好”。**限制**：n=36 只覆盖 near-capacity greedy 的
3 个 seed/3 个 cell；不是 population 样本；witness 仍是 decision-log prefix。

## 8. 受控瓶颈（见 §3）

（待填：`controlled-bottleneck/RESULTS.md`）

## 9. 结论（结束前冻结）

（待填）
