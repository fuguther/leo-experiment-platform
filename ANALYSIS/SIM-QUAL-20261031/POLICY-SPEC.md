# POLICY-SPEC-20261031 — 四种路由策略的冻结数学定义（实现前冻结，禁止按结果调参）

> 本文件是 `policy-builder` 的实现合同。任何定义变化都必须先改本文件并记录理由；
> 实现完成后不得为实验结果改 η、α、代价项或候选集规则。
> 所有策略共享：同一物理网络、同一 trace、同一 packet identity、同一 seed、同一 topology、
> 同一 link 参数、同一 control dissemination（广告周期/大小/vis_k/TTL/优先级）。
> **唯一**变化的是"如何把同一批候选的代价排成动作"。

## 1. 决策对象与信息边界（四策略共用）

节点 `i` 要转发目的 cell `d` 的包。候选动作 = `i` 的合法有向 ISL 出口方向 `a`（对端 `ρ_a`），
候选集由现有 `routing.choose_next_hop` 产生（不修改），并按现有 loop/物理/队列合法性过滤。
打分所需的代价项来自现有 time-alignment 评分器的 `terms`（秒），每个候选 `a` 有：

- `tx_s`：本包在本地第一跳的序列化时间（本地已知速率）；
- `prop_s`：本地第一跳传播时间；
- `peer_process_s`：对端节点处理/计算时间（广告/本地已知）；
- `local_egress_wait_s`：本节点该有向出口已排队 bits / 本地速率；
- `resource_work_s = Q̂_a / rate_a`：对端出口 `ρ_a` 的在途工作量换算的等待时间，
  `Q̂_a` 只能来自**已真实到达**的广告（`state_source=advertised`），
  `rate_a` 来自同一广告；缺失时该候选记为 fallback（不发明数值）；
- `remaining_prop_s`：从 `ρ_a` 到最近的已广告服务节点的可见拓扑剩余跳数 × 已知单跳传播。

基准代价（`queue_greedy` 的 J）：

```
J_a = tx_s + prop_s + peer_process_s + local_egress_wait_s + resource_work_s + remaining_prop_s
```

## 2. Policy 0 — geometry（负对照）

```
J_a^geo = tx_s + prop_s + remaining_prop_s
```

即完全剔除 `resource_work_s` 与 `local_egress_wait_s`（一切动态队列项）。
目的：stale state 在理论上不应改变其动作。不得使用任何队列/拥塞状态，也不得使用 peer_process 状态。

## 3. Policy 1 — queue_greedy（高敏感基线）

```
a* = argmin_a J_a
```

`Q̂_a` 使用决策时刻缓存中该资源的**最近一条已到达广告**（`hold_last` 语义，不外推），
即"看到多少就用多少"的硬队列贪心。排序 tie-break 与现有评分器一致
（fallback 后排、再按 `total`、`visible_order`、方向名）。

## 4. Policy 2 — queue_greedy_hysteresis

与 Policy 1 相同的 J，但引入每个 `(卫星 i, 目的 cell d)` 的**已提交方向记忆** `L`：

- `L` 只在**真正提交 forward 动作**时更新为该动作（deliver/hold/fail 不更新；观测阶段不更新）；
- 决策时先算 `J_a`（同一时刻、同一信息），令 `a* = argmin J_a`；
- 若 `L` 仍然合法且 `J_L <= J_{a*} + η`，则**保持** `L`；否则切到 `a*`；
- `η` 单位秒，`η=0` 时退化为"只要 L 仍是最优就保持"（此时等价 greedy）；
- `η` 只允许取预注册网格 `{0.0, 0.002, 0.005, 0.010}` 中的值；本轮主实验固定
  `η = 0.005 s`，其它值只作 sensitivity，不得事后换主值。

## 5. Policy 3 — queue_greedy_ewma

与 Policy 1 相同的 J，但 `resource_work_s` 用 EWMA 平滑后的队列水平替换瞬时值：

```
q̄_0 = q_1
q̄_k = α * q_k + (1-α) * q̄_{k-1}        k = 2..n
J_a^ewma = J_a - resource_work_s + q̄_a / rate_a
```

- 序列 = 该资源**已真实到达**的广告样本，按 `measured_at` 排序、去重，只取
  `measured_at <= snapshot_at`（未来样本绝不进入）；
- `n=1` 时 `q̄ = q_1`，即退化为 greedy；
- `α` 只允许取预注册网格 `{0.1, 0.25, 0.5}`；本轮主实验固定 `α = 0.25`；
- EWMA 状态由"已到达样本序列"在决策时重新计算（纯函数、确定性），不维护隐藏运行状态。

## 6. 实现位置（最小改动）

- 新模块 `CODE/leo_sim/state_policy.py`：纯函数，输入 = 现有 `ScoredCandidates` 的逐候选
  `terms/predicted_bits/service_rate_bps/fallback`、资源历史序列、`last_direction`、参数，
  输出 = 新的候选方向顺序 + 机器可读 audit。
- `config.py`：新增 `time_alignment.state_policy`（默认 `"legacy"`，取值
  `legacy|geometry|queue_greedy|queue_greedy_hysteresis|queue_greedy_ewma`）与
  `time_alignment.hysteresis_eta_s`（默认 `0.0`）、`time_alignment.ewma_alpha`（默认 `0.25`）；
  严格 fail-loud 校验；进入 config identity。
- `kernel.py`：`_score_order` 在 `plan.scored` 之后、`order` 生成处调用
  `state_policy.rank(...)`；`_record_decision` 只在 kind=="forward" 且动作提交时更新
  `self._state_policy_last[(sat, dst)]`；`ta_audit` 增加 `state_policy` 块
  （policy 名、last_direction、per-direction J、hysteresis 是否保持、EWMA 样本数）。
- `time_alignment.py`：仅当实现 EWMA 需要复用 `order_history`/`_latest_by_resource` 时按原函数调用，
  **不修改评分器既有语义**。
- `legacy` 必须与改动前**逐字节等价**（同 config、同 seed 的 outcome/sha 不变），用测试锁定。

## 7. 必须新增的测试

1. 纯函数单测：geometry 排序忽略 queue；greedy = 现有 ranking；hysteresis 在 `η` 内保持、
   超过 `η` 切换；ewma 在 n=1 时等于 greedy、α=1 时等于瞬时、α→0 时趋近首样本。
2. 配置测试：非法值 fail-loud；默认 `legacy`；`state_policy` 改变 config identity。
3. 集成测试（用 `tests/helpers.py` 的 tiny fixture）：
   - geometry 在 queue 漂移下动作序列与"无队列"基线一致；
   - hysteresis 在两条等代价分支抖动时减少切换次数；
   - legacy 与改动前完全一致（同一 fixture 的 outcome/事件序列）。
