# 研究线:状态时间错位

**状态:ACTIVE**  ·  起始 2026-09-23
能力契约:`ANALYSIS/T1-MEASUREMENT-PROTOCOL.md`(门禁验收标准以 `EXPERIMENTS/experiment-program.yaml` 的 `gates` 为准)

> 本文件描述**当前**主线。它会被替换、会被判死。
> 平台本体见 `../CHARTER.md`;工作方式见 `../WORKING-MODEL.md`。

---

## 问题

在真实 LEO 星座中,分布式逐跳路由决策面对状态传播、计算、生效和数据到达之间的时间错位时,**状态应该对应哪个时间尺度**,以及这种时间语义是否影响下一跳选择。

---

## 平台能力现状

`CHARTER.md` 的四条不变式在本线上的落地情况。**平台能力已齐备**——五道门于 PR #201-#208 落地 main,`gates.t1_platform_ready = completed_verified`。

| 不变式 | 平台机制 | 状态 |
|---|---|---|
| 不变式 | 平台机制 | 在正式运行路径上 |
|---|---|---|
| 可反溯 | `decision_id` 每决策唯一;重决策可区分(`_next_decision_id` `kernel.py:1497`) | ✅ 引擎内 |
| 可归因 | 十一字段链折叠(`decision_ledger.build_ledger`) | ❌ **折叠无生产入口;源工件在信任链外** |
| 可干预 | `refresh` / `frozen` 两种观测模式 | ✅ 引擎内 |
| 可干预 | 候选动作级反事实重放(`counterfactual.py`) | ❌ **harness 无生产入口** |
| 代价可量化 | F2 节点处理/调度开销与 PHY 带宽可分离 | ✅ 引擎内 |

### ⚠️ 两处「能力存在、链路不闭合」

实测:

```
decision_ledger.build_ledger    def 1 处 + 测试调用 13 处 + 生产调用 0 处
counterfactual.py               forced_actions 的唯一非测试传入者是它自己;
                                而它自己没有任何非测试调用者
```

且 `ANALYSIS/T1-MEASUREMENT-PROTOCOL.md:45` 明载:decision log 工件**不在 receipt / ledger 信任链内**。程序化核验:`RECEIPT_KEYS_V5` 与 `LEDGER_KEYS` **均不含**十一时刻字段。

**→ 正式运行里,T1 的核心证据既没有折叠入口,也无法被 `receipt verify` 校验。**

这不是疏忽:协议 §3.3-3 明确知道该工件在链外,**未被权衡的是它的后果**。

> **本表更早的版本把这四项一律标为 ✅ 已落地,是错的。** 判据只看了"能力是否存在",没看"生产路径是否可达"。同一张表此前还犯过反向的错(把已落地写成待落地)——两次都源于同一个习惯:**用存在性代替可达性**。

---

## 十一个时刻

```
t_measure          t_control_rx           t_decision_start
t_decision_commit  t_local_queue_enter    t_service_start
t_service_finish   t_peer_arrival         t_peer_redecision
t_peer_target_egress_enter                 t_peer_target_egress_service_start
```

字段清单见 `decision_ledger.TIMELINE_FIELDS`。三者相等时(`t_measure = t_decision_start = t_decision_commit`)仍分别记录、不折叠——否则计算时延落地后无法回填语义。

---

## 状态处理方式

| 处理方式 | 平台现状 | 状态 |
|---|---|---|
| 原始状态 | `observation_mode=frozen` —— 观测冻结于决策开始,提交只做合法性校验 | ✅ 已实现 |
| 补偿到当前 | `observation_mode=refresh` —— 提交时刻重新观测(默认) | ✅ 已实现 |
| 候选到达时刻 | 只有**事后真值** `truth_at_target`;作为**决策输入**不存在 | ❌ 需新增 |
| 共同未来时刻 | 把各候选对齐到同一未来时刻再比较 —— 无对应机制 | ❌ 需新增 |

`VALID_OBSERVATION_MODES = {"refresh", "frozen"}`(`leo_sim/config.py:247`)。`frozen` 要求 `compute_delay > 0`。

### 平台没有向未来投影的能力

平台现有的**全部**预测都在"决策时刻"取值:

| 对象 | `prediction_method` |
|---|---|
| `estimate_at_start` | `same_policy_on_advertised_peer_state` |
| 候选级审计预测 | `same_policy_full_cache_at_decision_time` |

**没有任何机制把状态推进到未来时刻。**

`decision_ledger.py` 对此有明确声明:`truth_at_target` 折叠自到达快照,**MISSING while the target instant has not happened** —— 它在决策时刻**结构上不可得**,只能用于事后评分,不能作为决策依据。

而"候选到达时刻"与"共同未来时刻"作为决策输入,**本质上都要求同一种前向投影**。

> **因此 RQ2 的四路对照目前只能做两路。第 3、4 项共享同一个缺失能力,不是两个独立缺口。**

### 必须先设计、不能由平台自行发明的部分

前向投影的语义决定了 RQ2/RQ3 究竟在测什么,属于**研究设计**而非实现细节:

- 用什么模型把状态推进到未来时刻
- "共同"时刻取什么(固定前瞻 Δ?各候选到达时刻的最大值?决策周期边界?)

平台侧可以做的是提供机制、把时刻作为参数。**但参数语义必须先定** —— 否则四路对照测的不是同一个东西,差异也无法归因。

平台不自行发明这套语义:猜错会直接改变实验测到的东西。

---

## 尚不可声称的

以下两条是 `gates.t1_platform_ready.known_partial_criteria` 记录的限制,**直接约束本线允许的结论**:

**1. `T1-COMPUTE-DELAY-PASS`(finding R8-A10)**
`frozen` 已存在,但:**仅 producer-verified,尚未在正式 VM 工件上运行**;v1 限制它(无 learning arm、无 forced_actions)。且 `refresh` 仍是默认,仍不能表示计算导致的陈旧。

**2. `T1-PRESSURE-WINDOW-PASS`(finding R8-A9)**
access 在约 20 Mbps 以上未解除限流,利用率平台在 **0.56** 而非趋近 1.0。**这是一个中高压力窗口,不得用于声称 ISL 饱和。**

---

## 候选问题

- **RQ1** 机制验证:时间错位是否真实存在、可观测
- **RQ2** 时间对齐:四种处理方式的受控对照
- **RQ3** 预测是否有用
- **RQ4** 部署代价

**这些是候选,不是冻结的 claim。** 判死其中一条只修改本文件。

---

## 下一步

平台能力不再是瓶颈。下一个里程碑是 `T1-MEASUREMENT-PROTOCOL.md` 所述的 **Phase 1 微机制证据**,以及在此之上的**正式因果实验授权**——在授权之前,不得产出任何 T1 因果结论。

---

## 判死的条件

出现以下任一情况,本线应转为 `RETIRED`:

- 实测表明十一个时刻的错位在任何负载下都落在测量噪声内
- 四种处理方式的输出差异无法归因到时间语义,而归于其他机制
- 部署代价与收益不成比例
