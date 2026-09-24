# T1 压力窗口：解析型单 OD 走廊（实测特征化）

> **SUPPORTING**；最后核验：2026-09-23。本文是 `CODE/leo_sim/profiles/t1_pressure_corridor.yaml` 的实测特征化记录，用于支撑 `T1-PRESSURE-WINDOW-PASS`。当前合同与门禁状态以 `ANALYSIS/T1-MEASUREMENT-PROTOCOL.md` 与 `EXPERIMENTS/experiment-program.yaml` 为准。

## 1. 为什么需要它

2026-09-23 分层审查判定 `EXP-20260829-GLOBAL-PRESSURE-BRACKET-R02` **不适合作为 T1 主压力场景**：10/20/40/80 Mbps 下没有饱和有向 ISL，也没有持续 hotspot；80 Mbps 的 1 s active-window p99 利用率约 0.5%、最大约 1%。

## 2. 构造（对照审查报告的三条要求）

| 要求 | 本 profile 的做法 |
|---|---|
| 固定 OD corridor / hotspot | `endpoints.sites` 只有两个（equator / north），全部报价负载落在**一个 OD 对**上，而不是摊到全球人口代理上 |
| access 不限流 | `uplink_rate_mbps = downlink_rate_mbps = 1000`（远高于 `isl_rate_mbps = 10`），且 `unavailable_policy: queue`（无可见星时排队而非丢包） |
| constant PHY | `rate_model: constant` + 固定 `isl_rate_mbps`，每包服务时长精确可算 |
| **可解析** | 窗口档位由**单一字段** `demand.offered_mbps` 设定，见下表的线性标定 |

几何、站点与控制面取自已知可用的 `smoke` profile（保证走廊可达），只改负载、PHY 速率与接入速率。

**关键配置陷阱（已修，记录以免重犯）**：必须设 `execution.available_capacity_interval_s`。不设时 per-link `utilization` 的分母是**服务窗口自身之和**，对任何恒速服务都读出 ~1.0，**完全不能反映链路有多忙**。设了可用容量采样之后才是真实占空比。

## 3. 实测（window = 300 s，24 星 / 3 面，isl_rate = 10 Mbps，constant）

| offered (Mbps) | admitted (Mbps) | delivered (Mbps) | **有向 ISL 最大利用率** | util/offered |
|---|---|---|---|---|
| 2.0 | — | 0.85 | 0.0443 | 0.0222 |
| 4.0 | — | 1.84 | 0.0977 | 0.0244 |
| 8.0 | — | 3.63 | 0.1870 | 0.0234 |
| 12.0 | — | 5.75 | 0.2910 | 0.0243 |
| 20.0 | 9.97 | 9.66 | **0.4953** | 0.0248 |
| 40.0 | 19.50 | 9.26 | **0.5653** | 0.0141 |
| 80.0 | 39.85 | 5.86 | **0.5567** | 0.0070 |

## 4. 结论与限制（如实记录，不夸大）

**成立的部分：**
- 该窗口确实产生**有向 ISL 压力**：最大利用率达 **0.55–0.57**，约为 R02 bracket（~0.005–0.01）的 **55–110 倍**，且随时间持续（非瞬时尖峰）。
- 在 **offered ≤ 20 Mbps** 的区间，利用率对负载**高度线性**：`util_max ≈ 0.0235 × offered_mbps`（比值 0.0222–0.0248，偏差 < 11%）。**这就是"可解析"的落点**：给定目标利用率即可反解所需 offered。
- 无丢包的区间存在（offered ≤ 12 时 fate 只有 DELIVERED / IN_SYSTEM_AT_STOP）。

**未成立 / 限制：**
1. **"access 不限流"只部分成立**。offered > 20 Mbps 后 `ACCESS_QUEUE_OVERFLOW` 与 `HOLDING_QUEUE_OVERFLOW` 显著增长，**delivered 反而下降**（9.66 → 9.26 → 5.86 Mbps），利用率在 ~0.56 处**平台化而非趋向 1.0**。原因是端点对单颗星的可见窗口是间歇的，接入队列（64 Mbit）在高负载下被填满——**接入侧成为共同瓶颈，而非星上 ISL 单独受压**。因此本 profile 目前只能作为"中高压力窗口"，**不能**用来声称"ISL 饱和"。
2. **单链路闭式预测 `util = F / isl_rate` 在本拓扑下不成立**。实测比值恒为 ~0.0235 而远大于 0，说明流量在多条平行/随拓扑演化的路径上摊开了，而不是固定串在一条链上。**能站得住的解析式是经验标定线 `util_max ≈ 0.0235 × offered`**，不是教科书式的 `F/rate`。
3. 上表是**单种子**（seed 7）结果；未做多种子重复，故**不得**据此给出统计结论。

## 5. 后续（若要达到饱和窗口）

- 收紧走廊：降低 `num_satellites`/`num_planes` 或缩短 `max_isl_km`，使 OD 路径被迫串到尽量少的链路上，让 `F/isl_rate` 重新成为有效上界；
- 或把接入侧真正移出瓶颈：增大端点接入队列、放宽关联约束；
- 以上任一改动后**必须重跑本表**，因为窗口的标定线会变。
