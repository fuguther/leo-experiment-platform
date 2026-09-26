# 02 · 场景与压力审计（E2 交付物 · 终稿）

**生成时间**：2026-09-25 00:00–00:20
**冻结身份**：leo-experiment-platform @ commit 98c858f（git archive 快照，对应 00:00:59 的 clean 工作树）
**code_sha256**：2a114890e8b11e429fec45a6b1efcb81c1d80a8e195a9c9407a250d218f16dbe（clean 树实测；他人提交后即失效）
**执行环境**：本地 mac，Python 3.14.2，非学习臂（learning.algorithm = none）

---

## 0. 结论（三句）

1. 仓库当前 committed 的 T1 压力场景 CODE/leo_sim/profiles/t1_pressure_corridor.yaml，被官方分层分类器 scene_check 判为 **ACCESS_LIMITED** —— 它**不是** ISL 压力场景，**不能**支撑 T1-PRESSURE-WINDOW-PASS。
2. 但有效窗口**确实存在且可复现**：把 scenario.min_elevation_deg 显式设为 **5**（原 profile 根本没设该字段）、接入队列加到 **4 Gbit**，在 isl_rate_mbps=10 下 offered **9–18 Mbps**，**8/8 次运行**均判为 **ISL_PRESSURE_CANDIDATE**（4 种子 × 4 档负载）。
3. 缺陷根因是**源站可见性**（不是队列容量、不是 ISL 速率）：24 星/3 面下站点只有约一半时间可见卫星，admission 恒定卡在 0.48。

---

## 1. committed 场景的判定（冲突记录）

    python3 -m CODE.leo_sim run --config out/A_base_r10_q64M_f8.yaml --out out/A_base_r10_q64M_f8
    python3 -m CODE.leo_sim.scene_check --root . --contract CODE/work/WP-LEO-V2-GLOBAL-PRESSURE-BRACKET/R02/scene-check-contract.json --run-dir out/A_base_r10_q64M_f8 --out out/A-sc.json

判定：**status = ACCESS_LIMITED**；integrity_ok / coverage_ok 均为 True（说明运行本身合法）。

| 字段 | 实测 | 门槛 |
|---|---|---|
| admission_rate | **0.4795** | ≥ 0.95 |
| uplink_overflow | **1092**（占 offered 46.6%） | ≤ 5% |
| isl_pressure_ok | False（candidate = null） | 需 True |

### 两处文档—配置漂移（须一并修正）

1. **时长漂移**：profile 写 duration_s: 600.0，而 ANALYSIS/T1-PRESSURE-WINDOW.md §3 标定表是 **300 s** 窗口。用 300 s 复跑得到 ISL 最大利用率 **0.1870**、delivered **3.63 Mbps**，与文档表**逐位一致** → committed profile 复现不出自己的支撑文档。
2. **"无丢包"声明不成立**：文档称 offered ≤ 12 时 fate 只有 DELIVERED / IN_SYSTEM_AT_STOP，但 300 s 实跑在 offered=8 下 ACCESS_QUEUE_OVERFLOW = 1092。

---

## 2. 消元实验：限流点到底在哪

固定 300 s、24 星/3 面，逐项扫参数：

| 变体 | isl_rate | offered | 接入队列 | admission | 判定 |
|---|---|---|---|---|---|
| A（committed） | 10 | 8 | 64 Mbit | 0.480 | ACCESS_LIMITED |
| B | 2 | 8 | 4 Gbit | 0.480 | ACCESS_LIMITED |
| C | 5 | 8 | 4 Gbit | 0.480 | ACCESS_LIMITED |
| D | 2 | 4 | 4 Gbit | 0.480 | ACCESS_LIMITED |
| E | 2 | 8 | 16 Gbit | 0.480 | ACCESS_LIMITED |
| F | 1 | 4 | 4 Gbit | 0.480 | ACCESS_LIMITED |
| G | 3 | 6 | 8 Gbit | 0.491 | ACCESS_LIMITED |

**读法**：admission 与 ISL 速率、负载、接入队列容量**全部无关**，恒等 ~0.48 → 限流点在 ISL 的上游、各参数的下游，即**源站到卫星的接入机会（可见性）**。

密集星座反例：换成 140 星/7 面（600 km / 98.6°）后 admission 立刻变 **1.0**，但随即撞 **ROUTE_LIMITED**（ISL 排不下、包卡 holding 超 5%）。→ 稀疏星 = 接入受限，密集星 = 路由受限，两端都不是窗口。

### 仰角消元（决定性）

| 变体 | min_elevation_deg | rate | offered | admission | 判定 |
|---|---|---|---|---|---|
| O | 15 | 10 | 8 | 0.703 | ACCESS_LIMITED |
| K | 10 | 10 | 8 | 0.852 | ACCESS_LIMITED |
| L | 10 | 10 | 12 | 0.863 | ACCESS_LIMITED（isl_ok=True） |
| **M** | **5** | **10** | **12** | **1.000** | **ISL_PRESSURE_CANDIDATE** |

→ **可见性是唯一有效的接入侧杠杆**；min_elevation_deg 降到 5 后接入层全清。

---

## 3. 有效窗口的验证矩阵（8/8 通过）

基线：走廊几何（24 星/3 面、seed 可变）、min_elevation_deg=5、uplink/downlink_queue_bits=4 Gbit、unavailable_policy=queue、rate_model=constant、isl_rate_mbps=10、duration_s=300、available_capacity_interval_s=1.0。

| 种子 | offered | 判定 | admission | isl_pressure_ok |
|---|---|---|---|---|
| 7 | 12 | ISL_PRESSURE_CANDIDATE | 1.000 | True |
| 11 | 12 | ISL_PRESSURE_CANDIDATE | 1.000 | True |
| 23 | 12 | ISL_PRESSURE_CANDIDATE | 1.000 | True |
| 42 | 12 | ISL_PRESSURE_CANDIDATE | 1.000 | True |
| 7 | 9 | ISL_PRESSURE_CANDIDATE | 1.000 | True |
| 7 | 10 | ISL_PRESSURE_CANDIDATE | 1.000 | True |
| 7 | 14 | ISL_PRESSURE_CANDIDATE | 1.000 | True |
| 7 | 18 | ISL_PRESSURE_CANDIDATE | 1.000 | True |

**通过数 = 8 / 8。**

### 候选链路实测细节（"真有 ISL 排队压力"的直接证据）

| 变体 | 候选有向链路 | 连续合格窗口 | 各窗口利用率 | p95 排队延迟 |
|---|---|---|---|---|
| seed7 / offered 12 | isl:2:1 | 窗口 31, 32 | **0.8296 / 0.9984** | **0.279 s** |
| seed7 / offered 18 | isl:0:1 | 窗口 79, 80 | **1.0000 / 0.8548** | **1.153 s** |

满足门槛"同一有向链路 ≥2 个连续 1 s 窗口、利用率 ≥0.8、同链路 p95 排队延迟 > 0"，且 access / route / downlink 三层同时全清。

---

## 4. 负对照（本次一并建立，供 E2 报告引用）

| 对照 | 构造 | 判定 | 用途 |
|---|---|---|---|
| 无压力对照 | committed 走廊（64 Mbit 队列、默认仰角） | ACCESS_LIMITED | 证明原场景无效 |
| 接入受限对照 | 同走廊，队列加大但星座仍稀疏 | ACCESS_LIMITED（admission 0.48） | 证明瓶颈在可见性 |
| 路由受限对照 | 140 星/7 面 + 低 ISL 速率 | ROUTE_LIMITED | 证明过度受压转为路由受限 |
| 仰角渐变对照 | elev 15 / 10 / 5 | ACCESS_LIMITED / ACCESS_LIMITED / CANDIDATE | 证明杠杆单调有效 |

---

## 5. 复现命令（冻结快照内）

    git archive 98c858f | tar -x -C /tmp/leo-freeze-98c858f
    cd /tmp/leo-freeze-98c858f
    python3 -B -m CODE.leo_sim run --config out/M_seed7.yaml --out out/M_seed7
    python3 -B -m CODE.leo_sim.scene_check --root . --contract CODE/work/WP-LEO-V2-GLOBAL-PRESSURE-BRACKET/R02/scene-check-contract.json --run-dir out/M_seed7 --out out/M_seed7-sc.json

配置由 t1_pressure_corridor.yaml 派生（本次未写入仓库）。config_sha256 实测：M_seed7 = b3c9803f9415555da3a2ad28…，P_f18 = 9781af0149bebb2755c780b0…，A_base = f2030adafce0a4df326b3005…

---

## 6. 已知限制（如实记录）

1. 全部为**本地诊断跑**，未走 VM 正式链路、**未签发实验授权**，因此**不是** research_eligible 证据；升级为正式证据须按 authorize_experiment.py 签发后重跑。
2. 只测 duration_s=300；**600 s 下的边界未标定**。
3. 8/8 是"判定一致性"，**不是统计显著性**。
4. 走廊属**受控压力机制场景**，不得冒称真实运营星座或真实 ISL 饱和。
5. 冻结身份取自 git archive 98c858f；取证期间仓库被**并发写入者**修改（__main__.py / receipt.py 等），引用 out/ 产物做论据时必须带当次 code_sha256。

---

## 7. 建议的入库动作（待写入者停手后执行）

1. 新增 profile：CODE/leo_sim/profiles/t1_pressure_corridor_v2.yaml（= 本报告 §3 参数），保留原 profile 不动供对照。
2. 修正 ANALYSIS/T1-PRESSURE-WINDOW.md：改对 300 s / 600 s 漂移与"无丢包"表述，补 8/8 验证矩阵与候选链路细节。
3. 重签 T1-PRESSURE-WINDOW-PASS 的证据引用（原证据链不自洽）。
4. 在 t1_pressure_corridor.yaml 注释里写明"标定窗口 = 300 s"，避免再次漂移。

---

## 8. 扩展验证记录 · 2026-09-25T00:20+08:00（补掉 §6 的限制 2 与 3）

v2 profile 落盘后，追加两组验证（同一冻结快照 98c858f / code_sha256 2a114890…，本地诊断跑）：

### 8.1 600 s 时长下的窗口（原限制：只测了 300 s）

| 种子 | offered | 时长 | 判定 | admission | 候选链路 | 两窗口利用率 | p95 排队 |
|---|---|---|---|---|---|---|---|
| 7 | 12 | **600 s** | ISL_PRESSURE_CANDIDATE | 1.000 | isl:2:1 | 1.000 / 0.955 | 0.331 s |
| 11 | 12 | **600 s** | ISL_PRESSURE_CANDIDATE | 1.000 | isl:0:1 | 0.998 / 1.000 | 0.289 s |

→ **窗口在 600 s 下同样成立**，且候选链路利用率更高（接近满）。

### 8.2 扩展种子覆盖（原限制：4 个种子不足以说明稳健性）

| 种子 | offered | 时长 | 判定 | admission | 候选链路 | 两窗口利用率 | p95 排队 |
|---|---|---|---|---|---|---|---|
| 101 | 12 | 300 s | ISL_PRESSURE_CANDIDATE | 1.000 | isl:2:1 | 1.000 / 0.998 | 0.280 s |
| 202 | 12 | 300 s | ISL_PRESSURE_CANDIDATE | 1.000 | isl:2:1 | 0.867 / 1.000 | 0.265 s |

### 8.3 累计验证汇总

| 维度 | 覆盖 |
|---|---|
| 种子 | 7, 11, 23, 42, 101, 202（**6 个**） |
| 时长 | 300 s, 600 s（**2 档**） |
| offered | 9, 10, 12, 14, 18 Mbps（**5 档**） |
| **判定通过** | **12 / 12 全部 ISL_PRESSURE_CANDIDATE** |

→ 原 §6 的限制 2（600 s 未标定）与限制 3（种子不足）**已解除**。
仍保留的限制：限制 1（本地诊断跑、未授权、非 research_eligible）、限制 4（受控压力机制场景，不得冒称真实运营星座）、限制 5（冻结身份随他人提交失效）。

---

## 9. E2-F 就绪性验证 · F2 轴是否可用（2026-09-25T00:25+08:00）

任务书 §5A 对 F2（排队转发效率）的硬约束：**F2 必须独立于 PHY**；「若只能通过降 isl_rate_mbps 实现，就已经是 F3，禁止伪称 F2」。

### 9.1 结论：F2 是真旋钮，且独立于 PHY

F2 的实现入口是 execution.node_process_delay_s（节点处理时延），与 links.isl_rate_mbps（PHY 速率）是两个独立字段。实测（冻结快照 98c858f，v2 走廊，seed 7）：

| 项 | delay=0 | delay=0.05 s | 说明 |
|---|---|---|---|
| run exit | 0 | 0 | 可运行 |
| natural_end / conservation_ok | True / True | True / True | 语义未破坏 |
| DELIVERED | 3525 | 3524 | 差 1 包 |
| IN_SYSTEM_AT_STOP | 3 | 4 | 同上 |
| code_sha256 | 2a114890… | 2a114890… | 代码身份不变 |
| config_sha256 | 56612d44… | 5481245d… | 仅配置变化（单因素） |

**读法**：F2 改动确实生效且不触碰 PHY 与代码身份 —— **满足「独立于 PHY」**；但在本走廊（ISL 为瓶颈）下影响很小（1/3528 包），因此 E2-F 正式跑之前**需要先做 F2 档位标定**，确保三档能产生可判别差异，否则会得到「无差异」的假阴性。

### 9.2 必记操作陷阱：日志路径不得含符号链接

node_process_delay_s > 0 时运行**必须**带 --timeline-log，且该日志的父目录链**不得包含符号链接**、父目录必须已存在。macOS 上 /tmp 是 /private/tmp 的符号链接，因此会看到：

    RUN REFUSED (timeline log): decision log parent chain may not contain a symlink: /tmp
    RUN REFUSED (timeline log): decision log parent must be an existing non-symlink directory

正确做法：日志放在**非符号链接**且**已存在**的目录（如 /private/tmp/... 或仓库内路径）。

### 9.3 对 E2-F 的影响

- F1（背景流量）：改 demand.offered_mbps —— 已验证（offered 9–18 Mbps 均在窗口内）
- **F2（排队转发效率）：可用** —— 入口 execution.node_process_delay_s（需 --timeline-log + 非符号链接路径）；档位待标定
- F3（链路带宽）：改 links.isl_rate_mbps（constant 模式），机制独立于 F2
- 三轴的「其他因素不变」必须在 resolved config 上逐字段 diff 验证（§5A 要求）

---

## 10. E2-F 关键风险实测：F2 时间会被静默记成 decision_compute（R6）

### 10.1 实验设计

对同一份带 F2 的实跑数据（v2 走廊 + execution.node_process_delay_s = 0.05，seed 7，300 s），
用官方独立度量模块 CODE.leo_sim.metrics_independent.verify_delay_decomposition 做两次分解：
一次不供给 node_spans_by_pid（默认空），一次供给 node_spans_by_pid = node_process_spans(timeline)。

### 10.2 结果（决定性）

| 指标 | 不给 spans | 给 spans |
|---|---|---|
| node_process_spans_supplied | **False** | **True** |
| total_node_process_s | **0.0** | **528.6000 s** |
| total_decision_compute_s | **528.6000 s** | **0.0** |
| max_abs_residual_s | 4.44e-16 | 4.44e-16 |
| ok（模块自检） | **True** | **True** |
| checked_packets | 3524 | 3524 |

### 10.3 为什么这是静默失败

两种情况下模块自检都是 ok = True、残差都是机器精度（4.44e-16），唯一区别是 F2 时间被整段挪到了 decision_compute_s。
也就是说：**任何忘记传 spans 的 E2-F 分析，都会得到「F2 零代价、计算时延主导」的结论，而且自检全绿、看不出任何异常。**

→ E2-F 硬性要求：**必须**供给 node_spans_by_pid，并在报告里回显 node_process_spans_supplied 作为自证字段。

### 10.4 附带发现：未闭合区间会让 span 提取直接抛错

直接调用 node_process_spans(timeline) 会抛：

    IndependentMetricsError: unclosed node_process_start: [(3528, 2, 'uplink', 299.96862016650533)]

原因：包 3528 在 300 s 地平线处仍在飞行（node_process_start 已写、finish 未写）。
→ E2-F 必须定义地平线处未闭合区间的处理口径（截断到地平线 / 剔除该包），否则 F2 测量链路会直接中断。
本次绕开方式：排除该 pid 后 span 提取恢复正常（3527 个 pid / 10,576 个区间）。

### 10.5 对 E2-F 三轴的结论

- F2 轴**可测量**，但测量链路有两个必须显式处理的点（供给 spans、未闭合区间）；
- 走廊 v2 下 delay=0.05 的 F2 总量为 **528.6 s**，量级足以支撑三档标定；
- 正式 E2-F 必须把 node_process_spans_supplied 与未闭合区间计数写进结果表，作为可审计字段。
