# 状态年龄机制 pilot（2026-10-31）：第一批结果

> 性质：**DIAGNOSTIC / MECHANISM PILOT**（未走 formal 门，未预注册判据，不进论文主表）。
> 结论一律按 FACT / INFERENCE / UNRESOLVED 标注；单 seed、单轨迹，不得外推为普遍严重性。
> 复算脚本：`ANALYSIS/TIME-ALIGNMENT-20261030/ta_pilot_analyze.py`
> 机器可读摘要：同目录 `pilot-results/*.json`
> 设计/就绪证据：`PILOT-20261031.md`

## 0. 身份

| 项 | 值 |
|---|---|
| code commit | `fd1cb141048efd806f9b7dd242e315aa0e557e23`（branch `night/time-exp-20261031`） |
| release | `fd1cb141048efd806f9b7dd242e315aa0e557e23-06cd728e1eb8a946768cad02b9c0795bc03cd0dc38e321e325231ed2deec14f7` |
| VM 运行时 | python 3.11.15 / linux / aarch64，runtime contract `b3b548fd…`（runner 强制通过） |
| 场景 profile | `CODE/leo_sim/profiles/ta_pilot_140_adv1.yaml`（140/7/600 km/98.6°，100 Mbps ISL，64 Mbit 队列，1 Mbit 包，1 s 广告，bounded_linear h=4，candidate=arrival，GE off） |
| 流量 | `s1_stationary` 声明程序：tokyo→singapore 热点 0.70 + 其余 29 个有向对平分 0.30；高负载 cell 加 `pulse{8.0 s, 0.5 s, ×1.6}` |
| 时间轴 | 0–4 s warm-up，4–14 s 评估，8–8.5 s burst（仅高负载），14–20 s drain |

运行（全部 `DIAGNOSTIC_COMPLETE`，natural_end=true，回传 `verified`）：

| run-id | cell | arms | trace source SHA-256 | rows | wall |
|---|---|---|---|---|---|
| `ta-pilot-calib-20261031-01` | cal-040 | now | `d94f0893…931f4` | 427 | 60 s |
| `ta-pilot-calib-20261031-02` | cal-120 | now | `db9e6651…c14c2` | 1224 | 178 s |
| `ta-pilot-calib-20261031-03` | cal-200 | now | `2b27aac1…c667c` | 2047 | 430 s |
| `ta-pilot-arms-low-20261031-01` | low-040 | stale, now | `d94f0893…931f4` | 427 | 124 s |
| `ta-pilot-arms-high-20261031-01` | high-120-burst16 | stale, now, candidate | `64f505df…f022e` | 1255 | 514 s |

证据 URI：`evidence://t1/<run-id>`（本机证据根 `三端文件与同步治理/evidence/t1/`）。
高负载 cell 的三臂**共用同一次 trace 编译**（cell 级 trace-manifest，1255 行）；把
`resolved_config.time_alignment.arm` 掩码后三臂 config 逐字段相同（已核）。

## 1. 硬 gate（高负载 cell，post-warmup 4–14 s）

| # | gate | 实测 | 判定 |
|---|---|---|---|
| 1 | time-alignment 完整覆盖 ≥95% | 99.97 / 99.93 / 99.93%（stale/now/candidate） | PASS |
| 2 | 多候选 fallback ≤5% | 0.25% candidate slot（决策级 207–208/14.5k） | PASS |
| 3 | target work==0 的 slot ≤80% | 68.2 / 67.7 / 67.8% | PASS（但已偏高，见 §5） |
| 4 | 目标 corridor 实测 rho ≥0.7 | 0.744（`isl:128:127`、`isl:127:126`） | PASS（低于 0.8–0.9 目标） |
| 5 | access/compute/holding 非主瓶颈 | access wait p50 2.59 ms；holding p50 0；compute 0 server | PASS |
| 6 | NO_ROUTE / hop-cap 非主损失 | 120 Mbps：0 NO_ROUTE、0 溢出、1–2 in-system | PASS |
| 7 | 多候选决策足够 | 14,543 / 14,721 / 14,725 | PASS |
| 8 | probe 覆盖足够 | 行覆盖 97.5%；多候选决策标签覆盖 98.4–98.5% | PASS |
| 9 | 四臂 trace hash 一致 | 同一 cell trace `64f505df…f022e` | PASS |
| 10 | 除 arm 外 resolved config 逐字段一致 | 掩码 arm 后三臂 JSON 相同 | PASS |

低负载控制（40 Mbps，无 burst）同样满足 1/2/3/7/8/9/10；rho=0.300（15 条 link ≥0.2）。

## 2. 网络结果（post-warmup）

| 指标 | LOW stale | LOW now | HIGH stale | HIGH now | HIGH candidate |
|---|---|---|---|---|---|
| mean E2E (ms) | 291.0 | 291.2 | **305.6** | **319.8** | 320.0 |
| p50 / p95 / p99 (ms) | 288.9/427.7/439.1 | 288.9/427.7/435.6 | 295.4/461.7/501.3 | 301.0/475.1/536.3 | 301.0/475.1/536.3 |
| delivery | 427/427 | 427/427 | 1254/1255 | 1253/1255 | 1253/1255 |
| fate 损失 | 全 0 | 全 0 | 0 损失，1 in-system | 0 损失，2 in-system | 同 now |
| queue wait p50/p90/p99 (ms) | 0/0/0 | 0/0/0 | 4.2/18.0/35.2 | 4.7/19.6/37.2 | 4.7/19.5/37.2 |
| tx p50 / prop p50 (ms) | 180.1/108.6 | 180.1/108.6 | 180.1/108.6 | 180.1/108.7 | 180.1/108.7 |
| holding / access p50 (ms) | 0 / 2.59 | 0 / 2.59 | 0 / 2.59 | 0 / 2.59 | 0 / 2.59 |
| isl_data queue area (Mbit·s) | 0.8 | 0.8 | 8.3 | 9.0 | 8.9 |
| 平均跳数 / p95 / max | — | — | 18.35/28/40 | 19.15/28/40 | 19.16/28/40 |

- FACT（配对到同一 pid，1253 个共同送达包）：now − stale 平均 **+14.22 ms**，
  256 个更慢 / 94 个更快 / 903 个相同；now − candidate **+0.23 ms**（4 慢 / 3 快）。
- FACT（按发射时段）：pre-burst +12.13 ms（67 慢/24 快）；burst **−0.66 ms**（36 慢/24 快）；
  post-burst +18.01 ms（153 慢/46 快）。
- FACT（低负载同样配对）：now − stale **+0.18 ms**（2 慢 / 1 快）→ 负对照成立：低负载几乎无差异。
- FACT：同一 4–14 s 窗口内每条 link 的净流量差最大约 ±8 Mbps；now 把约 8 Mbps 从
  `41→40/42→41/43→42/44→43`（stale rho 0.616，headroom 38.4 Mbps）移到
  `56→57→58→59→40`（stale rho 0.293，headroom 70.7 Mbps），`delta/eps` 分别
  **−0.205 / +0.115**，全网净变化≈0。

## 3. predictor 在 target instant 的准确度（秒）

probe 真值定义 = queued(data+ctrl) + in-service remaining；只统计
`identity_source=used_state` 且 generation 一致的 ISL 行（downlink/无广告行排除）。

| arm | 行覆盖 | 多候选决策覆盖 | signed bias | MAE | median AE | p90 AE | p99 AE | ≤10 ms |
|---|---|---|---|---|---|---|---|---|
| high stale | 97.4% | 98.5% | −2.14 ms | 2.99 ms | 0.00 ms | 10.00 ms | 10.98 ms | 98.6% |
| high now | 97.5% | 98.4% | −2.02 ms | 3.09 ms | 0.03 ms | 10.00 ms | 12.87 ms | 97.6% |
| high candidate | 97.5% | 98.4% | −2.02 ms | 3.09 ms | 0.03 ms | 10.00 ms | 12.89 ms | 97.6% |
| low stale / now | 97.3% | 98.8% | −2.49 / −2.45 ms | 2.81/2.82 ms | 0 / 0 | 10.0 ms | — | — |

- FACT：误差的 p90 恰好 = 10 ms = **一个包的 serialization**；中位误差≈0。
  predictor 在亚包尺度几乎无偏，但量化为“整包”尺度。
- FACT：candidate ETA（query − snapshot）p50 = 17.28 ms，p90 = 17.28 ms，p99 = 27.28 ms。
- FACT：now→candidate 的 predicted work 差（同一决策同一候选）p50 = 0 ms，
  p90 = **0.008 ms**，仅 22.8% 非零 ⇒ 预测在额外 17 ms 上看不到差别。

## 4. margin、差分 D、oracle 重排（离线，绝不参与在线路由）

truth-oracle：只把 `resource_work_s` 换成 `truth.work_ahead_bits / service_rate_bps`，其余分数项冻结。

| arm | 决策 | ranking reversal | action reversal | signed `D>M` | 与真实反转一致 | M p50 | D p50 | \|D\|/M p50 / p90 |
|---|---|---|---|---|---|---|---|---|
| high stale | 14,529 | 27.61% | 27.79% | 26.73% | 99.12% | 11.46 ms | 4.72 ms | 0.323 / 3.21 |
| high now | 14,701 | 27.77% | 27.95% | 26.54% | 98.72% | 11.61 ms | 4.44 ms | 0.323 / 3.40 |
| high candidate | 14,705 | 27.72% | 27.90% | 26.51% | 98.70% | 11.61 ms | 4.45 ms | 0.323 / 3.41 |
| low stale | 4,942 | 27.58% | 27.74% | 27.22% | 99.64% | 12.91 ms | 5.82 ms | 0.323 / 5.16 |

- FACT：**三臂的 reversal 率几乎相同**（27.6–27.8%），即 stale/now/candidate 的预测
  与真值的偏差在统计上不可区分；bounded_linear 的外推没有带来可测的准确性差异。
- FACT（stale，按 margin 四分位）：Q1 (<3.97 ms) reversal **68.3%**、`D>M` 65.3%；
  Q2 41.7%；Q3 0.3%；Q4 0.2%。margin<2 ms 的决策占 **14.1%**，<5 ms 占 30.3%。
  ⇒ 链条“D 跨过 M”只发生在近并列尾部，且被路径长度先验（margin p50 11.5 ms）挡住。

## 5. 方向性 / 持续性 / 队列放大

- FACT（决策级）：stale 的方向偏差 `b = |N(A→B)−N(B→A)|/N_flip = 0.558`，now 0.489，
  candidate 0.490；最长同向连续 run = **6 个决策 / 82.3 ms**，随后消散。
- FACT（网络级，时间分辨）：now 在两个 ~1 s 窗口（[7,8) 与 [12,13)）把整条链的流量从
  繁忙链切到备用链（繁忙链 67→22 Mbps，备用链 23→59 Mbps），其余时间与 stale 相同；
  burst 窗口 [8,9) 两臂都重新走繁忙链。⇒ **振荡/来回切换，而不是单向持续偏流**。
- FACT：目标资源真实队列很浅：probe truth 在主要 corridor 资源上 mean 0.4–1.0 Mbit
  （0–1 个包）；`resource_work_s == 0` 的候选 slot 68%；ISL data queue area
  stale 8.3 vs now 9.0 Mbit·s（+8%），无 ISL_QUEUE_OVERFLOW，无 NO_ROUTE。
- INFERENCE：在 rho=0.744、64 Mbit 缓冲、1 Mbit 包的条件下，0.5 s ×1.6 burst
  只造成 ~1 个包的瞬时排队，无法形成持续 queue growth；因此“误路由→拥塞放大”链条
  **在本 regime 断在“队列放大”一环**，而不是断在决策翻转一环。

## 6. 200 Mbps 点为什么被拒绝

`cal-200` 达到 rho=0.943（2 条 link ≥0.9），但同时出现：平均跳数 **26.9**（120 Mbps
时 18–19）、最大 63 跳、87 个包在 stop 时仍未送达、14 个 NO_ROUTE、交付 1946/2000。
这是路由器走向长绕行/接近 hop-cap 的路径病态，不是要被解释的 ISL 排队机制。
⇒ 本轮高负载点**冻结在 120 Mbps（rho 0.744）**，不拿 200 Mbps 的结果解释“状态过期导致拥塞”。

## 7. 十个问题的回答

1. **代码能否执行四种时间语义？** FACT：能。四臂在代码中存在且只差查询时刻；本跑执行了
   stale/now/candidate 三臂（common 未跑，需另一次 publish）。candidate 的
   `query_targets==eta_targets` 占 99.91%，trace/config gate 9/10 通过。
2. **predictor 在 target instant 准不准？** FACT：candidate 臂 MAE 3.09 ms、median 0.03 ms、
   p90 10.0 ms（=1 包）、p99 12.9 ms、bias −2.0 ms。准确到“一个包”的量化极限。
3. **stale 信息是否跨过 margin 改变动作？** FACT：是，但只在近并列尾部：整体
   ranking reversal 27.6%、`D>M` 26.7%；margin Q1（<4 ms）68.3%，Q3/Q4 ≈0.3%。
   跨臂实际动作不一致率仅 2.94%（stale vs now）。
4. **错误随机还是持续有向？** FACT：弱方向性（b≈0.49–0.56），最长同向 run 82 ms；
   网络级表现为两个 ~1 s 窗口的**来回切换**。INFERENCE：是振荡，不是羊群式持续偏流。
5. **是否形成额外 queue / congestion？** FACT：没有。queue area +8%，目标队列 0–1 包，
   无溢出、无 route 损失；交付率 99.84–99.92%。
6. **E2E/p95/p99 是否变化？** FACT：高负载 now 比 stale 差 +14.2 ms mean（p95 +13.4 ms，
   p99 +35 ms），但方向与假设相反（更新鲜状态更差）；candidate≈now；低负载无差异。
7. **Candidate 相比 Now 的增量？** FACT：≈0（0.05% 动作差，配对 E2E +0.23 ms，
   predicted work 差 p90 0.008 ms）；ETA 只有 17.3 ms。
8. **Candidate 无收益的原因？** FACT/INFERENCE：future-use gap 17 ms 小于 1 包
   serialization（10 ms）且远小于 state age（0.5 s）与 advert 周期（1 s）；目标队列本身
   只有 0–1 包的变化；margin 由路径长度先验主导。predictor 不是瓶颈（它已到量化极限）。
9. **有无其他机制污染？** FACT：120 Mbps 高负载点没有 NO_ROUTE/hop-cap/access/compute
   污染；200 Mbps 标定点有（平均 26.9 跳、max 63、87 in-system、14 NO_ROUTE）→ 拒绝。
10. **下一轮唯一最值得做的实验？** 在同一 140 星场景内换一条**两跳等价、无长绕行**的对称
    corridor，用离线参考路由先验证：两个候选的 remaining propagation 差 < 1 ms，并找到使
    共享链路 rho≈0.85–0.9 的 offered load；只改 OD 与 load，不改 scorer/advert/predictor。
    目的：让 margin 由队列项而不是路径长度先验决定，检验 `D>M → 翻转 → 队列放大`
    是否在正确的危险区出现。

## 8. 必须原样保留的限制

- 单 seed、单轨迹、单 regime；无统计显著性声明。
- 只验证 target instant 上的 state prediction；**不能**完整验证 ETA 真值
  （kernel 无可靠 `t_target_resource_use` 里程碑，未为此改 kernel）。
- target probe 的 downlink/无广告行不能当 prediction label；本报告只使用
  `identity_source=used_state` 的 ISL 行。
- 目标队列 68% 候选 slot 为 0，意味着本运行对“队列状态价值”的检出力有限。
- 不是 formal run；不代表论文结论；也不能写成“状态过期严重/不严重”的普遍判断。
