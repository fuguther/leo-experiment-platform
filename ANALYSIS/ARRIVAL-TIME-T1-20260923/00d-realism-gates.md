# 00d — 真实度先决门（Realism Gates）

**文件身份**：E0 交付物（任务书 v1.4 §8；门定义见任务书 v1.4 §R7 第 88–92 行）
**生成时间**：2026-09-24T16:07Z
**本文件对应的代码身份**：`code_sha256 = 57e86d4471af7a94b584202cb8b2418a92f6d2a7850a46b7e6c4e0696e489b7c`
**对应仓库状态**：HEAD = `98c858f642aeca8056d215224b20783b00354026`，分支 `chore/retire-dead-lines-q0-tiny`，**工作树脏**（`CODE/leo_sim/__main__.py`、`CODE/leo_sim/receipt.py`、`CODE/leo_sim/tests/test_pairing_trace_identity.py`、`CODE/leo_sim/tests/test_review_residuals.py`、`CODE/scripts/remote/pull-results-remote.sh`、`CODE/scripts/remote/remote_job.py` 存在未提交改动）

> **冻结值失效声明（必读）**
> 上列 `code_sha256` 是 **2026-09-24T16:07Z 这一时刻的实测值**（由 `CODE.leo_sim.receipt.code_sha256()` 对 `CODE/leo_sim/*.py` 逐文件求 SHA 得到）。
> **它会随任何第三方对 `CODE/leo_sim/` 的提交或工作树改动而失效。** 本文件生成期间已实测到该值从 `2a114890…` 漂移至 `57e86d44…`，漂移来源是他方改动，非本文件作者所为。
> 同一时刻的 `config_sha256` **未漂移**（因为 `config.py` 与 profile 未被改动）：E0 工程基线 `c2c59497…`、`smoke.yaml` `9343153…`、`t1_pressure_corridor.yaml` `52dfe3d9…`、`acceptance/ddqn.yaml` `913dc916…`。
> **任何下游引用必须先重新实测 `code_sha256` 并核对比对；不等即表示代码身份已变，本文件全部门判定需重新评估。**

---

## 1. 五门判定总表

判定口径：**MET** = 已有可追溯证据且满足门定义；**NOT MET** = 有明确证据表明尚未满足；**UNKNOWN** = 证据不足无法判定。

| 门 | 判定 | 依据 | 缺口 | 现阶段不能声称什么 |
|---|---|---|---|---|
| **REAL-SOURCE-PROVENANCE-PASS**<br>每条所谓真实参数都可对应原始来源、年份、样本群、字段与限制；不存在"真实流量"泛称（任务书 v1.4:88） | **NOT MET** | `LEO_T1_真实证据来源台账_v1.4.csv`（SHA `08a9f1d7e71068bc0fd25c5caba9f88849f62fdd65c10db42b5345def48220af`）已含 9 条 `source_id`，每条带 `evidence_class` / `usable_fields` / `prohibited_inference` / `url`，方向正确；但该台账仅 7 列，**缺少任务书 §R3（:53）明令的 `population`、`date_range`、`methodology`、`field_unit`、`selection_bias`、`original_bytes_sha`、`transform_sha` 共 7 项**；且全项目**只有 2 个本地原始提取文件**（`官方Starlink地区性能_直接提取16行.csv` = `792bb109…`、`Starlink_补充轨道三星快照.csv` = `53cc2cdf…`），其余条目在"原始文件 + SHA"上均为 UNVERIFIED（`获取状态.json:14-17` 四项 `downloaded=false`，`starlink_isl_internal_trace_available=false`） | 原始字节落盘与 SHA；采集方法、抽样偏倚、字段单位、时间人群；官方页面快照存档（含抓取时间戳）；台账补齐 7 个缺失列 | 不得使用"真实流量"泛称；不得把地区性能、终端测速或官方标称速率称为真实 ISL 业务量；不得因为 `mode=mlab` 就记作 `REAL_TRAFFIC_REPLAY`（任务书 :262、:50） |
| **REAL-PACKETIZATION-PASS**<br>真实/代理包长与旧 1 Mbit 单元的决策请求率差已复算；处理能力与流量双向单位都一致（任务书 v1.4:89） | **NOT MET**<br>（换算这一半已达成；"真实/代理包长"本身未校准到原始 trace） | **已达成**：换算已复算且与既有产物逐位一致（`00c` §2；`报告_v2_真实包长与推理压力.md` §3 的 8 493.93 / 14 029.18 与 `逐包DDQN_包长与鲁棒性全网格.csv` 首行 `2 123.48129` 均逐位吻合）；双向单位一致（`R`(Mb/s) → `λ`(pps) → `μ_AI`(决策/秒)，经 `k_reuse` 与 `ρ ≤ 0.7` 连接，口径见 `00c` §1）。**未达成**：全部包长均为二手或人工——1471.64 B / 1501.79 B 为论文 Fig.3(c) 图表转述（原始 zip 未下载，`报告_v2` :55 明写）；891 / 372 / 64 B 为 CAIDA 网页统计（未取原始 pcap）；1500 B 为人工情景。且 `packet_bits` 默认 8 000 000 与任务书口径 1 000 000 **未裁决**，E0 冒烟证据（`out/e0-smoke-20260925/trace.csv`）实际跑在 `bits=8000000` 上（详见 `00c` §3） | 原始逐包 trace（CAIDA 需申请；SpaceSec Zenodo `WFdata75x80.zip` 未下载）；`packet_bits` 默认值裁决；host capture 的 TCP offload 核验（`报告_v2` :9：地面 Firefox 中位 2188.94 B 已超 1500 B MTU） | 不得写"按真实 Starlink 包长分布仿真"；不得把 1471.64 B 当作全业务加权均值；不得把任何解析筛查 PASS 称为在轨可部署（`报告_v2` :63）；不得在未声明 `packet_bits` 档位的情况下作跨 profile 请求率对比 |
| **REAL-HARDWARE-PROXY-PASS**<br>硬件样本类型与可复现实测结果绑定；缺 Starlink 路由芯片参数明写 UNKNOWN（任务书 v1.4:90） | **NOT MET** | 外部硬件证据**全部无本地字节**：ESA OPS-SAT（Cortex-A9 + Cyclone V FPGA）、Intel PhiSat-1（Myriad 2）、ESA PhiSat-2、ESA GR740、Aethero Deimos（Jetson Orin 100 TOPS，厂商声明）、NASA HPSC（研制中）——均 UNVERIFIED；平台内决策耗时是**显式假设**：`07_模型和统计.json:58` `"complete_decision_time_MS": "ASSUMED NOT MEASURED"`；**未找到任何代理硬件 benchmark 产物**；任务书 :39 要求的记录项（板卡 / 固件 / 频率 / 热状态 / 功率 / 电源模式 / 单核多核 / 软件栈）**一项未落**；Starlink 路由芯片参数缺失，任务书 :90 要求的"明写 UNKNOWN"**未在任何产物中出现** | 代理设备选型与购置；本仓库 DDQN 完整在线决策路径的 p50/p95/p99；功耗 / 热 / 并发；`flight-case / commercial-spec / obtainable-proxy` 三分类落位；Starlink 路由芯片参数的 UNKNOWN 声明 | 不得由 TOPS 换算为 ms；不得把开发 VM 的秒数当作卫星执行时间（任务书 :78）；不得声称任何在轨推理耗时；不得声称能耗或能效指标；不得把网络算术量（6 213 参数 / 约 12 160 FLOPs）说成完整决策成本 |
| **REAL-LINK-GENERATION-PASS**<br>RF MCS 1 Gbps generic 与运营商 100 Gbps optical 明确分 profile；不得挪用 RF 公式去声称光学链路实测（任务书 v1.4:91） | **NOT MET** | 平台确有 RF/MCS 遗留路径：`CODE/leo_sim/config.py:322-323` `rate_model="constant"` + `mcs_table=link_budget.LEGACY_DVBS2X`；`config.py:325-330` `rf_isl` = 26 GHz / 500 MHz / 10 W / 0.26 m 天线，注释自承 "legacy ISL params, markovianMatchingTwo (SimulationRL.py:8353)"；`config.py:345` `isl_rate_mbps=1000.0` 为平台默认。**E0 工程基线 profile 显式使用 RF/MCS**：`CODE/leo_sim/profiles/mlab_multiod_burst_t0_queue_280x14_e25.yaml:46` `rate_model: mcs`，而该 profile 正是 `EXPERIMENTS/experiment-program.yaml:430-431` 标记的 `frozen_e0_engineering_baseline`。**全仓库不存在任务书要求的两个 profile**：对 `CODE/` 全量 `*.yaml` 与 `*.py` 检索 `published-optical|generic-legacy|optical` **零命中**，`CODE/leo_sim/profiles/` 与其 `acceptance/` 子目录亦无同名文件——任务书 :67、:91 要求的"必须分别开 `generic-legacy-ISL` 与 `published-optical-ISL` profile"**未执行** | 建立两个显式 ISL profile 并各自记录物理根据与缺口；明确 100 Gbps 仅为厂商标称链路能力而非保证任意时间/方向满速；把高带宽参照明确标注为 fixed-rate / optical-capacity diagnostic | 不得把 1 Gbps 实验命名成"实测 Starlink 激光链路仿真"；不得挪用 RF/MCS 公式去声称光学链路实测；不得把 `rate_model: mcs` 的 E0 基线结果表述为光学 ISL 容量结论（任务书 :67、:91） |
| **NO-ISL-TELEMETRY-CLAIM**<br>没有真实 ISL 运行业务/队列日志时，不生成"实测 Starlink 拥塞/算法收益"论文措辞（任务书 v1.4:92） | **MET**<br>（约束门，非能力门：当前全部产物确实遵守该约束） | `获取状态.json:17` `"starlink_isl_internal_trace_available": false`；`LEO_T1_真实证据来源台账_v1.4.csv` 末行："商业卫星内部 ISL 队列/逐跳流量，目标数据，**未获得**，缺失，**不得用终端吞吐、RTT 或官方速率替代**"；`直接数据提取与适用边界.md` :4 / :38 / :42 明确不把终端指标换名为 ISL 队列；`02_报告源文档.md` :6 / :8 / :63 与 `报告_v2` :63 使用限定表述（"容量/时间尺度必要条件研究，不是网络包级闭环仿真结果"）；平台实际数值亦支持无 ISL 压力：`experiment-program.yaml:460` 的 `isl_max_utilization` 为 0.00080674（10 Mbps）/ 0.00188239（25）/ 0.00389923（50），即 ≤ 0.39% | 无（作为约束门不产生能力缺口）。**但需持续执行**：论文与答辩措辞、图表标题、以及任何由本平台生成的结果，都必须显式声明"无真实 ISL 运行日志" | 不得生成"实测 Starlink 拥塞"或"算法在真实星座上的收益"等措辞；不得把平台的阴性压力结果包装为运营网络结论；不得由终端 RTT 拆出每跳 ISL 队列；亦不得反向把"平台未观测到饱和"表述为"真实星座不存在拥塞" |

**判定汇总**：5 门中 **4 门 NOT MET（门 1–4）**、**1 门 MET（门 5）**。

---

## 2. 五门与交付物的对应关系

| 门 | 主要证据载体 | 判定 | 阻塞的下一步 |
|---|---|---|---|
| REAL-SOURCE-PROVENANCE-PASS | `00b-real-world-source-ledger.csv` | NOT MET | 原始字节 + SHA + 台账 7 个缺失列 |
| REAL-PACKETIZATION-PASS | `00c-packetization-and-hardware-bench.md` §2–§3 | NOT MET | 原始逐包 trace；`packet_bits` 默认值裁决 |
| REAL-HARDWARE-PROXY-PASS | `00c` §5 | NOT MET | 代理板完整在线路径实测 |
| REAL-LINK-GENERATION-PASS | 本文件门 4 | NOT MET | 建立两个 ISL profile |
| NO-ISL-TELEMETRY-CLAIM | 本文件门 5 + 全部产物措辞 | MET | 持续执行（无需新增证据） |

---

## 3. 解除各门所需的外部输入

| 门 | 需要用户提供或申请的东西 | 入口 |
|---|---|---|
| REAL-PACKETIZATION-PASS | CAIDA 被动 trace 访问（含 anonymized pcap 或逐包 size/time） | https://www.caida.org/catalog/datasets/passive_100g_dataset/ ；https://www.caida.org/catalog/datasets/trace_stats/ |
| REAL-PACKETIZATION-PASS | SpaceSec 2024 原始包数据（`WFdata75x80.zip`，224.1 MB，MD5 `689527862a162a1a5a448d05038e7a1b`） | https://zenodo.org/records/10641853 ；论文 https://www.ndss-symposium.org/wp-content/uploads/spacesec2024-15-paper.pdf |
| REAL-PACKETIZATION-PASS | StarFrame-UDP 原始包间隔（26.6 GB；inter-packet delay 需向作者申请） | https://doi.org/10.26249/FK2/I7BANY |
| REAL-HARDWARE-PROXY-PASS | 2 台可购买/可复现代理设备 + 完整在线 DDQN 路径实测（p50/p95/p99、功耗、热、并发） | 需采购与实验室时间；**无公开数据可替代** |
| REAL-SOURCE-PROVENANCE-PASS | 官方页面快照存档（含抓取时间戳）；LENS 月度归档；Starlink 单向时延实验（5.9 GB） | https://starlink.com/sb/technology ；https://starlink.com/business/aviation ；https://github.com/clarkzjw/LENS ；https://zenodo.org/records/16275284 |
| REAL-LINK-GENERATION-PASS | 无需外部输入——纯仓库内可修（建立两个 ISL profile）；但新增 config key 会改变**全部** `config_sha256`（`ANALYSIS/T1-MEASUREMENT-PROTOCOL.md:43-44`），须在 E0 冻结前一次性定稿 | 仓库内变更，需主写入者执行 |
| NO-ISL-TELEMETRY-CLAIM | 真实 ISL 运行日志 / 运营商 telemetry——公开渠道**不可得**；只能靠合作或授权。**现阶段应按"不可得"设计研究**，不得将其列为待办而阻塞其他工作 | 无公开入口 |

---

## 4. 附：本文件所引用的冻结哈希

| 对象 | 值 | 实测时刻 |
|---|---|---|
| `code_sha256` | `57e86d4471af7a94b584202cb8b2418a92f6d2a7850a46b7e6c4e0696e489b7c` | 2026-09-24T16:07Z（**易失效**） |
| `config_sha256` — E0 工程基线 `mlab_multiod_burst_t0_queue_280x14_e25.yaml` | `c2c59497e2444c981cb37449a60cdb5a75f4c96a46f35827b416c15e240891a4` | 同上 |
| `config_sha256` — `smoke.yaml` | `934315391f9572ca2f03a641a3b2a800889330e8f0579fccbb7ee29379c68816` | 同上 |
| `config_sha256` — `t1_pressure_corridor.yaml` | `52dfe3d92c1e0e6cbd7281c8b4725d9889c5c2376848a22a5b25a980ed4c47a3` | 同上 |
| `config_sha256` — `acceptance/ddqn.yaml` | `913dc916f74201b555218d0bba2d937a39edb573347ef4a85f0d716449ae832d` | 同上 |
| `trace_sha256` — `out/e0-smoke-20260925` | `47a4af9fcf7fc58dd1ec3c5f8ce0961296fff2344f711bac5b02889ceff18ecd` | 同上 |
| `trace_identity_sha256` — 同上 | `3c8d3443c3a3ce295aa9985215d4eff86b94050320a19f853e9e1101330f2e0f` | 同上 |
| `trace_sha256` — E0 基线 50 / 25 / 10 Mbps 臂 | `f6981c327f4c36e659d3f7b5ef66128f94a199d0203591401c88ed0e8ab22de4` / `22d341d9a5f8028f91ca8bb32f6a580a4a28a08dd6409a774e76690e31937193` / `e2b469b984a7fc677f4ae8a61621f7e1e9c93ff3b3ade097461a68f365a2d23` | 引自 `EXPERIMENTS/experiment-program.yaml:433,460` |

> `code_sha256` 覆盖 `CODE/leo_sim/*.py` 逐文件 SHA（`CODE/leo_sim/receipt.py:124-131`、`:152`）；`config_sha256` 覆盖 merged config 的 canonical JSON（`CODE/leo_sim/config.py:970-976`）。**两者的失效条件不同**：前者随任何 `CODE/leo_sim/` 代码改动失效，后者仅随 config key 或 profile 内容改动失效。本次漂移只影响前者。

---

## 更新记录 1 · 2026-09-25T00:20+08:00（主会话执行，§R5-1 与 §R5-3 落盘）

**本次变更**：新增 3 个 profile 文件（未修改任何原有文件、未新增任何 config key，故既有 config_sha256 不受影响）。

| 新 profile | config_sha256 | 用途 |
|---|---|---|
| CODE/leo_sim/profiles/linkgen_generic_rf_mcs_1g.yaml | c8a5624449631a448f0a5de63511939cec28f173796cae6f025332112456fa84 | REAL-LINK-GENERATION 门 · RF/MCS 1 Gbps 级 generic 档（rate_model=mcs） |
| CODE/leo_sim/profiles/linkgen_published_optical_100g.yaml | 468923abdedee16d1ca654736a5a4e651c94de869e2f5d7204c9e37573e20981 | REAL-LINK-GENERATION 门 · 光学固定速率容量诊断档（rate_model=constant, 100 Gbps） |
| CODE/leo_sim/profiles/t1_pressure_corridor_v2.yaml | 98c082a90f571a8d414e3c37d7d60ecefe62e84546b07e26d8c1c45a1443803d | §R5-3 压力场景口径：经验证的有效 ISL 压力窗口 |

三份均通过 python3 -m CODE.leo_sim config validate（status=ok）与 run --dry-run（exit 0）。

### 对门 4（REAL-LINK-GENERATION-PASS）的判定更新

- 原判定 **NOT MET**，理由是「全仓库检索 optical/generic-legacy 零命中，两个 profile 根本没建」。
- **本次已解除该结构性缺口**：两个链路代际 profile 已建立并明确分离，头部注释写明「不得用 RF-MCS 公式背书光学链路」「不得把本 profile 利用率当作实测占用率」。
- **整体判定仍为 NOT MET**，剩余缺口有两条：
  1. **标称口径未裁定**：100 Gbps（v1.4 §R3 + 证据台账 L1）vs 200 Gbps（对话正文与《直接数据提取与适用边界.md》）vs 6x400 Gbps（V3 设计）。当前 optical profile 暂取 100 Gbps，已在文件头标注「裁定后须同步修改」。
  2. **无实测**：本条只解决「两者是否分开」，不解决「光学链路是否被实测过」——那属于 REAL-HARDWARE-PROXY 门与 §R6 S3/S4 的范围。

### 对 §R5-3（压力场景口径）的判定更新

原 committed 场景 t1_pressure_corridor.yaml 被官方分类器判为 **ACCESS_LIMITED**（admission 0.4795，上行溢出 1092/2344），**不能**支撑 T1-PRESSURE-WINDOW-PASS。

新 profile t1_pressure_corridor_v2.yaml 的实测（冻结于 commit 98c858f，code_sha256 2a114890…，本地诊断跑）：

| 项 | 结果 |
|---|---|
| 官方分类器判定 | **ISL_PRESSURE_CANDIDATE** |
| access / route / downlink clean | True / True / True |
| admission_rate | 1.0000 |
| 候选链路 | isl:2:1，连续窗口 31,32，利用率 0.8296 / 0.9984 |
| 同链路 p95 排队延迟 | 0.279 s |
| 多种子复现 | 4 种子（7/11/23/42）x 4 档负载（9/10/12/14/18）= **8/8 通过** |

完整证据链与负对照见 ANALYSIS/ARRIVAL-TIME-T1-20260923/02-scene-and-pressure-audit.md。
**注**：本次为本地诊断跑，未走 VM 正式链路、未签发实验授权，故不是 research_eligible 证据；T1-PRESSURE-WINDOW-PASS 的证据引用须在授权后重跑方可重签。


---

## 更新记录 2 · 2026-09-25T01:20+08:00（§R5-2 复算工具落地）

**新增**：ANALYSIS/ARRIVAL-TIME-T1-20260923/r5_packetization_check.py（可复跑的决策请求率复算与单位自检工具）。

**实测结果**（单位一致性自检 unit_consistency_ok = **true**）：

| 聚合单元 | offered | 单元口径 决策/s | 1500 B 包/s | 放大 | 1471.64 B 包/s | 放大 | 891 B 包/s | 放大 |
|---|---|---|---|---|---|---|---|---|
| 1 Mbit | 25 Mbps | 25.0 | 2083.33 | **83.33x** | 2123.48 | **84.94x** | 3507.30 | **140.29x** |

**对门 2（REAL-PACKETIZATION-PASS）的判定更新**：

- 「真实/代理包长与旧 1Mbit 单元的决策请求率差已复算」：**已满足**（工具化、可复跑、单位双向自检通过）；
- 「处理能力与流量双向单位都一致」：**已满足**（round-trip 逐一为真）；
- **整体判定仍为 NOT MET**，剩余缺口两条：
  1. 包长仍是**二手/人工锚点**（1471.64 / 891 / 372 / 64 / 1500 B），**不是**真实分布；需 owner 决定是否用 CAIDA/MAWI 公开分布做代理；
  2. packet_bits 的正式口径未收敛（见 07-caliber-decisions.md §1：现按双口径并存处理，8 Mbit 相对 1500 B 放大 666.7x）。

**口径纪律**：任何引用本门结论的文档必须标注 packet_bits 口径与包长来源。
