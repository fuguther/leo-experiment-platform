# CONTROL-FIX-COMPARISON-20261031 — control 死信修复前后：控制面 / AoI / **data plane 逐位一致性**

> 性质：**DIAGNOSTIC 对比**（pre-fix Round-1 归档证据 vs post-fix 新证据）。不是 formal run，不进论文主表。
> 目的：回答"control 死信修复是否只清掉了无效包，还是也改变了数据面"。
> 机器可读：`calibration/control_fix_compare.json`；复算脚本 `calibration/control_fix_compare.py`（只读证据根，输出仅该 JSON）
> 修复提交：`fb90c01` "fix(control): skip physically unavailable ISL directions at control injection"（两个注入点加 `geometry.isl_available` 过滤 + `control_geometry_unavailable_skips` 计数器）
> post-fix release：`fb90c01ef…-96da8e3f…`；pre-fix release：Round-1 `76427851…`

## 0. 结论先行

1. **控制面：修复完全达到目的。** 两个 cell 都是"被跳过的死信数 = 消失的注册数"，且**所有曾被服务的控制包一个不少**：
   - `pf-high-greedy`：registered 44,240 → **32,124**（−12,116 = `skips`），expired 6,556 → **0**，`isl_ctrl` 队列面积 848.3 Mbit·s → **86.6 kbit·s（−99.99%）**，delivered bits **逐位不变**（291,812,840）。
   - `pf-sync-greedy`：registered 436,800 → **317,600**（−119,200 = `skips`），expired 60,196 → **0**，`isl_ctrl` 面积 2,860.2 Mbit·s → **330.3 kbit·s（−99.99%）**，delivered bits **逐位不变**（1,012,604,400）。
2. **AoI：基本不变。** high：p50 0.4835 → 0.4828 s、p90 0.9135 → 0.9119 s（−0.7/−1.7 ms）；sync：p50 0.0570 → 0.0570 s、p90 0.0972 → 0.0973 s。**死信从来没有占用有用广告的链路服务**，所以它们不影响信息新鲜度 —— 这一点与"死信在几何不可用链路上排队"一致。
3. **data plane：不一致，而且不是数值噪声。** 两个 cell 的 fates / deliveries / packet_events / link_service_windows **全部 hash 不同**：
   - 平均跳数 **+1.98 跳（high）/+2.49 跳（sync）**；forward 决策 +10.8% / +13.3%；`link_service_windows` +9.8% / +12.0%（与 hop 增量一一对应）。
   - E2E 均值 **+35.0 ms（305.6→340.6, high）/ +42.8 ms（313.1→355.9, sync）**；配对后 503 更慢 / 150 更快 / 600 相同（high）。
   - 交付结果：high 交付 1,254 = 1,254（不变）、0 丢包；sync 交付 **1,253 → 1,252（−1）**，2 个包 fate 从 DELIVERED 变 IN_SYSTEM_AT_STOP、2 个相反。**两 cell 都没有任何 NO_ROUTE / 队列溢出 / deadline 丢包。**
4. **原因已定位到信息，不是调度：死信把"几何不可用的对端出口"伪装成有排队的资源，修复后这些出口看起来是空的，于是决策把包送进去、在对端 hold 再重选，多走约 2 跳。** 证据链见 §4 —— 最强的一条：pre-fix 决策里被当作"对端出口有积压"的样本中 **40.9%（high）/ 45.6%（sync）恰好等于整数个控制包线长**（9,184 / 3,184 bit），post-fix 该类样本 **精确为 0**。
5. **对 Round-3 的直接含义：Round-1 的 pre-fix 数据面数字不能与 post-fix 结果混用**（同一 cell 同一 trace 同一 seed，E2E 相差 35–43 ms、跳数差 2）。要么用 post-fix release 重跑 Round-1 的 8 个基线 cell，要么在所有比较里显式声明"pre/post 不可混"。**"修复只删无用包、数据面不变"这个假设被实测否证。**

## 1. 配对有效性（先证明两组可比，再谈差异）

| 项 | pf-high-greedy | pf-sync-greedy |
|---|---|---|
| pre run-id / cell | `sq-p1-high-greedy-20261031-01` / `p1-high-greedy` | `sq-p2-sync-greedy-20261031-01` / `p2-sync-greedy` |
| post run-id / cell | `sq2-pf-high-greedy-20261031-01` / `pf-high-greedy` | `sq2-pf-sync-greedy-20261031-01` / `pf-sync-greedy` |
| `trace_source_sha256` 相同？ | **是** `64f505df72cd…f022e`（1,255 行） | **是** `64f505df72cd…f022e`（1,255 行） |
| resolved config 差异 | **仅** `demand.csv_path`（`p1-` vs `pf-` 前缀，cell id 派生） | 同左 |
| 关键冻结项 | adv 1.0 s / ctrl 8,000 bit / `arm=now` / `state_policy=queue_greedy` / `predictor=hold_last` / `state_source=advertised` / pulse{8.0,0.5,×1.6} / D4 | adv **0.1 s** / ctrl **2,000 bit**，其余同 |
| 诊断开关差异（已知、非同等） | pre `truth_probe=true`，post `false` | 同左 |

配置逐字段比对见 `control_fix_compare.json → pairs[].pairing.config_diff_masked`（两个 pair 都只有 `demand.csv_path` 一项）。
**关于 `truth_probe` 的差异**：按 kernel 契约（`target_probe.py`）探针只读、不消耗业务随机数、不占业务资源；且本报告 §4 的差异全部落在**广告内容**（`used_state[*].bits`）上，探针不可能写入该字段。因此该开关差异**不是**本次 data-plane 差异的来源；但它仍是 pre/post 之间唯一的非同等项，后续若要"严格同开关"复现，应把 pre 侧也按 `truth_probe=false` 重跑（或把 post 侧打开），成本每 cell ~3–4 min。

## 2. 控制面 pre/post

| 指标 | high pre | high post | Δ | sync pre | sync post | Δ |
|---|---|---|---|---|---|---|
| snapshots_created | 2,940 | 2,940 | 0 | 28,000 | 28,000 | 0 |
| registered / entered_queue | 44,240 | 32,124 | **−27.4%** | 436,800 | 317,600 | **−27.3%** |
| **expired** | 6,556 | **0** | −100% | 60,196 | **0** | −100% |
| in_system（stop 时） | 5,924 | 364 | −93.9% | 59,004 | **0** | −100% |
| arrived / transmission_completed | 31,760 | 31,760 | 0 | 317,600 | 317,600 | 0 |
| transmission_started | 32,124 | 32,124 | 0 | 317,600 | 317,600 | 0 |
| overflow / lost / geometry_lost | 0 / 0 / 0 | 0 / 0 / 0 | 0 | 0 / 0 / 0 | 0 / 0 / 0 | 0 |
| `control_geometry_unavailable_skips` | （字段不存在） | **12,116** | — | （不存在） | **119,200** | — |
| offered bits | 406,526,960 | 295,158,216 | **−27.4%** | 1,393,122,000 | 1,012,604,400 | **−27.3%** |
| delivered bits | 291,812,840 | **291,812,840** | **0（逐位相同）** | 1,012,604,400 | **1,012,604,400** | **0（逐位相同）** |
| terminal_loss bits | 60,247,504 | **0** | −100% | 192,035,064 | **0** | −100% |
| in_system bits | 54,466,616 | 3,345,376 | −93.9% | 188,482,536 | **0** | −100% |
| **`queue_area_bits_s["isl_ctrl"]`** | 848,318,590.6 | **86,637.8** | **−99.99%**（×9,791） | 2,860,189,079.1 | **330,292.4** | **−99.99%**（×8,660） |
| 每实例线长（bits/registered） | 9,189.1 | 9,188.1 | ≈0 | 3,189.4 | 3,188.2 | ≈0 |

**恒等式核对（两 cell 都成立）**：`registered(post) + skips = registered(pre)`；`delivered(pre) = delivered(post)`；`started(pre) = started(post)`。即：修复**只删掉了永远不可能被服务的那些实例**，所有曾被服务的实例逐位保留。这就是"死信"的定义性证据。

## 3. AoI（`decisions` 的 `used_state`，窗口 ≥7.0 s）

| | high pre | high post | Δ | sync pre | sync post | Δ |
|---|---|---|---|---|---|---|
| 样本（决策×方向） | 32,861 | 36,177 | +10.1% | 33,695 | 36,812 | +9.2% |
| p50 (s) | 0.4835 | 0.4828 | **−0.7 ms** | 0.0570 | 0.0570 | ~0 |
| p90 (s) | 0.9135 | 0.9119 | **−1.7 ms** | 0.0972 | 0.0973 | +0.1 ms |
| p99 / mean / max | 0.9978 / 0.4969 / 1.0110 | 0.9978 / 0.4970 / 1.0110 | ≈0 | — | — | — |

样本数 +10% 不是"信息变多"，而是 post 侧决策更多（见 §5：forward 决策 +10.8%/+13.3%），有 `used_state` 的决策行随之变多。**AoI 本身（按决策时刻算）没有改善 —— 修复不是"让状态更新鲜"，而是"让状态的数值不再被死信污染"。** high 的 p90 上限 0.913 s < 1 s 广告周期，符合"hold_last 用最近一条已到达广告"的语义。

## 4. data plane：不一致，且已定位到原因

### 4.1 逐项量化

| 项 | high pre | high post | sync pre | sync post |
|---|---|---|---|---|
| `fates` 全表 hash | `9e507e36…` | `5f0eff51…` | 不同 | 不同 |
| `deliveries`（pid→delivered_at+path）hash | `049975ec…` | `664046e8…` | 不同 | 不同 |
| `packet_events` hash | `b46507c5…` | `0652f7d8…` | 不同 | 不同 |
| `link_service_windows` hash | `99735a7c…` | `ca7f7779…` | 不同 | 不同 |
| packet_events 行数 | 105,993 | 116,005（+10,012） | 107,613 | 120,154（+12,541） |
| isl 服务窗口数 | 25,534 | 28,030（+2,496） | 25,907 | 29,011（+3,104） |
| forward 决策数 | 23,025 | 25,521（+10.8%） | 23,399 | 26,504（+13.3%） |
| 平均路径长度（跳） | 19.34 | **21.32（+1.98）** | 19.61 | **22.10（+2.49）** |
| 首个分歧事件 | idx 10,296 @ **t=5.0285 s**（queue_enter, pid 122, `isl:24:44` vs `isl:24:23`） | | idx 1,357 @ **t=4.2327 s**（queue_enter） | |
| fate 变化 | 2 包：`463` DELIVERED→IN_SYSTEM、`851` 反向 | | 5 包：`73/634/878` DELIVERED→IN_SYSTEM、`557/613` 反向 | |
| 交付数 | 1,254 | 1,254 | 1,253 | **1,252（−1）** |
| 丢包（NO_ROUTE/溢出/deadline） | 0 | 0 | 0 | 0 |
| E2E 均值 / p50 / p90 (ms) | 305.6 / 295.4 / 432.8 | **340.6 / 325.2 / 452.1** | 313.1 / 299.1 / 449.3 | **355.9 / 318.5 / 506.3** |
| 配对 ΔE2E | — | **均值 +35.0 ms**，503 慢 / 150 快 / 600 同 | — | **均值 +42.8 ms**（配对 +43.0），531 慢 / 259 快 / 460 同 |
| `queue_area_bits_s` isl_data | 8.25 M | 9.08 M | 11.77 M | 11.70 M |
| `queue_area_bits_s` holding | 8.90 M | 11.73 M | 21.88 M | 34.44 M |

**首个分歧点的时间戳是最强的时间因果证据**：high 在 t=5.0285 s、sync 在 t=4.2327 s，都紧接第一批广告被接收之后（两 cell 的首批广告 `received_at` 均为 4.003–4.007 s；high 的广告 `measured_at=4.0`，sync 为 4.1/4.2），**不是**随机事件、也不是仿真启动差异。

### 4.2 原因：死信污染了"对端出口积压"这个决策输入

以 high 的 pid=122 / hop 0 / t=4.8304 / sat=128 为例（两侧**唯一**不同的字段）：

```
used_state[N]  resource_id=isl:129:9  measured_at=4.0  received_at=4.00737  bits: 36,736(前) → 0(后)
used_state[S]  resource_id=isl:127:7  measured_at=4.0  received_at=4.00737  bits: 36,736(前) → 0(后)
=> scores[N].total_s 0.114945 → 0.114578 ; scores[S].total_s 0.100395 → 0.100028
```

**同一条广告、同一生成时刻、同一到达时刻，只有 payload 里的队列值变了。** 36,736 = 4 × 9,184 —— 正好 4 个控制包的线长，即"那些被注入到几何不可用链路上、永远不会被发送的控制包，被邻居当成对端出口的真实积压读走了"。

量化（`control_fix_compare.json → pairs[].advertised_backlog`）：

| | high pre | high post | sync pre | sync post |
|---|---|---|---|---|
| 正积压样本（决策×方向） | 11,058 | 5,084（−54%） | 10,555 | 5,236（−50%） |
| 其中**恰为整数个控制包线长** | **4,519（40.9%）** | **0** | **4,817（45.6%）** | **0** |
| 承载这类"纯控制积压"的不同资源数 | **79** | **0** | **90** | **0** |
| 正积压 bits p50 / p90 | 385.7 k / 763.0 k | 491.8 k / 915.5 k | 499.3 k / 1,592.0 k | 603.0 k / 989.8 k |
| 折算预测等待（100 Mbps） | p50 3.9 ms / p90 7.6 ms | p50 4.9 / p90 9.2 | p50 5.0 / p90 15.9 | p50 6.0 / p90 9.9 |

因果链（按证据顺序）：
1. 修复前，几何不可用定向 ISL 上被注入了控制包 → 它们在**对端出口的 ctrl 队列**里排队、永不发送（§2 的 expired / isl_ctrl 面积）。
2. 该对端的广告 payload 把自己的 `isl_queue_bits`（含这些 ctrl bits）发给邻居 → 邻居的 `queue_greedy` 把它当 `resource_work_s = bits/rate` 计入候选代价（0.4–7.6 ms 量级，与决策 margin 同阶）。
3. 于是这些**实际不可用**的出口在 pre-fix 里"看起来有排队"（偶发地被避开）。
4. 修复后它们显示 0（真实的、可服务的积压），看起来**空闲**，决策会更常把包交给这些对端；
5. 对端的数据合法性门仍然拒绝那个出口 → 包 hold / 重决策 → **多约 2 跳**；
6. 每跳约 10 ms 序列化 + ~5.4 ms 传播 ⇒ **+30~35 ms**，与实测 +35.0 / +42.8 ms 量级一致。
7. 交付总量基本不变（仅 sync −1 个、并在 stop 时仍在系统内），因为没有新增丢包路径。

**一句话**：pre-fix 的死信偶然地充当了"该出口不可用"的拥挤信号；修复把它们清掉后，广告里就再没有任何字段表达"这个出口物理上不可用"，于是决策会朝它试探并付出额外跳数。**这是信息缺失（availability 未被广告）的暴露，不是修复引入的 bug。**

## 5. 对 Round-3 的直接要求

1. **不可混用**：任何"policy A vs policy B"比较必须在**同一 release** 下重跑全部臂；Round-1 的 pre-fix 数字（E2E 305.6/313.1 ms、19.3/19.6 跳）与 post-fix（340.6/355.9 ms、21.3/22.1 跳）不能并列。
2. **基线要重跑**：Round-1 的 8 个基线 cell（p1/p2 × 4 policy）在 post-fix release 下都需要重跑才能作为 Round-3 的对照，成本 ≈ 8 × 4–8 min ≈ 40–60 min（含 1 次 publish 不需要——release 已安装）。
3. **若要消除本差异**：(a) 在广告里显式携带"出口可用性/几何可达性"（协议 v3 字段），让修复后的信息完备；(b) 或者接受当前口径并在预注册里写明"availability 不在广告内，方向选择含 2 跳的试探成本"。
4. **报告口径**：凡引用 control 修复后的结果，必须同时给出"data plane 与 pre-fix 不同"这一事实与量级，不能写成"修复不影响数据面"。

## 6. 异常与缺口

1. **`sq2-x-20261031-01` 失败**（`exit_code=2`，`unknown cell 'x'`）：发射命令行多传了一个占位参数（launch 模式第 3 个位置参数是 contract，其后全部当 cell）。它在创建输出目录前即被拒绝，无产物；run-id 已烧掉，未重试，不影响任何 cell。
2. **`truth_probe` 开关不同**（pre=true / post=false）是 pre/post 之间唯一的非同等项；理由与排除见 §1。若要严格同开关，重跑成本每 cell 3–4 min。
3. **`fates` 全表 hash 不同但 `fate_counts` 只有 2/5 个包变化**：其余差异来自 `deliveries`（时间/路径）而非 fate；不要把"hash 不同"读成"丢包不同"。
4. **AoI 的 p50/p90 是对 `used_state` 逐方向样本统计**（不是逐决策取 max）；逐决策 max 见 JSON 的 `aoi.*.age_per_decision_max_s`，结论相同。
5. 本对比是**单 seed、单轨迹**；"+35/+43 ms、+2 跳"是这两个 cell 的实测值，不能当作"控制面负载普遍导致 2 跳"的普适结论。
