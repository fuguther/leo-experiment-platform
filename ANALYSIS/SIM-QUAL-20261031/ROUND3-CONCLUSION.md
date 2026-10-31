# ROUND3-CONCLUSION-20261031 — 响应强度 vs 新鲜度（FROZEN）

> 依据：`ROUND3-RESULTS.md`、`round3-results/`、`controlled-bottleneck/`、`response-dynamics/`、
> `fork-results-n36.*`、`round3-results/realized-pairs.*`、`PROTOCOL-ROUND3`（冻结于运行前）。
> 所有矩阵结论 = post-fix；pre-fix（Round-1）数字不并列。证据等级在 §2 明确区分。

## 0. 最终判断

> **原始 “stale state 造成系统性能损失 / 预测 stale queue 能显著改善一般 LEO E2E” 作为论文主命题：
> KILL。**
>
> **Action-level 现象（stale 与 fresh 的动作不同、Γ 可预测）：RETAIN 为诊断/边界，不再作为性能主张。**
>
> **新的正结果主线：RETAIN —— “路由如何响应动态队列状态”比“状态多新鲜”更决定性能；
> 存在由 capacity slack 决定的响应强度最优区，且 freshness 的价值取决于决策正则化。”**

理由（全部来自本轮判据）：
1. 20 ms AoI（p50 16.6 ms，−96.7%）**没有**稳定系统收益：6 策略 × 4 regimes 中，
   state-driven 策略在高载下反而变差（C：EWMA +33.7 ms、greedy +16.6 ms；B：EWMA +25.6），
   只有 decision-regularized 的 sticky 变好（B/C −10.3/−29.0 ms），但其 1 s 基线更差，
   从未超过最优 1 s 配置（EWMA 380.9 ms @C）。
2. same-continuation n=36 的 causal regret 正负各半、总体 ≈0（cohort CI 跨 0），
   不存在 fresh 系统性更好/更差。
3. 多策略多 seed 下 freshness 效应（|ΔE2E| ≤ 34 ms）**远小于 policy-class 效应**
   （B/C：EWMA 358/381 vs geometry 665/674，≈300 ms）。
4. 受控 C regime（ρmax=0.943）下没有持续 amplification：z_bin 可放大延迟（最多 +147 ms），
   但窗口级队列斜率超额 ≤1% 容量；140-sat 的 oracle 状态实验也没有系统收益。
5. 简单正则化（EWMA 平滑）已解释主要差异；hard greedy/惯性/随机化都更差。

## 1. 八个必答问题

### Q1 Γ=Lτ/m 是否仍是稳定的 action-level sensitivity predictor？

**是（聚合层面），但不是“性能”预测器。**
把全部 queue-aware cell 的 Γ 分箱合并：flip rate 0.1879（Γ<0.5）→ 0.4069（0.5–1）
→ 0.4775（1–2）→ 0.5414（≥2），单调；105 个多 bin cell 中 69 个逐 cell 单调。
它与 proxy regret（1.6–2.1 ms/decision）相关，但**不能**用于预测 E2E 损失：
同一 Γ 区间里 E2E 的方差几乎全部来自策略类别与负载。

### Q2 策略响应强度与 flip/switch/directional persistence 的关系？

- flip 与“是否使用队列状态”强相关：geometry 恒 0；queue-aware 19–28%（softmin 的 truth flip
  因采样上升到 40–47%）。
- switch rate 的排序：geometry 0.22（拓扑移动驱动）> greedy 0.28–0.43 > EWMA 0.16–0.26 >
  hysteresis 0.11–0.24 > sticky 0.13–0.21 > softmin 0.59–0.63（随机）。
  但 **switch 与 E2E 没有单调关系**（C 1 s：EWMA switch 0.196 最好，greedy 0.276 差 21 ms，
  hysteresis 0.144 差 57 ms，softmin 0.627 差 112 ms）。
- 方向性持续：Round-2 的 70.6–81.8% flip 属于 ≥3 同向连续 run 的结论在新数据上保持；
  persistent bias 的大小不随 freshness 改善而消失（C 1 s→20 ms Δflip 只有 −0.005…−0.03）。
- **关键否定**：把“响应强度”定义为“对更新更敏感”（Δswitch 或对 fresh 的反应）时，
  它与性能损失**不是**同向关系（ΔE2E vs Δswitch r=0.218）；真正与损失同向的是
  **路径响应强度** Δhops（r=0.978）。

### Q3 freshness 与 decision stability 是否存在 trade-off？

**存在，但方向由策略决定，不是普遍“越新越不稳→越差”。**
- 所有 queue-aware 策略在 20 ms 下 switch 都上升（+0.002…+0.146），geometry 不变；
  即 freshness→instability 是真实的。
- 但 E2E 只在 state-driven 策略上变差（EWMA/greedy），在 sticky 上变好。
- 所以 trade-off 的正确表述是：
  **freshness 提高“对瞬时队列的响应”，这既增加 switch，也增加 path stretch；
  收益/损失取决于策略是否把瞬时响应转成更长的绕路。**

### Q4 为什么 AoI↓ 不一定 E2E↓？

**因为 state-driven 策略把“更新的队列快照”转成了更长的路径。**
- 20 ms−1 s 的 ΔE2E 与 Δhops 的相关为 **+0.978**（24 对）；与 Δswitch 仅 +0.218。
- C 档 EWMA：+1.89 hops、E2E +33.7 ms；greedy +0.88 hops、+16.6 ms；
  sticky −1.16 hops、−29.0 ms（唯一反向）。
- 机制：fresh snapshot 把“此刻较空的 far-hop 出口”显示为最优，包被送去后该出口排队；
  路径变长与后续 queueing 一起抬高 E2E。Round-2 的“AoI↓、switch↑、E2E↑”不是 chattering 成本，
  而是 **detour/herding 成本**。
- 另外，即使换成 oracle 当前状态（8 对 realized 实验），C 1 s 下 E2E 变化为
  −1.1…+11.0 ms（符号随策略），只有 C 20 ms 档 −10…−20 ms；仍不敌最优 1 s 配置。

### Q5 是否存在“适度响应”的最优区？

**存在，但随 regime 平移，且不是简单 U 型。**
- A（ρ≈0.60）：**零响应最优**（geometry 280.9 ms），任何 queue 响应差 15–167 ms。
- B/C（ρ≈0.88/0.94）：**平滑响应最优**（EWMA 358/381 ms）；
  hard argmin 差 16/21 ms；决策惯性（hysteresis/sticky）差 50–92 ms；
  随机化响应（softmin T=10 ms）差 127–112 ms 且 PDR 降至 82–84%。
- 因此按“响应越强越接近最优”的单调轴不成立；按“状态使用方式”排序：
  **不使用（A 好、B/C 差）→ 平滑使用（B/C 最好）→ 硬 argmin → 惯性保持 → 随机化（最差）**。
  这是本轮的正结果：**响应形状（smoothing）比响应强度（switch/freshness）更决定性能。**

### Q6 capacity slack→0 时是否出现理论 amplification？

- 在**受控瓶颈机制模型**里：放大确实出现，但是**有界延迟放大**，不是持续 queue growth。
  ε/μ=0.02、τ=500 ms 时 stale 平均延迟 297 ms vs fresh 150 ms（≈2×，p95 459 vs 195），
  89.5% 包变慢；但窗口级队列斜率超额 ≤0.011 pkt/s（≈1% 容量），
  放大由 herding 换边造成并在窗口内自限。
- 预注册的「z>1 ⇒ 持续 queue growth」**不成立**；只有 transient（分箱）口径 z 有判别力
  （r=+0.931），窗口级 z_net 无（r=+0.113）。
- 140-sat C regime：z_realized 最大 0.68–0.74，从未 ≥1；oracle 状态不改善 E2E。
  ⇒ 主星座 C 档没有出现 capacity-slack amplification。

### Q7 主 140-sat 与受控瓶颈的结论分别是什么？

- **主 140-sat（A/B/C/D × 6 策略 × 2 sync × 3 seeds，post-fix）**：
  策略类别一阶（~300 ms），freshness 二阶且符号随策略（−29…+34 ms），
  机制是 hop-stretch/detour；best config 始终是 **1 s EWMA**。
- **受控瓶颈（两 corridor 机制模型）**：
  temporal error 可造成 2–5× 延迟放大（有界振荡），但不造成持续队列漂移；
  z 必须用 transient 口径；反事实 δ 高估 realized 负载差 5–75×。
  两环境一致的信息：**有界、自限、不会把 stale-state 放大成系统性性能崩溃**。

### Q8 原始 stale-performance 命题：RETAIN / SHRINK / KILL？

**KILL（作为论文主命题）；同时保留 SHRINK 后的 action-level 诊断。**
- KILL 的依据：20 ms 无稳定收益；causal ≈0；freshness 效应 ≪ policy-class 效应；
  C 无 amplification；正则化已解释主要差异（用户任务书 §19 的五条全部满足）。
- 保留（SHRINK）的依据：flip 仍 19–28%，Γ 仍单调预测 flip；方向性偏差仍存在（267–398 Mbps
  一侧搬运）；这些是真实的动作层现象，但**不转化为可重复的系统性能损失**。
- 保留的新正结果：**response-shape（smoothing vs hard vs inertia vs randomization）
  与 capacity slack 的交互**是性能的一阶因素（§Q5），且 hop-stretch 是 freshness 伤害的介导变量。

## 2. 证据等级

### Strict（定义/程序语义保证）

S1. 六策略只改 score→action 映射；candidate/scorer/legality/fallback/control/data 合同不变；
    legacy 逐位回归保持（`state_policy` 73 tests + 全量 1258 passed / 5 pre-existing）。
S2. geometry 的 flip≡0（全部 geometry cell 交叉检查 0 违例）。
S3. P4 的 RNG 由按键派生的独立流决定，挂/不挂 decision sink 的 result 逐位相同；
    T→0 极限为 hard greedy（单测）。
S4. same-continuation 的 `branch_states_identical` 只是**决策日志前缀见证**，不是 full SimPy state；
    36/36 为 true 不改变这一表述边界。
S5. 20 ms 档的 measured AoI p50 = 16.3–16.6 ms，不是“advertisement interval=20 ms 即 AoI=20 ms”。

### Defeasible（本轮数据支持，依赖 simulator/topology/scenario）

D1. 策略类别一阶：B/C 下 queue-aware 比 geometry 低 ~300 ms（3 seeds）。
D2. 响应形状排序：EWMA < greedy < hysteresis≈sticky < softmin（B/C，1 s）。
D3. freshness 效应符号随策略：state-driven 变差（C EWMA +33.7、greedy +16.6），
    sticky 变好（−29.0）；所有 queue-aware Δswitch>0。
D4. hop-stretch 介导：ΔE2E vs Δhops r=+0.978（24 对）；vs Δswitch +0.218。
D5. 受控瓶颈：有界放大、无持续增长；z_bin 有判别力；反事实 δ 高估 realized 5–75×。
D6. 140-sat oracle：realized 负载改变大但 E2E 无系统收益（8 对）。
D7. n=36 fork：cohort mean −0.065 ms（CI 跨 0），tagged 15/15 正负对半。

### Unknown

U1. 真实 Starlink / 真实运营网络的 AoI 分布、queue trace 与路由策略；
    本组为合成 30/70 hotspot + 固定 140-sat。
U2. 真实网络是否存在本 topology 未覆盖的“ε→0 且持续”的 regime；
    受控模型只能证明有界放大，不能证明真实系统同样有界。
U3. same-continuation causal 的总体分布（n=36，非概率样本，单一策略/负载）。
U4. P4/P5 的 temperature/hold 敏感性未完成全网格（time budget），
    主结果只用预注册 T=10 ms、H=100 ms。
U5. formal 判据下的统计显著性与论文级外推（本轮全部 diagnostic）。

## 3. 论文方向建议（只从证据出发）

**主推荐（1 个）：**
> **Responsiveness–stability trade-off in distributed LEO routing:
> how routing uses dynamic queue state matters more than how fresh that state is.**
> 证据：A/B/C/D × 6 策略 × 1 s/20 ms × 3 seeds（156 cells）+ 受控瓶颈 + realized oracle。
> 核心图：策略响应形状（no-state / smoothed / hard argmin / inertia / randomized）在不同 slack 下的
> E2E/p99 + hop-stretch 机制；freshness 作为调节变量而非主变量。
> 这是正结果：存在可复现的“适度/平滑响应”最优区（B/C），且响应形状可解释 ~300 ms 的差异。

**备选（≤1）：**
> **Decision robustness under stale local state**: Γ=Lτ/m 作为 action-level sensitivity 判据 +
> consequential-flip 分类（P(consequential|flip)≈95–99% 但 per-flip 代价小）。
> 仅作为主线的机制/边界章节，不单独作为“性能提升”主张。

**明确不建议**：把“预测 stale queue 改善 E2E”作为论文主创新点；本轮证据与 Round-1/2 一致地否定它。
