# 01 · E1 物理时间与 ETA 验证记录（首轮）

**时间**：2026-09-25 00:30+08:00
**身份**：commit 98c858f，code_sha256 2a114890e8b11e429fec45a6b1efcb81c1d80a8e195a9c9407a250d218f16dbe（clean 快照）
**被测配置**：CODE/leo_sim/profiles/t1_pressure_corridor_v2.yaml（config_sha256 56612d44ba449499…）
**脚本**：ANALYSIS/ARRIVAL-TIME-T1-20260923/e1_eta_validation.py
**报告**：e1-report-t1_pressure_corridor_v2_seed7.json

---

## 1. 定位与边界（先说清楚什么没做）

- 内核**没有 ETA 对象**：kernel 的 _estimate_at_start 预测的是"邻居出口队列比特数"，不是时间。因此 ΔT_arrival 与 ETA 误差**由本脚本自建**，内核只提供被对照的真值（十一时刻）。
- 日志**不含几何/距离**（决策流的 candidates 只有方向字符串），因此**传播时延的独立重算需要几何模块**——本轮**未实现**，脚本内以 NOT_IMPLEMENTED 显式标注，不做任何形式的假装通过。

## 2. 数据源管线（已验证）

    python3 -B -m CODE.leo_sim run --config <cfg> --out out/E1pipe \
      --decision-log out/E1pipe/decisions.jsonl --timeline-log out/E1pipe/timeline.jsonl
    python3 -B -m CODE.experiment_platform.fold_decision_ledger \
      --decision-log out/E1pipe/decisions.jsonl --timeline-log out/E1pipe/timeline.jsonl \
      --out out/E1pipe-ledger.json

产物：decisions.jsonl 138 MB、timeline.jsonl 15.8 MB、ledger 折叠 10,579 决策 / 67,410 里程碑，自检无重复 decision_id、无孤儿重决策。

## 3. 校验结果（10,579 决策）

| 校验 | 判定 | 结果 |
|---|---|---|
| **A1 星座参数核对** | **PASS** | resolved：24 星 / 3 面 / 550 km / 53° / min_elev 5° / seed 7；字段无缺失 |
| **A-S 序列化固定点** | **PASS** | 期望集合 {isl: 0.1 s, access_uplink: 0.001 s, access_downlink: 0.001 s}；匹配计数 {isl: 7052, access_uplink: 3525}；**最大绝对误差 2.36e-14 s**，违例 0 |
| **OBS 观测模式语义** | **PASS** | refresh 模式 10,579 条全部满足 t_measure == t_decision_commit，违例 0 |
| **D 时间分解单调性** | **PASS** | 7,051 条可判定（3,528 条含 missing 标记已跳过），**违例 0** |
| **D1 ΔT_arrival 统计** | **PASS** | n=10,576；min 0.001835 s / p50 = p95 = max = **0.017669 s** |

### 3.1 本轮抓出的建模错误（值得记录）

脚本第一版**只按 ISL 速率核对**，于是把 3,525 条接入侧（GSL）记录判成违例，最大误差 0.099 s —— 恰好等于 |0.001 − 0.1|。
→ 修正为"按链路类型分别核对"后全部通过。**这说明固定点校验必须覆盖全部链路类型（ISL / access 上行 / access 下行），不能只算 ISL。**

### 3.2 固定点关系的物理含义

- ISL：1,000,000 bit / 10 Mbps = **0.1 s**（7,052 条命中）
- 接入上行：1,000,000 bit / 1000 Mbps = **0.001 s**（3,525 条命中）
- 接入下行：本轮 0 条（该场景下行不构成服务段）

最大误差 2.36e-14 s 说明：内核的服务时长与"包长/速率"闭式**在浮点精度内完全一致**，时间分解没有额外加项、也没有重复计时。

## 4. NOT_IMPLEMENTED（下一轮补）

| 项 | 阻塞原因 |
|---|---|
| A2/A3 真实几何与可用性定点 | 需独立重算星座几何（轨道参数 + 时刻 → 卫星位置 → 星间距离）并与平台模型比对 |
| A4 脚本↔内核逐 hop 交叉比对 | 同上，依赖几何模块 |
| ETA 误差按几何（临近/远离）分组 | 依赖几何事件判定 |

## 5. 与现有测试的关系（来自 E1-验收测试盘点）

四条验收标准中 B/C/D 已被现有测试覆盖（B：commit_rejected + attempts 保留 + 决策 sink 无行；C：test_compute_delay 8 条 + test_frozen_observation 11 条；D：残差 ≤1e-9、相位和恒等且断言非平凡）。**A 组是缺口**，本脚本补的正是 A 组中不依赖几何的部分（A1 + A-S），并把 A2/A3/A4 显式列为未实现。

## 6. 复现

    # 在非符号链接路径下运行（macOS /tmp 是符号链接，F2/双流日志会被拒）
    python3 ANALYSIS/ARRIVAL-TIME-T1-20260923/e1_eta_validation.py \
      --run-dir out/E1pipe --ledger out/E1pipe-ledger.json \
      --out out/e1-report.json
    # → {"status": "PASS", ...}

> 注意：本记录为**本地诊断跑**，未走 VM 正式链路、未签发授权，故不是 research_eligible 证据。

---

## 7. 追加：A2 传播几何固定点（本轮新增，现已 PASS）

### 7.1 方法与独立性

日志里没有任何几何（决策流的 candidates 只有方向字符串），所以本项**由脚本用轨道参数闭式独立重算**，再与内核实际发生的时刻比对：

- 观测传播时延 := t_peer_arrival − t_service_finish；观测距离 := ΔT × c
- 同轨相邻星弦长（Walker，球面地球+圆轨道）：2·(R_earth + h)·sin(π / (n/p))，取 R_earth = 6371 km、c = 299792.458 km/s
- 地面-卫星斜距上界：−Re·sin e + sqrt((Re·sin e)² + h² + 2·Re·h)，e = min_elevation_deg

### 7.2 结果（PASS）

| 类别 | 观测条数 | 独立期望/允许区间 | 实测 | 判定 |
|---|---|---|---|---|
| **ISL（同轨相邻）** | 7,051 | 弦长 **5,297.104 km** | 观测距离 **5,297.104 km**，最大相对误差 **1.50e-08** | **PASS**（容差 1e-6） |
| GSL（地面-卫星斜距） | 3,525 | 允许区间 **[550, 2205] km** | 实测 551 / 553 / 860 / 1785 km 等，**越界 0 条** | **PASS** |

**读法**：内核的传播时延与独立闭式在 **8 位有效数字**上吻合（相对误差 1.5e-08），GSL 斜距全部落在物理可解释区间内。这条证据同时排除了两类错误：几何参数用错（如沿用示意值）与时间分解混入额外项。

### 7.3 六项校验总览（全部 PASS）

| 校验 | 判定 |
|---|---|
| A1 星座参数核对 | PASS |
| A-S 序列化固定点（按链路类型） | PASS（最大误差 2.36e-14 s） |
| **A2 传播几何固定点** | **PASS（ISL 相对误差 1.50e-08；GSL 越界 0）** |
| OBS 观测模式语义 | PASS |
| D 时间分解单调性 | PASS（违例 0） |
| D1 ΔT_arrival 统计 | PASS |

### 7.4 仍未实现（如实保留）

- 逐时刻轨道位置传播（含相位因子与地球自转），以及由此得到的**精确** GSL 斜距（当前只做上下界）；
- **ETA 误差按几何（临近/远离）分组** —— 依赖几何事件判定；注意本场景下 ISL 距离恒定（弦长固定），分组在本场景是退化的，需要换一个几何随时间变化的场景才有判别力。

---

## 8. 追加：outcome 过滤与链路口径检查（回应 E1 规格的两条新发现）

### 8.1 t_service_finish「首条陷阱」——实测不触发，但已加防线

规格指出：账本的 t_service_finish 取该 decision 下**第一条** service_finish 里程碑（decision_ledger._first_at），
而 _transmit 被中断后会重算并再次写入，因此该字段**可能是失败窗口的结束时刻**。

实测统计（v2 走廊 seed 7，10,579 决策）：

| 项 | 值 |
|---|---|
| service_finish 里程碑总数 | 14,105 |
| 其 outcome 取值分布 | **{ok: 14105}**（100% ok） |
| 有 service_finish 的 decision 数 | 10,577 |
| **第一条 outcome 非 ok 的 decision** | **0** |
| 有多个 service_finish 里程碑的 decision | **0** |

→ **本场景下该陷阱不触发**，故 §3/§7 的结论不受影响。
但代码级风险真实存在（中断/抢占场景会触发），脚本已加入 --timeline 参数与 filter_by_outcome 防线：
一旦出现非 ok 窗口，对应 decision 会被剔出 A-S / A2 / D / D1 的统计，并在报告 outcome_filter 字段中计数。

### 8.2 链路口径检查（A3，新增）

规格指出的口径缺口：max_isl_km = 6000 km 大于 **ISL MCS 零速率边界 5826.776601117517 km**，
即区间 [5826.78, 6000) 内链路**几何可用但速率为 0**（几何可用 ≠ 可服务）。

脚本已加入 A3 检查：rate_model=mcs 且 max_isl_km 越过该边界时报 REVIEW 并给出零速率区间；
constant 模式下不适用（本次即 SKIP，因为 v2 走廊是 constant 模式）。
另记：constant 模式**不复现 legacy 链路预算**（test_link_budget_characterization.py 明确说明），两模式口径不可互相背书。

### 8.3 七项校验总览（本次运行）

| 校验 | 判定 |
|---|---|
| A1 星座参数 | PASS |
| A-S 序列化固定点 | PASS |
| A2 传播几何固定点 | PASS |
| A3 链路口径（MCS 零速率边界） | SKIP（constant 模式不适用） |
| OBS 观测模式语义 | PASS |
| D 时间分解单调性 | PASS |
| D1 ΔT_arrival 统计 | PASS |

### 8.4 一处归属澄清

规格子 agent 报告「/tmp/leo-freeze-98c858f 快照被并发写入者改动（00:13:57 写入 3 个 profile、00:17:38 出现 out/F2_zero_nolog）」——
经核对，**这些改动是主会话自己做的**（把新建的三个 profile 复制进快照以供干净身份验证、以及 F2 旋钮测试的产物），
不是第三方写入者。真正观察到并发写入的是**仓库工作树**（CODE/leo_sim/__main__.py、receipt.py 等 6 个未提交改动），而快照本身是 git archive 的独立副本。

---

## 9. 追加：frozen 观测模式验证（补齐 OBS 只覆盖 refresh 的缺口）

规格 S1 建议跑两组基线：compute_delay_s=0 / refresh（默认）与 compute_delay_s=0.05 / decision_observation_mode=frozen。
首轮只跑了前者（frozen 分支 checked=0）。本轮补齐第二组：

| 项 | refresh 组（首轮） | **frozen 组（本轮）** |
|---|---|---|
| 配置 | v2 走廊默认 | v2 走廊 + compute_delay_s=0.05 + decision_observation_mode=frozen |
| 决策数 | 10,579 | **10,576** |
| OBS 覆盖 | refresh checked=10,579，违例 0 | **frozen checked=10,576，违例 0** |
| 折叠诊断 | 无重复 / 无孤儿 | 无重复 / 无孤儿（里程碑 67,563） |
| 七项校验 | 全 PASS（A3 SKIP） | **全 PASS（A3 SKIP）** |

frozen 模式的语义断言：t_measure 冻结在决策开始时刻（== t_decision_start），与 refresh 模式（== t_decision_commit）互为对照，两种模式**各自内部一致、且互不相同**——这正是 E1 要验证的观测模式语义。

报告文件：e1-report-frozen-mode-seed7.json
