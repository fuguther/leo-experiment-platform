# E0–E3 交付物总览（入口）

**目录**：leo-experiment-platform/ANALYSIS/ARRIVAL-TIME-T1-20260923/
**生成时间**：2026-09-24 起，最后更新 2026-09-25T03:05+08:00
**代码身份（冻结口径）**：commit 98c858f，clean 树 code_sha256 = 2a114890e8b11e429fec45a6b1efcb81c1d80a8e195a9c9407a250d218f16dbe

> 口径纪律见 07-caliber-decisions.md：**结论必须带口径标签**（packet_bits / 链路 / 代码身份 / 证据级别）；
> 不得把「厂商标称」「aggregate 单元」「代理分布」写成实测或真实。

---

## 1. 这份交付集是什么

任务书 v1.4 规定的 E0–E3 阶段交付物（§8 命名），外加 3 个新增 profile 与 9 个可复跑脚本。

| 阶段 | 结论 | 关键文件 |
|---|---|---|
| E0 只读取证 | 完成；五门判定 4 NOT MET + 1 MET | 00- / 00b / 00c / 00d / 00e / manifest.json |
| E1 物理时间与 ETA | 完成；七项校验全 PASS（refresh 与 frozen 双模式） | 01-formal-model / 01-e1-validation-record / e1_eta_validation.py |
| E2 压力窗口 | 完成；12/12 判 ISL_PRESSURE_CANDIDATE（6 种子 × 2 时长） | 02-scene-and-pressure-audit |
| E2-F 三因素 | 完成；8 臂 7 CANDIDATE + 1 负对照；严格单因素 0 非预期变化 | 02b / e2f_arms.py / e2f_run.py / e2f-arms/ |
| E3 反事实与信息价值 | 骨架完成并自证；H1 判定为**当前记录面下不可判定** | 03 / 04 / 05 / 06 / 08 与 e3_*.py |
| §R5 现实一致性 | 三项收口（1 落盘、2 工具化+口径裁定、3 场景已验证） | 00d 更新记录、profiles/、r5_packetization_check.py |

---

## 2. 精确复现配方（重要）

本交付集**不等于** commit 98c858f 本身：其中的新 profile 与新文档在仓库里是**未跟踪文件**。
从零复现必须两步：

    第一步：取出提交态
      git archive 98c858f | tar -x -C <目标目录>

    第二步：叠加本交付集的新增件
      cp <本目录>/profiles/*.yaml  <目标目录>/CODE/leo_sim/profiles/
      cp <本目录>/*.py             <目标目录>/ANALYSIS/ARRIVAL-TIME-T1-20260923/

然后按各文档里的命令跑（关键命令见 05 §6/§7 与 04 §11）。

> **注意**：日志路径的父目录必须已存在，且**不得含符号链接**（macOS 的 /tmp 是符号链接，会被平台拒绝）。

---

## 3. 文件地图

| 类别 | 文件 |
|---|---|
| E0 取证 | 00-repository-and-source-audit.md、00b-real-world-source-ledger.csv、00c-packetization-and-hardware-bench.md、00d-realism-gates.md、00e-field-time-and-information-permission-table.md |
| 模型与 E1 | 01-formal-model.md、01-e1-validation-record.md、e1_eta_validation.py、e1-report-*.json |
| E2 与 E2-F | 02-scene-and-pressure-audit.md、02b-three-factor-one-at-a-time.md、e2f_arms.py、e2f_run.py、e2f-arms/ |
| E3 | 03-preregistration.md、04-counterfactual-validation.md、05-results-and-negative-findings.md、06-decision-gate.md、08-recording-gap-proposal.md、e3_regret.py、e3_scorer.py、e3_arms_compare.py、e3_placebo.py、e3_o3_probe.py（SUPERSEDED）、e3-*.json |
| 口径与 §R5 | 07-caliber-decisions.md、profiles/（3 个新 profile）、r5_packetization_check.py |
| §1A 核对 | screenshot-review-matrix.md |
| 登记 | manifest.json（51 个文件 + 身份 + 授权链 + 边界声明） |

---

## 4. 当前边界（必须随结论一起转述）

1. **H1 在当前记录面下不可判定**：决策相关的远端边属于路径上的其他卫星，其缓存真值平台未记录；
   五种替代解释已逐一被实验排除（见 06 §8.1）。修法见 08-recording-gap-proposal.md（待 owner 评审）。
2. **全部结果为本地诊断或合成诊断**，无一条达到 formally governed VM，**均不可升级为论文证据**；
3. **O2（共同未来真值）未记录**，故任务书主指标 Regret(O2strong)−Regret(O3) 尚未执行；
4. **§R5-2 的包长为二手锚点**，不是真实分布；packet_bits 正式口径未收敛（现双口径并存）。
---

## 5. 复现验证记录（2026-09-25T03:05+08:00）

从零解出一份干净的 commit 98c858f 快照（424 文件），按 §2 配方叠加本交付集后，完全按文档命令执行：

| 步骤 | 命令来源 | 结果 | 与既有记录对比 |
|---|---|---|---|
| 基线跑（capacity + 双流日志） | 04 §11.2 | exit 0，conservation_ok = True，DELIVERED **3525**、IN_SYSTEM_AT_STOP 3 | **与 E3cap 逐位一致** |
| 折叠账本 | 04 §11.2 | 10,579 决策、**67,410 里程碑**，无重复 decision_id、无孤儿重决策 | **一致** |
| E1 校验 | 01-e1-validation-record | **七项全 PASS**（A3 SKIP） | **一致** |
| 评分器保真度自证 | 05 §7.1 | **200/200，agreement_rate = 1.0** | **一致** |

### 5.1 本次验证抓到的真问题（已修）

**首跑失败**：仅按 `git archive 98c858f` 取快照时，脚本报 `FileNotFoundError: CODE/leo_sim/profiles/t1_pressure_corridor_v2.yaml` ——
因为三个新 profile 在仓库里是**未跟踪文件**，不在提交里。

修法：把三个新 profile 复制进本交付集的 `profiles/` 子目录，并把「提交态 + 叠加交付集」写进 §2 的精确配方。
在此之前，任何"只按 commit 复现"的尝试都会失败——这条已在本书面固化。

---

## 6. 最终状态（2026-09-25T04:00+08:00）

| 阶段 | 结论 |
|---|---|
| E0 | 完成；五门 4 NOT MET + 1 MET |
| E1 | 完成；七项校验全 PASS（refresh 与 frozen 双模式，冻结语义保陈旧） |
| E2 | 完成；12/12 ISL_PRESSURE_CANDIDATE |
| E2-F | 完成；8 臂 7 CANDIDATE + 1 负对照，严格单因素 0 非预期变化 |
| **E3** | **完成；H1 判定为阴性，原因定量明确（队列差 ≈ 决策余量/15）** |
| §R5 | 三项收口 |

### 6.1 四条可直接引用的稳定结论

1. 平台只有 capacity/delay 消费邻居状态；18 个既有 profile 全用 hop -> 机制在此前实验里按定义不可能显现；
2. 评分器复刻保真度 100%（200/200），可复用于任何 E3 类分析；
3. 陈旧性的效应通道是**可用性**而非精度（2 s ≡ 10 s；30 s 崩塌 + 1211 次 holding 溢出）；
4. capacity 的决策是**总代价最小**，队列项权重远小于路径长度项 —— 这是 H1 阴性的根本原因。

### 6.2 待 owner 的决策（唯一一项）

是否把 recording-extension.patch 应用到仓库（附件已 5/5 验收：语义逐位不变、116 测试通过、保真度不回退、1,893 B/决策）。
应用价值：把 A/B 两组记录固化进主线，使 O1/O3 类对照今后可直接复跑；
不应用也不影响 H1 的既有判定（对照已在一次性快照内用完整信息完成）。
