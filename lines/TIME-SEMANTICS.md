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
| 可反溯 | `decision_id` 每决策唯一;同一包的重决策可区分(`_next_decision_id` `kernel.py:1497`) | ✅ 已落地 |
| 可归因 | 十一字段链可从事后工件重建;缺项标 `MISSING` 而非填 0(`decision_ledger.build_ledger`) | ✅ 已落地 |
| 可干预 | `refresh` / `frozen` 两种观测模式;候选动作级反事实重放(`counterfactual.py`) | ✅ 已落地 |
| 代价可量化 | F2 节点处理/调度开销与 PHY 带宽可分离 | ✅ 已落地 |

**"已落地"的含义是 producer-verified。** 它说明测量与反事实能力存在,**不代表** T1 的科学问题已被回答。

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

| 处理方式 | 平台机制 | 状态 |
|---|---|---|
| 原始状态 | `observation_mode=frozen`(决策开始冻结观测,提交仅做合法性校验) | 已实现,producer-verified |
| 补偿到当前 | `observation_mode=refresh`(提交时刻重新观测;**默认**) | 已实现 |
| 候选到达时刻 | `decision_ledger.truth_at_target`(记录该包到达时目标出口的真实竞争) | 已实现(事后真值) |
| 共同未来时刻 | 未找到对应机制 | **待确认** |

`VALID_OBSERVATION_MODES = {"refresh", "frozen"}`(`leo_sim/config.py:247`)。`frozen` 要求 `compute_delay > 0`。

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
