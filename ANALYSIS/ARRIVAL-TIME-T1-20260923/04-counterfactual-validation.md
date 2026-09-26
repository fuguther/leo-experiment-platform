# 04 · E3 反事实验证合同（分叉机制 / 快照与 RNG 一致性 / 每候选后果）

**状态**：草案，随 03-preregistration.md 一起**待 owner 冻结**（δ_min、代码身份口径、授权）。
**依据**：任务书 v1.4 §E3（配对反事实）、§6（逐候选记录）、§7 负对照 #9/#10/#11/#12
**平台实现**：CODE/leo_sim/counterfactual.py（branch_fingerprint:54、replay_with_forced_action:75、PRECOMMIT_FIELDS:34、OUTCOME_KEYS:41）+ CODE/experiment_platform/replay_counterfactual.py（CLI）

---

## 1. 分叉机制（平台已有，不另造）

严禁对 SimPy 运行态做未经验证的 deepcopy。采用的机制是**同一不可变 trace/配置 + 逻辑键随机数重播到事前状态，再替换目标包的一次决策**：

    python3 -B -m CODE.experiment_platform.replay_counterfactual \
      --config <resolved config> --decision-id <N> --forced-action <方向> --root . --out <新路径>

内部流程（counterfactual.py:75-104）：同一 resolved config 与同一 trace 行跑**两次**——
基线一次、在目标 decision 上强制指定动作一次；两次都挂 decision_sink / timeline_sink。

---

## 2. 分叉前一致性（必须逐项断言，缺一即 fail）

**指纹定义**（counterfactual.py:54-72）：SHA-256 over {该 decision_id **之前**的全部决策行} + {目标行的 PRE_COMMIT 字段}。

PRE_COMMIT_FIELDS（counterfactual.py:34-38，**刻意不含 chosen**）：
t, t_decision_start, decision_id, state_version, pid, src, dst, sat, kind, policy, candidates, own_queue_bits, obs, info_audit

必须断言：

| 断言 | 含义 | 失败处置 |
|---|---|---|
| branch_states_identical == True | 基线与强制跑的**分叉前指纹逐位相同** | 丢弃该对比，fail-loud |
| legal_at_branch_point == True | 强制动作在分叉点确属合法候选 | 丢弃，不得当作反事实 |
| action_changed == True | 确实换成了不同的动作 | 否则该次重放无信息量，记录并不计入统计 |
| state_version / info_audit 一致 | 缓存与信息权限在分叉点相同 | 任何不一致即 INVALID |

---

## 3. RNG 与外生原语配对

- 运行 manifest 记录 rng_streams（实测形如 {"demand": "SeedSequence(7).spawn[0]"}）：**同一 seed 的两跑必须给出同一外生流身份**；
- 外部到达与故障原语按**稳定身份**（packet_id / 事件身份）配对，**不因事件调用顺序漂移**；
- 每次都须回显 trace_identity_sha256（实测 855431d83621e22b…）与 config_sha256，作为配对前提。

---

## 4. 每候选后果（比较面）

OUTCOME_KEYS（counterfactual.py:41-42）：fate_counts, totals, occupied, queue_area_bits_s, access, events_processed。

叠加 §6 要求的逐候选字段（不发生的事件存 null + reason，**不得写 0**）：
direction, peer, local_queue/in_service/control_bits, rate, availability, distance, t_target, t_eta_est, t_q/t_s/t_f/t_a_actual, downstream_resource_kind, target_workload_truth, workload_prediction, predicted_cost, counterfactual_cost, fate。

**硬约束**：分支间差异**只允许**目标动作及其因果后果；O3/O4 只作离线诊断器，**不得**反向影响评分。

---

## 5. 已知限制（必须写进任何引用本合同的文档）

| 限制 | 事实 | 影响 |
|---|---|---|
| 反事实 v1 要求 learning.algorithm == none | counterfactual.py:86-91 显式拒绝非确定性路由器 | E3 的 O0–O4 全是**推理期对照组**、与学习无关，故不受影响；**H2 及之后涉及学习臂的反事实不可用**，需先扩平台 |
| 一次重放只换一个决策 | CLI 语义 | 多决策闭环（H4）须另行设计，不能靠单点重放外推 |
| 计算耗时 | E3 在**零额外计算耗时**下比较；耗时影响属 H3/E5 | 不得把 E3 的结论外推到含耗时场景 |

---

## 6. 测试矩阵（映射负对照 #9/#10/#11/#12）

| # | 要测什么 | 断言 |
|---|---|---|
| 9 | 分叉前一致、分叉后仅合法干预不同 | branch_states_identical==True 且 action_changed==True 且仅目标动作不同 |
| 10 | 真实到达时间与未来队列真值不得进入可部署预测 | 在线臂字段白名单自动检查 + 主动非法访问 fail-loud；O3/O4 标记离线 |
| 11 | **四象限**防归因错误：真实ETA×真值队列 / 估计ETA×真值队列 / 真实ETA×预测队列 / 估计ETA×预测队列 | 四组分别报告配对差，避免把误差全归因于时刻对齐 |
| 12 | 安慰剂/泄漏：乱序标签、错配候选、复制同一共同时刻、同包重复入队 | 每项应有接近零的伪效应；显著非零即判为泄漏 |

---

## 7. 与 E2-F 诊断的衔接

E2-F 已提供：场景有效性（7 档 CANDIDATE + 负对照 NO_ISL_PRESSURE）、单因素隔离证据（结果层 F2 列只在 F2 轴非零）、F2 可识别性（必须传 spans，否则静默失败）、未闭合区间口径。
E3 需在此之上补：决策快照抽取与合法候选枚举、O0–O4 五组状态注入、Regret(O2strong)−Regret(O3)、反转率与净反转代价、按 run/trace/seed 簇的区间估计。

---

## 8. fail-loud 清单（不得静默通过）

1. 指纹不一致；2. 强制动作在分叉点非法；3. 动作未改变；4. trace/config/RNG 身份不一致；5. 出现未注册的字段访问（白名单外）；6. 未送达包被写入 0 而非 null；7. 同一运行内包被当作独立样本。

---

## 9. 机制可用性实测（2026-09-25T00:36+08:00，冻结快照身份 2a114890…）

在 base 臂（v2 走廊、CANDIDATE）的真实决策流上做一次严格配对重放，**合同中的断言全部成立**：

    python3 -B -m CODE.experiment_platform.replay_counterfactual \
      --config <base 臂用户层配置> --decision-id 204 --forced-action N --root . --out out/cf-test.json

| verification 字段 | 实测值 |
|---|---|
| status | replayed |
| target_decision_id | 204（pid 19, sat 2, candidates [S, N]） |
| baseline_action → forced_action | S → N |
| baseline_branch_fingerprint | b4b90823ccbfc6808a8acb7d347475e7ee2d90a6030a61667d63dd46a9748a51 |
| counterfactual_branch_fingerprint | **完全相同** |
| **branch_states_identical** | **true** |
| **legal_at_branch_point** | **true** |
| **action_changed** | **true** |

outcome_delta（真实因果差，E3 的 Regret 原料）：

| 键 | 差值 |
|---|---|
| fate_counts | DELIVERED **−1**，IN_SYSTEM_AT_STOP **+1** |
| totals | delivered_bits **−1,000,000**，in_system_bits_at_stop **+1,000,000** |
| occupied | isl_s **−0.100 s**，gsl_downlink_s **−0.001 s** |
| queue_area_bits_s | holding **+297,839,898.7**，isl_data **−466,752.5** |
| events_processed | **−13** |

**结论**：E3 所需的「同快照—只换一个动作—可比后果」机制**已在本平台跑通且自证一致**。

### 9.1 实测发现的工程约束（E3 必须提前规划）

1. **单次重放输出达 460 MB**（含基线与强制两跑的完整数据）。一次决策一次重放；若 E3 要覆盖数百个决策，需要先定存储与裁剪方案（只留 verification + outcome_delta + 必要的逐候选行）。
2. **CLI 要用户层配置**，不接受 resolved_config.json（后者是 {config, version} 包装层，会被判 unknown top-level groups）。这与 E2-F 臂生成器遇到的坑同源。

---

## 10. E3 重放的存储与裁剪方案（2026-09-25T00:55+08:00）

### 10.1 问题规模

实测：单次重放输出 **460 MB**（含基线与强制两跑的完整数据）。
若 E3 覆盖 100 个快照 × 每快照 2–3 个候选分支，即约 200–300 次重放：**naive 累加 92–138 GB**，不可接受。

### 10.2 方案：立即抽取 + 逐次删除（不改平台代码）

**关键约束**：CODE/leo_sim/counterfactual.py **列在 ANALYZER_FILES 内**，改它会改变分析器 Git 身份绑定。
因此**不修改平台代码**，改为运行后立即抽取、随即删除原始大文件：

1. 调 replay_counterfactual，输出到临时路径 cf-快照-候选.json
2. 立刻抽出三样写入 slim 记录：
   - verification（指纹、branch_states_identical、legal_at_branch_point、action_changed）
   - outcome_delta（E3 的 Regret 原料）
   - source（身份与指向）
3. 删除 460 MB 原始文件（保留 slim 记录，实测每条 < 10 KB）

**成本**：磁盘峰值 ≈ 1 条原始文件（约 0.5 GB，串行执行）；300 条 slim 记录合计约 3 MB。
**可审计性**：slim 记录保留 verification 全文与 outcome_delta 全键，**指纹与一致性断言可复核**；
原始明细不留存——因此 slim 记录必须同时存 config_sha256 / code_sha256 / trace_identity_sha256，
保证任何人可用同一身份重跑复现（重跑成本 = 一次重放）。

### 10.3 若日后需要留存原始明细

再加一层：对必须留证的少数关键快照（例如用于论文的示例），单独保留原始 JSON 并纳入 run 目录；其余一律 slim。
**不得**为了省事把 460 MB × N 全部堆在 Results 下。

---

## 11. E3 操作手册（脚本与命令，2026-09-25T01:30+08:00）

### 11.1 前置：必须用 capacity 策略

平台六个策略中只有 capacity 消费邻居广告队列（routing.py:247-279）；hop / info_queue / info_physical 都不消费，
用它们做 E3 会得到「任何注入都不改变动作」的伪阴性。因此 E3 基线配置 = 走廊 v2 + routing.policy = capacity。

### 11.2 三步流程

第一步：跑基线（带双流日志），并折叠账本。命令（单行）：

    python3 -B -m CODE.leo_sim run --config CAP.yaml --out out/E3cap --decision-log out/E3cap/decisions.jsonl --timeline-log out/E3cap/timeline.jsonl
    python3 -B -m CODE.experiment_platform.fold_decision_ledger --decision-log out/E3cap/decisions.jsonl --timeline-log out/E3cap/timeline.jsonl --out out/E3cap-ledger.json

第二步：跑 E3 骨架（逐候选强制重放 + 抽取 + regret）。

    python3 -B ANALYSIS/ARRIVAL-TIME-T1-20260923/e3_regret.py --run-dir out/E3cap --ledger out/E3cap-ledger.json --config CAP.yaml --snapshots 3 --out out/e3-report.json --work TMPDIR

第三步：查看 out/e3-report.json 的 summary 字段。

### 11.3 三条硬约束（脚本已内置）

1. **事实分支不重放**：重放会因 action_changed 断言被拒（REPLAY REFUSED: forcing X did not change the action）；选中动作的结局直接取基线运行。
2. **未送达分支没有 O3**：truth_at_target 只对实际到达的分支记录（contended_direction 为 null 即缺失），必须记录为缺失，**不得填 0**。
3. **slim 策略**：每次重放约 460 MB，抽取后立即删除；调试时加 --keep-raw。

### 11.4 结论标注要求

本骨架给的是 **O0/O1/O3** 对照，**不是** O2strong；输出里已固定写入该声明。引用其数字的文档必须同时标注：
探索性、非 O2strong 对照、本地诊断（非 formally governed VM）、以及 07-caliber-decisions.md 的口径标签。
