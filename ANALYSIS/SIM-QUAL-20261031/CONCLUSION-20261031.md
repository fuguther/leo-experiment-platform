# CONCLUSION-20261031 — Strict / Defeasible / Unknown

> 依据：`QUALIFICATION-20261031.md`（A–F 资格考试）、`RESULTS-20261031.md`（12 cell 诊断矩阵）、
> `AUDIT-MECHANISMS.md` / `AUDIT-EXISTING-TESTS.md` / `AUDIT-HARNESS.md`（独立审计）。
> 证据边界：**DIAGNOSTIC**、单 seed（7）、单场景（140-sat Walker + 合成 30/70 hotspot trace）。
> 本文件只做结论分层，不把 Unknown 写成正结论。

## Strict（由定义/仿真器机制必然推出，不依赖实验幅度）

S1. 在已通过资格考试的实现里：`T_tx = S/R`、`T_prop = d/c`、serialization 与 propagation 分离、
    传播期间链路可服务下一包；control 与 data 共用同一 ISL 服务且非抢占优先。
    （事件级验证：A/B/C 组测试，`qualification-evidence/`。）

S2. 路由能"读到"的远端状态只能是 `generated_at ≤ received_at ≤ now` 且未过 TTL 的已到达广告；
    `AoI(now) = now − generated_at`。因此任何 stale-state 效应都必须通过"候选相对代价的变化"
    才能发生。（E 组测试 + `control.CacheEntry.valid_at` / `LocalCache`。）

S3. 对任意两个候选 a,b 与阈值规则：若在决策使用的状态上 `J_b(t) − J_a(t) = m > 0`，
    且到使用时刻真实代价变化满足 `|ΔJ_ab| < m`，则动作不可能因状态误差而翻转。
    反之，翻转（在 truth-substituted 口径下）必然意味着 `ΔJ_ab` 跨过了 margin。
    这是 `Γ = Lτ/m` 与 `R = (D−m)_+` 的定义性内容，不是实验结果。

S4. `geometry` 策略的 J 不含任何队列状态项 ⇒ 在同一候选集下，"fresh state"不可能改变其排序，
    flip 必须恒为 0。本矩阵中 4 个 geometry cell 的 flip 全为 0，与此一致；
    若以后 geometry 出现非零 flip，只能是候选集/合法性规则被状态污染，应视为实现错误。

S5. **控制面的死信是建模事实**：广播树沿逻辑拓扑入队，196/560 个有向方向超过 3000 km 不可用，
    这些控制包永不被服务并 TTL 过期（可用链路上 ctrl 队列面积≈0）。
    因此 `control.bits.offered` 与 `isl_ctrl` 队列面积包含死信，**不能**当作真实控制拥塞证据。
    （本地 4 s 诊断 + 20 s run 的 control ledger；见 QUALIFICATION §12。）

S6. 本报告的 flip 是 **truth-substituted same-decision re-ranking**，不是 re-simulated
    counterfactual；它回答"同一决策在真实状态下的 argmin 是否不同"，
    **不能**升级为"同一历史下重放 A/B 的因果后果"。因果 regret 只能由
    `counterfactual.replay_with_forced_action` / `minimal_branch_compare` 一类的
    same-continuation fork 给出（本轮未做规模化的这类实验）。

## Defeasible（在本矩阵/本 regime 下被支持，但依赖模型、单 seed、合成 trace）

D1. **action flip 在本 regime 中是结构性的、不是噪声**：queue_greedy 在 high load 的
    multi-candidate 决策里 flip 27.8%（14,752 个决策中 4,098 个）；Γ 分箱单调
    （Γ<0.5: 15.8% → Γ≥5: 82.8%），说明翻转随"状态变化速度 × AoI / margin"上升。
    但 Γ<0.5 仍有 10–16% flip ⇒ Γ 是一阶预测器，不是精确判据（离散包事件未被平滑量捕获）。

D2. **flip 的 regret 很小，且负载不敏感**：flip 的 regret 均值 1.5–1.9 ms、p90 6.7–7.9 ms，
    相对 E2E p50（约 300 ms）不足 1%；high 与 low 的 flip rate 几乎相同（27.8% vs 27.6%）。
    ⇒ 在本 regime，"有 flip"不等于"有可测性能损失"。

D3. **把 AoI 压低 8.7× 不改变网络性能、也不消除 flip**：adv 1.0 s → 0.1 s 使 AoI p50
    487.7→56.3 ms（其中 50 ms 是半周期、7 ms 是传播），但：
    - greedy/hyst/ewma 的 flip 仍为 22.3% / 16.4% / 19.6%（只降 3.5–8.9 pp）；
    - E2E 变化为 −1.7/+7.5/−17.6/+2.3 ms，没有系统性改善；
    - greedy 的 per-(sat,dst) switch rate 反而从 0.187 升到 0.316（更频繁观测 → 更多候选抖动）。
    ⇒ "高频同步 + 简单稳定机制"在 action 层面**不能**基本消除问题；在性能层面，
    本 regime 本来就没有可被它消除的损失。

D4. **策略类别（是否看队列）是网络性能的主因，stale state 不是**：
    high load 下 queue-aware 策略比 geometry 快 46–66 ms（配对 1,255 包，763–900 个更快），
    差在"把流量从最短路分散出去"（isl_data 队列面积 118.9 M vs 7.8–12 M bit·s；hops 17.5 vs 18.8–21.1）；
    **low load 下符号反转**：geometry 比 greedy 快 20.7 ms（配对 427 包）。该收益与 AoI 无关。

D5. **hysteresis 降低的是切换/抖动，不是离最优的距离**：
    η=5 ms 时 rule-relative flip 27.8%→18.2%、per-(sat,dst) switch 0.187→0.071，但相对 truth-optimal 的
    偏离率反而 29.7%（greedy 27.7%）、regret 均值略升（1.87 vs 1.66 ms），
    且 high load E2E 305.6→339.6 ms、hops 19.3→21.1、1 个 NO_ROUTE；low load 下 E2E 反而略好
    （291.0→287.2）。**EWMA(α=0.25) 不降低 flip（28.5%）但给出最好的 high-load E2E（295.0）
    与最低队列面积。**
    ⇒ "用简单稳定机制把问题解决"在本 regime 不成立；稳定机制改变的是"在哪儿堵"与"抖不抖"，
    不是"离最优多远"。

D6. **多候选 + 小 margin 是风险集中区**：margin p50 约 11–13 ms，而 queue 项变化常远小于它；
    flip 集中在 truth margin 小的决策，W（连续错误决策时长）p50 29–159 ms 后回到 fresh-optimal。
    ⇒ 与已有 O3（margin 门控）一致，不是持续单向偏流。

## Unknown（当前无法回答，不得写成结论）

U1. **真实 Starlink 中这种毫秒—百毫秒 AoI 的分布、发生率、对真实业务性能的贡献**：
    本矩阵使用合成 `s1_stationary` 30/70 hotspot + 单 pulse trace，不是真实包级 trace；
    `external prevalence unknown`。

U2. **多 seed / 多负载档 / 近容量与超临界窗口的稳定性**：本轮只有 seed 7、40/120 Mbps 两档；
    high-load 的 hysteresis 绕行代价与 NO_ROUTE、fresher-state 的切换抖动，都需要多 seed 复核。

U3. **因果 regret 的规模化证据**：本轮 R 是 truth-substituted 口径；历史 3 个 same-continuation
    fork（−51.8/+89.7/+234.6 ms）只证明"单点动作可造成该量级后果"，不代表 population 平均；
    n≥12 的 fork 尚未做。

U4. **形式判据（formal）下的显著性**：本矩阵是 DIAGNOSTIC，不满足 formal 门/预注册显著性；
    任何"策略 A 更好"的推断都未经过 formal 流程。

U5. **控制面死信的修复后行为**：本轮未修（保持与 ta-pilot 可比）；修复广播树的 availability
    过滤后，控制开销、可达性与高负载控制拥塞的结论可能变化。

U6. **真实控制面协议可行性**：adv=0.1 s + 2000 bit 广告在真实星上控制链路的开销/可靠性未验证。

## 与研究主线的净影响

- 支持：**"状态错位会改变动作"这一机制在平台里真实存在且可测**；`Γ=Lτ/m` 是有用的一阶判据；
  margin 门控是主因；策略类别决定网络性能。
- 收缩：**"状态错位造成可测的端到端性能损失"在本 regime 未得到支持**——AoI 降低 8.7× 不改变
  E2E，flip 的 regret 只有 ms 量级且负载不敏感。此前 C5 的三个 fork 只证明存在性，不是普遍幅度。
- 因此本轮的诚实结论是：
  > 在合成 140-sat / 中高负载 / 毫秒—百毫秒 AoI 的受控场景里，
  > stale state 是**动作层**的真实现象（flip 16–28%，随 Γ 单调），
  > 但在本 regime 里它不是**性能层**的主要瓶颈；把 fresh state 当作解药没有收益。
  > 若要把研究推进到"性能层严重性"，必须先找到 margin 更小、状态变化更快、或
  > 错误更持续的 regime（近容量/超临界），并用多 seed + formal 判据验证——而不是继续优化预测器。
