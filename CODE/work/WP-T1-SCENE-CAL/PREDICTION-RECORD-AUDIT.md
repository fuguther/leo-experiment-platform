# 预测记录字段审计（2026-10-19，据实际落盘核对）

来源：`scc-sc-cond-r050-truth-s111/arms/now/decisions.jsonl.gz` 的首条记录，共 **267 个路径**。
**结论：我上一轮说"大部分字段没有"是错的。绝大多数已在落盘。** 逐项如下。

## 已存在且实际落盘

| 需求 | 实际字段 | 备注 |
|---|---|---|
| packet / satellite / decision | `pid`, `sat`, `t`, `t_decision_start`, `decision_id` | ✓ |
| 合法候选集（全部，不只选中） | `candidates`, `observation_at_start.four_direction_audit.candidate_details.{E,W,N,S}` | ✓ |
| 最终选择 | `chosen`, `time_alignment.applied_order`, `time_alignment.ranking` | ✓ |
| 预测器名称 | `time_alignment.predictor` | ✓（版本/训练哈希见"缺失"） |
| 信息条件 | `time_alignment.arm`, `time_alignment.state_source` | ✓ |
| **目标资源唯一 ID 与 generation** | `used_state[d].resource_id`（如 `isl:82:90`）、`used_state[d].generation`（如 330） | ✓ |
| **决策时刻** | `time_alignment.snapshot_at`, `t` | ✓ |
| **源测量时刻** | `used_state[d].measured_at` | ✓ |
| **接收时刻** | `used_state[d].received_at` | ✓ |
| **使用/目标时刻** | `used_state[d].used_at` + `time_alignment.query_targets[d]` | ✓ 两者都在 |
| 状态值（bit） | `used_state[d].bits`, `used_state[d].queued_bits` | ✓ |
| 状态来源 | `used_state[d].source`（advertised / ideal_current） | ✓ |
| 候选取用的资源与量 | `candidate_details[d].resource_id/.peer/.generation/.queue_bits_before/.rate_bps` | ✓ |
| 评分分项 | `candidate_score.terms.{resource_work_s, prop_s, tx_s, compute_wait_s, ...}` 共 11 项 | ✓ |
| 评分总分与排序 | `candidate_score.total_s`, `time_alignment.scores`, `ranking` | ✓ |
| ETA 与分项 | `eta_targets`, `eta_terms`, `eta_unknown_terms` | ✓ |
| **目标时刻真值** | `truth_at_commit.candidate_truth[d].downstream.{peer_in_service_bits, peer_egress_data_bits, peer_egress_link_id, ...}` + `.available` | ✓ |
| 真值取得方式 | `truth_at_commit.source`, `.mapping_status`（"truth_audit_not_learner_tensor"）, `.t_observed`, `.contract` | ✓ |
| 无法配对 / 缺失 / 回退原因 | `time_alignment.missing_directions`, `fallback_directions`, `candidate_details[d].filter_reasons` | ✓ |
| 查询代价 | `time_alignment.query.{requests, servers, service_s, wait_s}`, `query_delay_s` | ✓ |
| 计算面状态 | `time_alignment.compute_state.{busy, queued, servers, service_s, wait_estimate_s}` | ✓ |

## 存在但不完整

| 需求 | 现状 | 影响 |
|---|---|---|
| **历史输入（或可重建引用）** | `time_alignment.history_samples[d]` 只记**样本条数**（如 `E: 1`），**不记样本内容** | 无法从该记录无歧义重建预测输入；需补样本序列或内容寻址引用 |
| **预测值的单位一致性** | `resource_work_s` 是**秒**，而 `used_state[d].bits` 是 **bit**；两者同源不同口径 | 离线评价必须显式换算（乘 `rate_bps`），否则表 A 会错口径 |
| `observation_age` | 在该记录中为 `None` | 可由 `used_at - measured_at` / `received_at - measured_at` 重算，非硬缺失 |

## 真正缺失

| 需求 | 现状 | 影响 |
|---|---|---|
| 预测器**版本/训练配置哈希** | 只有名字（`hold_last` 等） | 学习模型接入后无法证明用的是哪个权重；必须补 |
| 每次预测的**唯一预测 ID** | 无独立 ID，靠 (run, pid, sat, t, direction) 组合定位 | 可接受，但须在文档里写死键定义 |

## 对上一轮说法的更正

我上一轮把 G2 写成"§四 的预测记录字段大部分没有"，**这是错的**。
准确说法是：**记录链路基本可用，缺两处（历史样本内容、预测器版本哈希），另有一处口径隐患（秒 vs bit）。**
因此 G2 从"必须新建"降级为"**小补丁 + 单位换算约定**"。
