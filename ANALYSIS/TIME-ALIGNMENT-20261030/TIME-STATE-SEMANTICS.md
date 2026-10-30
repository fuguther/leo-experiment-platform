# 时间 / 状态资源语义表（Q_ρ 到底是什么）

> 代码身份：`instr/ledger-export-20261030 @ 7e7fd5a`（工作树 `.wt-instr-20261030`，clean）。
> 行号均为该 commit 的行号；换 commit 必须重核。
> 本文只回答"状态变量与四个时刻的物理语义是什么"，不包含任何实验结论。

## 1. 结论先行

| 问题 | 答案（有代码证据） |
|---|---|
| `Q_ρ` 是本地节点 `i→ρ` 的发送队列吗？ | **不是**。本地有向出口队列是独立的一项 `local_egress_wait_s`（`time_alignment.py:1130-1140`，`egress_bits/…/local_egress_in_service_s`）。 |
| 是候选卫星 ρ 的 aggregate queue 吗？ | **不是**。`ResourceKey` 是"命名有向出口"，`VALID_KINDS = ("isl","downlink")`（`time_alignment.py:35-37,207-234`）；ISL 资源身份 = `(satellite=ρ, direction=ρ 的下一跳方向, kind="isl", generation)`。 |
| 是 destination-specific queue 吗？ | **两者都不是纯目的队列**。ISL 出口是**fifo 有向链路队列**（`link.data_bits + link.ctrl_bits`，`kernel.py:3798`）；只有终端 downlink 资源是目的相关（`downlink_queue_scope/queue_semantics` 随广告下发，`kernel.py:5742-5760`）。 |
| 是 workload/occupancy/bucketized score 吗？ | 状态量是**bits（工作量）**：`value = queued data+ctrl bits`；另有 `work_ahead_bits_proxy = queued + 在服剩余`（`kernel.py:3784-3830`）。预测器预测的是 **该有向出口在目标时刻的 bits**（`ResourcePrediction.predicted_bits`）。 |
| predictor 预测哪一个量？ | `predicted_bits` @ `target_at`，服务换算 `resource_work_s = predicted_bits / resource_service_bps`（`time_alignment.py:1120-1140`），单位秒。 |
| 数据来源？ | 控制面广告：由**源节点自己**在该出口上测得 `queued_bits`、在服剩余、`rate_bps`、`generation`、`generated_at`，经 `advertisement_protocol_version: 2` 传播到邻居缓存（`kernel.py:3784-3830`、`_observed_cache_entries`）。 |
| 更新位置？ | 广告生成=源节点出口对象；收到=邻居 `self.caches[sat]`（含 `generated_at / received_at / hops / aoi`）；使用=`_build_ta_snapshot` → `StateSample(measured_at=generated_at, received_at=…)`（`time_alignment.py:242-280`）。 |
| 服务开始/结束？ | `link_service_windows` 事件账本逐包记录 `start/end/rate_bps/outcome`；队列账本 `queue-levels` 逐次 add/remove 记录 `(t, queue, bits, event)`。 |

## 2. 四个时刻的定义（按当前实现）

| 记号 | 语义 | 代码出处 | 备注 |
|---|---|---|---|
| `t_g` | 广告在**源节点**被测量的时刻 = `StateSample.measured_at` = 广告 `generated_at` | `time_alignment.py:261-263`；`kernel.py:5719-5723`（`measurement.generated_at`） | 每个候选资源各自一个 `t_g`，可能不同 |
| `t_r` | 本节点**收到**该广告的时刻 = `received_at`（≥ `t_g`，`__post_init__` 强制） | `time_alignment.py:262,276-279` | 乱序到达不会用旧覆盖新（`order_history`，`:605-622`） |
| `t_d` | 决策时刻：`snapshot_at`。`refresh` 模式 = commit 时刻；`frozen` 模式 = 计算开始前 | `time_alignment.py:315-330`；`decision_ledger.measure_instant`（`decision_ledger.py:167-177`） | 本诊断用默认 `refresh`，compute_delay=0 时两时刻重合 |
| `T_ρ` | **使用的物理时刻**——但不是唯一答案，见 §3 | `estimate_eta` 的 `target_at`；四臂在 `arm_target_at` 选择不同 instant（`time_alignment.py:832-852`） | `candidate` 臂在 `f666b59` 后 = 到达 + 预测排队等待 |

四臂的查询时刻（`arm_target_at`，`time_alignment.py:832-852`）：

- `stale` → `last_measured_at`（即 `t_g`，最旧一条被采纳样本的源时刻）
- `now` → `snapshot_at`（`t_d`）
- `common` → `t_d + common_horizon_s`（所有候选共用；`median_eta` 规则，`resolve_common_horizon`）
- `candidate` → `eta_target + predicted_bits/resource_rate`（`f666b59`，固定点一步）

## 3. `T_ρ` 必须由资源语义决定（本文档的关键判断）

被评分的状态量是**该有向出口前方的工作量（bits）**，它通过 `Q/rate` 换算成等待秒数进入分数。
对 FIFO 出口，决定"这个包要等多久"的，是**包到达该出口那一刻**前方已有的工作量：

```
T_arrival(ρ)  = eta_target(ρ)                      # 到达候选出口的时刻
Q_arrival(ρ)  = Q_ρ(T_arrival)                     # 此时排在前面的 bits —— 因果相关量
wait(ρ)       = Q_arrival(ρ) / rate_ρ
T_service(ρ)  = T_arrival(ρ) + wait(ρ)             # 包自己的服务开始时刻
```

因此在**当前状态变量语义（work-ahead bits）下，物理使用时刻是 `T_arrival`，不是 `T_service`**。
`f666b59` 把 candidate 臂的目标改成 `T_arrival + predicted_bits/rate`（即 `T_service`），
这带来两个必须记录、不得掩盖的后果：

1. `Q_ρ(T_service)` 的物理含义是"这个包开始发送时前面还剩多少"，按定义应接近 0；
   它不是决定等待的量，用它做 prediction 目标与"等待"语义不自洽。
2. `score_candidates` 在 `T_service` 上又加了一次 `resource_work_s = predicted_bits/rate`
   （`time_alignment.py:1120-1140`），在 candidate 臂上存在**重复计入等待**的结构性风险。

**本文档立场**：本研究的 S2（use-time truth）按资源语义取 `T_arrival`；
若论文要主张"按候选自身使用时刻对齐"，必须显式声明"使用时刻"是 arrival 还是 service-start，
并给出对应的 Q 语义。不得混用。该判断只依赖代码语义，不依赖任何实验结果。

## 4. 候选资源身份（资源 id 映射）

- 候选动作 = 本节点的一个合法方向 `d`；对端 `peer = topo[sat][d]`。
- 被预测的资源 = **对端 `peer` 自己的下一跳出口方向 `egress`**，由**同一路由策略**在
  「本节点已收到的广告」上算出（`_candidate_resource_map`，`kernel.py:5659-5810`），
  而不是直接读对端真实队列（注释明确禁止）。
- 身份：`ResourceKey(satellite=peer, direction=egress, kind="isl", generation=广告携带的 gen)`；
  对应审计字段 `candidate_resources[d].{peer,egress_direction,kind,measurement{generated_at,received_at,age_s,hops}}`。
- 队列账本 qid 形如 `isl_ctrl:isl:0:1:gen0`（`queue_ledger`），与上述 `(sat, peer, gen)` 可一一对应（待 `AUDIT-INSTRUMENTATION.md` 逐字段核验）。
- 因此 `Q_ρ` 是**两跳之外**的拥塞信息（对端的出口队列），不是本地队列，也不是对端聚合队列。

## 5. 由此产生的三个"必须分别报告"的真值

| 真值 | 定义 | 何时可用 |
|---|---|---|
| `S0 = Q_ρ(t_g)` | 广告自带的源测量值（`used_state.bits/queued_bits`） | 在线可得；真值需队列账本回放到 `t_g` |
| `S1 = Q_ρ(t_d)` | 决策时刻同一资源的真值 | 需队列账本（新） |
| `S2 = Q_ρ(T_arrival)` | 到达时刻真值 = 因果相关量 | 需队列账本 + 每候选 `eta_target` |
| `S2' = Q_ρ(T_service)` | 服务开始时刻真值（若采用 f666b59 语义） | 同上；仅当显式声明语义时使用 |
| `truth_at_target` | 用 `peer_arrival` 实际快照 + 对端真实选择的出口（`decision_ledger.py:189-235`） | 事后可得，回答"真正竞争的资源"而非"预测的资源" |

`truth_at_target` 与 `S2` **不是同一个量**：前者绑定"包真的到了对端、对端真的选了方向 e",
后者绑定"决策时预测的资源"。两者都必须报，且差异本身就是"候选资源预测是否有效"的证据。

## 6. blocker 判定

- 该状态变量**有明确物理 use-time**（对 FIFO 出口 = 到达时刻；对终端 downlink = 到达地面终端时刻，
  其 `queue_scope/queue_semantics` 随广告给出）。**不构成 blocker**。
- 但"candidate 臂当前实现按 `T_service` 查询"这一点是**语义不一致**，必须在实验设计中
  显式冻结用哪个 `T`，并不得用它去论证另一个 `T` 的结论。
