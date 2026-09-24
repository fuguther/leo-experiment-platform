# 研究线:状态时间错位

**状态:ACTIVE**  ·  起始 2026-09-23  ·  来源 `T1-MEASUREMENT-PROTOCOL.md`

> 本文件描述**当前**主线。它会被替换、会被判死。
> 平台本体见 `../CHARTER.md`;工作方式见 `../WORKING-MODEL.md`。

---

## 问题

在真实 LEO 星座中,分布式逐跳路由决策面对状态传播、计算、生效和数据到达之间的时间错位时,**状态应该对应哪个时间尺度**,以及这种时间语义是否影响下一跳选择。

---

## 本线要求平台提供的能力

`CHARTER.md` 的五条不变式在这条线上的具体落地:

| 不变式 | 本线的具体能力 | 状态 |
|---|---|---|
| 可反溯 | 每次决策有唯一 `decision_id`;同一包的重决策可区分 | 待落地 |
| 可干预 | 四种状态处理方式可在同一 trace 上对照 | 已实现 2 / 4 |
| 可归因 | 十一个时刻可从事后工件重建 | 待落地 |
| 代价可量化 | 节点处理/调度开销与 PHY 带宽可分离 | 已有 |

---

## 十一个时刻

```
t_measure                          t_control_rx
t_decision_start                   t_decision_commit
t_local_queue_enter                t_service_start
t_service_finish                   t_peer_arrival
t_peer_redecision                  t_peer_target_egress_enter
t_peer_target_egress_service_start
```

三者相等时(`t_measure = t_decision_start = t_decision_commit`)也**必须分别记录**,不得折叠。否则计算时延落地后无法回填语义。

---

## 四种状态处理方式

| 方式 | 实现 |
|---|---|
| 原始状态 | — |
| 补偿到当前 | `refresh`(已实现) |
| 计算期间冻结 | `frozen`(已实现) |
| 共同未来时刻 / 候选到达时刻 | 待补能力 |

`VALID_OBSERVATION_MODES = {"refresh", "frozen"}`(`leo_sim/config.py:247`)

---

## 候选问题

- **RQ1** 机制验证:时间错位是否真实存在、可观测
- **RQ2** 时间对齐:四种处理方式的受控对照
- **RQ3** 预测是否有用
- **RQ4** 部署代价

**这些是候选,不是冻结的 claim。** 判死其中一条只修改本文件。

---

## 判死的条件

出现以下任一情况,本线应转为 `RETIRED`:

- 实测表明十一个时刻的错位在任何负载下都落在测量噪声内
- 四种处理方式的输出差异无法归因到时间语义,而归于其他机制
- 部署代价与收益不成比例
