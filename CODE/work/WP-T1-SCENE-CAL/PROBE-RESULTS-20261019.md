# 探针实跑结果（2026-10-19）

## 版本与运行身份

| 项 | 值 |
|---|---|
| 冻结 release | `2c4f416122565335b1bbe9146a13b6e2374964db-24e61c919f6f995954cfd3bd006786e56c79321bc551263c4f0719b3df801bc0` |
| Git commit | `2c4f416122565335b1bbe9146a13b6e2374964db`（分支 `codex/20261018-scene-calibration`） |
| 远端备份 | `remote_backup_pending`（未 push；按规则只能作诊断，不构成正式实验授权） |
| cell | `sc-probe-truth-s111`（三条城市 OD、250 Mbps、6 s、链路 1 Gbps、已知在服策略） |
| run-id（正式证据） | `scpt-sc-probe-truth-s111-20261019-17` |
| run-id（作废：时间偏样本） | `scpt-sc-probe-truth-s111-20261019-16` |
| contract sha256 | `3046e351be9cded454eba262231152166a2839a43b01390c632a925dd962dd7a` |
| arm config sha256 | `9662044172d525462ae0398abed9f287fc48e8b05ac8b515aadb01130ccb6ac5` |
| 墙钟 / 停时 | **870.3 s** / 6.0 s（natural_end=true，rc=0） |
| 探针文件 sha256 | `7c13ef733c50a6c82b50bc38e01c6570eca37b3487a55c6a95bf5fc1c58d34e9`（47 755 行） |
| 预算 | 本轮内核启动 **2 次**（run 16 / run 17）；人口 run 另计 1 次 |

## 预测输出与评分一致性（run 17）

| 检查 | 结果 |
|---|---|
| 探针行数 / 采样步长 | 47 755 / stride=4（另有 143 268 行按步长跳过，**非**上限截断） |
| 标签覆盖 | **98.45%**（47 014 / 47 755） |
| 缺失原因 | 仅 `no_prediction` 741 条 |
| `sampled_at == eval_target` | **0 不一致** |
| 决策日志 `eta_targets` == 探针 `eval_target` | **0 不一致** |
| 资源 ID 对应 | **0 不一致** |
| generation 对应 | **0 不一致** |
| `predicted_bits / service_rate` == 评分用的 `resource_work_s` | **0 不一致 / 47 014** |

| 误差（bit，n=47 014） | 值 |
|---|---:|
| 带符号均值 | **−489.0**（系统性低估） |
| 平均绝对 | **766.4** |
| 中位 | 0.0（多数候选目标时刻确实无积压） |
| 标签非零比例 | **4.55%**（2 141） |
| 预测非零比例 | **2.01%**（944） |
| 选中候选 \|误差\| 均值 | **1 783.6**（n=17 348） |
| 未选中候选 \|误差\| 均值 | **171.5**（n=29 666） |

→ `hold_last` 在**正确的目标时刻任务**上确有系统性低估，方向与 `PREDICTION-LABELS.md` 一致；
但这次标签来自**同一次运行的 in-run 真值**，且逐条可核对资源身份与 generation。

## 探针不干扰仿真的现场证据

run 16 与 run 17 是**同一契约、同一 release、同一种子**，唯一差异是探针抽样强度
（stride=1 截断到 4 万行 / stride=4 全窗 4.77 万行）。业务结果**逐位相同**：

| 指标 | run 16 | run 17 |
|---|---:|---:|
| 包数 | 7 212 | 7 212 |
| DELIVERED | 7 024 | 7 024 |
| NO_ROUTE | 188 | 188 |
| DATA_DEADLINE_EXPIRED | 0 | 0 |
| 交付率 | 0.9739323349972269 | 0.9739323349972269 |
| uplink 队列面积 | 1 246 227.39 | 1 246 227.39 |
| isl_data 队列面积 | 52 276.19 | 52 276.19 |

探针强度差别很大而轨迹完全一致 ⇒ **探针只读、不占业务资源、不消费业务随机数**。

## 接入 vs 队列（同 run）

- `access.requests = 0`、`wait_time_s_total = 0.0`、`waiting_at_stop = 0`、`grants = 3`。
- 队列面积：uplink 1.25e6、isl_data 5.23e4、isl_ctrl 1.14e3（bit·s）。
- 本格接入**没有产生等待**；三城市 250 Mbps 探针的失败全部是 `NO_ROUTE`（188 = 2.61%），
  零超期、零溢出——与新场景族的既有结论一致。

## 原始记录样例

`target_probe_sample_3.jsonl`（前 3 条，原文）：

```
{"algo_query_target_s": 2.110251986463245, "arm": "candidate", "candidate_directions": ["E","W","N","S"], "chosen": "E", "decision_id": 0, "direction": "E", "eval_target_s": 2.110251986463245, "generation": 330, "identity_source": "used_state", "kind": "forward", "pid": 2, "predicted_bits": 0.0, "predictor": "hold_last", "resource_id": "isl:82:90", "resource_peer": 82, "sampled_at_s": 2.110251986463245, "sampled_generation": 330, "sampled_resource_id": "isl:82:90", "sat": 74, "selected": true, "service_rate_bps": 1000000000.0}
```

证据目录：`三端文件与同步治理/evidence/t1probe-20261019/<run-id>/`
（`probe-verification.json`、`cell-summary.json`、`run-identity.json`、200 行原始样例、探针 sha256）。

## 仍需完成

- 与离线标签构造器（`build_prediction_labels.py`）的**同一格**一致性比较：
  in-run 真值 vs 事件日志重建，两者定义不同（前者含在服剩余、后者按服务窗折算），差异要如实报告。
## 人口格实跑（run `scpp-pop-probe-r020-s111-20261019-18`）

| 项 | 值 |
|---|---|
| cell | `pop-probe-r020-s111`（人口加权 93 OD、149 格、200 Mbps、6 s） |
| contract sha256 | `70818c36a935b9f8324f25663a9b0ac25650895c881ba1700829a25c5224b668` |
| arm config sha256 | `c0ee36cf5ec6c0eb14f972168ad67c08721ee4e73bc98c956380f13e91fa6465` |
| 墙钟 | **806.6 s**（rc=0，natural_end=true） |
| trace rows | 5 827（trace manifest sha256 `e16803e14f9b26bd980cf4e5ca1a66fc58bb43e1424c65255ee5cd6cf8888add`） |
| 探针 sha256 | `56d0208dd5891bb5af43c4aaeaa037684e5dc927d9b66fe712151ef0ad12df54` |

### 探针核验

| 检查 | 结果 |
|---|---|
| 覆盖 | **99.11%**（59 467 / 60 000） |
| 缺失 | 仅 `no_prediction` 533 |
| sampled_at / 资源身份 / generation / eta 对应 | **全部 0 不一致** |
| predicted/rate == resource_work_s | **0 不一致 / 59 467** |
| 带符号误差均值 | **−810.2 bit**；平均绝对 **860.2 bit** |
| 标签非零 | **7.59%**（4 513）；预测非零仅 **0.27%**（162） |
| 选中候选 \|误差\| 均值 | **2 300.9**（n=20 527）；未选中 100.8 |

**抽样偏差（如实记录）**：该格 stride=1、上限 60 000，实际候选行 125 043（`dropped_overflow=65 043`），
因此这 60 000 行只覆盖 eval_target ∈ [2.114, 2.579] s，**仍是按时间的偏样本**；
标签统计不能当作全窗结论。业务统计（下面）不受影响——探针不干扰仿真已由 run16/17 逐位相同证明。

### 业务统计：接入主导（关键判定）

| 指标 | 值 |
|---|---:|
| 包数 | 5 827 |
| DELIVERED | 4 420（交付率 **0.7585**） |
| **IN_SYSTEM_AT_STOP** | **1 397（24.0%）** |
| NO_ROUTE | 10 |
| 超期 / 各类溢出 | 0 / 0 |
| **access.wait_time_s_total** | **268.5 s** |
| **access.wait_time_s_max** | **3.90 s**（≈ 4 s 期限） |
| access.requests / grants | 348 / 389 |
| **access.waiting_at_stop** | **88** |
| holding 队列面积 | **7.94e7 bit·s** |
| uplink 队列面积 | 3.14e7 bit·s |
| isl_data 队列面积 | **2.58e4 bit·s** |

ISL 积压比接入侧小 **约 3–4 个数量级**，且近四分之一的包到仿真结束还没送出。
⇒ **人口场景在 200 Mbps + 每条卫星 8 个接入时隙下是接入受限的**，
其失败与延迟**不能归因于路由或状态时间错位**。

### 因此的判定

- 用户设定的**“接入等待不许主导”验收未通过**，所以**不执行**基于该场景的 S/O/N 对照；
  先要让接入侧成为非瓶颈（提高 `slots_per_satellite`、降负载、或改接入关联规则），再谈路由性能。
- 交付物本身（人口加权 OD、格内位置、固定外生 trace、接入映射、探针记录）已完成且可复算。


## 2026-10-29：truth-score replay 可用性复核

run 17 的原报告确认探针按 stride=4 输出 47,755 行，并报告了同一次运行内的目标时刻误差与资源身份核验。随后对源码做了审查：旧版 stride 在候选行循环内部应用，而不是按决策组应用。每个决策有多个候选方向，truth-score replay 又要求每个有限、非 fallback 候选都具备同一决策的真值，所以旧抽样方案没有保证候选集完整。

这不推翻原报告的候选级预测误差统计，也不证明原始文件中每个决策都恰好缺少某个特定方向；它说明的是：**没有逐决策候选完整性核验，就不能把 run 17 当作 ranking-flip replay 的有效输入。** 当前版本化仓库没有该 replay 的实际汇总。代码已改为按决策组抽样、整组处理 cap，并对强制标记、资源身份、generation、目标时刻和分数加和进行 fail-closed 核验。要取得 ranking_flip_rate 与 proxy_regret，必须在新代码身份下重新运行同一诊断格并核验回执。
