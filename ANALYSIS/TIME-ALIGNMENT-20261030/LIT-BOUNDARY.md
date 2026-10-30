# LIT-BOUNDARY · 参数可信区间 与 新颖性边界

> **写者**：`lit-scout`（本轮唯一写者，只写本文件）。**时间**：2026-10-30。
> **任务**：task-4 —— 只做两层证据，不做广泛文献综述。
> **输入**：`domain-map`（187 篇，`data/papers/Pxxxx.yaml` + `sources/core-corpus|library` 全文转换件）；`2026-10-27-perfect-state/F3-LITERATURE-20261028.md`、`01-文献审计.md`（**作为线索，逐条复核，不照抄**）。
> **证据等级标注**：
> `[实测]`＝论文/报告报告的测量值；`[推算]`＝由已核事实按明确公式推出的量（公式写在旁）；`[假设]`＝仿真配置或模型选择，作者自己也不声称是运营值；`[未核]`＝本文件未核到原文/无 URL。
> **能力边界（先说清楚）**：本机 `web_fetch` 对多数站点返回 `non-public IP`（starlink.com、orbitalradar.com 均失败），外部核验改用 `curl` 取白名单可达页（Wikipedia 成功）。**凡标 `[未核]` 的条目不得进入论文**。所有 domain-map 引用均在本机全文转换件上按行核对过；转换件**无出版商排版页码**，引用页码须另行核对。

---

## 0. 一句话结论

1. **参数层**：在本仓库场景的三个关键无量纲量上，我们是**离开 LEO 实测区间的**——`τ_ser/τ_prop ≈ 0.9–1.8`（真实 LEO ≈ 3.6e-4，差 ~2 500–5 000×）、`ISL 2 Mbps`（已发表假设的下限 10 Mbps 之下 5×，光链路低 4 个量级）、`广告 20/200 ms`（唯一实测的运营周期是 **15 s**，快 75×–750×）。**必须写成"受控压力场景"，不能写成"LEO 典型值"。**
2. **新颖性层**：文献在"**如何补偿陈旧状态**"上已很拥挤（TAP-DAR / LoFi / PMPF / DPP-E2E / LOGGIA / VoI 理论）；在"**何时陈旧状态真的改变决策并产生可归因 regret**"上，**本文件未能找到任何一篇把 age→drift→差分 drift→margin 穿越→动作改变→regret→系统严重度 这条链完整做出来的工作**。但边界**不是全新**：Mirchandaney-Towsley-Stankovic 1989 已经做过"延迟 × 负载 → 状态值不值得采集"的**系统级可行域**。⇒ 可以主张"**决策条件化、逐候选、含丢包通道**"的空白，**不可以**主张"首次追问陈旧状态何时有害"。
3. **最要命的一条**：`3.24%`（TAP-DAR 距 oracle 的**平均**残差）与 `11.97%`（相对 Scheme 2 的**最大**降幅）是**两个不同口径**；已逐字复核，**不能用 3.24% 反推"陈旧价值 ≤3.5%"**。

---

# Layer 1 · 参数可信区间

| # | 参数 | 可信区间（LEO 现实） | 来源 | 置信度 |
|---|---|---|---|---|
| 1 | **状态/分配更新周期（运营 LEO，实测）** | **15 s**，全局同步；终端↔卫星分配每 15 s 重算一次 | `P0002`（arXiv:2307.00402，§2 全文实测：延迟台阶出现在每分钟第 12/27/42/57 s）；`P0065`（DOI 10.1145/3589334.3645328，摘要＋§7 明写 "globally synchronized 15-second reconfiguration intervals"） | **实测**（两篇独立测量一致） |
| 2 | **链路状态/负载广告周期（研究系统）** | 无统一值：**事件触发＋阈值**（负载变化超 δ 才发，TTL 限 n 跳）为主流；早期 ELB/TLR 为**固定周期** | `P0181`（DOI 10.1109/SATELLITE59115.2023.00010，§II/§III-A：LoFi 参数 (n, δ)，"ELB…TLR…exchange the link load status periodically under a fixed duration"） | **实测性描述**（对既有协议机制的描述），但**具体秒数未给** ⇒ 周期数值 `[未核]` |
| 3 | **控制/重评估步长（研究级）** | 跨 **5 ms – 60 s**：5 ms 步长×400 步（2 s episode）｜15 s 时隙｜1 min 时隙（＋包到达事件触发） | `P0163`（arXiv，§5 "400 time steps of τ = 5 ms"）；`P0111`（DOI 10.1109/tcomm.2025.3636092，Table II `τ = 15 s`）；`P0174`（DOI 10.1109/TCCN.2026.3720213，Table I `T_slot = 1 min`，§IV 状态广播由**包到达事件**触发） | **假设**（各文自定仿真配置） |
| 4 | **ISL 单跳传播时延** | **3.3 ms @1 000 km｜6.7 ms @2 000 km｜13.3 ms @4 000 km**（`τ=d/c`, `c=3.34 µs/km`） | 距离：`P0161`（TRACE，Table "ISL distance mean 1 000 km"）、`P0111`（1 100 km 例、500 km 量化段）；`P0174`（"thousands of kilometers… millisecond-level delay"）。公式 `τ=d/c` 见 `P0009`（§V "d(t)=dist(t)/c"） | **推算**（几何＋光速；距离取自已发表仿真参数） |
| 5 | **ISL 容量** | 已发表假设跨 **10 Mbps – 25 Gbps**：10 Gbps（10⁴ Mbps）｜20 GHz 光带宽｜10 Mbps（类 Iridium 仿真，作者写明 "for convenience of simulation"）｜**25 Gbps @ ≤4 000 km**（SpaceX "mini laser"） | `P0009`（Table I `ISL bandwidth 10 Gbps`，并自述 "configuration assumptions, not operational measurements"）；`P0161`（`ISL bandwidth 20 GHz`）；`P0181`（§V `10 Mbps`）；外部：[Wikipedia·Starlink](https://en.wikipedia.org/wiki/Starlink) 引 SpaceX 2025-08-25 帖（"link speeds of 25 Gbps at distances up to 4000 km"），并注明 **SpaceX 对 ISL 细节保密** | **假设＋外部转述**；**运营值 `[未核]`**（厂商保密） |
| 6 | **单星/单用户队列深度** | **≈1 400–1 600 包**（按**包**限而非按字节限；`drop-front` 而非 `drop-tail`；**非** per-flow 公平队列） | `P0140`（§7 Queue sizing：拟合 1 400–1 600 包；同文 §4 drop-front、§6 非 per-flow FQ） | **实测**（>10⁷ 探测包、2 站点、模型拟合） |
| 7 | **队列 ≈ 多少 BDP** | **≈1.8–2.2 × BDP**（1 500 包×1 500 B=18 Mbit；BDP @200 Mbps×40–50 ms = 8–10 Mbit） | 队列：`P0140`；速率与 RTT：`P0065`（Table：Starlink 空闲 RTT 40 ms，受控吞吐 220 Mbps） | **推算**（两个实测值相除；调制/共享假设未建模） |
| 8 | **流量非均匀性（top-k OD 份额，实测）** | **本库无任何实测 top-k OD 份额**（定向检索 `OD matrix / traffic matrix / gravity / Zipf / top-k` 命中 20 篇，**逐篇查看后无一给出实测份额**）。可用的**实测代理**：区域 min RTT 15–18 ms（基建好的区域）vs 35–40 ms（偏远）；全球中位 RTT 40–50 ms、p75 goodput 50–100 Mbps（下行） | 代理实测：`P0007`（§V-B：Bruhl/Seattle/Victoria min RTT 15–18 ms，Ulukhaktok 35–40 ms）；`P0065`（§4 全局分布）。**合成** OD：`P0175`（DOI 10.1109/TAES.2026.3686797，§III-B gravity 模型 `β=1.2, η=1.6`，§VI-A） | **实测（仅非均匀性的存在）／假设（top-k 份额）** ⇒ **我们的 OD 偏斜必须声明为受控干预** |
| 9 | **突发/热点在 100 ms–秒 尺度的可信度** | **结构上有据、形态上无据**。实测到的百毫秒级结构：1.33 ms 链路帧；15 s 重构边界的受影响窗口（前 140 ms／后 75 ms，起始峰 +74 ms 一类）；**探测突发本身**在 ~140–230 ms 处可触发 50% 丢包与 30–60 ms OWD 平台。**"区域需求 ×N、持续 200 ms"没有任何实测或建模先例** | 帧与重构：`P0140`（§3 "1.33 ms link layer frames"＋"15-second reconfiguration interval"）、`P0002`、`P0065`；突发内动态：`P0140`（Fig. 3：6 000 包 @500 Mbps→丢包 50%，发送时长 ≈144 ms；Fig. 3b 50 ms/230 ms 处容量调整）；秒级 ON/OFF 为**模型**：`P0175`（Pareto `ℓ_min=500 ms, κ=1.5`） | **实测（粒度/边界/帧）／假设（突发乘性幅度与区域维度）** ⇒ 200 ms ×3 **不得**写成 LEO 现实现象 |
| 10 | **利用率/拥塞区间** | 运营：中位远未饱和（p75 goodput 50–100 Mbps vs 受控峰值 220 Mbps ⇒ **推算 0.23–0.45**）；**拥塞确实可观测**：下载时 RTT 抬升 2–4×、可达 400–500 ms。研究：大量工作在 ρ→1 附近做**压力**评估 | `P0065`（§4 Latency Under Load："≈2–4× increased RTTs, reaching almost 400–500 ms"；Table 220 Mbps）；`P0177`（摘要 "under high overloading"）；`P0174`（5 档负载，**具体数值本文件未核 `[未核]`**） | **实测（拥塞现象）／推算（利用率量级）／假设（ρ≈0.9–1.1 为压力档）** |
| 11 | **无环 / 有界绕行（工程实践）** | **无环是硬约束**：按构造保证（"never sent back to satellites where they came from"）｜跳数上限 H=15，超限丢弃｜环路流量直接丢＋`T_max=200 ms`｜实际路径伸展 **<8%**（平均、最差划分） | `P0033`（DOI 10.1109/90.917071，§III-C-3 显式无环规则＋本地缓冲阈值 ξ 拥塞规避）；`P0161`（约束 14c/14d：`H=15`）；`P0111`（§III：含环即丢）；`P0009`（§VI-E：`S_hop=H_area/H_flat`，最差划分平均 stretch <8%） | **实测（P0009 为包级仿真测量）／设计约束（P0033/P0161/P0111）** ⇒ 绕行预算 ≈ **8%** 是可比的上界 |

### 1.1 我们场景的三个"出区间"事实（必须写进 limitations，不能回避）

| 量 | 我们的取值 | 现实区间 | 倍数 | 后果 |
|---|---|---|---|---|
| `ISL 速率` | **2 Mbps** | 10 Mbps–25 Gbps（已发表的最低假设是 10 Mbps） | 低 5× – 10⁴× | 链路变成**带宽受限**而非**传播受限** |
| `τ_ser/τ_prop` | **0.9–1.8**（1 500 B/2 Mbps = 6 ms；τ_prop 3.3–6.7 ms） | **≈3.6e-4**（1 500 B/10 Gbps = 1.2 µs；÷3.3 ms） | 差 **2 500–5 000×** | 排队项主导，而非传播项主导 ⇒ **不是 LEO 的主阶结构** |
| `广告周期` | **20 / 200 ms** | 运营实测 **15 s**（全局分配）；研究系统 5 ms–60 s | 快 **75×–750×** | 我们比现实**更新鲜** ⇒ 检出效应是"高新鲜度下仍有"，**不能**外推为"15 s 下只会更大"（无单调性定理） |

> 结论：**正确表述**是"在预注册的 2 Mbps / 1 500 B / 20–200 ms 压力变换下，机制方向…"；**错误表述**是"LEO 典型参数下…"。`τ_ser/τ_prop` 反向是本场景最需要向审稿人交代的一条。

---

# Layer 2 · 新颖性边界

## 2.1 四条线的现状（只列已核到全文的原句）

**(i) 延迟/陈旧网络状态信息用于路由/负载均衡**

- **`P0192` Mirchandaney, Towsley & Stankovic 1989**（IEEE ToC 38(11):1513-1525）：Forward/Reverse/Symmetric 三种负载共享算法，**状态信息在决策时已过期**；原文明确目标是"determine **the range of delays and traffic intensities over which state information is worth gathering** and useful load sharing can be performed"。⇒ **"何时状态不值得采集"这一问法本身是 1989 年的先例。**
- **`P0193` Atar & Lipshutz 2021**（Math. of OR 46(1):268-300，DOI 10.1287/moor.2020.1056）：原句 "a routing policy that performs well when applied to the current state of the queues **can perform quite poorly when applied to the delayed state**"；用**估计值**替代当前值，证明重交通下 estimated-JSQ 渐近最优。假设：**无限容量缓冲**、单跳并行队列、FIFO、重交通。
- **`P0194` Katsikopoulos & Engelbrecht 2003**（IEEE TAC 48(4):568-574）：把含**观测延迟/动作延迟/代价延迟**的 MDP 约化为无延迟 MDP，**状态空间扩大**。⇒ 框架级结论：最优值依赖整条 age 剖面；但**没有**给出陈旧代价的刻画，也不涉及网络。
- **`P0174` Liu et al. 2026 TAP-DAR**（IEEE TCCN，DOI 10.1109/TCCN.2026.3720213）：LEO 专属；"time-aware prediction + dynamic attention"；把**无补偿的过期值**（Scheme 2, Delayed-Comm）当对照；自报 `11.97%`（相对 S2 的**最大**时延降幅）、`29.05%`（相对 S2 的**平均**丢包降幅）、`3.24%`（相对 oracle 的**平均**残差）。**关键自述**：①"as the constellation density increases… the performance advantage of TAP-DAR correspondingly narrows"；②未来工作明写要引入 "more realistic traffic models that capture **self-similarity and burstiness**" ⇒ **突发维度作者自己认作空白**。
- **`P0181` Shan et al. 2023 LoFi**：用 `δ`（负载变化阈值）与 `n`（广告 TTL 跳数）两个旋钮在"广告精度 vs 控制开销"间取折中；结果指标是丢包/时延/开销，**没有**逐决策分析。
- **`P0177` Ouyang et al. 2024 PMPF**（IEEE TVT，DOI 10.1109/TVT.2024.3449804）：**只把"会显著影响性能"的拥塞通知发出去**，且"routing table **only when it would significantly improve network performance**" 才更新 ⇒ **决策相关性门控**（本库最接近"新信息是否会改变决策"的思想），但阈值是启发式，**没有 age 参数、没有 regret 分解**。

**(ii) 遥测陈旧性的恢复/预测**

- **`P0163` Boltres et al. 2026 LOGGIA**（arXiv）：把 telemetry-aware routing 明确建模为**带通信与推理延迟的闭环控制**；结论：忽略延迟的集中式状态训练会在真实延迟下失效，**全本地**部署最好。⇒ 这是"遥测延迟确实改变结果"最强的一手实证，但拓扑是 B4/GEANT（数据中心/WAN），步长 5 ms，指标是 goodput，**不是**逐决策 regret。
- **`P0027` Chiariotti et al. 2020**：多跳 LEO 的 AoI 闭式界；"需要识别 age 的**瓶颈节点**"。⇒ age 沿路径累积、由最慢一跳支配；但它是 **AoI 界**，不是**决策价值**。
- **`P0171` Soleymani et al. 2021 VoI**（IEEE TAC，DOI 10.1109/TAC.2021.3113472；配套 `P0172` 全局最优、`P0173` 语义度量）：把 VoI 定义为**值函数对一条信息的变差**；证明 `VoI` 是 trigger 与 controller 估计差的对称函数；由此可直接推出 **最小化 AoI ≠ 最大化决策性能**。⇒ 这是"价值取决于决策"的定理级表述，但为单源 Gauss–Markov + LQG、无排队、无路由、无多候选。

**(iii) 候选特定 / 到达时刻特定的状态评估（candidate ETA as state use-time）**

- **`P0175` Kim et al. 2026 DPP-E2E**（IEEE TAES，DOI 10.1109/TAES.2026.3686797）— **本库最贴近的一条**。原文把问题命名为 "**routing–topology mismatch**"：路径在 `t₀` 选定，但第 k 跳要在 `t_k > t₀` 才尝试，"by which time the neighbor set or satellite–ground visibility may have changed"；方案 "**filters candidate next hops according to predicted transmission–start-time feasibility**"。⇒ **逐候选、按该候选自己的使用时刻**做状态评估，**已有人做**。
- **`P0174`**：预测"邻居在**目标时刻**的负载"，但每个邻居只有**一个**预测值，不是"每个候选各自的 ETA 一个值"。
- 其余（`P0177` 的 potential、`P0181` 的 δ/n、`P0111` 的 tile-coding 上下文）都是**当前时刻**的状态，不是到达时刻。

**(iv) 拥塞感知的分布式 LEO 路由**

- **`P0033` Ekici et al. 2001**（ToN，DOI 10.1109/90.917071）：**完全本地**——不交换任何流量信息，靠输出缓冲填充度 `> ξ` 判拥塞并改走次方向；**按构造无环**（不回到来处，极区除外）。⇒ 用"零状态交换"**绕开**了陈旧问题。
- 分布式/拥塞感知家族的共同做法：只交换**一跳或几跳**的粗粒度负载（队列长度、BUSY 等级、potential），以控制开销为主约束（`P0181`、`P0177`、`P0111`、`P0161`、`P0130`、`P0117`）。**没有**一篇把"拥塞信号的**年龄**"作为一阶对象去标定。

## 2.2 直接回答那个问题

> **问题**：现有工作大多在解"**如何补偿陈旧状态**"；那么**有没有人系统研究过"陈旧状态何时真的改变决策并产生可归因的 regret"**——即 age → drift → 差分 drift → margin 穿越 → 动作改变 → regret → 系统严重度？

**答案：没有（在本文件核到的范围内）。但"边界"必须切成两半说。**

| 链条环节 | 是否已有工作 | 证据 |
|---|---|---|
| age（度量新鲜度） | **有，且很成熟** | `P0027`（LEO 多跳 AoI 界）、`P0171/172/173`（VoI≠AoI）；AoI 家族另有 `P0015`、`P0061`、`P0087` |
| drift（状态漂移 + 补偿） | **有，拥挤** | `P0174`（T-LSTM 预测）、`P0163`（延迟感知闭环）、`P0188`（预测式路由，仅摘要级 `[未核]`）、`P0185`（TE 的流量预测） |
| **差分 drift（逐候选、按其使用时刻）** | **部分有，仅一例且只做可行性否决** | `P0175` 的 transmission-start-time feasibility filtering |
| **margin 穿越（预测误差是否跨过决策边界）** | **无人以 age 为自变量做过** | 最接近：`P0192` 的（延迟×负载）**可行域**（系统级）；`P0177` 的"显著才更新"（启发式阈值）；DFL 家族的 regret 是**预测误差**而非**年龄**（`P0164`、`P0165`、`P0187`、`P0178`） |
| 动作改变（argmin 是否翻转） | **无** | 未检到任何工作把 `P(argmin 翻转 | age)` 当因变量 |
| regret（决策层归因） | **理论有、路由无** | `P0171/172`（VoI＝值函数差）、`P0193`（延迟状态的重交通代价）；`P0174` 只给**聚合**差（11.97%/29.05%） |
| 系统严重度（丢包/时延） | **有聚合值，无分解** | `P0174`；`P0192`（系统级吞吐/时延） |

**⇒ 明确判定：边界"部分清楚"**：
- **可主张的空白**：**决策条件化的、逐候选的、含有限缓冲丢包通道的 age→regret 因果分解**，在 LEO 包级路由里**没有**。
- **不可主张的空白**："首次追问陈旧状态何时有害"（`P0192` 1989 已有系统级可行域）、"LEO 里没人研究陈旧状态"（`P0174/0175/0177/0181/0163` 都在）、"候选到达时刻评估是新的"（`P0175` 已做可行性版）。

## 2.3 最接近的 5 篇（各覆盖什么 / 不覆盖什么）

| 排名 | 工作 | 覆盖 | **不覆盖（相对我们的链条）** |
|---|---|---|---|
| **1** | **`P0174` TAP-DAR**（Liu 2026, IEEE TCCN, DOI 10.1109/TCCN.2026.3720213） | LEO 分布式路由 + 一跳邻居队列**陈旧状态**；给出 stale(S2) 与 oracle(S4) 之间的**聚合**差距；重负载下优势收窄 | 无**逐决策**归因（不报 argmin 翻转率）；无 margin/drift 分布；无 age 扫描（广播由**包到达事件**触发，非固定周期）；缓冲 50 包、8 KB、500 MHz、`T_slot=1 min` ⇒ 与我们 `τ_ser` 量级差极远；**作者自认突发性为未来工作** |
| **2** | **`P0175` DPP-E2E**（Kim 2026, IEEE TAES, DOI 10.1109/TAES.2026.3686797） | **决策时刻 ≠ 使用时刻**的形式化（"routing–topology mismatch"）；**逐候选**按预测的**发送开始时刻**做可行性过滤；含空间非均匀（gravity）与 ON/OFF 长流 | 只做**可行性否决**（拓扑/可见性），不量化"陈旧导致的决策错误"；无 age 参数、无 regret 分解；指标是丢包/吞吐而非决策层因果 |
| **3** | **`P0177` PMPF**（Ouyang 2024, IEEE TVT, DOI 10.1109/TVT.2024.3449804） | **决策相关性门控**：只发"显著影响 potential"的拥塞通知；只在"显著改善"时改路由表；以 <8% 信令换最多 43% 增益 | 阈值是启发式且**静态**，没有把 age/抖动作为变量；无"何时门控失效"的分析；不涉丢包通道的因果归因 |
| **4** | **`P0171` VoI**（Soleymani 2021, IEEE TAC, DOI 10.1109/TAC.2021.3113472；＋`P0172`/`P0173`） | **价值＝值函数变差**的形式化；VoI 是估计差（即"决策侧差"）的对称函数；**AoI 不是 VoI 的函数**、最小 AoI ≠ 最优决策 | 单源 Gauss–Markov / LQG、无排队网络、无多候选、无逐包；不提供路由场景的可计算判据 |
| **5** | **`P0193` Atar & Lipshutz**（2021, Math. of OR, DOI 10.1287/moor.2020.1056）；旁支 `P0192`(1989)、`P0194`(2003) | 明写"**当前状态上好的策略，用在延迟状态上可以很差**"；用估计器补偿并在重交通下证渐近最优；`P0192` 给出（延迟×负载）的**可行域** | **无限缓冲**、单跳并行队列、单流、FIFO、重交通极限；**无丢包通道**、无突发、无多跳 cost-to-go、无逐包；`P0192` 是 3 节点负载共享而非路由 |

> 近旁但未进前五（避免堆砌，注明理由）：`P0181` LoFi（广告精度/范围折中，但无决策层分析）、`P0163` LOGGIA（遥测延迟闭环，但非 LEO、非逐决策）、`P0027` Chiariotti（AoI 界，非价值）、`P0033` Ekici（零状态交换**绕开**陈旧，不是研究陈旧）。

## 2.4 仍然开放的（可证伪陈述）

- **O1｜逐决策判据**：`P(argmin 改变 | age=a)` 及其条件律 —— 是否就是 `|Δcost| < drift(a)`（margin vs drift）？LEO 包级路由无任何实测。
- **O2｜差分 drift**：候选 j 的漂移应在其**自身使用时刻** `τ_j` 评估，而非常见"now"；`P0175` 只在可行性维度做了近似。
- **O3｜regret 分解**：端到端丢包/时延中，**age 导致的动作翻转**占多少、排队/丢弃动态占多少。DFL 家族量化的是**预测误差**导致的 regret（`P0164/0165/0187/0178`），不是**年龄**导致的。
- **O4｜有限缓冲 + 丢包通道（最硬的一条）**：`P0192/193/194` 与既有延迟信息理论全部默认**无限缓冲**；丢包是 `P(Q>B)` 的**尾部**（大偏差），重交通**时延**最优定理**不保证**尾部概率。⇒ 这是真正的理论空白，也是唯一可能支撑大效应的通道。
- **O5｜突发 × age 交互**：`P0174` 明确把突发性留给未来工作；`P0192/0193` 假设平稳/Poisson 类。**无 A/B（burst vs stationary）对比证据。**
- **O6｜系统严重度映射**：从翻转率到 ρ≈1 附近的丢包率，没有标定关系。

## 2.5 我们**不能**主张的（写论文前逐条对照）

1. ❌ **"首次追问陈旧状态何时有害"** —— `P0192`（1989）已做（延迟×负载）系统级可行域。
2. ❌ **"LEO 里陈旧状态被忽视"** —— `P0174`、`P0175`、`P0177`、`P0181`、`P0163` 都在做。
3. ❌ **"候选到达时刻的状态评估是新的"** —— `P0175` 已做（可行性版）。
4. ❌ **把 2 Mbps / 1 500 B / 20–200 ms / "几包缓冲" 说成 "LEO 典型值"** —— 三项分别偏离 5×–10⁴×、2 500–5 000×（`τ_ser/τ_prop`）、75×–750×。
5. ❌ **"200 ms ×3 区域突发是 LEO 已知形态"** —— 无实测；实测只有 1.33 ms 帧与 15 s 边界。这是**预注册压力变换**，必须原样声明。
6. ❌ **"我们的场景是传播受限的 LEO"** —— `τ_ser/τ_prop` 反向（我们是带宽/排队受限）。
7. ❌ **"3.24% ⇒ 陈旧价值 ≤3.5%"** —— 已逐字复核：`3.24%` 是 TAP-DAR 距 oracle 的**平均**残差，`11.97%` 是相对 Scheme 2 的**最大**降幅，`29.05%` 是丢包**平均**降幅；**三者口径不同，不可相加、不可互推**。
8. ❌ **"不存在 >10% 的效应"** —— 未证明；O4 的丢包通道整片空白。
9. ❌ **引用 `P0188`/`P0061` 的具体数字** —— 本文件只读到摘要级 `[未核]`。
10. ❌ **把 `P0192/193` 的定理套到我们场景** —— 它们的假设（无限缓冲、单跳、重交通/平稳）我们**全部违反**。

---

## 3. 可直接用于 related-work gap 段的段落（**DRAFT**，英文）

> **DRAFT — do not paste without re-checking citation keys against the final reference list.**
>
> Prior work on stale network state in LEO routing has largely addressed *compensation*: TAP-DAR predicts one-hop neighbour queues and re-weights asynchronous measurements with a time-aware attention model, reporting an 11.97% maximum latency reduction and a 29.05% average loss reduction against a delayed-state baseline, against a 3.24% average residual to an oracle [P0174]. Adjacent lines ask when state should be disseminated at all — threshold-triggered, range-limited link-state advertisement [P0181], decision-relevance-gated congestion notification [P0177] — and when a candidate should be vetoed because it will have changed by the time the packet actually arrives [P0175]. A separate theory literature characterises the cost of delayed state for canonical load-balancing rules [P0192, P0193] and shows that the value of information is a value-function difference rather than a function of age alone [P0171]. **What has not been established, for packet-level LEO routing with finite buffers and a loss channel, is a decision-conditioned account of *when* staleness actually changes the forwarding decision** — the joint distribution of decision margin and age-induced differential drift, the resulting argmin-flip rate, and the share of end-to-end loss attributable to margin crossings rather than to queueing and drop dynamics.

*（4 句；第 5 句可删。若审稿人要求"novelty 更强"，也**不要**加"we are the first to ask when stale state hurts"——`P0192` 就在那里。）*

---

## 4. 未核 / 不可用清单（诚实边界）

| 项 | 状态 | 原因 |
|---|---|---|
| Starlink / OneWeb **运营 ISL 容量** | `[未核]` | 厂商保密；Wikipedia 明记 "SpaceX enforced confidentiality regarding details of the optical inter-satellite links"；仅得 "mini laser 25 Gbps @4 000 km"（二手转述 X 帖） |
| `P0002`、`P0065`、`P0009` 等的**印刷页码** | `[未核]` | 本机只有 MinerU 转换件，无排版 PDF；引用页码须另行核对 |
| `P0174` **5 档负载的具体数值** | `[未核]` | Table I 未列；正文只给相对趋势 |
| `P0181` 中 ELB/TLR 的**固定周期秒数** | `[未核]` | 原文只写 "periodically under a fixed duration" |
| `P0188`、`P0061`、`P0015`、`P0087` 的具体结论 | `[未核]`（仅标题/摘要级） | 本任务范围限定，未读全文 ⇒ **不进入任何论断** |
| **LEO 实测 top-k OD 份额** | `[未核]／恐不存在` | 187 篇定向检索无命中；只能给非均匀性代理与合成 gravity 模型 |
| `web_fetch` 直取 starlink.com / orbitalradar.com | 失败 | `non-public IP address`；改用 `curl` + Wikipedia（可复核，但为**二手**） |
| `01-文献审计.md` 的 E1 级转引（如 `Luo25`、`Bha23`、`Ata20` 等） | `[未核]` | 这些条目来自上一轮 scout 的 E1 转引，**不在 domain-map 187 篇内**；本文件未重读其原文 ⇒ **不得进论文** |

**已独立复核（与 `F3-LITERATURE-20261028.md`／`01-文献审计.md` 一致）**：`P0174` 的 `16.16% / 11.97% / 29.47% / 29.05% / 18.59% / 19.41% / 3.24% / 3.34%` 与 Table I（`500 MHz`、`T_slot=1 min`、`8 KB`、`L_max=50`、`N=66/140/351`）、"状态广播由**包到达事件**触发"、"星座变密收益收窄"、以及"未来工作：引入自相似与突发流量模型"——均在 `sources/core-corpus/Liu 等 - 2026 - …State Information Lag/*.md` 中按行核对到。**"谁来做主结论"仍须由 Lead 决定；本文件只提供证据与边界。**

---

## 5. 引用索引（本文件实际用到）

| Key | 文献 | 定位 |
|---|---|---|
| `P0002` | Making Sense of Constellations（Starlink 调度） | [arXiv:2307.00402](https://doi.org/10.48550/arxiv.2307.00402) |
| `P0007` | Deciphering Region-Level Signatures | domain-map 全文转换件 |
| `P0009` | How Area Geometry Shapes Link-State Routing Scalability | domain-map 全文转换件 |
| `P0027` | Information Freshness over LEO Multi-Hop | domain-map 全文转换件 |
| `P0033` | Ekici et al., distributed routing for datagram traffic in LEO | [DOI 10.1109/90.917071](https://doi.org/10.1109/90.917071) |
| `P0065` | A Multifaceted Look at Starlink Performance | [DOI 10.1145/3589334.3645328](https://doi.org/10.1145/3589334.3645328) |
| `P0111` | SkyLink | [DOI 10.1109/tcomm.2025.3636092](https://doi.org/10.1109/tcomm.2025.3636092) |
| `P0140` | Characterizing the Configuration of Starlink Queuing | domain-map 全文转换件 |
| `P0161` | TRACE | domain-map 全文转换件 |
| `P0163` | Boltres et al., Near-Real-Time Telemetry-Aware Routing | domain-map 全文转换件 |
| `P0171` | Soleymani et al., Value of Information in Feedback Control | [DOI 10.1109/TAC.2021.3113472](https://doi.org/10.1109/TAC.2021.3113472) |
| `P0174` | Liu et al., TAP-DAR（State Information Lag） | [DOI 10.1109/TCCN.2026.3720213](https://doi.org/10.1109/TCCN.2026.3720213) |
| `P0175` | Kim et al., DPP-E2E（Wide-Area LEO Backbones） | [DOI 10.1109/TAES.2026.3686797](https://doi.org/10.1109/TAES.2026.3686797) |
| `P0177` | Ouyang et al., PMPF | [DOI 10.1109/TVT.2024.3449804](https://doi.org/10.1109/TVT.2024.3449804) |
| `P0181` | Shan et al., How Many Link-State Updates Do We Need? | [DOI 10.1109/SATELLITE59115.2023.00010](https://doi.org/10.1109/SATELLITE59115.2023.00010) |
| `P0192` | Mirchandaney, Towsley & Stankovic, Effects of Delays on Load Sharing | IEEE ToC 38(11):1513-1525（转换件全文） |
| `P0193` | Atar & Lipshutz, JSQ with Delayed Information | [DOI 10.1287/moor.2020.1056](https://doi.org/10.1287/moor.2020.1056) |
| `P0194` | Katsikopoulos & Engelbrecht, MDPs With Delays | IEEE TAC 48(4):568-574（转换件全文） |
| ext-1 | Starlink（ISL 保密 / mini-laser 25 Gbps @4 000 km） | [Wikipedia·Starlink](https://en.wikipedia.org/wiki/Starlink)（二手，引 SpaceX 2025-08-25 X 帖） |
