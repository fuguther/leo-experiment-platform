# AUDIT-SCENARIO — Stage C 交通/场景语义溯源，与"机制区制"预冻结规则

| 字段 | 值 |
|---|---|
| 日期 | 2026-10-30 |
| 作者 | scenario-auditor（team task `task-3`） |
| 代码身份 | `instr/ledger-export-20261030 @ 7e7fd5a`（`/Users/lge/Desktop/topic/.wt-instr-20261030/CODE`，只读） |
| 对象 profile | `CODE/leo_sim/profiles/stage_c_e2_6g_adv200.yaml` |
| 性质 | **只读审计 + 预冻结前置文件**。不产生任何实验结果；不替代预注册。 |
| 硬约束 | 无代码改动、无 VM/ssh、无 commit、未启动任何仿真。§1/§4 的解析数字由**离线几何/算术**算出（脚本见 §8），不来自 run 产物。 |
| 本文件的用途 | ① 说清这个场景到底是什么、能声明什么；② 在**任何时态结果被查看之前**写死"区制选择规则 + 有效性门"，使"时态失配严重/不严重"的结论不能靠事后挑场景得到。 |

**阅读约定**：`[CODE]` = 读码所得（文件:行）；`[解析]` = 本文件离线计算所得（可复算，非实测）；`[实测-同族]` = 2026-10-27/28 线在**其它** profile 上的实测（引用并标注差异）；`[未测]` = 本 profile 尚无任何实测；`[不成立]` = 无来源支持，不得声明。

---

## 0. 结论先行（7 条）

1. **这个场景的"业务"是合成重力的 6 城全互连流量，不是 M-Lab、不是人口、不是任何实测业务。**
   9000 个 600 kbit 包在 `[0.1,1.0] s` 内按 6 个等权源的**非齐次泊松**过程发出（`offered 6000 Mbps`、`packet 600 kbit`；`trace.py:1358,1376,1392`）。
   目的端是 `P(j|i) ∝ 1/d_ij²`（`gravity_alpha=2.0`、`d_floor=100 km`、6 个 `demand_weight=1.0`；`trace.py:283-296`）。**没有 burst 变换、没有 diurnal、没有 M-Lab 权重、没有人口栅格**（profile 里三者均未启用）。
2. **它不是 SPEC §2E 预注册的机制场景，而是"对比/压力层"。** SPEC 预注册场景 = 24 星/3 面/8000 bit/ISL 5.5 Mbps/18 城/**M-Lab OD**/4 s（`SPEC.md` §2E.1-3），并明写"不得再用人口重力替代机制场景"（§2E.3）。本 profile = 140 星/7 面/600 km/98.6°/600 kbit/ISL 1000 Mbps/**6 城 gravity**/1.2 s，正落在 SPEC §3 表里"星座 = Kepler 140（**对比**）"那一格。
   ⇒ 用它的结果只能说"在 140 星对比层、6 城重力 OD 下……"，**不能**说"（预注册机制场景的）时态失配严重"。
3. **它真正压的是一条跨洲干线 + 少数几条方向性干线，不是全网拥塞。**
   `[解析]` tokyo↔singapore 双向各 560/589 Mbps（占各自源端发出量的 56.0%/58.9%），压在同一条 1000 Mbps 有向 ISL 序列上（5 跳、36.0 ms 传播）；下一层是 lagos↔london（433/388）、newyork↔london（425/314）。
   `[解析]` 按"单一最短跳路径"假设：85 条有向 ISL 承载、其中 60% ≥0.2、13 条 ≥0.5、**单链路最大 in-window 载流比 0.935（上界）**；而全网 ISL 平均只有 **7.1%**（23.16 Gbit 转发 / 327.6 Gbit 容量，0.9 s 窗内）。即**点拥塞、面空闲**。
   `[解析]` OD top-5 = **40.3%**（SPEC §2D 目标 40–70%，**刚压线**）、top-1 = 9.8%。
4. **1.2 s 内几何几乎冻结：这里的"时间失配"只可能是队列状态年龄，不可能是位置/拓扑陈旧。**
   `[解析]` 轨道角速度 360°/5792.3 s ⇒ 1.2 s 走 **0.0746°**（ISL 长度变化 ~9 km、传播 ~30 µs）；`[解析]` 6 城可见卫星数在 `[0,1.2]` 内按 0.1 s 网格采样**始终不变**（tokyo 5/singapore 3/london 4/newyork 4/saopaulo 4/lagos 2）⇒ 无切换事件（0.1 s 分辨率下）；`topology.recompute_interval_s` 缺省 `None`（静态拓扑）⇒ **0 次重匹配**（`config.py:516`；`routing.py:70-93`）。
   ⇒ 任何"陈旧几何/陈旧拓扑"的表述 `[不成立]`；本场景只有 0.2 s 广告龄的**队列/资源状态**。
5. **主终点当前不可用：profile 没有 `demand.deadline_s`，且 T=1.2 s 会让有效期限被 horizon 截断。**
   `[CODE]` D4 主终点 = `100×|{p: not(DELIVERED 且 delay≤D)}|/|population|`（`outcome_metrics.py:452-467`），必须有 D；profile 未给，只能用 CLI `--deadline-s` 注入（`t1_tasks.py:2654`），且 D 是**场景选择旋钮**，必须与臂一起冻结。
   `[CODE]` 逐包有效期限实为 `min(D, T−e)`（e=发射时刻，T=horizon）；平台自身要求 `deadline_binding.binding == true` 才算 cell success（`t1_tasks.py:2270-2280`）。见 §4.4 的截断检查。
6. **预冻结区制规则见 §5**：唯一物理旋钮 `g`（`--offered-mbps 6000g`，λ_ij → g·λ_ij），分类标量 = **in-window ISL 利用率 p90**，门槛 = queue-share，6 条点级有效性门 + 1 条轮级覆盖门；**分类表冻结前不得计算任何臂间对比**（§5.7）。
7. **成本量级**：`[解析/拟合]` 1.2 s @g=1 ≈ **612–862 s（10–14 min）/run**、峰值 RSS 8.1–9.7 GB、输出 ~158 MB；**4 s 但发射窗仍 0.9 s ≈ 同价**（+5–10 s）；4 s 且发射窗拉到 3.7 s ⇒ **43–60 min/run、32–40 GB、并发上限 C=1**。见 §6。

---

## 1. 交通模型：代码事实（不是文档事实）

> 警告：`stage_c_e2_6g_adv200.yaml:1-10` 的**文件头注释是陈旧的**，它从 `EXPERIMENTS/2026-10-24-corridor/profiles/spread20_12g.yaml` 整段复制而来，声称"24 星 3 平面 53° 550 km / ISL 10 Mbps / 包 8000 bit / 48 条有向 ISL 平均 4641 km"——**与文件正文逐条冲突**。以正文与代码为准，不得引用该注释。

### 1.1 源端与速率

| 环节 | 事实 | 代码 |
|---|---|---|
| 聚合总率 | `total_rate = offered_mbps·1e6 / packet_bits` = 6000e6/600000 = **10 000 包/s** | `trace.py:1358` |
| 每源端基率 | `base_rate_i = total_rate · w_i/Σw`；6 个 `demand_weight=1.0` ⇒ 每城 **1666.7 包/s = 1000 Mbps** | `trace.py:1376`、权重 `trace.py:1370-1378`、端点 `trace.py:248-270` |
| 抽样 | `t += exponential(1/(base_rate·max_mult))`；`max_mult=1`（无 burst/diurnal）⇒ 纯泊松 | `trace.py:1392` |
| 稀释/接受 | `if gen.random() > multiplier/max_mult: continue`；multiplier 恒 1.0 ⇒ 恒接受 | `trace.py:1396`、`trace.py:325-330` |
| 每源端包数 | 1000 Mbps × 0.9 s / 600 kbit = **1500 包/城**；全网 **9000 ± 95**（泊松 √N） | 由上式 |

### 1.2 目的端（gravity）

`[CODE]` `P(j|i) ∝ (w_j)^1 · max(d_ij, d_floor)^−alpha`，`alpha=2.0`、`d_floor=100 km`、`w_j=1.0`（`trace.py:283-296`；profile `demand.gravity_alpha: 2.0` / `gravity_d_floor_km: 100.0`）。
**没有 population 项**（`destination_population_exponent` 只在 `population_gravity` 模式下 >1；`trace.py:280-284`）。6 城两两距离与份额 `[解析]`：

```
           tokyo  singapore   london  newyork  saopaulo   lagos      (km)
tokyo          0       5314     9559    10850     18535   13472
singapore   5314          0    10848    15333     15987   11143
london      9559      10848        0     5570      9497    5013
newyork    10850      15333     5570        0      7686    8473
saopaulo   18535      15987     9497     7686         0    6374
lagos      13472      11143     5013     8473      6374       0

目的端份额 P(j|i)：tokyo→sing 56.0% / lond 17.3% / newy 13.4% / saop 4.6% / lago 8.7%
                 sing→toky 58.9% / lond 14.1% / newy 7.1% / saop 6.5% / lago 13.4%
                 lond→lago 38.8% / newy 31.4% / saop 10.8% / toky 10.7% / sing 8.3%
                 newy→lond 42.5% / saop 22.3% / lago 18.4% / toky 11.2% / sing 5.6%
                 saop→lago 41.4% / newy 28.5% / lond 18.6% / sing 6.6% / toky 4.9%
                 lago→lond 43.3% / saop 26.8% / newy 15.2% / toky 6.0% / sing 8.8%
```

**语义后果**：6 城离散 + 1/d² 会产出**长距离干线**（tokyo→london 9559 km 占 17.3%）。这不是任何真实运营商的城际分布，只是"离散点 + 幂律衰减"的算术结果（见 §3）。

### 1.3 发射窗、包、时长

| 项 | 值 | 代码/出处 |
|---|---|---|
| 发射窗 | `[emission_start_s, emission_end_s]` = `[0.1, 1.0]` ⇒ **0.9 s**（右端实际闭区间，测度 0） | `trace.py:1393-1394`、profile:53-54 |
| 包 | 600 000 bit = 600 kbit/包（τ_ser@1 Gbps = **0.6 ms**） | profile:55 |
| horizon | `duration_s: 1.2` ⇒ 发射结束到停只有 **0.2 s 排水** | profile:14 |
| 时长与 trace 的耦合 | `emission_end_s` 显式给出时，**duration 不进入 trace**（`emission_end = duration if emission_end_s is None else ...`）⇒ 改 T 不改 trace digest | `trace.py:1159-1162` |
| trace 包上限 | `execution.max_packets: 400000`（本场景 9000，不约束）；`max_events: 60e6` | profile:92-94 |

### 1.4 `demand.offered_mbps` 到底缩放了什么

`[CODE]` 它**只**进入 `total_rate`（`trace.py:1358`），进而线性缩放每个源端的泊松强度；**不**改变源端份额（权重归一）、**不**改变目的端份额（份额与 offered 无关）、**不**改变包长、不改变发射窗。
⇒ 存在唯一的等价物理旋钮

```
g ≡ offered_mbps / 6000        λ_ij → g · λ_ij  (对所有 i,j 同倍)
实现：--offered-mbps $((6000*g))   （t1_tasks.py:2595, 2654-2656；无代码改动）
```

`[CODE]` 注意两个「offered_mbps 不等于实际量」的坑：
1. 有 burst 时实际到达率 ≠ offered（`TRAFFIC-MODEL-SPEC.md` §3.3 的 `mlab_multiod_burst_t0`：目标 50，实测 64.95 Mbps）。**本 profile 无 burst，等式成立**。
2. `nested_master_offered_mbps` 未设置 ⇒ 无嵌套稀释（`trace.py:671-677`）。

### 1.5 burst / diurnal 变换

`[CODE]` profile 无 `burst_start_s`/`burst_duration_s`/`burst_multiplier`、无 `diurnal_*`、`temporal_model` 缺省 ⇒ `_rate_multiplier()` 恒返 1.0（`trace.py:325-345`）。
⇒ **本场景的非平稳性只有泊松本身的统计涨落**。任何"重尾/ON-OFF/实测突发"的表述 `[不成立]`。

### 1.6 接入侧（access）

| 项 | 值 | 语义/代码 |
|---|---|---|
| 槽位 | `slots_per_satellite: 256`（平台缺省 4） | 每星可同时被多少个地面关联占用；`kernel.py:3449,3563` |
| 关联 | `association: bbm`、`acquisition_delay_s: 0.01`、`hysteresis 0`、`min_dwell 0` | 由信号最强星关联，抢占/轮转由 `slot_lease_s=10 s`+`idle_release_s=0.2 s` 管 |
| 上下行速率 | `uplink/downlink_rate_mbps: 20000` | 每方向 20 Gbps（**合成值，无链路预算支撑**，见 §2） |
| 队列 | `uplink/downlink_queue_bits: 4e9`（4 Gbit≈4 s@1000 Mbps） | 地面侧排队 |
| holding | `holding_queue_bits: 268435456`（256 Mibit）**每星** | 包已上星但"此刻不转发"的暂存；`unavailable_policy: queue` |
| 拥塞下的行为 | 槽位满 ⇒ 确定性 FIFO 等待队列 | `kernel.py:14-21`（模块 docstring） |

`[解析]` g=1 时接入不是瓶颈：每城 1000 Mbps / 20 Gbps = **5%**；140 星对 6 城、每城仅 2–5 个可见星，单星通常只服务 1 个地面端，256 槽远大于并发；单跳上行串行化 600 kbit/20 Gbps = 0.03 ms。

### 1.7 链路侧（ISL）

| 项 | 值 | 后果 `[解析]` |
|---|---|---|
| `isl_rate_mbps: 1000.0`、`rate_model: constant` | 常数 1 Gbps | 不启用 DVB-S2X MCS 表（见 §2） |
| `isl_queue_bits: 24000000` | 24 Mbit = **40 个 600 kbit 包** = **24 ms** 串行化 | 与 BDP 比：BDP = 1000 Mbps × 7.28 ms = 7.28 Mbit ⇒ **3.30×BDP**（SPEC §2D 目标 2–8×BDP ✓） |
| `max_isl_km: 3000.0` | 同面邻星恒 2181 km（可用）；面间按索引配对，905–6049 km，**t=0 仅 84/280 条有向面间边可用** | 面间连通只在高纬；赤道附近面间断链 |
| `ge_enabled: false` | 无随机中断 | — |
| 拓扑 | `topology.recompute_interval_s: None` ⇒ 静态；`matching: markovian` 不生效 | 邻居身份固定，可用性逐次现算（`model.py:646-663`） |
| 连通性 | `[解析]` t=0 全 140 星强连通（平均出度 2.6/4） | ⇒ NO_ROUTE 不来自连通性，只来自 policy/路径 |

**单跳代价**：`[解析]` τ_prop(intra) = 2181/299792.458 = **7.28 ms**；τ_prop(inter, 可用集均值) = 1673 km = **5.58 ms**；ratio_ser = τ_ser/τ_prop = 0.6/7.28 = **0.082 ≤ 0.1（SPEC §2D 传播主导判据 ✓）**。

### 1.8 控制面 / 路由 / 时态机制

| 项 | 事实 | 代码 |
|---|---|---|
| 广告 | `advertise_interval_s: 0.2`、`vis_k: 3`、`protocol v2`；控制包是**真包**，占 ISL 服务时间（非抢占优先），最多走 3 个真实跳 | `control.py:1-8`、`kernel.py` |
| 广告数量 | `[解析]` 1.2 s /0.2 s = **6 个广告纪元**（发射窗内 4.5 个）⇒ `bounded_linear` 的历史最多 ~5 个样本（`history_limit: 8` 用不满） | `time_alignment.py:634-640` |
| 路由 | `policy: known_serving` + `max_hops: 64`：**目的地服务集是真值**（不吃广告），路径按**单位跳**多源 BFS（反向图） | `routing.py:208-216, 263-272`、`kernel.py:7255` |
| ⇒ 信息结构 | "谁知道目的地"是完美的；**只有"队列/资源状态"是陈旧的**（advertised + bounded_linear） | — |
| 失败语义 | `no_info`（无星服务该 cell）⇒ **holding 等待**（不是 NO_ROUTE）；`unreachable` ⇒ **NO_ROUTE** | `kernel.py:7254-7276` |
| 四臂 | `stale` / `now` / `common` / `candidate` 只差**查询时刻**；`candidate` = 投影到该候选自己的预计资源使用时刻 | `time_alignment.py:1-30` |
| profile 的 `arm:` 是死字段 | 被 CLI `--arms` 覆盖（2026-10-27 线已实测确认） | `2026-10-27-perfect-state/03-平台与场景核查.md` §2.5 |

---

## 2. 溯源表：外部依据 vs 合成

置信度用 `ANALYSIS/CAPABILITY-TABLE.md` 的五级词表（实现存在 < 命令可达 < 本地实跑 < VM 实跑 < 研究比较完成）叠加"是否有外部来源"。

| # | 要素 | 本 profile 的值 | 来源 / 依据 | 是否在本 profile 的数据路径上 | 置信度 |
|---|---|---|---|---|---|
| P1 | 6 城坐标 | tokyo/singapore/london/newyork/saopaulo/lagos 经纬度 | profile:22-47。城市经纬度是公开地理事实，可核 | ✅ 直接用（源端/目的端身份） | **接地（地理）**，但与真实城际流量无关 |
| P2 | OD 权重 | 全部 `demand_weight: 1.0` + 1/d² | profile:48-55；`trace.py:283-296` | ✅ 决定流量分配 | **合成（knot-tying）**：无任何实测 OD |
| P3 | M-Lab 实测 OD | **未使用** | `CODE/data/traffic/mlab_2026-05-27.csv` 存在（**[已核]** 44 929 数据行，9 列，逐小时城对聚合）；`README.md:3-7` 自述"measurement proxy / not calibrated user demand"；`receipt.py:334-340` 机器强制 `not_calibrated_user_demand` | ❌（`demand.mode: gravity`） | 数据在库：可作**权重先验**；**不是** offered load |
| P4 | GPW 人口 | **未使用** | `CODE/population_map/gpw_v4_population_count_rev11_2020_15_min.tif` 存在；`population.py` 只在 `population_gravity` 模式读取；`config.py:99` 的模式枚举含 `population_gravity` | ❌（本 profile 用 gravity） | 数据在库（CIESIN GPW v4.11）；本场景**不在**路径上 |
| P5 | 星座 140/7/600 km/98.6° | 是 | `SPEC.md` §3 表把 "Kepler 140" 记为**对比**星座；仓库内**没有**任何文献/接口来源证明这组参数复现了某个真实壳层。`model.py:469-521` 是**无相位差**的 Walker-delta（每面同一相位 `phase = 2π(idx/per_plane + t/period)`），比真实星座的相位设计更"整齐" | ✅ 决定全部几何 | **部分**：名称有出处（"Kepler 140"），**逐参数无来源 → 不得称"复现某真实星座"** |
| P6 | 轨道力学 | 圆轨道、`μ=398600.4418`、`R=6371`、地球自转、无 J2/无摄动 | `model.py:469-530` | ✅ | **接地（教科书常数）** |
| P7 | ISL 容量 | 常数 1000 Mbps | profile:72；**不是**链路预算推出 | ✅ | **合成**。SPEC §2E.2 只对"机制场景 (5.5 Mbps)"和"对比场景 (DVB-S2X MCS)"拍过板；本档 1000 Mbps 属第三族（SPEC §3 "96 星/1 Gbps/1500 B"，且被标注**不可与 v3/v4 混统计**） |
| P8 | DVB-S2X 链路预算 | **未启用**（`rate_model: constant`） | `link_budget.py:1-26` 明写是 **legacy `SimulationRL.py` 公式的重新实现**，用 golden test 钉住，**不是**对 DVB-S2X 标准文档的引用；且本 profile 不走 MCS 路径 | ❌ | 若有人写"本场景用了 DVB-S2X 链路预算"→ `[不成立]` |
| P9 | 缓冲 | ISL 24 Mbit（3.3 BDP）、holding 268 Mbit/星、上下行 4 Gbit | profile:61-73 | ✅ | **合成**（SPEC §2D 只要求 2–8×BDP；本档满足，但数值是选的） |
| P10 | 包长 | 600 kbit | profile:55 | ✅ | **合成**。SPEC §2E.2 拍的是 8000 bit（机制）/64.8 kbit（对比）；600 kbit 无出处 |
| P11 | 负载 | offered 6000 Mbps | profile:50 | ✅ | **合成**。客观后果（非声明意图）：它让 tokyo↔sing 干线达到 560/589 Mbps，即单条 1000 Mbps ISL 的 0.56–0.59 |
| P12 | 广告周期/vis_k | 0.2 s / 3 | profile:77-80 | ✅ | **合成**（SPEC §3 表把 0.2 s 记为对机制场景"已定"，但那是配 8000 bit/5.5 Mbps 的场景） |
| P13 | 需求随机性 | 泊松（指数间隔） | `trace.py:1392` | ✅ | **合成**。真实 LEO 业务的到达过程 `[不成立]`（无实测回放；库里唯一 csv 回放通道只被自造微场景用过） |
| P14 | 突发结构 | 无 | profile 无 burst 字段 | ✅（=不存在） | 无重尾/ON-OFF：**任何突发结论 `[不成立]`** |
| P15 | GE 随机中断 | 关闭 | profile:75；`config.py:511-514` 的 `ge_gsl/ge_isl` 缺省被注释为 "abstract defaults, NOT calibrated to any real constellation operator" | ✅（=不存在） | 掉线建模**不在**本场景 |

**一句话**：这个场景里**唯一真正接地的**是城市经纬度、轨道力学常数、以及"GPW/M-Lab 数据确实躺在库里"这两件事；**流量结构、容量、缓冲、包长、负载全部是合成选择**。

---

## 3. 不得声明清单（除非另找到来源）

| # | 不得声明 | 理由 |
|---|---|---|
| N1 | "本场景的流量等于/近似真实 Starlink（或任何运营商）业务" | 合成泊松 + 6 城 1/d²；`data/traffic/README.md:3-7` 明确否认可校准性 |
| N2 | "实测 OD 矩阵" / "M-Lab OD 用于本场景" | 本 profile 用 `gravity`；M-Lab 数据不在路径上（P3） |
| N3 | "人口加权的业务分布" | GPW 不在路径上（P4） |
| N4 | "真实队列占比 / 真实排队时延分布" | 队列来自合成到达过程与合成容量；无外部队列测量 |
| N5 | "真实热点走廊（如某城际链路）" | tokyo↔sing 的 56% 是 1/d² 在 6 点上的算术结果，不是观测到的走廊 |
| N6 | "用了 DVB-S2X 链路预算 / 真实 MCS 自适应" | `rate_model: constant`，链路预算模块未被调用（P8） |
| N7 | "复现了 Kepler 星座" / "复现了任何真实壳层" | 只有名字有出处，参数与相位设计无来源（P5） |
| N8 | "实测突发 / 重尾 ON-OFF 到达" | 无 burst 变换（P14）；库内 burst 只被定义为"预注册的受控压力变换"（`F3-LITERATURE`/SPEC §2D） |
| N9 | "陈旧拓扑 / 陈旧几何导致的失配" | 1.2 s 内 0 切换、0 重匹配、几何冻结（§0.4） |
| N10 | "这个场景通过了 SPEC §2D 的流量结构判据" | OD top-5 = 40.3%（压线）；有排队链路占比取决于分母口径（14.0% 或 60.0%），**两种口径都不在 20–40% 带内**；结构性判据**未满足** |
| N11 | "丢包通道已打开 / 丢包由 ISL 缓冲导致" | F3 线的教训：`ISL_QUEUE_OVERFLOW` 仅 0.5–0.8%，`util` 不够高才是原因（`F2-STOPPED-20261027.md` §2、`00-论证方案.md` §11.3-2）；本 profile 的溢出量**未实测**；若按 M/M/1/40 在 ρ≈0.94 处粗估约 0.5%/跳（纯估计，不作证据） |
| N12 | "1.2 s 的运行代表稳态" | 队列从空开始、只 0.9 s 发射、0.2 s 排水；2026-10-17 线已记录"填充瞬态"会把短窗结论做反（`STATUS.md` 第四轮）：统计须从 `emission_start+0.20 s` 起，且最小可用发射窗 0.35 s |

---

## 4. 可测区制指标：定义、出处、本 profile 的状态

### 4.1 可用指标（全部可从 `*-summary.json` 取到）

> 结构：`arms[i].outcome.*`（`t1_tasks.py:1590-1650`）、`arms[i].congestion_metrics.*`（`metrics.summarize`，含 `packets` 与 `links`）、`arms[i].measurement_window`、`arms[i].network_outcome`。summary 保留完整 `congestion_metrics`（`t1_tasks.py:2217-2225`），只丢弃 replay/action-log/routing 逐条记录。

| 指标 | 精确定义 | 输出位置 | 本 profile 状态 |
|---|---|---|---|
| **ISL 利用率分布** u_k | 平台 `links[k].utilization = served_bits / available_capacity_bits`，分母 = `available_capacity_interval_s=1.0` 网格上**实际可用**容量（`metrics.py:332-345`；`kernel.py:2520-2543`，`max(1.0,...)` 截顶）。**分母覆盖整个 horizon（1.2 s），不是发射窗** | `congestion_metrics.links[link_id ∈ isl:*]` | `[未测]`；`[解析]` in-window 上界 p50 0.235 / p90 0.745 / max 0.935 |
| **in-window 利用率** u^W_k（本文件定义，见 §5.3） | `served_bits_k / (isl_rate_bps · W)`，`W = last_emission − first_emission = 0.9 s` | 由 `served_bits` + 冻结常数算出 | `[未测]`；与平台 u_k 的关系：u_k ≈ u^W_k × (W/available_time)，本 profile `available_time≈1.2 s` ⇒ **u_k ≈ 0.75·u^W_k** |
| **queue share**（队列占 e2e 份额） | `φ = mean(queue_wait_s)/mean(e2e_s)`（ISL egress 队列；`queue_wait_s` 不含 `holding_wait_s`，两者分列） | `congestion_metrics.packets[pid].{queue_wait_s,holding_wait_s,e2e_s}`；`[uncoded]` 需自查 | `[未测]`；锚点见 §4.2 |
| **queue area** | `queue_area_bits_s` = ∫ backlog dt，分 `uplink/downlink/holding/isl_data/isl_ctrl` | `outcome.queue_area_bits_s`（`kernel.py:7630-7640`） | `[未测]`；F3 同族实测 holding 8.69 vs isl_data 3.24 Mbit·s（2.7×，holding 主导）——**这是 2 Mbps 场景，不可外推到 1 Gbps** |
| **delivery** | `delivered/offered`（窗口内），并单列 censoring（`IN_SYSTEM_AT_STOP`，**不是损失**） | `outcome.delivery_ratio`、`outcome.censoring`、`network_outcome.terminal_loss` | `[未测]` |
| **drop（终局损失）** | 仅 `ACCESS_REJECTED / ACCESS_QUEUE_OVERFLOW / HOLDING_QUEUE_OVERFLOW / ISL_QUEUE_OVERFLOW / NO_ROUTE / DATA_DEADLINE_EXPIRED / GEOMETRY_LOSS_IN_FLIGHT / RANDOM_OUTAGE_IN_FLIGHT` | `outcome.fate_counts`、`terminal_loss.by_fate`（词表：`outcome_metrics.py:100-112`） | `[未测]` |
| **NO_ROUTE** | `known_serving` 下 = "有服务星但无有向可达路径"；`no_info`（无星服务该 cell）走 **holding** 而非 NO_ROUTE | `fate_counts.NO_ROUTE`；`kernel.py:7254-7276` | `[未测]`；`[解析]` 连通性充分、覆盖恒定 ⇒ 预期待为 0 |
| **miss（D4）** | `100·|{p: not(DELIVERED 且 delay≤D)}|/|population|` | `outcome.deadline_primary_loss.miss_pp` | `[未测]`；**且当前 profile 无 D**（§0.5） |
| **deadline binding** | D 是否真的能拒包（有 late 或 expired 才算） | `deadline_primary_loss.binding.binding` | `[未测]`；平台强制要求为 true 才算 cell success |
| **e2e / p90 / p95 / p99** | 逐包 `delivered_at − emitted_at` | `outcome.e2e_*` | `[未测]`；`[解析]` 传播地板 7.3–58.2 ms（1–8 跳，见下） |
| **events** | `events_processed` | `outcome.events_processed` | `[未测]`；`[解析/同族]` 1.16–1.62 M |

`[解析]` 30 条有向 OD 的最小跳/传播地板（单最短跳假设）：

```
tokyo→sing 5 跳 36.0 ms | sing→toky 5 36.0 | lond→lago 1 7.3 | lago→lond 1 7.3
newy→lond 2 14.1 | lond→newy 2 14.1 | saop→lago 7 50.5 | lago→saop 7 50.5
newy→saop 3 21.8 | saop→newy 3 21.8 | toky→lond 3 21.8 | toky→newy 4 29.1
sing→lond 6 42.8 | lond→toky 3 21.8 | ... 最长 saop→toky 8 跳 58.2 ms
流量加权平均跳数 = 4.29；全网 ISL 转发 23.16 Gbit / 容量 327.6 Gbit = 7.1%
```

### 4.2 最近的实测锚点（**不是**本 profile 的实测）

| 锚点 | 场景 | 实测 | 归类（按 §5 阈值，事后回看） |
|---|---|---|---|
| `mtu1500_s7` | 1500 B / ISL 200 Mbps / offered 40 / T=1.2 | util_max **0.0145**、ISL 排队 0.21 ms（0.30% e2e）、orc−stale **−0.0004 ms** | **P**（零效应） |
| F3 `control/stale`（6 seeds 均值） | 1500 B / ISL **2 Mbps** / offered 50 / T=1.6 / D=0.5 | util p50 **0.36** / p90 **0.62** / max **0.76**；ISL 溢出 0.5–0.8%；送达率 **≈92–94%**；miss **18.31 pp**；真值 vs 广告 `orc−nowadv` **−1.90 pp**（6/6） | **T**（主效应存在） |
| `c6big_s7` | 2 Mbit / ISL 200 Mbps / offered 2000 / T=1.2 | util_max **0.90**、ISL 排队 184.6 ms（**45.4%** e2e）、orc−stale **−4.26 ms** | **C**（效应最大） |
| `stageC_s7`（20 城，脏 CODE `815606ae`） | 600 kbit / ISL **1 Gbps** / offered **6000** / T=1.2 | util_max **0.792**、frac(≥0.2)=**0.291**、ISL 排队 **19.2 ms（15.5%** e2e）、e2e 124.07 ms、miss 32.2% | 本 profile 的**最接近类比**；但 20 城、且代码不可复现（不得当证据） |

**可用的方向性观察（注意三种对比不同口径，不能相加或直接比大小）**：φ 0.3% ⇒ `orc−stale` ≈ −0.0004 ms；φ 15.5% ⇒ `orc−nowadv` −1.90 pp 的 miss 收益；φ 45.4% ⇒ `orc−stale` −4.26 ms。方向上"φ 越大、状态项越有杠杆"一致，因此支持"用 queue-share 当门槛"，但**不构成**对本 profile 的定量预测。

### 4.3 缺失指标（必须先补或明确降级）

| 缺什么 | 为什么需要 | 从哪来 |
|---|---|---|
| **path stretch**（路径传播/大圆传播） | 判"绕路是否把传播地板抬高"，以及状态机制是否在用更长的路换更短的队列 | 不是一等指标。summary 只有 `propagation_time_mean_s`（全跳平均）；真正的逐包跳数/路径只在 **raw result** 的 `routing_audit_log.decision_records`（含 `hop_index`/`chosen`/`observation_at_start.candidate_resources`，受 `T1_AUDIT_MAX_DECISIONS` 采样上限）。`scene_load_calibration.py:632-641` 里的 `median_alternative_stretch` 是**设计期静态计算**，不是 run 产物 |
| **candidate margin**（候选间拓扑差） | 机制能翻决策的物理条件：状态项差 > 候选 margin（平台实测 p50 **10.9 ms**、`u_90=0.60 ms`，P-10） | 同上：raw `action_log` + `observation_at_start` |
| **utilization 分母声明** | `F3-RESULTS-20261028.md:36,404` 报的 p50/p90/max **未写分母口径**；平台存在服务窗/可用容量两套分母（`metrics.py:332-345`；无 available 采样时分母退化为服务窗容量 ⇒ 任何成功服务的链路都≈1.0，见 `primary_metrics.py:47-54`） | 本文件 §5.3 把它钉成公式 |
| **逐跳队列深度时间序列** | 判"排队是常驻还是瞬态" | raw `replay.queue_state_events`（summary 丢弃） |
| **warm-up 子窗统计** | 规避填充瞬态（§3 N12） | 由 `trace.csv` + `packet_outcomes` 离线重算 |

### 4.4 主终点的结构性前置（必须先钉住，否则 D4 不可解释）

`[CODE]` miss 的逐包有效期限实际是 `min(D, T − e)`：T 到时仍在系统里的包记 `IN_SYSTEM_AT_STOP`（censoring），而 censoring 包在 miss 公式里**算 miss**（`outcome_metrics.py:452-467` 只认 `DELIVERED 且 delay≤D`）。因此：

```
若 D > T − last_emission ，则发出较晚的包即使真时延 ≤ D，也可能因 horizon 截断而记 miss。
截断量 = P(e > T − D 且 T − e < δ ≤ D)。它同 trace 同 horizon ⇒ 对臂是共同的，
但会把绝对 miss 水平抬高（使 F2 式"miss∈[10%,60%]"的有效期门被机械满足）。
```

**预冻结的截断检查（可离线复算，用 `packet_outcomes` 的 `emit_time`/`delivery_time`）**：

```
M_decl = miss(D)            M_eff = miss(D_p(e)), D_p(e) = min(D, T − e)
若 |M_decl − M_eff| > 1.0 pp ⇒ 场景对主终点无效（延长 T 或缩短 D），不得出 D4 结论。
```

本 profile：`T=1.2`、`last_emission=1.0` ⇒ 若 `D ≥ 0.3 s`，最后 0.1 s 发出的包其有效期限会被压到 `T−e < 0.3 s`（是否真被截断取决于时延分布，**所以必须跑上面的检查**而不是推断）。F2/F3 用 `T=1.6` 与 `D=0.5`（`T−last_emission=0.6 > 0.5`）因此**没有**这个问题。
⇒ **冻结建议：`T ≥ last_emission + D + 0.1 s`**（D=0.5 ⇒ T≥1.6 s；取 4 s 更稳，代价见 §6）。

---

## 5. **PRE-FREEZE 区制规则**（在查看任何时态结果之前锁定）

> 地位：本 §5 是 `TIME-ALIGNMENT-20261030` 的**场景前置契约**。它的作用是让"时态失配严重/不严重"这句话**只能**以"在某区制的某个 g 上、斜率如何"的形式出现，而不能由"事后挑一个好看的场景"产生。
> 冻结后，本 §5 的任何数字（1 个标量、2 个门槛、G1–G6 点级门 + G7 轮级门、1 个顺序）**不得修改**；要改必须新开预注册并作废本轮。

### 5.1 唯一物理旋钮

```
g ∈ G = {0.25, 0.50, 1.00, 2.00, 4.00}         （offered = 6000·g Mbps）
实现： --offered-mbps 1500|3000|6000|12000|24000
物理含义： λ_ij → g·λ_ij，对所有 OD 同倍；不动物理容量、不动包长、不动时序
红线上不可动： isl_rate_mbps(1000) / isl_queue_bits(24e6) / packet_bits(600e3) /
              emission 窗(0.1–1.0) / adv(0.2) / vis_k(3) / policy(known_serving) /
              max_isl_km(3000) / 星座(140/7/600/98.6)
```

理由：改变容量或包长会同时改变 τ_ser、缓冲的包数、ratio_ser（SPEC §2D 的三条判据），那就不是在"扫负载"，而是在换尺子。

### 5.2 参考臂与"冻结前不许看的量"

- 分类只用**参考臂**：`--arms stale` + `state_source=advertised` + `predictor=bounded_linear`，seed 7，1 run/g。
- **允许看**（场景指标）：occupied/utilization/queue area/fate/delivery/delivery 时间分布、`deadline_binding`。
- **不许看**（时态结果）：任何形如 `Y[treat] − Y[ref]` 的量——含 stale−now、candidate−stale、orc−stale、miss 差、e2e 差、`time_alignment_audit` 的命中率。**在 §5.7 的冻结文件落盘之前，禁止对这些量做任何计算、绘图或口头比较。**

### 5.3 指标定义（写死公式，防分母口径漂移）

以 `ls` = `congestion_metrics.links` 中 `stage=="isl"` 的条目集合（每个有向 ISL 一条）。

```
W            = measurement_window.last_emission_s − measurement_window.start_s      (= 0.9 s，冻结)
u^W_k        = links[k].served_bits / (1000e6 · W)          # in-window 载流比，k∈ls
u^W_p90      = P90({u^W_k : k∈ls 且 served_bits>0})         # 只对"活跃链路"取分位
f_02         = |{k∈ls : u^W_k ≥ 0.20}| / |ls|               # |ls| = 有 available-capacity 分母的全部有向 ISL（含 0），对齐 SPEC "有排队链路占比"
φ_isl        = mean(packets[p].queue_wait_s) / mean(packets[p].e2e_s)      # 仅对 DELIVERED 的 p
φ_hold       = mean(packets[p].holding_wait_s) / mean(packets[p].e2e_s)
f_noroute    = fate_counts.NO_ROUTE / outcome.offered
f_loss       = (ACCESS_REJECTED + ACCESS_QUEUE_OVERFLOW + HOLDING_QUEUE_OVERFLOW
                + ISL_QUEUE_OVERFLOW + DATA_DEADLINE_EXPIRED) / offered
d_deliv      = outcome.delivery_ratio.by_packets（denominator_kind = offered_packets_all_fates）
f_censor     = outcome.censoring.packets / offered
M_decl, M_eff = §4.4 的两个 miss
n_cand2      = 至少 2 个合法候选方向的决策数 / 总决策数（**仅 raw result 可得**，见 §4.3）
```

### 5.4 分类阈值（PRE-FROZEN：分类器只用一个标量，门槛另判）

```
Step A 有效性门（§5.5 G1..G6）：不通过 ⇒ INVALID，不进任何区制。
Step B 分类器（只按 u^W_p90，全覆盖、无 gap、无并列）：

   P  传播主导     u^W_p90 <  0.30
   T  过渡        0.30 ≤ u^W_p90 < 0.75
   C  拥塞敏感     u^W_p90 ≥ 0.75

Step C 门槛复核（决定该点是否真的可用）：
   P：无 φ 要求
   T：需 φ_isl ≥ 0.10
   C：需 φ_isl ≥ 0.25
   不满足 ⇒ INVALID（门名 G6），**不降级到低区制**。
```

为什么分类只用一个标量：两个标量分类会出现"u 高而 φ 低"的空白格，空白格的处理方式（归 T 还是作废）就是事后裁量权。这里把 u 定为唯一分类器（它直接就是"链路负载"这个物理量），把 φ 降为"这个负载是否落在机制能触及的地方"的**门槛**；因此没有需要解释的空白格。

**阈值出处（不是事后拟合）**：

| 阈值 | 依据 |
|---|---|
| `u^W_p90 = 0.30` 下界 | 零效应锚点 `mtu1500` 的实测 u_max 0.0145（T=1.2、W=0.5）⇒ in-window ≈ 0.0145×1.2/0.5 ≈ **0.035**；0.30 比它高一个数量级，仍远低于拥塞。物理上 0.30 ⇒ 每跳排队 ≈ 0.43·τ_ser ≈ 0.26 ms（M/M/1 近似）≪ 候选 margin p50（10.9 ms）⇒ 状态项不可能翻决策 |
| `u^W_p90 = 0.75` 上界 | SPEC §2D 把"热点 ρ 0.85–0.95"定为机制场景的目标带；0.75 放在带下缘之前，使"带宽内"自动落进 T、只有真的过载才进 C。物理上 0.75 ⇒ 每跳排队 ≈ 3·τ_ser ≈ 1.8 ms（M/M/1 近似），仍 < margin p50，但 p90 margin 会更小 |
| `φ_isl = 0.10` | 三个实测锚点的 φ：0.3%（mtu1500，零效应）/ 15.5%（stageC 类比）/ 45.4%（c6big，效应最大）。0.10 把"零效应区"排除，且不去动中间锚点 |
| `φ_isl = 0.25` | 同上，0.25 位于 15.5% 与 45.4% 之间；C 要求队列确实是 e2e 的主项 |
| `f_02` 不参与分类 | §3 N10：本场景 f_02 随分母口径在 14%–60% 之间跳，**口径不安全**，只作报告量、不作判据 |

### 5.5 有效性门（任一不通过 ⇒ 该 (g, D, T) 点 INVALID，写明失败门名）

| 门 | 判据 | 理由 |
|---|---|---|
| **G1 期限有效** | `deadline_primary_loss.status == COMPUTED` 且 `binding.binding == true` | 平台自身 cell-success 要求（`t1_tasks.py:2270-2280`）；D 不 binding 时 miss ≡ 100−delivery，主终点失去"迟到"语义 |
| **G2 无 horizon 截断** | §4.4：`|M_decl − M_eff| ≤ 1.0 pp` | 否则绝对 miss 不可解释 |
| **G3 NO_ROUTE 不主导** | `f_noroute ≤ 0.05` | 否则场景在测"不可达"，不是"拥塞/时态"；`[解析]` 本场景预期 0，一旦出现即为结构性缺陷 |
| **G4 交付不塌** | `d_deliv ≥ 0.80` 且 `f_censor ≤ 0.10` | 80% 沿用 2026-10-27 线 F2 的发射前冻结门（`prereg-criteria-f2.json`）；censoring 单列，避免把"没跑完"当"丢包" |
| **G5 损失不主导迟到** | `f_loss ≤ 0.10` | 否则进入"丢包通道"，与迟到/队列机制不同源；F2 的教训是两条通道要分开设计（`F2-STOPPED-20261027.md` §2） |
| **G6 状态项有物理通道** | T 需 `φ_isl ≥ 0.10`，C 需 `φ_isl ≥ 0.25`；并（若取到 raw）`n_cand2 ≥ 0.20` | 若 e2e 主要耗在 access/holding（机制**碰不到**，见 `03-平台与场景核查.md` §2.4：`ideal_current` 只替换 ISL egress 队列），或决策几乎无第二候选，则"时态失配"没有可测的物理通道 |

**外加一条场景级不适格**（不针对某个 g，而是针对整轮设计）：

```
G7 三区制覆盖：最终用于主结论的 g 集合，必须至少含 {P, T, C} 各 1 个 VALID 点。
   若拿不到（例如 g=4 撞内存墙、g=0.25 全 0），则本轮**只能**报"区制未识别"，
   不得把仅存的那个点写成"时态失配（不）严重"。
```

### 5.6 选点顺序与覆盖规则（消除事后挑选）

```
Step 0  g=1.00 分类（这是 profile 的现状，先测它，避免"先挑好看的点"）
Step 1  g=0.50 分类
Step 2  g=2.00 分类
Step 3  g=0.25 分类
Step 4  g=4.00 分类
（固定顺序，不因 Step k 的结果而换点；每点 1 run 参考臂）

Step 5  若已覆盖 {P,T,C} 三个 VALID 点 ⇒ 停扫描
        否则把 5 个 g 全部测完，按 G7 处理
Step 6  预算门：单点若触发 §6.5 的停止线（>60 min 或 RSS>40 GB）⇒ 记 "NOT_RUN_BUDGET"，
        不许用其它 g 顶替，也不许缩减臂数来偷跑

Step 7  时态矩阵只允许跑在"VALID 且被 §5.7 冻结表收录"的 g 上；
        每个 g 至少 3 臂（stale / now / candidate）× 6 seeds，同 trace、同 seed、同 D。
```

**主结论的形式（强制定量、免疫单点挑选）**：

```
主报告量  Δ(g) = miss_pp[candidate] − miss_pp[stale]   （逐 seed 配对，t-CI df=5）
结论 = "Δ 随 g 的斜率/单调性 + 每个区制内的点估计与 CI"
即：要么给出跨区制的剂量-反应，要么明确写"区制未识别"。
禁止：只报某个 g 的 Δ 并据此说"严重/不严重"。
```

### 5.7 冻结机制（"在看到时态结果之前"的可核验实现）

1. 跑 Step 0–5 的参考臂（`--arms stale`），只导出 §5.3 的场景指标。
2. 把 `{g, u^W_p90, φ_isl, f_02, d_deliv, f_censor, f_noroute, f_loss, G1..G6 判定, 归类}` 写成一个 JSON 表。
3. **对该表算 sha256，写入 `TIME-ALIGNMENT-20261030/PREREG-REGIME-<date>.md` 并提交**；表里同时写死 §5.4/§5.5 的全部数字与 6 个 seed 列表。
4. **第 3 步完成之前，禁止发射任何 `now`/`candidate`/`orc` 臂，禁止计算任何臂间差。**
5. 此后若发现分类表与实测不符（例如重跑同一 g 得到不同 u^W_p90），只能追加"重测记录"，不得改判历史点。

### 5.8 本规则如何防止"事后挑场景"

| 典型作弊 | 被哪一条挡住 |
|---|---|
| 看完结果再选 g | §5.6 固定顺序 + §5.7 冻结表；顺序与门槛在看结果前已写死 |
| 只报最有利的 g | §5.6 主报告量是 Δ(g) 的剂量-反应/斜率，且 G7 要求覆盖三区制 |
| 换分母口径让场景看起来"过渡" | §5.3 把 u^W 钉成 `served_bits/(1000e6·0.9)`；平台 u_k 只能作对照（×0.75） |
| 把不可达/丢包型失败混进"时态失配" | G3/G5 直接判 INVALID |
| 用 1.2 s 短窗 + 大 D 制造高 miss | G2 截断检查 + §4.4 的 `T ≥ last_emission + D + 0.1` |
| 用"holding 主导"的场景宣称测到了队列时态价值 | G6：φ_isl 门槛；机制在物理上碰不到 access/holding（`03` §2.4） |

---

## 6. 成本：单体 run 与批次预算

### 6.1 拟合（数据：`2026-10-27-perfect-state/cost-runs-20261028.csv`，`microbench` 组同配置只变 duration）

三个点（duration 0.4/0.8/1.6 s，offered 50 Mbps、packet 12000 bit、adv 0.2、audit 2000）：33.262 / 104.449 / 311.834 s。

```
log-log OLS:  wall = 147.25 · duration^1.6144      （n=3）
逐对指数:     0.4→0.8 : 1.6509    0.8→1.6 : 1.5780    0.4→1.6 : 1.6144
文档口径:     wall ∝ duration^1.65  （COST-MODEL-20261028.md §0/§2.1，与之相容）
```

**同批还有两条必须一起用的拟合**（同文档，n=9，`P1` 组）：

```
wall_s        = −21.0 + 5.452e-4 · events        （本文按同 3 点重拟合；文档 9 点值 = −12.7 + 5.277e-4，R²=0.9937）
peak_rss_kb   = 310517 + 913.9 · packets        （R²=0.9961）
output_bytes  = 7.18e6 + 10730·packets + 27004·audit_records   （R²=0.9896；audit_records=min(decisions,2000)）
events        ≈ 155–183 · packets（主口径臂间）；600 kbit 族实测 129–180 ev/pkt（`03` §7.2）
```

⚠️ **两条拟合在本题会打架**：duration 序列里 `packets ∝ duration`（同为线性），所以 `wall ∝ duration^1.65` 与 `wall ∝ events^1.65` 不可分；而直接用 events 线性式外推到 1.6 M 事件给出 862 s，用 duration^1.61 从 1.6 s 锚点推 1.2 s 只给 198 s——**差 4×**。原因：duration 序列的"超线性"里混着"队列变深 → 每次事件更贵"，而 F2 实测（3.4 M 事件 / 18–20 min ⇒ 0.32–0.35 ms/事件）又表明大事件量时单位成本**下降**。故本节一律**双模型给区间，并以 events 轴为主**，标注为**外推**。

### 6.2 本 profile 的包数/事件/内存/输出 `[解析]`

```
packets(g)  = 10000·g × 0.9 = 9000·g      （g=1 ⇒ 9000 ± 95）
events(g)   ≈ 129…180 × packets(g)        = 1.16…1.62 M at g=1
RSS(g)      ≈ 8.1…9.7 GB at g=1           （两式：310517+913.9·packets ； 6 GB/1e6 events）
output(g)   ≈ 61 + 96.6·g MB             （audit 2000：7.18 + 10730·9000g + 54.0 MB）
```

### 6.3 估计表（**ESTIMATE**，双模型；并发按 `C = min((24−4)/1.01, (64−16)/RSS_GB, 磁盘/输出)`）

| g | offered | packets | events | wall 主（events 线性） | wall 对照（0.32–0.52 ms/ev） | RSS | 输出 | C |
|---|---|---|---|---|---|---|---|---|
| 0.25 | 1500 | 2 250 | 0.29–0.41 M | 137–200 s （2.3–3.3 min） | 93–211 s | 2.3–2.5 GB | 85 MB | 19 |
| 0.50 | 3000 | 4 500 | 0.58–0.81 M | 295–421 s （4.9–7.0 min） | 186–421 s | 4.4–4.9 GB | 109 MB | 9 |
| **1.00** | **6000** | **9 000** | **1.16–1.62 M** | **612–862 s （10–14 min）** | 372–842 s | **8.1–9.7 GB** | **158 MB** | **4** |
| 2.00 | 12000 | 18 000 | 2.32–3.24 M | 1 245–1 746 s （21–29 min） | 743–1 685 s | 16–19 GB | 254 MB | 2 |
| 4.00 | 24000 | 36 000 | 4.64–6.48 M | 2 511–3 512 s （42–59 min） | 1 486–3 370 s | 31–39 GB | 447 MB | **1** |

两列 wall 的差额就是 §6.1 的模型冲突：0.32 ms/事件来自 F2（小缓冲、无明显排队），本场景排队更深，**排程请用"主"列**（更保守），"对照"列只作下界参考。

**1.2 s vs 4 s（两种解释，必须先拍板用哪种）**：

| 方案 | 改动 | packets | wall（g=1） | RSS | 评价 |
|---|---|---|---|---|---|
| **A. 只加 horizon** | `duration_s: 1.2→4.0`，发射窗仍 `[0.1,1.0]` | **9000（不变）** | **≈620–870 s（+5–10 s）** | 8–10 GB | **trace 不变**（`emission_end_s` 显式）；只多出控制面 ~8.4 k 事件；**修好 §4.4 的截断**，D 可以取 0.5 s |
| **B. 时长与发射同扩** | `duration_s: 4.0` + `emission_end_s: 3.8`（W=3.7 s） | 37 000 | **2 580–3 610 s（43–60 min）** | 32–40 GB | 只在需要更长稳态/多档负载时才值；C 塌到 1 |

`duration^1.61` 外推（仅作上界参考，**不推荐**用）：1.2 s→198 s、4.0 s→1 380 s；与 events 轴差 3–4×，见 §6.1 的警告。

### 6.4 批次预算（ESTIMATE）

- **区制扫描（§5.6 Step 0–5）**：5 个 g × 1 run（stale, seed 7）≈ 0.6–1.5 h 串行；按 C=4 并发（g≤1）+ 其余串行，**≈ 40–70 min**。
- **时态矩阵（每个 g）**：3 臂 × 6 seeds = 18 run。g=1 ⇒ 18×13 min ÷ 4 ≈ **1.0 h**；g=0.5 ⇒ 18×6 min ÷ 9 ≈ **12 min**；g=2 ⇒ 18×26 min ÷ 2 ≈ **3.9 h**。
- **覆盖 {P,T,C} 的最坏情况**（g=0.5,1,2 三个点）：**≈ 5.2 h 墙钟**、输出 18×(0.11+0.16+0.25) ≈ **9.4 GB**、峰值内存 g=2 时 2×19 GB = 38 GB（64 GiB 的 59%）。
- 若改用方案 A（T=4 s、发射窗不变），以上数字**不变**（±5%）。

### 6.5 停止线（照抄 2026-10-28 线的发射前规则，本场景沿用）

| 指标 | 正常 | 警戒 | 停止（停本批） |
|---|---|---|---|
| 单 run wall | ≤1.5×上表估计 | 1.5–2× | **>2× 或 >2 个 run 超时** |
| 单 run RSS | ≤1.1×上表 | 1.1–1.4× | **≥48 GB 或任一 run `rc=137`** |
| 单 run 输出 | ≤200 MB | 200–400 MB | >600 MB（说明 audit/输出模式变了，代价模型失效） |
| 失败率 | 0 | 1 个工程失败 | **≥2 个真失败** ⇒ 停批查因 |

---

## 7. 与任务书/交接材料的差异（已核实，请上游据此更正）

| # | 交接里的说法 | 核实结果 |
|---|---|---|
| 1 | "ISL constant 1000 Mbps with 24 Mbit queue 和 **3 Mbit**…" | profile 里**不存在任何 3 Mbit 常数**。实测值：`isl_queue_bits: 24000000`（24 Mbit，40 包）、`holding_queue_bits: 268435456`（256 Mibit）、`uplink/downlink_queue_bits: 4e9`、`control_plane.packet_bits` 未设 ⇒ 缺省 8000 bit（`config.py:524`）。"3 Mbit" 疑为笔误，**不得引用** |
| 2 | "prior line found ISL util p50 **0.36**" | 该数字属于 **F3 底座场景**（1500 B / ISL **2 Mbps** / offered 50 / T=1.6），**不是**本 profile（600 kbit / 1 Gbps / 6000 / T=1.2）。见 `F3-RESULTS-20261028.md:36`、`MORNING-REPORT-20261028.md:50`。本 profile **无任何实测** |
| 3 | "access-side holding dominated" | 属 2 Mbps / 400 Mbps 族（`holding 8.69 vs isl_data 3.24 Mbit·s`）。本 profile 的接入侧是 256 槽 / 20 Gbps / 268 Mbit holding，`[解析]` 每城 1000 Mbps 只占上行 5% ⇒ **不能沿用该结论**，必须重测 |
| 4 | 交接未提 | profile **没有 `demand.deadline_s`** ⇒ 主终点必须靠 CLI `--deadline-s` 注入，且 D 是场景旋钮（§4.4） |
| 5 | 交接未提 | 1.2 s 内 **无切换（0.1 s 网格）/ 0 次拓扑重匹配 / 几何只走 0.075°** ⇒ 只能测"队列状态年龄"，不能测"几何/拓扑陈旧"（§0.4） |
| 6 | 交接未提 | `routing.policy: known_serving` 使用**真值服务集**（不吃广告）⇒ 场景里**不存在**"目的地可达性信息陈旧"这一层；陈旧只发生在队列/资源状态（§1.8） |
| 7 | 交接未提 | profile 文件头注释整段陈旧（v7b150 / 24 星 / 10 Mbps / 8000 bit），与正文冲突（§1 警告） |

---

## 8. 复现（只读，本机，无 run）

```bash
cd /Users/lge/Desktop/topic/.wt-instr-20261030/CODE
# 1) profile 事实
python3 -c "import yaml,sys;print(yaml.safe_load(open('leo_sim/profiles/stage_c_e2_6g_adv200.yaml')))" 2>/dev/null \
  || sed -n '11,98p' leo_sim/profiles/stage_c_e2_6g_adv200.yaml
# 2) 代码事实
sed -n '283,296p;1358p;1376p;1392,1396p' leo_sim/trace.py
sed -n '332,345p' leo_sim/metrics.py ; sed -n '452,500p' experiment_platform/outcome_metrics.py
sed -n '558,571p;646,663p' leo_sim/model.py ; sed -n '208,216p' leo_sim/routing.py
# 3) §1/§4 的几何与 OD 解析（无仿真，纯算术；打印 §1.2/§1.7/§4.1 的数字）
python3 - <<'PY'
import sys, math; sys.path.insert(0,'.')
from leo_sim import model, routing
N,P,ALT,INC=140,7,600.0,98.6
c=model.Constellation(N,P,ALT,INC,min_elevation_deg=5.0,max_isl_km=3000.0)
topo=routing.build_topology(c,N,["N","S","E","W"])
adj={s:{} for s in range(N)}
for s,nb in topo.items():
    for d,n in nb.items():
        if c.isl_available(s,n,0.0): adj[s][n]=math.dist(c.ecef(s,0),c.ecef(n,0))
sites=[("tokyo",35.68,139.69),("singapore",1.35,103.82),("london",51.51,-0.13),
       ("newyork",40.71,-74.01),("saopaulo",-23.55,-46.63),("lagos",6.52,3.38)]
R=6371.0; C=299792.458
def hav(p,q):
    a,b=math.radians(p[1]),math.radians(p[2]); cc,d=math.radians(q[1]),math.radians(q[2])
    h=math.sin((cc-a)/2)**2+math.cos(a)*math.cos(cc)*math.sin((d-b)/2)**2
    return 2*R*math.asin(min(1.0,math.sqrt(h)))
vis={n:[s for s in range(N) if c.ground_visible(s,la,lo,0.0)] for n,la,lo in sites}
load={}; rows=[]
for a in sites:
    ds=[max(hav(a,b),100.0) for b in sites]
    w=[0.0 if a is b else 1.0/ds[j]**2 for j,b in enumerate(sites)]; tw=sum(w)
    for j,b in enumerate(sites):
        if a is b: continue
        flow=1000.0*w[j]/tw; tg=vis[b[0]]
        dist={s:10**9 for s in range(N)}; dq=[]
        for t in tg: dist[t]=0; dq.append(t)
        while dq:
            u=dq.pop(0)
            for v in adj[u]:
                if dist[v]>dist[u]+1: dist[v]=dist[u]+1; dq.append(v)
        best=None
        for s in vis[a[0]]:
            if dist[s]>=10**9: continue
            path=[s]; cur=s; pc=0.0
            while dist[cur]>0:
                nxt=[(adj[cur][v],v) for v in adj[cur] if dist[v]==dist[cur]-1]
                if not nxt: break
                r,v=min(nxt); pc+=r; cur=v; path.append(v)
            cand=(dist[s],pc,tuple(path))
            if best is None or cand<best: best=cand
        h_,pc,path=best; rows.append((a[0],b[0],flow,h_,pc/C*1e3))
        for u,v in zip(path,path[1:]): load[(u,v)]=load.get((u,v),0.0)+flow
u=sorted(v/1000.0 for v in load.values()); navail=sum(len(x) for x in adj.values())
print("active edges %d / available %d ; Mbps-hop %.0f ; mean hops %.2f"%(
      len(load),navail,sum(load.values()),sum(load.values())/6000))
print("in-window util(active): p50 %.3f p90 %.3f max %.3f ; f(u>=0.2) over all = %.3f"%(
      u[len(u)//2], u[int(0.9*len(u))], u[-1], sum(1 for x in u if x>=0.2)/navail))
print("OD top5 share %.3f"%(sum(sorted((r[2] for r in rows),reverse=True)[:5])/6000))
PY
```

**复算校验**：`[已核]` `wc -l CODE/data/traffic/mlab_2026-05-27.csv` = 44 930（含表头 ⇒ 44 929 数据行）；`[已核]` `COST-MODEL-20261028.md` 与 `cost-runs-20261028.csv` 的 duration 三点数值一致（33.262 / 104.449 / 311.834）。
