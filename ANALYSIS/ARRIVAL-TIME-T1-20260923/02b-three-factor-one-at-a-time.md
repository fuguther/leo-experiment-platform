# 02b · 三因素单因素实验臂与严格性证据（E2-F 准备）

**时间**：2026-09-25 00:35+08:00
**身份**：commit 98c858f，code_sha256 2a114890e8b11e429fec45a6b1efcb81c1d80a8e195a9c9407a250d218f16dbe（冻结快照）
**基线 profile**：CODE/leo_sim/profiles/t1_pressure_corridor_v2.yaml（config_sha256 56612d44ba4494992be3696fbd31d8784a43ab20b8faf1c05b55469139a5cc0b）
**生成器**：e2f_arms.py（只解析配置、不跑仿真）
**产物**：e2f-arms/*.resolved.json + e2f-arms/summary.json

---

## 1. 臂表

| 臂 | 轴 | config_sha256（前16） | 与基线的差异字段 | 用途 |
|---|---|---|---|---|
| base | 基线 | 56612d44ba449499 | —（基线） | 共享基准 |
| F1_offered_9 | F1 背景流量 | d92ebf404cc56946 | demand.offered_mbps | λ 低档 |
| F1_offered_15 | F1 背景流量 | 15831597dacec8f0 | demand.offered_mbps | λ 高档 |
| F2_delay_0p05 | F2 排队转发效率 | 5481245d10cc575c | execution.node_process_delay_s | 服务机制中档 |
| F2_delay_0p15 | F2 排队转发效率 | dc67553008ac615a | execution.node_process_delay_s | 服务机制高档 |
| F3_isl_rate_8 | F3 链路带宽 | a65e8ea2eb52310a | links.isl_rate_mbps | C_phy 低档 |
| F3_isl_rate_12p5 | F3 链路带宽 | 2cd3863d6dce24e2 | links.isl_rate_mbps | C_phy 高档 |
| NEG_no_queue | 负对照 | 1894b6a759707b9d | demand.offered_mbps | 无排队负对照（offered=1 Mbps） |

基线固定不变的量（§5A「先固定基准」要求）：星座几何（24 星 / 3 面 / 550 km / 53° / min_elev 5°）、种子、时长 300 s、控制面规则、TTL、路由策略（hop）、学习算法（none）、接入速率与队列、包长 1 Mbit、可用容量采样间隔 1 s。

## 2. 严格单因素证据（本次核心）

方法：用平台自己的解析器把每个臂解析成 **resolved config**，全部叶子字段展平成点号路径后与基线逐字段 diff。

结果：

    strict_single_factor_overall = True
    非预期变化总数 = 0

即：**每个臂相对基线只改变一个字段**，其余全部逐字段相同。这是"避免伪单因素"的可审计证据，**在跑实验之前**就已具备。

> 说明：sha256 与 canonical_json 是配置内容的派生表示（内容一变它们必变），已在 diff 中排除，不计入参数差异。

## 3. F2 可识别性审计（关键，来自实测）

任务书 §5A 对 F2 的硬约束是"必须独立于 PHY"。除结构独立性外，本平台还有两个**必须显式处理**的测量条件：

| 条件 | 事实 | 后果 |
|---|---|---|
| **必须供给 node_spans_by_pid** | 实测：不给 spans 时 total_node_process_s = 0.0、total_decision_compute_s = 528.6 s；给了 spans 后两者互换 | **静默失败**：模块自检两种情况下都是 ok = True、残差都是 4.44e-16，看不出异常 |
| **未闭合区间会让 span 提取抛错** | node_process_spans() 抛 IndependentMetricsError: unclosed node_process_start（包 3528 在地平线处仍在飞行） | F2 测量链路直接中断，需定义地平线处理口径 |

→ E2-F 执行时的强制要求：
1. 运行必须带 --timeline-log（否则 node_process_delay_s > 0 直接拒绝运行）；
2. 分析必须调用 node_process_spans 并把结果作为 node_spans_by_pid 传入；
3. 结果表必须回显 node_process_spans_supplied 与未闭合区间计数，作为可审计字段；
4. 日志路径不得含符号链接（macOS /tmp 是符号链接，会被平台拒绝）。

## 4. 负对照

| 对照 | 构造 | 期望 |
|---|---|---|
| NEG_no_queue | offered 降到 1 Mbps（其余同基线） | 不应出现有效 ISL 压力（利用率远低于 0.8），用于证明"压力是被自变量造出来的，不是场景固有" |

## 5. 执行计划（待授权）

每个臂的运行形态（本地诊断）：

    python3 -B -m CODE.leo_sim run --config e2f-arms/<arm>.resolved.json \
      --out <非符号链接路径>/<arm> \
      --decision-log <arm>/decisions.jsonl --timeline-log <arm>/timeline.jsonl
    python3 -B -m CODE.leo_sim.scene_check --root . \
      --contract CODE/work/WP-LEO-V2-GLOBAL-PRESSURE-BRACKET/R02/scene-check-contract.json \
      --run-dir <arm> --out <arm>-sc.json

顺序：先 baseline 与 NEG（确认压力/无压力两端成立），再各轴单独扫描；每跑完一个臂即核对 resolved config 与臂表一致。

## 6. 三因素结果表（占位，待授权后填写）

| 臂 | 判定（scene_check） | 有向 ISL 最大利用率 | ΔT_arrival p50 | F2 总占用 | node_process_spans_supplied | 未闭合区间 |
|---|---|---|---|---|---|---|
| base | 待跑 | 待跑 | 待跑 | 待跑 | 待跑 | 待跑 |
| F1_offered_9 / 15 | 待跑 | 待跑 | 待跑 | 待跑 | 待跑 | 待跑 |
| F2_delay_0p05 / 0p15 | 待跑 | 待跑 | 待跑 | 待跑 | 待跑 | 待跑 |
| F3_isl_rate_8 / 12p5 | 待跑 | 待跑 | 待跑 | 待跑 | 待跑 | 待跑 |
| NEG_no_queue | 待跑 | 待跑 | 待跑 | 待跑 | 待跑 | 待跑 |

> 本节按任务书 §8 要求保留「三因素结果表」字段；**未授权前不填任何数字**——避免把本地诊断的数字与正式证据混淆。

---

## 7. F2 线性度实测（本轮，臂扫描前的独立验证）

对已完成的两个 F2 臂用官方度量模块独立复算（node_process_spans 迭代排除未闭合 pid 后供给 verify_delay_decomposition）：

| 臂 | 配置 delay | F2 总量 total_node_process_s | 每包均值 | decision_compute_s | 未闭合 pid | 残差 | ok |
|---|---|---|---|---|---|---|---|
| F2_delay_0p05 | 0.05 s | **528.450** | 0.149957 s | 0.15 | 2 | 4.44e-16 | True |
| F2_delay_0p15 | 0.15 s | **1583.100** | 0.449744 s | 0.90 | 9 | 4.44e-16 | True |

**读法**：delay 提高 3 倍，F2 总量正好提高 3 倍（528.45 → 1583.10）；每包均值 = 3 × delay（每包经过 3 个节点的处理时延）。
说明 **F2 轴线性、可控、可测量**，且 node_process_spans_supplied = True、decision_compute_s 保持在 1 s 量级（未被 F2 污染）。
附带观察：未闭合 pid 随 delay 增大而增多（2 → 9），即更多包在地平线处仍在处理中——这是必须显式记录的口径。

### 7.1 诊断运行器首轮的两个 bug（已修，记录以免重犯）

1. **run-dir 越出项目根**：scene_check 报 run-dir escapes project root。首轮把运行目录放在项目根之外，导致**全部 8 臂没有分类结果**。修法：运行目录改到根内 out/e2f/ 下。
2. **未闭合 pid 解析过宽**：异常消息形如 unclosed node_process_start: [(3528, 2, uplink, 299.96...)]，首版把消息里**所有数字**当 pid，连卫星号 2 也被剔除，导致 spans 变空、F2 总量被报成 0.0。修法：只取每条记录的第一个字段作为 pid，并**迭代**排除直到成功。

---

## 8. 诊断跑的代码身份（溯源声明，必须记录）

首轮可用的臂扫描（F1/F2/F3/NEG）实际运行在**真实仓库的工作树**上，而非 E1/E2 使用的冻结快照：

| 项 | 值 |
|---|---|
| 臂回执 code_sha256 | **57e86d4471af7a94b584202cb8b2418a92f6d2a7850a46b7e6c4e0696e489b7c** |
| 回执 schema | **leo-sim-receipt/v6**（双流日志齐备时升级，符合规格） |
| 冻结快照身份（E1/E2 用） | 2a114890e8b11e429fec45a6b1efcb81c1d80a8e195a9c9407a250d218f16dbe |
| config_sha256 | 与臂表逐位一致（15831597… / d92ebf40… / 5481245d…） |

差异来源：诊断运行器的项目根由脚本自身位置推出，解析到了真实仓库；而真实仓库当时含并发写入者的 6 个未提交改动（即 57e86d44 这个身份）。

处理方式：
1. 本文所有诊断数字**只对 57e86d44 这个代码身份成立**，不得与 2a114890 的 E1/E2 证据混用；
2. 如需与 E1/E2 同身份，可把 e2f_run.py 按同样相对路径（ANALYSIS/ARRIVAL-TIME-T1-20260923/）复制进冻结快照后重跑，一条命令即可；
3. 这些是**本地诊断**，未签发授权、非 research_eligible。

---

## 9. 三因素结果表（诊断版，2026-09-25T00:32+08:00）

代码身份：57e86d44…（真实仓库工作树，回执 schema v6）；本地诊断、未授权、非 research_eligible。
分类器：CODE.leo_sim.scene_check，契约 CODE/work/WP-LEO-V2-GLOBAL-PRESSURE-BRACKET/R02/scene-check-contract.json。

| 臂 | 轴 | scene_check 判定 | admission | acc/route/down | isl_ok | 候选有向链路 | 连续窗口 | 各窗口利用率 | 同链路 p95 排队 |
|---|---|---|---|---|---|---|---|---|---|
| F1_offered_9 | F1 背景流量 | **ISL_PRESSURE_CANDIDATE** | 1.000 | T/T/T | True | isl:0:1 | 216,217 | 0.8426 / 0.8000 | 0.167 s |
| F1_offered_15 | F1 背景流量 | **ISL_PRESSURE_CANDIDATE** | 1.000 | T/T/T | True | isl:0:1 | 179,180 | 1.0000 / 0.8736 | 0.600 s |
| F2_delay_0p05 | F2 排队转发效率 | **ISL_PRESSURE_CANDIDATE** | 1.000 | T/T/T | True | isl:2:1 | 32,33 | 0.9984 / 0.8220 | 0.279 s |
| F2_delay_0p15 | F2 排队转发效率 | **ISL_PRESSURE_CANDIDATE** | 1.000 | T/T/T | True | isl:0:1 | 102,103 | 0.9510 / 0.9762 | 0.277 s |
| F3_isl_rate_8 | F3 链路带宽 | **ISL_PRESSURE_CANDIDATE** | 1.000 | T/T/T | True | isl:2:1 | 34,35 | 0.8807 / 0.8488 | 0.556 s |
| F3_isl_rate_12p5 | F3 链路带宽 | **ISL_PRESSURE_CANDIDATE** | 1.000 | T/T/T | True | isl:2:1 | 208,209 | 0.9048 / 0.8760 | 0.160 s |
| **NEG_no_queue** | **负对照** | **NO_ISL_PRESSURE** | 1.000 | T/T/T | **False** | **无** | — | — | — |
| base | 基线 | 运行中 | — | — | — | — | — | — | — |

### 9.1 三条立即成立的结论

1. **负对照成立（最关键）**：offered 降到 1 Mbps 后判定为 NO_ISL_PRESSURE，且 access/route/downlink 三层依然 clean——
   说明压力是**被自变量造出来的**，不是场景自带的伪压力（§5A 明令禁止靠 access 拒绝 / holding / 地平线截断制造假拥塞）。
2. **三轴都不破坏场景有效性**：F1/F2/F3 的 6 个非基线档全部保持 ISL_PRESSURE_CANDIDATE，admission 恒为 1.000、三层全 clean。
   这是单因素实验的前提——若某一档把场景打成 ACCESS_LIMITED 或 ROUTE_LIMITED，该档就不能用于比较。
3. **可测的错位信号已经出现且随轴变化**：同链路 p95 排队延迟在 0.160 s（F3 高档）到 0.600 s（F1 高档）之间变化，
   而传播时延 ΔT_arrival 恒为 0.017669 s（几何决定、与三轴无关）。**p95 排队 / 传播 ≈ 9× 到 34×**——
   这正是「观察到的邻居状态」与「包真正抵达时的状态」可能错位的量级来源。

### 9.2 待补

- base 行（同配置无改动的基线）完成后补齐；
- §5A 要求的最终回答需要把 p95 排队延迟与**队列自相关时间**对照，算出排序反转比例（M(M+δ)<0 的命中率）——
  这属于 E3 的排序反转分析范畴，须在授权后用正式运行的数据做。

### 9.3 完整 8 臂结果表（含 F2 记账列，2026-09-25T00:33+08:00）

| 臂 | scene_check 判定 | admission | isl_ok | F2 总量 (s) | spans_supplied | 未闭合 pid | ΔT_arrival p50 (s) |
|---|---|---|---|---|---|---|---|
| base | ISL_PRESSURE_CANDIDATE | 1.000 | True | 0.0 | True | 0 | 0.017669237 |
| F1_offered_9 | ISL_PRESSURE_CANDIDATE | 1.000 | True | 0.0 | True | 0 | 0.017669237 |
| F1_offered_15 | ISL_PRESSURE_CANDIDATE | 1.000 | True | 0.0 | True | 0 | 0.017669237 |
| F2_delay_0p05 | ISL_PRESSURE_CANDIDATE | 1.000 | True | **528.60** | True | 1 | 0.017669237 |
| F2_delay_0p15 | ISL_PRESSURE_CANDIDATE | 1.000 | True | **1584.00** | True | 6 | 0.017669237 |
| F3_isl_rate_8 | ISL_PRESSURE_CANDIDATE | 1.000 | True | 0.0 | True | 0 | 0.017669237 |
| F3_isl_rate_12p5 | ISL_PRESSURE_CANDIDATE | 1.000 | True | 0.0 | True | 0 | 0.017669237 |
| **NEG_no_queue** | **NO_ISL_PRESSURE** | 1.000 | **False** | 0.0 | True | 0 | 0.017669237 |

**8/8 臂全部完成**：7 个 ISL_PRESSURE_CANDIDATE + 1 个负对照 NO_ISL_PRESSURE。

三点读法：

1. **F2 列只在 F2 轴上非零**（528.60 / 1584.00 s，其余全 0.0）——**单因素隔离在结果层面也被确认**：
   改 offered 或 isl_rate 不会动 F2 记账，改 node_process_delay 才会。且 0.05→0.15（3×）给出 528.60→1584.00（3.00×），线性。
2. **ΔT_arrival p50 在 8 个臂上完全相同**（0.017669237 s）——因为它是纯几何传播、与三轴无关。
   这说明「到达时刻」本身不是被三轴改变的；被改变的是**抵达时对端队列的状态**（p95 排队 0.160–0.600 s）。
   这正是 §5A 要研究的错位：观察时刻的状态 vs 抵达时刻的状态。
3. **未闭合 pid 只在 F2 轴上出现**（1 与 6，delay 越大越多），与 F2 处理时延使更多包在地平线仍在处理中一致；
   node_process_spans_supplied 全为 True——R6 陷阱在本轮**没有发生**。

原始汇总：e2f-arms/summary-diagnostic.json

### 8.1 跨代码身份的确认跑（告警闭环，2026-09-25T00:35+08:00）

为闭环 §8 的身份告警，把 e2f_run.py 按同样相对路径放进冻结快照，用**干净身份**对 3 个代表臂复跑：

| 臂 | 快照身份 2a114890… | 脏树身份 57e86d44… | 一致 |
|---|---|---|---|
| base | ISL_PRESSURE_CANDIDATE | ISL_PRESSURE_CANDIDATE | YES |
| F2_delay_0p05 | ISL_PRESSURE_CANDIDATE，F2 = **528.6000000000561** | ISL_PRESSURE_CANDIDATE，F2 = **528.6000000000561** | YES（**逐位相同**） |
| NEG_no_queue | NO_ISL_PRESSURE | NO_ISL_PRESSURE | YES |

回执身份核对：快照跑 code_sha256 = 2a114890e8b11e429fec…，脏树跑 = 57e86d4471af7a94b584…，两者 config_sha256 与 schema（v6）相同。

**结论**：并发写入者的 6 个未提交改动（集中在流/回执/远端脚本层）**不影响本诊断的状态判定与 F2 记账**——两套身份下结论与数值完全一致。
故 §8 的身份告警可标记为已闭环；但其中三条处理方式仍然有效——正式证据仍须固定到单一身份。
