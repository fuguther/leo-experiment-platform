# ROUND3-RESULTS-20261031 — 响应强度 × 新鲜度 × 容量余量（post-fix 全量矩阵）

> 状态：**FROZEN**（156 个矩阵 cell + 36 个 same-continuation fork + 8 个 realized oracle 对
> 全部 natural_end / pullback VERIFIED；本文冻结于 2026-11-01 04:50）。
> 协议：`ROUND3-PROTOCOL.md`（在任何 Round-3 运行前冻结）。
> 代码/release：P4/P5 `2354c0f`，矩阵 release `2354c0f3…-c5d2dc9f…`；fork release
> `73154a8…-2820a6ae…`（CODE 与 Round-2 `c63c071` 逐字节相同）；realized 附录 release
> `b92df2c…-02e8c82d…`。
> 证据：`evidence/t1/sq5-*`（156）、`sq5r-*`（8）、`fk3-*`（24）+ `fk-c-*`（12）。
> pre-fix（Round-1）数字任何情况下不与本文并列。

## 0. 一句话

在本 topology、本组校准负载与 post-fix release 上，**队列状态“用不用”是一阶的
（B/C 下 geometry 655–674 ms vs EWMA 358–381 ms），但状态“有多新”不是一阶的**：
AoI 从 ~500 ms 降到 ~16 ms 后，除 sticky 外所有 state-driven 策略的 E2E 都变差，
且变差幅度可由**路径变长（hop stretch）**解释（r=0.978），而不是由 switch/chattering 解释（r=0.218）。
原始“stale-state 造成性能损失”主张作为主命题被否定（见 `ROUND3-CONCLUSION.md`）。

## 1. 六策略谱系（P4/P5 为 Round-3 新增，参数冻结于运行前）

| 策略 | 定义 | 冻结参数 | 本轮实测审计 |
|---|---|---|---|
| P0 geometry | J=tx+prop+remaining_prop，无动态队列 | — | flip 恒 0（全部 geometry cell 交叉检查 0 违例） |
| P1 queue_greedy | 现有 scorer 的 argmin（hold-last 队列） | — | flip 19–28%，switch 0.18–0.43 |
| P2 queue_greedy_hysteresis | J_last ≤ J_best+η 则保持 | **η=5 ms** | flip_rule 18–21% / flip_truth 27–29%（rule-relative） |
| P3 queue_greedy_ewma | queue 项用 arrived-sample EWMA 水平 | **α=0.25** | flip 24–27%，switch 最低档之一 |
| P4 queue_softmin | J 同 P1；P(a)∝exp(−(J_a−J_min)/T) 采样 | **T=10 ms**（网格 2.5/10/40；margin p50=10.78 ms，冻结证据 `round3-results/TEMPERATURE-FREEZE.json`） | 分布熵 0.88–0.89 bit；switch 0.59–0.63（最高）；PDR 82–87%（最低） |
| P5 queue_greedy_sticky | J 同 P1；(sat,src,dst) OD-pair flowlet 方向保持 H | **H=100 ms**（网格 20/100/500） | keep rate 24%(A)/36%(B)/43%(C)/33%(D)，override 7–22%；flip_rule 13–22%，flip_truth 40–47% |

P4 的 RNG 每个决策由 `rngmod.link_stream(seed,"softmin:<sat>:<pid>:<ref>")` 派生（不使用全局流；
pid+hop index 作为稳定 ref，使挂/不挂决策 sink 的 result 逐位相同），audit 记录
probabilities/entropy/uniform/sampled。P5 只在真正提交 forward 时更新记忆，合法性/fallback 不变。
所有策略只改 score→action 映射；candidate/scorer/legality/fallback/control/data 语义完全不变。

## 2. 主矩阵（6 policies × {1 s, 20 ms} × A/B/C/D × seeds 7/8/9；seed 均值）

完整表见 `round3-results/tables.md`；下面列每个 regime 的 1 s 与 20 ms（E2E mean / p99）。

### A stable（90 Mbps，实测 ρmax≈0.60）

| policy | 1 s E2E | 1s p99 | 20 ms E2E | 20ms p99 | ΔE2E (20−1s) |
|---|---|---|---|---|---|
| geometry | **280.9** | 385.7 | **280.0** | 378.6 | −0.86 |
| queue_greedy_ewma | 295.4 | 476.5 | 320.3 | 568.8 | +24.93 |
| queue_greedy | 308.2 | 499.7 | 320.1 | 578.2 | +11.97 |
| queue_greedy_sticky | 320.5 | 600.3 | 334.5 | 694.9 | +13.92 |
| queue_greedy_hysteresis | 343.1 | 561.0 | 354.0 | 600.2 | +10.83 |
| queue_softmin | 448.2 | 970.9 | 445.1 | 977.3 | −3.17 |

**A 档 slack 充足：零响应（geometry）最好；任何 queue 响应都付出 detour 代价。**
EWMA 在 A 也只比 geometry 差 14.5 ms，但 hard greedy/hyst/sticky 差 28–62 ms；softmin 差 167 ms。

### B high-stable（160 Mbps，ρmax≈0.877）

| policy | 1 s E2E | 20 ms E2E | ΔE2E |
|---|---|---|---|
| geometry | 665.2 | 656.7 | −8.45 |
| **queue_greedy_ewma** | **358.0** | 383.6 | +25.59 |
| queue_greedy | 374.3 | 376.1 | +1.78 |
| queue_greedy_hysteresis | 408.2 | 410.2 | +1.95 |
| queue_greedy_sticky | 412.1 | 401.8 | −10.26 |
| queue_softmin | 484.8 | 480.2 | −4.60 |

### C near-capacity（200 Mbps，实测 ρmax≈0.943，ε≈5.7 Mbps）

| policy | 1 s E2E | 20 ms E2E | ΔE2E |
|---|---|---|---|
| geometry | 673.5 | 665.2 | −8.32 |
| **queue_greedy_ewma** | **380.9** | 414.6 | **+33.67** |
| queue_greedy | 402.1 | 418.7 | +16.56 |
| queue_greedy_hysteresis | 437.8 | 443.8 | +5.99 |
| queue_greedy_sticky | 473.3 | 444.4 | **−28.95** |
| queue_softmin | 493.0 | 501.2 | +8.18 |

### D over-critical burst（140 Mbps + 0.5 s×1.6 @8.0 s）

| policy | 1 s E2E | 20 ms E2E | ΔE2E |
|---|---|---|---|
| geometry | 602.2 | 595.4 | −6.73 |
| **queue_greedy_ewma** | **361.0** | 374.8 | +13.78 |
| queue_greedy | 377.5 | 363.1 | −14.44 |
| queue_greedy_sticky | 388.5 | 394.9 | +6.43 |
| queue_greedy_hysteresis | 402.4 | 395.1 | −7.35 |
| queue_softmin | 467.0 | 474.5 | +7.58 |

**FACT-M1（策略类别一阶）**：B/C 下 queue-aware 最好者（EWMA 358/381 ms）比 geometry
（665/674 ms）低 **307/293 ms**；geometry 的 isl_data queue area 为 680/797 Mbit·s，
queue-aware 为 28–50 Mbit·s。是否使用数据面队列信息，是最大的一阶效应。

**FACT-M2（regime 交叉）**：A 档最优是 geometry（零响应），B/C 档最优是 EWMA（平滑响应）。
响应强度没有全局最优值——它取决于 capacity slack。

**FACT-M3（响应形状非单调）**：B/C 下按 E2E 排序
EWMA < greedy < hysteresis ≈ sticky < softmin << geometry。
“hard argmin” 并不比“平滑 queue 估计”好（C：402 vs 381 ms）；
“decision 保持/惯性”（hyst/sticky）更差（438/473 ms）；
“随机化响应”（softmin T=10 ms）最差（493 ms）且 PDR 只有 82–84%。
故本轮不存在“越响应越好”或“越稳定越好”的单调规律。

**FACT-M4（P4/P5 的偏差）**：softmin 的 PDR 崩坏说明“按概率采样”在高载下把包送进
晚到/错误路径的比例显著上升（flip_truth 40–47% vs rule 26–27%）；sticky 的 truth flip
40–47% 明显高于 rule flip 13–22%，即“保持”让决策离 truth-optimal 更远（与 Round-1 的
hysteresis rule-relative 结论一致）。二者都不是“更好的响应策略”。

## 3. freshness–stability trade-off（Δ = 20 ms − 1 s；同 trace/seed 配对）

| regime | policy | ΔAoI p50 | Δflip | Δswitch | ΔE2E mean | Δhops | Δarea |
|---|---|---|---|---|---|---|---|
| A | geometry | −496 | 0.0000 | −0.0031 | −0.86 | −0.04 | −0.1 M |
| A | greedy | −494 | −0.0069 | +0.1268 | +11.97 | +0.63 | +1.0 M |
| A | EWMA | −493 | −0.0034 | +0.0290 | **+24.93** | **+1.39** | +0.7 M |
| A | hysteresis | −494 | −0.0050 | +0.0759 | +10.83 | +0.57 | +0.8 M |
| A | sticky | −503 | −0.0787 | +0.0695 | +13.92 | +0.56 | +2.6 M |
| A | softmin | −496 | −0.0026 | +0.0098 | −3.17 | −0.19 | −0.1 M |
| B | geometry | −491 | 0.0000 | −0.0037 | −8.45 | −0.05 | −12.4 M |
| B | greedy | −482 | −0.0161 | +0.1371 | +1.78 | +0.09 | +0.7 M |
| B | EWMA | −483 | −0.0150 | +0.0556 | **+25.59** | **+1.45** | +0.9 M |
| B | hysteresis | −485 | −0.0224 | +0.0612 | +1.95 | +0.10 | −0.2 M |
| B | sticky | −486 | −0.0760 | +0.0446 | **−10.26** | −0.56 | −2.2 M |
| B | softmin | −488 | −0.0029 | +0.0030 | −4.60 | −0.26 | −1.1 M |
| C | geometry | −489 | 0.0000 | +0.0005 | −8.32 | −0.04 | −15.2 M |
| C | greedy | −488 | −0.0058 | +0.1460 | +16.56 | +0.88 | +3.5 M |
| C | EWMA | −486 | −0.0300 | +0.0626 | **+33.67** | **+1.89** | +1.9 M |
| C | hysteresis | −486 | +0.0049 | +0.0945 | +5.99 | +0.49 | −4.1 M |
| C | sticky | −485 | −0.0614 | +0.0298 | **−28.95** | **−1.16** | −21.1 M |
| C | softmin | −489 | −0.0015 | +0.0023 | +8.18 | +0.49 | +0.2 M |

**FACT-F1**：AoI p50 从 ~500 ms 压到 ~16 ms 在所有策略/regime 都实现（ΔAoI≈−490 ms；
20 ms 档 control offered 1,162 Mbit vs 1 s 档 24.4 Mbit，47×）。
**FACT-F2**：`Δswitch > 0` 是普遍现象（queue-aware 策略 +0.002…+0.146；geometry ≈0），
即 fresh state 确实让决策更不稳定——但 **switch 变化与 E2E 变化几乎无关**（§4）。
**FACT-F3**：freshness 的性能效应**符号随策略而变**：
- state-driven 策略（EWMA、greedy）在高载下变差：C 档 EWMA **+33.7 ms**、greedy +16.6 ms；
  B 档 EWMA +25.6 ms；A 档 EWMA +24.9 ms。
- decision-regularized 策略（sticky）在 B/C 变好：**−10.3 / −29.0 ms**；
  hysteresis 近似不变（+2.0/+6.0）。
- 因此“fresh info + over-reactive policy 的耦合”是真实存在的：EWMA 在 1 s 下最好，
  但对 freshness 最敏感；sticky 方向相反但基线更差。
**FACT-F4（最重要）**：**没有任何 queue-aware 策略在 20 ms 下超过它在 1 s 下的最佳值**：
C 最优 1 s EWMA=380.9 ms，最优 20 ms EWMA=414.6 ms（差 +33.7 ms）；
B 最优 1 s EWMA=358.0，最优 20 ms greedy=376.1。freshness 本身不是性能杠杆。

## 4. 机制：freshness 的伤害由 hop stretch 介导，不是 chattering

对全部 24 个 (regime × policy) 的配对差做相关：

| 关系 | Pearson r |
|---|---|
| ΔE2E vs **Δhops**（20ms−1s） | **+0.978** |
| ΔE2E vs Δswitch | +0.218 |
| ΔE2E vs Δisl_data area | +0.743 |

**FACT-MEC**：fresh state 并没有让策略更多地在同一路径上抖动，而是让它**绕更长的路**
（C 档 EWMA +1.89 hops、greedy +0.88 hops、sticky −1.16 hops）。
这是因为“更新的队列快照”把某个此刻较空的 far-hop 出口显示为最优，包被送去后该出口排队，
路径 stretch 与后续 queueing 一起抬高 E2E。Round-2 观察到的 “AoI↓、switch↑、E2E↑”
因此不是“抖动成本”，而是“追逐瞬时队列 → 绕路”的路径选择成本。
**P5 sticky 是这个机制的反例**：它抑制改向，使 fresh state 下 hops 反而减少
（−1.16 hops）、queue area −21 M、E2E −29 ms；但 1 s 基线太差（473 ms），不构成赢家。

## 5. 方向性持续偏差与 flip 后果（Round-2 数据 + n=36 fork）

- `response-dynamics/`：flip 不是孤立抖动——**70.6–81.8% 的 flip 落在 ≥3 条同向连续 run 内**，
  仅 8.6–15.6% 快速 remerge；argmin 策略的 per-flip dJ p50 5.7–8.1 ms、p90 11.5–17.5 ms；
  P(consequential|flip)≈95–99%（proxy）。
- C regime：z≥1 的 flip 占 52.8%，但各 z 箱 backlog growth p50≈0；geometry（0 flip）
  才是 corridor 逼近容量的一侧（τ_relax 420 ms、area 797 M）。
- same-continuation **n=36**（s7/s8/s9，7 层分层）：`branch_states_identical` 36/36 true
  （决策日志前缀见证，非 full state）；**cohort mean Δ = −0.065 ms，CI95 [−0.75,+0.42]，
  15 fresh 更好/16 stale 更好；tagged Δ median 0，15/15 正负对半**；
  高 z 层 tagged 全为 stale 更快（+125.6 ms），large-proxy-R 层全为 fresh 更快（−343.7 ms）。
  ⇒ **E[ΔM_fresh−stale] ≈ 0**，没有系统性方向。

## 6. 受控瓶颈机制实验（与主环境严格分开；机制模型，非 LEO 证据）

两条对称 corridor、单服务队列、λ_src=90 pkt/s、λ_bg 由 slack 解出、τ∈{0,20,100,500} ms、3 seeds。
`selftest` 断言 τ=0 与 fresh 逐位相同。

| ε/μ | τ=0.02 paired Δdelay | τ=0.1 | τ=0.5 | stale 窗口队列斜率 | 超额 vs fresh |
|---|---|---|---|---|---|
| 0.50 | +3.66 ms | +8.53 | +15.53 | ≤+0.002 pkt/s | ~0 |
| 0.20 | +3.98 | +19.23 | +94.37 | +0.003 | +0.004 |
| 0.10 | +3.92 | +21.47 | +133.01 | +0.014 | +0.008 |
| 0.05 | +3.76 | +22.52 | +146.37 | +0.040 | +0.011 |
| 0.02 | +4.21 | +20.80 | +147.23 | +0.043 | +0.002 |

- **正**：放大存在——ε/μ=0.02、τ=500 ms 时 stale 平均延迟 297 ms vs fresh 150 ms（≈2×），
  p95 459 vs 195 ms，89.5% 包变慢；ε/μ=.05,τ=.5 时超额接收侧 1 s 局部队列增长
  从 fresh 的 0.01–0.14 pkt/s 升到 **+15.3–16.4 pkt/s**。
- **负**：预注册的「z>1 ⇒ 持续 queue growth」**不成立**：窗口级队列斜率最大 +0.043 pkt/s、
  相对 fresh 超额 ≤0.011 pkt/s（≈1% 容量）；放大是 herding 换边的**有界振荡**。
- **口径**：只有分箱（transient）口径 z 有判别力——配对延迟差与 `z_bin_p95` r=+0.931、
  与超额侧 bin growth r=+0.963；窗口级 `z_net` 仅 r=+0.113（全网格 z_net≤0.832 却对应 +147 ms）。
- **反事实 δ 高估**：同一模型里 |δ|（反事实口径）在 ε/μ=.02 为 1.35 pkt/s，而 stale 与 fresh
  两次运行的**实际负载差**只有 0.018 pkt/s（75×）。这解释了 140-sat 里 “z≥1 但 backlog growth≈0”。

## 7. 140-sat realized freshness（oracle 当前状态；非可部署策略）

同 trace/seed 的 advertised vs `state_source=ideal_current` 配对（8/8 完成，
`round3-results/realized-pairs.*`）：

| policy | sync | Σ\|Δλ\| over ISLs | max \|Δλ\| | z_realized max | paired E2E (fresh−adv) |
|---|---|---|---|---|---|
| C greedy | 1 s | 923 Mbps | 16.0 Mbps | 0.364 | −1.07 ms（887/918） |
| C EWMA | 1 s | 2,020 Mbps | 32.0 Mbps | 0.682 | **+10.97 ms**（835/934） |
| C softmin | 1 s | 387 Mbps | 4.9 Mbps | 0.083 | −4.96 ms |
| C sticky | 1 s | 1,126 Mbps | 29.6 Mbps | 0.385 | **−21.21 ms**（875/812） |
| B greedy | 1 s | 695 Mbps | 19.6 Mbps | 0.384 | +4.65 ms |
| B EWMA | 1 s | 1,519 Mbps | 43.9 Mbps | 0.735 | +3.94 ms |
| C greedy | 20 ms | 645 Mbps | 12.8 Mbps | 0.206 | **−20.30 ms**（1018/770） |
| C EWMA | 20 ms | 1,962 Mbps | 39.5 Mbps | 0.634 | **−10.12 ms**（992/809） |

**FACT-R1**：反事实 δ 在 140-sat **没有**被高估——oracle 状态让逐链路实际负载大幅改变
（单链路最多 44 Mbps，z_realized 0.08–0.74，从未 ≥1）。
**FACT-R2**：即使信息完全正确（oracle），E2E 也没有系统性改善：C 1 s 下 −1.1…+11.0 ms（符号随策略），
只有 C 20 ms 档 greedy/EWMA 分别 −20.3/−10.1 ms；仍不敌最优 1 s EWMA（380.9 ms）。
**FACT-R3**：oracle 状态的收益远小于策略类间差（~300 ms），也小于 freshness 造成的损害
（C EWMA +33.7 ms）。⇒ 在 C regime，剩余状态误差不是一阶性能因素。

## 8. 统计与身份

- 推断单位：run/seed/paired trace/fork；packet 仅用于机制分布。3 seeds 只报
  “across-seed consistent/inconsistent”，不做显著性强断言。
- 跨 cell 身份检查：`seed_replicates_sharing_one_trace`、`same_seed_with_multiple_traces`、
  `cells_sharing_config_identity` 均为空（identity_checks）。
- 每个 cell：config sha、trace source sha、preflight、natural_end、pullback VERIFIED 均已审计。
- 运行失败记录：3 个 cell 的首次 attempt 在 launcher 被杀时留下 dead manifest（无 receipt/result），
  已把 manifest 标为 `failed`（含原因），并以新 run-id 后缀 `-20261031-02` 重跑成功；
  run-id 未复用（`sq5-m3-b-geometry-h1000-s7-20261031-01`、
  `sq5-m3-c2-queue_greedy-h1000-s8-20261031-01`、`sq5-m3-c2-queue_greedy-h1000-s9-20261031-01`）。
- 覆盖：A/B/C/D 四个 regime × 6 policies × 2 sync × seeds 7/8/9 **全部 156 cells 完成**
  （每组 n_cells=3，identity_checks 为空）。
- 已知小缺口：appendix 只跑了 Tier-1 中包含的 12 个 100 ms sanity cell；
  P4 的 temperature 网格（2.5/40 ms）与 P5 的 hold 网格（20/500 ms）未在 time budget 内运行，
  主结果只用预注册 T=10 ms、H=100 ms（协议已冻结），因此不改变主结论，但“T/H 敏感性曲线”
  只能留待下一轮。
