# CONCLUSION-ROUND2-20261031 — retain / shrink / kill（post-fix、校准负载、多 seed、20 ms 同步）

> 依据：`RESULTS-ROUND2-20261031.md`、`round2-results/*`、`persistence/*`、
> `fork-results.md`、`calibration/CALIB-20261031.md`、`calibration/CONTROL-FIX-COMPARISON.md`、
> `QUALIFICATION-20261031.md`（资格考试）、`COUNTERFACTUAL-AUDIT.md`。
> 所有矩阵结论使用 **post-fix** release（control 死信修复之后）；Round-1 pre-fix 数字不并列。

## 0. 最终判断

> **SHRINK。**
>
> 在本模型、本组实测工作区（A/B/C/D、合成 30/70 hotspot、140-sat）里，
> **stale state 是真实且可测的“动作层”现象，但没有形成可重复、系统性的“性能层”损失**；
> 把同步频率提高到 20 ms（AoI p50 = 16.6 ms）并不能消除 action flip，也不能改善 E2E
> （反而略差 +16.7 / +19.6 ms）。因此不能保留“状态时间错位是系统性能机制”的强主张。

不是 **kill**：即使 AoI 16 ms，queue-aware 策略的 truth-substituted flip 仍为 22.8–26.1%，
Γ 与 flip 的关系仍在，动作层现象没有被高频同步消掉。

不是 **retain**：没有任何一个预注册的判据同时满足“性能层可重复损失 + 高频同步不能消除 +
方向性队列放大”。方向性偏差 z≥1 的片段 backlog growth ≈0；flip 的 proxy regret 只有 1.6–2.1 ms；
E2E 对 AoI 不敏感甚至反向。

## Strict（由定义/仿真器机制必然推出，不依赖实验幅度）

S1. 资格考试确认的机制链（A–F）在本 release 上成立：逐包事件、方向独占链路、有限队列、
    `T_tx=S/R`、`T_prop=d/c`、tx/prop 分离、control 与 data 共用服务且非抢占、cache 只能读
    已到达且未过期的广告、compute 推进时钟、frozen/refresh 与 commit 重验按合同工作。
    ⇒ 任何 stale-state 效应必须经过“候选相对代价跨过 margin”这一条路径。

S2. `geometry` 的 J 不含状态项 ⇒ flip 恒为 0；本轮 C regime 3 seeds 全部为 0。
    （若以后非零，只能是实现错误或候选集被状态污染。）

S3. flip 是 **truth-substituted same-decision re-ranking**，不是 re-simulated counterfactual；
    same-continuation fork 的配对证据是**决策日志前缀指纹**，不是“完整 SimPy state 相同”。
    这两类数字不能互换表述。

S4. dead-letter 修复只删除永不服务的控制包（delivered ads/arrived 实例逐位不变、AoI 不变），
    但**会改变 data plane**（+2 跳、+35–43 ms E2E）：pre-fix 广告把几何不可用出口伪装成有积压。
    ⇒ pre/post 结果不可混；本文件全部 post-fix。

S5. 校准证明：queue-aware 策略下 realized 单链路 ρ 在 160→200 Mbps 只从 0.877 升到 0.943
    （次线性、绕行），目标带 0.95–0.99 在本 topology 上不可持续达到。C 档按实测 ε≈5.7 Mbps 定义。

## Defeasible（本组数据支持，但依赖模型/单场景/合成 trace）

D1. **action flip 在 A→B→C 都稳定在 26–28%**（greedy 27.7/27.0/26.1%，ewma 26.4/26.5%），
    与负载无关；proxy regret 1.6–2.1 ms 量级，远小于 E2E（285–422 ms）。Γ 分箱在高负载下仍单调
    （Round-1 已证；Round-2 的 C 数据同向）。

D2. **方向性偏差不等于队列放大**：C（ρmax 0.943）下 z≥1 的 flip 占 52.8%，
    但各 z 箱 backlog growth p50 ≈0（z≥1 箱 −0.76 Mbit/s），relaxed@+2 s 76–88%；
    非 flip 决策 E2E p50 与 flip 箱同量级。⇒ 本 regime 内，stale misrouting 被容量余量/后续跳吸收。

D3. **AoI 不是性能杠杆**：C 下 greedy AoI 510→16.6 ms（−96.7%）时 flip 27.9→26.1%，
    E2E 408.1→424.8（+16.7 ms，p95 664→715）；ewma 396.1→415.7（+19.6）且 flip 26.5→22.8%。
    switch rate 随 fresher state 单调升高（0.307→0.425 / 0.158→0.250）。
    0.1 s 甚至更差（+31.2 / +24.1 ms）。⇒ 高频同步在本模型里买不到性能。

D4. **网络性能由策略类别（是否看队列）决定**：geometry 在 B/C/D 的 E2E 为 685.9/678.0/626.1 ms、
    isl_data area 680/797/523 Mbit·s；queue-aware 为 354.7–422.3 ms、15–50 Mbit·s。
    配对 E2E（C，s7）：greedy−geom = −265.9 ms（1283 快/556 慢）、ewma−geom = −290.7 ms。
    这不是 stale-state 效应，而是 queue-blind 路由的拥塞集中。

D5. **hysteresis 只降低“抖动”，不降低“离最优的距离”**：C 下 flip 18.9%（更低）、switch 0.191，
    但 E2E 456.8 ms（比 greedy 差 48 ms）、area 50.0 M、proxy regret 2.08 ms（略高）。
    EWMA 在三个同步级别都保持最低 E2E（396/420/416），是唯一稳定优于 greedy 的简单策略。

D6. **same-continuation 单点因果可很大且符号混合**：n=12 的 tagged packet 因果差中已出现
    +306.6 / −177.0 / −43.3 / 0 / 0 ms；cohort（全网）均值≈0，说明单点效应在网络上被重新分配。
    proxy regret（6–26 ms）不能当作 causal regret 的代理。

D7. **多 seed 稳定性**：C regime 的 E2E 在 s7/8/9 的范围为
    geom 658.8–687.8、greedy 401.2–422.3、ewma 363.7–395.6；方向与量级稳定，
    seed 7 不是偶然（3 seed 结论同向）。

## Unknown（不得写成结论）

U1. 真实 Starlink 中毫秒—百毫秒 AoI 的分布/发生率、真实业务中的 prevalence：
    本组全是合成 `s1_stationary` 30/70 hotspot + 单 pulse；**external prevalence unknown**。
U2. 本 topology 上“可持续 ρ 0.95–0.99”不可得；其它拓扑/星座/流量形态下是否可得，未知。
U3. 因果 regret 的总体分布：n=12 是预注册样本，非总体；且宿主 harness 只保证决策日志前缀，
    不保证 full state。
U4. formal 判据下的显著性与论文级结论：本轮全部 diagnostic。
U5. 修复 dead-letter 后 data plane 改变，pre-fix 历史结论（Round-1）不可直接复用；
    若要历史对照需同 release 重跑。

## 对研究主线的净影响

- 保留：**Γ = Lτ/m 作为动作层敏感性判据**（margin 门控 + 状态变化速率），
  以及“策略类别比状态新鲜度更决定性能”的机制结论。
- 收缩：不能主张“stale state 造成系统性能损失”，也不能主张“提高状态同步频率改善性能”；
  在本模型里后者甚至略有害（chattering）。
- 下一步若要寻找性能层：应转向 **near-capacity 的其它定义/拓扑**（例如可控的瓶颈排队场景、
  更小 ε 的候选资源、或 queue-blind+stale 的组合），并且必须包含 **same-continuation 因果**
  与多 seed，而不是继续优化预测器。
- 论文方向：从 "predict stale state to improve E2E" 改为
  **"decision robustness / sensitivity boundary of distributed routing under delayed state"**。
