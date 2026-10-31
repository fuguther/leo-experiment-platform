# PROTOCOL-ROUND3-20261031 — 响应强度 × 新鲜度 × 容量余量（冻结于任何 Round-3 运行之前）

> 状态：**冻结**。本文件写于 Round-3 任何 cell 运行之前；运行开始后不得改策略参数、
> 温度/保持网格、负载、sync 等级、seed 或判据。若协议本身有错，另开 rev2 并说明。
> 主环境 = Round-2 post-fix simulator（control dead-link 已修，release `fb90c01…` 之后）。
> pre-fix（Round-1）数字不得与任何 Round-3 结果并列。

## 0. 本轮要回答的问题

- **Q1** 策略对动态 queue state 的“响应强度”是否决定 stale-state 动作敏感性
  （policy sensitivity → P(stale≠fresh)）？是否有稳定规律。
- **Q2** 是否存在“性能最优的中间响应区间”（太迟钝 → 绕不开拥塞；过度响应 → 追逐瞬时状态/抖动）？
  不预设 U 型，实验判断。
- **Q3** freshness 与 decision stability 是否真实 trade-off（Round-2 观察到 AoI↓、switch↑、E2E↑）？
  是否对所有响应型策略普遍成立。
- **Q4** 当 capacity slack 被真正压到接近 0 时，temporal-error amplification 是否才出现？
  只在独立的受控瓶颈机制实验中验证，不用主星座负载硬造。

## 1. 主环境冻结（与 Round-2 完全一致，除策略/同步外）

| 项 | 冻结值 |
|---|---|
| topology | 140-satellite / 7-plane / 600 km / 98.6° Walker（`ta_pilot_140_adv1.yaml`） |
| links | 100 Mbps ISL、64 Mbit queue、constant rate、geometry_loss=true、GE off |
| traffic | `s1_stationary`，tokyo→singapore 0.70 + 其余 29 向对平分 0.30，packet 1 Mbit，emission 4–14 s |
| loads（Round-2 实测校准，冻结） | A=90 Mbps（ρmax 0.599）、B=160（0.877）、C=200（0.943）、D=140 + pulse{8.0 s, 0.5 s, ×1.6} |
| control plane（两个 sync 等级共用） | **vis_k=1、control packet 2000 bit、非抢占优先、TTL 10 s**；只改 `advertise_interval_s` |
| sync 等级（主比较） | **h=1.0 s** 与 **h=0.02 s**；100 ms 只做 selected-cell sanity |
| scoring / candidate / legality / fallback / commit | 与 Round-2 完全相同；所有策略只改 score→action 映射 |
| execution | compute_delay=0、refresh 模式、arm=now、predictor=hold_last、candidate_target_rule=arrival |
| seeds | 主矩阵 **7, 8, 9**；关键 cell 资源允许时扩 10, 11（不同 trace seed，同 cell 配置） |

**为什么 vis_k=1 对两个 sync 等级都成立**：被评分的候选资源是“对端自身的出口队列”，对端是
1 跳邻居；vis_k=1 的广告即可提供该状态。AoI 由 interval + 1 跳传播决定，与 vis_k=3 时用于打分的
状态新鲜度相同；vis_k=1 只减少传播到更远节点的广告，因而让 20 ms 档在事件量上可负担。
100 ms 与 20 ms 不改变 data queue discipline 或 candidate generation。

## 2. 策略谱系（六个，参数冻结）

| 策略 | 定义 | 冻结参数 |
|---|---|---|
| P0 `geometry` | J=tx+prop+remaining_prop（无动态队列） | — |
| P1 `queue_greedy` | J=现有 scorer total（hold-last 队列），argmin | — |
| P2 `queue_greedy_hysteresis` | P1 + per-(sat,dst) 保持：J_last ≤ J_best+η 则 hold | **η = 5 ms** |
| P3 `queue_greedy_ewma` | P1 但 queue 项用 arrived-sample EWMA 水平 | **α = 0.25** |
| P4 `queue_softmin` | J 与 P1 完全相同；P(a) ∝ exp(−(J_a−J_min)/T)，按此采样 | **T = 10 ms**（网格 2.5/10/40 ms） |
| P5 `queue_greedy_sticky` | J 与 P1 相同；per (sat, src, dst) 方向在 H 内保持 | **H = 100 ms**（网格 20/100/500 ms） |

- P4 的 RNG：每个决策由 `rngmod.link_stream(seed, "softmin:<sat>:<pid>:<decision_id>")`
  派生独立流取一个 uniform（不是全局流），因此同 seed/同 trace 可复现且不改变其它机制随机性；
  audit 记录 probabilities/entropy/sampled/uniform/T。
- P5 的 flow identity = **(src_grid_id, dst_grid_id)**（OD-pair 级 flowlet；不是 TCP flow），
  每 (sat, src, dst) 记录 (last_direction, last_switch_at)，只在真正提交 forward 时更新；
  合法性/fallback 仍走现有合同，kept 方向非法则 fallback 并记 override。
- **温度网格的冻结依据**：在 Round-3 任何性能结果产生前，从 **Round-2 C regime** 的
  decisions 读取 margin（used-state 1st–2nd）中位数：
  `m2-c2-greedy-s7/8/9 = 10.72/10.96/10.73 ms`、`m2-c2-ewma-s7 = 10.68 ms`
  （命令：`sq_policy_analyze.decision_stats` 的 `margin_used_ms.p50`）。取 T 网格为
  `{0.25, 1, 4} × 10.8 ms`，四舍五入冻结为 **{2.5, 10, 40} ms**，主结果取 **10 ms**。
  该选择只依赖 Round-2 的 margin 尺度，不依赖任何 Round-3/E2E 结果。
- P2/P3 参数不改；不做按 E2E 挑参数的 sensitivity；附录只跑预注册网格并在协议里标注为 appendix。

## 3. same-continuation fork 扩展（Round-2 续）

- 已有 n=12（`m2-c2-greedy-s7`）→ 扩到 **n≥36**：s8、s9 各新增 12 个（总 36）；资源允许再回填
  s7 额外 12 个到 n=48。全部使用**同一 release** 的 pinned resolved config（s8/s9 各自 config）。
- 分层（每 cell 12）：large Γ 6 / small margin 5 / large proxy regret 5 / long persistence 5 /
  high z=|δ|/ε 5 / random control 5 / low-Γ control 5（跨 cell 汇总后各层≥5）。
  high z 用 chosen resource 的 ε=μ−λ（link_service_windows 4–14 s 窗）近似。
- 记录：tagged packet causal gap、fixed cohort mean gap、affected packet count、downstream queue area、
  sign、magnitude、proxy regret、Γ、margin、AoI、chosen resource；`branch_states_identical` 必须为 true。
- 证据等级：branch witness 仍是 **decision-log prefix**；不得升级为 full SimPy state identical。
- 统计单位是 fork（decision），不得把 packet-level 观测当 iid 做显著性。

## 4. 主矩阵（Round-3）

- 主矩阵：6 policies × 2 sync × 4 loads × 3 seeds = **144 cells**；cell id
  `m3-<regime>-<policy>-h<sync_ms>-s<seed>`；合同 `sq-round3-20261031.json`（172 cells 含附录）。
- 执行优先级：
  - **Tier 1**：B/C × 6 policies × 2 sync × seeds 7/8/9 = **72 cells**（最可能暴露响应敏感性）。
  - **Tier 2**：A/D 再 72 cells。
  - **Tier 3**：关键 cell 扩 seed 10/11（geometry/greedy/EWMA/P4/P5 × B/C × 2 sync）。
  - 附录：100 ms sanity（B/C × 6 policies × s7 = 12）、P4 T 网格（B/C × 2 sync × 3 T × s7 = 12）、
    P5 H 网格（B/C × 2 H × s7 = 4）。
- 不接受“某策略表现差就提前停止”；所有已配置 cell 必须 natural_end + pullback verified。
- 运行身份：preflight → publish release → run（≤6 并发，VM cgroup 24 核/64 GiB）→ pull verified →
  index 行提交；失败 run-id 不复用。

## 5. 指标合同（必须全部记录）

- **信息侧**：AoI p50/p90/p95/p99 + generated→received / received→decision 分解。
- **决策边界**：margin、L、Γ=Lτ/m、P(a_stale≠a_fresh)（truth-substituted 口径）。
- **策略动态**：switch rate（per (sat,dst)）、action entropy（P4）、同方向 run length 分布、
  P5 的 forced_keep / voluntary_switch / override rate。
- **方向性错误**：δ、z=|δ|/ε、sign persistence、同向 burst length、queue growth、queue relaxation、
  remerge time；重点不是 z≥1 占比，而是这些错误有没有形成持续 queue growth。
- **网络性能**：mean/median/p95/p99 E2E、PDR、throughput、hops、ISL queue area、max/p95 utilization、
  control bytes/area。
- **响应敏感性轴**（行为代理，明确非严格数学 sensitivity）：switch rate、平均 run length、
  P4 entropy、near-tie 比例（margin<ε 阈值）；输出 vs flip/switch/E2E/p99，检查单调/U 型/平台/无关，
  不强行拟合 U 型。
- **freshness–stability tradeoff**：每策略 1 s→20 ms 的 ΔAoI、Δflip、Δswitch、ΔE2E、Δp99。
- **flip 后果分类**：flip 但 gap≈0 / fresh 更好 / stale 更好 / 快速 remerge / 持续方向性 bias /
  真正 queue amplification；报告 P(consequential|flip)、P(harmful|flip)（proxy 口径需标注）。

## 6. 统计规则

- 推断单位优先：run > seed > paired trace > same-continuation branch；packet-level 只用于机制分布。
- 报告 seed-level 值、mean/median、range、paired difference；可用 bootstrap 时明确抽样单位；
  3 个 seed 时写 “across-seed consistent/inconsistent”，不写统计证明。
- 不因 packet 数量大而伪造显著性；不对相关 packet 当 iid。

## 7. 受控瓶颈机制实验（与主环境分开）

- 极小 topology：两条平行 corridor、单一源/目的；每 corridor 单服务队列（μ 可调、背景 λ_bg、可选有限队列）。
- slack：ε/μ = 1−λ_bg/μ ∈ {0.50, 0.20, 0.10, 0.05, 0.02}（记录实际值）。
- temporal delay：τ ∈ {0, 20, 100, 500} ms，同一 arrival list 的 fresh vs stale 对照。
- 指标：δ、z=|δ|/ε、queue growth 斜率、mean/p50/p95/p99 delay、flip 率、fresh−stale paired delta。
- 判定：z≪1 被吸收？z~1 放大？z>1 持续 queue growth？若不能复现理论 amplification，
  必须先检查 δ 定义/队列实现/理论假设，并如实报告“机制模型未复现”，不得宣称 capacity-slack amplification。
- 表述边界：只能说“在受控瓶颈机制场景中确认/未确认理论边界”，不得外推为真实 LEO 普遍严重。

## 8. 最终判断规则（run 之前冻结）

- **Outcome A（中等响应最好）**：geometry 明显差于 queue-aware；P1 过度响应（高 switch/flip、E2E/p99 差）；
  P2/P3/P4/P5 中至少一个稳定优于 P1 且不差于 P3；则主线 = responsiveness–stability trade-off。
- **Outcome B（fresh + regularization 最好）**：h=20 ms 只在 P4/P5（或 P2/P3）下显著优于 h=1 s，
  而 P1 在 20 ms 下更差；则主线 = freshness 的价值依赖 decision regularization。
- **Outcome C（所有 queue-aware 差不多）**：queue-aware 明显优于 geometry，但 freshness/regularization
  对 E2E 只有小影响（< 策略类间差的 1/3）；则收缩为“动态队列信息是否使用重要，精确时间新鲜度不是一阶因素”。
- **正式 SHRINK/KILL 原 stale-performance 主张**：若 20 ms 无稳定收益、same-continuation 因果正负混杂≈0、
  多策略多 seed 下 freshness 效应远小于 policy-class 效应、C 下无稳定 amplification、
  simple regularization 已解释主要差异 —— 则正式放弃“预测 stale queue 改善一般 LEO E2E”作为主命题，
  保留 Γ 与 decision-boundary / responsiveness trade-off / capacity-slack conditional amplification 作为新主线。

## 9. 禁止事项（与用户任务书一致）

不重训 DDQN/TAP-DAR/新增深度模型；不改 topology/热点/负载救主实验；不挑 seed；
不删负结果；不按结果重选 P4/P5 参数；不把 controlled bottleneck 说成现实 Starlink；
不把 proxy regret 叫 causal；不把 flip 叫 performance loss；不把 20 ms interval 当 20 ms AoI；
不混 pre-fix/post-fix。

## 10. 交付

`ROUND3-PROTOCOL.md`（本文件）、`round3-results/`、`ROUND3-RESULTS.md`、`ROUND3-CONCLUSION.md`、
`controlled-bottleneck/`、`fork-samples/round3-expansion.json`、`round3-results/fork-results-n36.*`；
所有 run 证据 `evidence://t1/sq5-*`/`fk3-*`，索引提交；reasoning state 更新。
