# RESULTS-ROUND2-20261031 — 校准负载 × 多 seed × 强同步 × 方向性持续偏差

> 性质：**DIAGNOSTIC**（不重放全量、不进 formal 主表）。所有 cell 均来自 **post-fix**
> （control 死信修复之后）的代码，pre-fix Round-1 数字不再并列（见 §1）。
> 证据：`三端文件与同步治理/evidence/t1/sq4-*`、`sq3-*`；分析器 `sq_policy_analyze.py`、
> `sq_persistence_analyze.py`、`sq_round2_analyze.py`、`fork_analyze.py`；矩阵结果
> `round2-results/{regimes,sync}.json/md`、`persistence/*`、`fork-results.*`。
> 状态：**sync 20 ms 与 same-continuation forks 仍在运行；§6/§7 的数字将在跑完后冻结。**

## 0. 代码与 release 身份

| 用途 | release | cells |
|---|---|---|
| A regime（load 90） | `4e7a7b5…-aac7223…` (R3) | `m2-a-{geom,greedy}-s7` |
| B/C/D/sync 主矩阵 | `038fb16…-b8eb375…` (R4) | `m2-{b,c2,d,sync*}-*` |
| same-continuation forks | `de632b3…-76a4127…` (R5) | fork driver + pinned c2 config |
| 校准 | `fb90c01…-96da8e3f…` (R2) | `cal-*`, `pf-*` |

R3/R4/R5 的 kernel 代码相同（control fix `fb90c01`）；R4/R5 只差合同/配置输入文件。

## 1. 必须先读：control 死信修复改变了 data plane

`CONTROL-FIX-COMPARISON.md` 的实测：

- 控制面：registered −27.4%、expired 6,556→**0**、`isl_ctrl` 面积 848→0.09 Mbit·s（−99.99%）、
  **delivered control bits / arrived instances 逐位不变**、AoI p50 0.4835→0.4828 s（不变）。
- **data plane：不一致**。平均跳数 +1.98/+2.49，E2E 均值 +35.0/+42.8 ms。
  原因：死信把几何不可用的对端出口伪装成"有积压"（pre-fix 40.9%/45.6% 的正积压样本恰为
  整数个控制包线长；post-fix 精确为 0）；修复后这些出口显示空闲 → 决策去试探 → 对端 hold 重选。
- 因此 Round-1（pre-fix）网络级数字**只作历史**；本文件全部基于 post-fix。

## 2. 校准结果（先测后冻结）

| regime | global load | 实测 max corridor ρ | 实测 ε(top link) | isl_data area | IN_SYSTEM | NO_ROUTE |
|---|---|---|---|---|---|---|
| A stable | 90 Mbps | **0.599** | 40.1 Mbps | — | — | — |
| B high-stable | 160 Mbps | **0.877** | 12.3 Mbps | 18.5 Mbit·s | 1 | 0 |
| C near-capacity | **200 Mbps** | **0.943** | **5.7 Mbps** | 36.1 Mbit·s | 16 | 6 |
| D over-critical burst | 140 Mbps + ×1.6 (8.0–8.5 s) | ≈1.31 瞬时（标度） | 瞬时负 | — | — | — |

- 目标 C 带 0.95–0.99 **未达到**：realized ρ 在 160→200 仅 0.877→0.943，曲线凹（queue-aware
  路由在接近容量前绕行），这是本轮必须报告的校准事实；C 按**实测 ε≈5.7 Mbps** 定义，
  不写成 0.99。
- 瓶颈链路始终是 `isl:128:127` 与 `isl:127:126`（tokyo→singapore 相邻两跳）。

## 3. 主矩阵：A/B/C/D × policy（s7；C 有 3 seeds）

（数值来源 `round2-results/regimes.md`；单位 ms，area 为 isl_data bit·s。）

| group | flip | R mean | AoI p50 | switch | W p50 | E2E mean | E2E p95 | E2E p99 | area |
|---|---|---|---|---|---|---|---|---|---|
| A geom | 0.0000 | 0.00 | 501.6 | 0.211 | n/a | 284.8 | 386.3 | — | 15.6 M |
| A greedy | 0.2765 | 1.98 | 494.4 | 0.159 | 111.1 | 302.1 | 437.6 | — | 4.07 M |
| B geom | 0.0000 | 0.00 | 501.1 | 0.215 | n/a | **685.9** | 1045.8 | — | **680.1 M** |
| B greedy | 0.2702 | 1.72 | 493.3 | 0.240 | 34.4 | 365.4 | 583.0 | — | 18.5 M |
| B ewma | 0.2642 | 1.61 | 496.0 | 0.138 | 39.3 | 365.7 | 506.2 | — | 18.7 M |
| C geom | 0.0000 | 0.00 | 504.3 | 0.222 | n/a | **687.3** | 1192.6 | 1210.7 | **830.6 M** |
| C greedy | 0.2661 | 1.90 | 507.6 | 0.298 | 20.0 | 422.3 | 723.4 | — | 36.1 M |
| C ewma | 0.2627 | 1.66 | 506.1 | 0.159 | 29.3 | 395.6 | 526.1 | — | 32.7 M |
| D geom | 0.0000 | 0.00 | 503.2 | 0.221 | n/a | 626.1 | 1024.6 | — | 523.5 M |
| D greedy | 0.2833 | 1.85 | 506.3 | 0.238 | 39.9 | 354.7 | 518.8 | — | 15.3 M |
| D ewma | 0.2771 | 1.98 | 507.4 | 0.138 | 38.9 | 365.9 | 514.3 | — | 14.5 M |

C 多 seed（s7/8/9；ewma 暂时 s7/8）：

| group | seeds | flip | R mean | E2E mean [min–max] | area |
|---|---|---|---|---|---|
| C geom | 7,8,9 | 0 | 0 | **678.0 [658.8–687.8]** | 796.7 M |
| C greedy | 7,8,9 | 0.2612 | 1.77 | **408.4 [401.2–422.3]** | 31.6 M |
| C ewma | 7,8 | 0.2639 | 1.68 | **379.7 [363.7–395.6]** | 29.4 M |

**FACT（network 层）**：
1. 负载从 A→B→C，queue-aware 策略 E2E 单调上升（greedy 302→365→408；ewma 302?→366→380），
   queue area 也上升（4→18→32 Mbit·s），**但在 B/C 仍有 ~400 ms 的 queueing 由负载本身贡献**。
2. **geometry 在 B 起崩溃**：B/C/D 的 E2E 稳定在 ~680–687 ms、queue area 680–830 Mbit·s，
   与 A（284.8 ms、15.6 M）形成强烈反差；配对 E2E（C，s7）greedy−geom = **−265.9 ms**
   （1283 快/556 慢），ewma−geom = **−290.7 ms**。
3. flip rate 在 B/C/D 都是 0.26–0.28，与 A 相近；regret 只有 1.6–2.0 ms 均值。

## 4. 强同步 sweep（C regime，adv=1.0/0.1/0.02 s，vis_k=1，ctrl 2000 bit）

（`round2-results/sync.json/md`；3 policies × 3 levels，同 control 配置，vis_k=1。）

| policy | adv s | AoI p50 ms | flip | R mean ms | switch | W p50 ms | E2E mean | E2E p95 | isl_data area | ctrl Mbit |
|---|---|---|---|---|---|---|---|---|---|---|
| greedy | 1.0 | 510.0 | 0.2786 | 2.03 | 0.307 | 20.0 | **408.1** | 664.4 | 35.9 M | 24.4 |
| greedy | 0.1 | **56.9** | 0.2680 | 2.12 | 0.410 | 27.3 | **439.3** | 775.2 | 46.7 M | 232.2 |
| greedy | **0.02** | **16.6** | **0.2613** | 1.90 | **0.425** | 30.0 | **424.8** | 715.0 | 39.1 M | 1,162.0 |
| ewma | 1.0 | 505.8 | 0.2646 | 1.67 | 0.158 | 28.2 | **396.1** | 526.6 | 32.4 M | 24.4 |
| ewma | 0.1 | 56.1 | 0.2276 | 1.28 | 0.208 | 26.4 | **420.2** | 603.3 | 33.6 M | 232.2 |
| ewma | **0.02** | **16.4** | **0.2283** | 1.29 | 0.250 | 19.2 | **415.7** | 619.3 | 34.0 M | 1,162.0 |
| geom | 1.0 | 506.0 | 0 | 0 | 0.222 | n/a | 687.2 | 1188.8 | 830.7 M | 24.4 |
| geom | 0.1 | 56.5 | 0 | 0 | 0.224 | n/a | 675.2 | 1173.8 | 807.3 M | 232.2 |
| geom | 0.02 | 16.3 | 0 | 0 | 0.222 | n/a | 676.1 | 1177.3 | 809.2 M | 1,162.0 |

**FACT（本轮最重要的反证）**：
1. AoI p50 从 510 ms 压到 **16.6 ms（−96.7%）** 后，greedy 的 flip 只从 27.9% 降到 26.1%
   （ewma 26.5%→22.8%），**没有接近 geometry 的 0**；flip 在 20 ms 同步下仍是结构性现象。
2. E2E **没有改善，反而略差**：greedy 408.1→424.8（+16.7 ms）；ewma 396.1→415.7（+19.6 ms）；
   p95 同样变差（664→715 / 527→619）。0.1 s 更差（+31.2 / +24.1 ms）。
3. switch rate 随 fresher state 单调上升：greedy 0.307→0.410→0.425；ewma 0.158→0.208→0.250。
   更频繁的观测带来更多候选抖动，而不是更好的性能。
4. 控制开销：20 ms 广告需要 1,162 Mbit（vs 1.0 s 的 24.4 Mbit，**47×**），但每链路仍只占
   ~0.16% 容量；开销不是 E2E 变差的原因（数据面利用率远高于它）。
5. 几何策略是干净的负对照：AoI 506→16.3 ms 时 flip 恒 0，E2E 仅 687.2→676.1 ms（±11 ms 噪声），
   证明同步级别本身没有改变 data plane 的物理条件；上述 greedy/ewma 差异来自状态影响决策。
6. 因此"高频同步 + 简单策略"既没有消除 action flip，也没有买到性能：**shrink 方向**。

## 5. 方向性持续偏差 z=|δ|/ε（post-fix）

（`persistence/m2-c2-s7-persistence.md`、`m2-b-persistence.md`；δ 为逐决策单跳归因，Σδ=0。）

| cell | flip | 一侧搬运 Mbps | ε p50 corridor | z≥0.5 | z≥1 | τ_error p50 | τ_relax p50 | 非 flip E2E p50 |
|---|---|---|---|---|---|---|---|---|
| C geom | 0 | 0.00 | 4.75 | n/a | n/a | n/a | 420.1 | — |
| C greedy | 0.2654 | 267.0 | 34.5 | 0.708 | 0.528 | 0.92 s | 21.4 | 446.3 |
| C ewma | 0.2621 | 398.2 | 32.4 | 0.751 | 0.592 | 0.94 s | 20.8 | — |

z 分箱的 E2E 与 backlog（C greedy）：

| z bin | n | E2E p50 | backlog growth p50 | relaxed@+2 s |
|---|---|---|---|---|
| [0,0.1) | 854 | 360.8 | 0.00 | 0.871 |
| [0.1,0.3) | 691 | 453.9 | 0.00 | 0.877 |
| [0.3,0.5) | 353 | 495.8 | 0.00 | 0.873 |
| [0.5,1) | 1,190 | 499.6 | 0.00 | 0.759 |
| [1,∞) | 3,504 | 441.7 | **−0.76** | 0.764 |

**FACT**：即使 z≥1（方向性偏差超过该资源剩余容量），**backlog growth p50 ≈0 或为负**，
2 s 后 76–88% 的资源已回到不增长；非 flip 决策的 E2E p50（446.3）与 flip 各箱同量级。
⇒ 在本 regime，stale misrouting 产生的方向性偏差**没有被队列累积放大**。
**FACT**：geometry（0 flip）才是 corridor 逼近容量的一侧（τ_relax 420 ms、queue area 830 M）。

## 6. same-continuation causal regret（n=12 冻结）

样本：`fork-samples/c2-greedy-s7.json`（large Γ 4 / small margin 3 / large proxy R 3 /
sustained 1 / random control 1）；配置 `fork-configs/m2-c2-greedy-s7.json`；release R5；
证据 `fk-c-*`；结果 `round2-results/fork-results.{json,md}`。

- 12/12 fork 的 `branch_states_identical = true`（决策日志前缀指纹；**不是** full state hash）。
- **tagged packet 因果差（fresh − stale）符号混合、幅度可达数百 ms**：
  −486.9, −472.6, −177.0, −117.8, −43.3, −10.5, 0, 0, 0, +0.2, +101.9, +306.6 ms
  （6 个 fresh 更快 / 3 个 stale 更快 / 3 个无变化）。
- **cohort（全网受影响包）均值 ≈0**：12 个 fork 的 cohort mean 中位 0.0 ms，
  5 个 fresh 更好 / 5 个更差；单点改动在网络上被重新分配。
- **proxy regret 不能替代 causal regret**：proxy 中位 9.5 ms，而 tagged causal 差可达
  −487 / +307 ms（例：decision 4537 proxy 47.0 ms vs tagged −486.9 ms）；2447 proxy 2.45 ms
  而 tagged 0。
- 与历史 C5 一致（−51.8/+89.7/+234.6，1/3 为负）：**“用新鲜状态”不是单向解药**。

**FACT**：单点决策的因果后果可以很大（百 ms 级），但其符号取决于具体状态/后续路径，
在 population 上相互抵消；因此不能用 proxy regret 推断“fresh 更好”，
也不能用单点 fork 的幅度推断系统性能损失。

## 7. 结论（2026-11-01 冻结，post-fix）

**判断：SHRINK —— stale state 是动作层现象，不是本模型中的系统性能主瓶颈；高频同步不改变这一点。**

依据（全部 post-fix、多 seed、含 20 ms 同步）：
1. **动作层真实存在**：queue-aware 策略 flip 26–28%，Γ 单调；方向性偏差 267–398 Mbps 一侧搬运。
2. **但没有性能层转化**：flip 的 proxy regret 1.6–2.1 ms；z≥1 的方向性偏差 backlog growth ≈0
   （甚至为负），2 s 内 76–88% 资源已不增长；非 flip 决策 E2E 与 flip 各箱同量级。
3. **AoI 不是性能杠杆**：510→16.6 ms（30×）后 flip 仅 −1.8 pp（greedy）/−3.7 pp（ewma），
   E2E 反而 +16.7/+19.6 ms；switch 率单调上升。0.1 s 更差。
4. **网络性能由策略类别决定**：geometry 在 B/C/D 的 queue-blind 行为造成 680–830 Mbit·s 队列、
   E2E 678–687 ms；queue-aware 为 355–422 ms。这是与 stale-state 无关的效应。
5. **hysteresis 只在"抖不抖"上有用**：C 下 flip 18.9%（更低）但 E2E 456.8 ms（比 greedy 差 48 ms）、
   queue area 50.0 M；EWMA 在 20/56/510 ms 各同步级别都保持最低 E2E（396/420/416），
   是唯一稳定优于 greedy 的简单策略。
6. **same-continuation 单点因果**（n=12 冻结后见 §6）：tagged packet 的因果差可达数百 ms 且
   符号混合；预测器 proxy regret 与真实因果 regret 不同量级/不同量纲，不能互相替代。

**不能写**（Unknown 保留）：真实 Starlink 中该 AoI 分布/发生频率、真实业务占比、
near-capacity 定义之外的 regime、以及 formal 判据下的显著性。

**可保留的研究方向**（若继续）：decision robustness / sensitivity boundary
（Γ 判据 + 策略类别 + 方向性持续偏差），而不是"预测 stale state 以提升 E2E"。
