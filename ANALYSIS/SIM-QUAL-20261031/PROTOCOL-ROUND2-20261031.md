# PROTOCOL-ROUND2-20261031 — 校准负载轴 + 多 seed + 强同步 + 因果 regret

> 状态：**冻结于第一次 Round-2 matrix 运行之前**。本文件在 calibration 完成后定稿；
> 定稿后不允许根据 flip/regret 结果改 cell 定义、负载、seed、策略参数或判据。
> 允许的例外只有"运行失败后按新协议重开"，必须新文件 + 说明原因。

## 0. 本轮唯一问题

> 在**更接近容量边界、更强压力、更低 AoI** 的条件下，stale-state action flip 是否会
> 转化成**可重复、系统性的性能损失**？还是仍然只是动作层现象？

判定必须能给出：**retain（性能层机制成立）/ shrink（仅动作层）/ kill（连动作层都被高频同步消除）**。

## 1. 相对 Round-1 的修复（先做）

| # | 修复 | 证据 |
|---|---|---|
| F1 | 控制注入按物理可用性过滤（两个注入点；196/560 死方向不再入队/过期） | `CODE/leo_sim/kernel.py` `_control_direction_available`；tests `test_control_availability.py` 4 项；release `fb90c01…` |
| F2 | Round-1 W/switch 的 per-sat 口径错误（decisions 投影丢 `dst`）→ 从 `trace.jsonl.gz` join 回 dst，按 (sat,dst) 重算 | `sq_policy_analyze.load_trace_map`；`RESULTS-20261031.md §0b` |
| F3 | 控制面修复不改变 data plane：pre/post 同配置 run 的 fates/deliveries/packet_events 必须逐位一致 | `CONTROL-FIX-COMPARISON.md` |

**pre-fix 结果（Round-1，release `76427851…`）与 post-fix 结果（本轮的 `fb90c01…`/`4e7a7b5…`）分开报告，不混合。**

## 2. 校准（先测后冻结，禁止硬编码 Mbps 当 near-capacity）

方法：同一固定 trace（30/70 hotspot，无 pulse），queue_greedy，adv=1.0 s，看 [4,14) s 窗的
directed-ISL `λ=served_bits/window`、`μ=rate`、`ρ`、`ε=μ−λ`。依据实测选择 global load：

（数值见 `calibration/CALIB-20261031.md` 的实测表；本节在 calibration 完成后冻结为下表）

| regime | 目标 | 选用 global load | 实测 max corridor ρ | 实测 ε（top link） | 说明 |
|---|---|---|---|---|---|
| A stable | ρ 0.4–0.6 | 90 Mbps | 0.599（待 runner 复核） | ≈40 Mbps | slack 大 |
| B high-stable | ρ 0.8–0.9 | 160 Mbps | 0.877 | ≈12 Mbps | 接近容量 |
| C near-capacity | ρ 0.95–0.99 | 180 或 200 Mbps（**实测决定**） | 见 CALIB | 见 CALIB | 若 realized ρ 平台化，按实测 ε/队列增长报告并明确偏离目标带 |
| D over-critical burst | 短时 ρ>1 | B(160) + pulse 1.6×, 8.0–8.5 s | 见 burst run | 瞬时 | 有限窗口，自然结束 |

**注意（校准发现的机制）**：queue-aware 路由在接近容量前会绕行，realized corridor ρ 呈次线性
（140→0.818，150→0.846，160→0.877），因此 "ρ≥0.95 sustained" 可能在该策略类下不可达；
C 的最终定义以**实测 ε 与队列增长**为准，并同时报告 offered-hotspot/capacity 比值。

## 3. 冻结的 cell 集合（`sq-matrix2-20261031.json`，release `4e7a7b5…`）

- 校准延伸：`cal-180`、`cal-200`（用于决定 C）。
- A：`m2-a-{geom,greedy}-s7`（load 90）。
- B：`m2-b-{geom,greedy,ewma}-s{7,8}`（load 160）。
- C：`m2-{c1,c2}-{geom,greedy,ewma}-s{7,8,9}`（c1=180、c2=200；**执行测后选定的一个**）。
- C hysteresis secondary：`m2-{c1,c2}-hyst-s7`（只跑选定 C）。
- D：`m2-d-{geom,greedy,ewma}-s7`（load 160 + pulse 1.6×/0.5 s）。
- 强同步 sweep（vis_k=1）：`m2-sync{1000,0100,0020}-{c1,c2}-{geom,greedy,ewma}-s7`；
  **只跑选定 C 的那一组**（3 levels × 3 policies = 9）。

**vis_k=1 依据**：候选动作的被评分资源是"对端自身的出口队列"，对端是 1 跳邻居；控制广告只需
1 跳即可提供该状态。vis_k=1 不改变状态新鲜度（AoI 仍 = 半周期 + 1 跳传播），但让 20 ms 广告
在事件量上可负担。三个 sync level 共用 vis_k=1，故 sync 轴本身干净。

## 4. 预注册假设

- **H-A（负对照）**：geometry flip=0（构造），且 A regime 下各策略 E2E 差异主要来自路径选择而非 AoI。
- **H-B（动作层）**：greedy/EWMA 的 flip 率在 A→B→C 上升或至少不下降；Γ=Lτ/m 分箱仍单调。
- **H-C（方向性）**：flip 的方向性偏差 δ_r 只有在 `z=|δ_r|/ε_r` 较大的资源/窗口才伴随
  队列增长与 E2E 尾部恶化；z 小的 flip 被 slack 吸收。
- **H-D（持续性）**：错误持续时间 τ_error 与 queue relaxation τ_relax 的比值 ≥1 的片段，
  比 <1 的片段出现更大 queue growth / E2E 损失。
- **H-E（强同步）**：adv 1.0→0.1→0.02 s 使 AoI 单调下降（最终由传播+半周期主导）；
  若 flip 率与 causal regret 随 AoI 下降而显著下降 → shrink 方向；
  若 AoI 已降到几十 ms 而 flip/regret/E2E 不变 → 性能层不成立（shrink/kill 方向）。
- **H-F（因果 regret）**：same-continuation forced-action fork（n≥12，分层）给出的
  causal metric regret 与 truth-substituted proxy regret 同号/同量级；若显著更小，
  说明 proxy 高估了真实代价（必须报告）。

**判定规则（先写死）**：
- retain 需要：在 C（和/或 D）regime、≥2 seeds、修复后 control plane 下，
  同时看到 (i) 方向性持续偏差 z≳1 的片段、(ii) 这些片段的队列增长/E2E 尾部恶化可重复、
  (iii) EWMA/hysteresis 不能完全消除。
- shrink：动作层 flip 高但 (i)/(ii) 不成立，或 AoI 降到 ≤100 ms 后 E2E 无改善。
- kill：高频同步下连 flip 也基本消失（相对 geometry 的 0 接近）。
- 单 seed/单 cell 的差异不作为结论；paired differences 优先于独立均值。

## 5. 指标（Round-2 新增）

1. **方向性偏差**：对每个 flip 决策，wrong resource = chosen candidate 的 target resource，
   correct resource = fresh candidate 的 target resource；逐 0.5 s 箱累计
   `δ_r = (+bits into r) − (bits that should have gone to r)`，`Σ_r δ_r = 0`。
2. **容量余量**：`ε_r = μ_r − λ_r`（窗口实测；同 calibration）。
3. **z 分箱**：`z=|δ_r|/ε_r`，分箱 [0,0.1)/[0.1,0.3)/[0.3,0.5)/[0.5,1)/[1,∞)；
   分母加 floor（`ε_r ≥ max(1, 0.01μ)` 的 valid 子集单独报告）。
4. **persistence**：同一 (sat,dst) 连续同方向 flip 的 run（条数与持续时间，用决策时间戳，
   不用 bin 宽度）；按 resource 的持续同号 δ 片段。
5. **queue relaxation**：corridor links 上 backlog（queued + in-service 剩余，service 期间线性衰减）
   从峰值回落 50% / baseline band 的时间 τ_relax。
6. **flip / regret / AoI / margin / E2E / control overhead**：沿用 Round-1 分析器（已修 dst）。
7. **因果 regret**：same-continuation fork 的 tagged packet E2E 差、fixed cohort mean、
   affected packet count；与 proxy regret 对照。

## 6. 运行与证据

- R2 calibration：release `fb90c01…-96da8e3f…`，cells `sq2-cal-*`（见 CALIB）。
- R3 matrix：release `4e7a7b5…-aac7223…`，run-id 前缀 `sq3-`，证据
  `三端文件与同步治理/evidence/t1/sq3-<cell>-20261031-01/`。
- 并发 ≤2–3（VM cgroup 24 核 / 64 GiB）；失败 run-id 不复用。
- 所有 run `DIAGNOSTIC_COMPLETE` + `verified` 才进入分析。

## 7. 表述边界

- fork 的配对证据是**决策日志前缀见证**，不是"完整 SimPy state 相同"；不得写 CRN 由 harness 保证。
- calibration/矩阵是合成 trace 的机制结果，不得外推真实 Starlink prevalence。
- control 开销数字必须注明含/不含死信修复。

## 8. 允许的最终判断

本协议结束时必须给出且只能给出以下之一（附证据指针与限制）：
**retain / shrink / kill**，并明确：动作层现象、性能层是否成立、在哪个实测 regime 成立。
