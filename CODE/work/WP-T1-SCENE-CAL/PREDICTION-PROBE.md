# 同一次运行内的预测—真值定向探针（2026-10-19）

## 为什么必须做

上一轮已确认：决策日志里的预测字段（predicted_bits、服务率、资源 ID、generation、
query_targets、eta_targets）大部分已经落盘，但真值只有**提交时刻**的
`truth_at_commit.t_observed = snapshot_at + 2 µs`。对 stale / common / candidate 三臂，
它与预测目标时刻相差 52 ms / 12.9 ms / 7.5 ms，**不能冒充未来目标时刻的真值**。
旧的离线标签构造器（`build_prediction_labels.py`）从事件日志重建积压，
能回答“积压是多少”，但无法证明预测与标签来自**同一次运行、同一资源、同一 generation、同一时刻**。

本探针补的正是这一条：**在运行中的仿真里，于预测自己的评估目标时刻，直接读取对应资源的完整工作量。**

## 入口与契约

| 项 | 实现 |
|---|---|
| 记录 | `CODE/leo_sim/target_probe.py`（新，审计专用） |
| 挂点 | `Kernel._record_decision`：动作**已提交**之后，读该决策自己的审计对象 |
| 真值读取 | `Kernel._isl_resource_state_advertisement`（**复用既有函数**，不另写一份工作量定义） |
| 启用 | 契约 `diagnostics: {"truth_probe": true, "truth_probe_max": N}`，默认关闭 |
| 输出 | `runs/<run-id>/cal/<cell>/arms/<arm>/target_probe.jsonl` + 手臂摘要里的 `target_probe` |

### 工作量定义（只定义一次）

```
queued_bits            = link.data_bits + link.ctrl_bits        （排队中的数据+控制）
in_service_remaining   = max(0, head.bits − elapsed × 该次传输开始时采样的速率)
work_ahead_bits        = queued_bits + in_service_remaining
```

三个分量**分别落盘**，消费方可自行复算，且**在服头部只出现在 in_service_remaining 里**，
不会被 queued_bits 重复计一次。`source = kernel_live_resource_at_target`。

### 资源身份

预测针对的是**候选交出去的下一跳资源**，即 `used_state[d].resource_id = isl:<peer>:<peer_egress>`，
**不是**本星本方向的链路。探针按这个 ID 在 `isls[<peer>]` 中找到指向 `<peer_egress>` 的链路，
并要求 `link.gen == 预测时记录的 generation`。不匹配就记原因，**不替换成后继 generation**。

### 同时间戳顺序

单个长驻采样进程维护**按目标时刻升序**的队列；在 `target` 时刻由 `env.timeout` 唤醒，
读取此刻资源状态。SimPy 对同一时刻的事件按调度顺序处理，因此读到的是
“该时刻、在采样器被唤醒之前已调度的全部事件之后”的状态；这一约定写进 summary 的
`same_instant_convention`。采样器**只读**：不请求/释放资源、不占用链路或算力、
不消费任何分布随机数（`env.timeout` 不是随机源）。

### 缺失与拒绝（都记录，不静默）

`no_eval_target`、`no_prediction`、`no_resource_identity`、`unparseable_resource_id`、
`resource_missing`、`prediction_generation_unknown`、`generation_changed`（附目标时刻的真实 generation）、
`link_retired`、`beyond_horizon`、`overflow`（超过 `truth_probe_max`）。

### 抽样与强制样本分离

探针本身是**全量**记录（每条决策的每个候选）。离线评分脚本 `probe_labels.py`
用固定步长抽出**统计样本**，并把带 `forced` 标记的**调试样本**单独计数，两者不混在一起。

## 本轮在本地暴露并修掉的两个真错误（保留记录）

1. **按本星方向找资源 = 找错链路。** 第一版用 `isls[本星][候选方向]` 定位真值，
   本地 6313 条里有 **5979 条**解析到 `isl:74:82`，而预测说的是 `isl:82:90`——
   即**预测针对下一跳的对端出口**。改为按 resource_id 定位并校验 generation 后，
   同一运行的中位误差量级从“几乎全 0”变成真实量级（见下）。
2. **队列未按目标时刻排序 = 采样偏晚。** 第一版只在启动时排序，新插入的行可能把更早的目标排到后面，
   采样器睡过头。实测 **2508 / 6313** 条记录晚读 8 µs–10.9 ms。
   改为插入即有序（bisect）后，`sampled_instant_mismatch = 0`。
   副作用：修好之后选中候选的平均绝对误差从 27 bit 变成 **602.7 bit**——
   说明**当初的“小误差”是读错时刻造成的假象**。

## 本地冒烟（非 VM 证据，仅接线核验）

契约 `out/smoke/contract.json`（300 包、8 Mbps、3 s 仿真）：

| 指标 | 值 |
|---|---:|
| 探针记录行 | 6 313 |
| 有标签 | 6 211（**98.38%**） |
| 缺失原因 | 仅 `no_prediction` 102 条 |
| sampled_at 与 eval_target 不一致 | **0** |
| 资源身份不一致 | **0** |
| generation 不一致 | **0** |
| 决策日志 eta 与探针 eval_target 不一致 | **0** |
| predicted/rate 与评分实际用的 resource_work_s 不一致 | **0 / 6 211** |
| hold_last 预测非零比例 | **0.00%** |
| 目标时刻标签非零比例 | **3.03%**（188 / 6 211） |
| 带符号误差均值 | **−220.9 bit**（系统性低估） |
| 选中候选 \|误差\| 均值 | **602.7 bit**；未选中候选 3.8 bit |

→ 与 VM 上 `PREDICTION-LABELS.md` 记录的“hold-last 系统性漏判积压”方向一致，
但这次是**同一次运行的 in-run 真值**，不是离线重建。

## 尚未证明的部分

- VM 正式探针尚未跑（本轮随后发射）。
- `link_service_windows` 不含 generation，故离线重建仍无法核验“同一资源”——这正是 in-run 探针存在的理由；
  两条路径的一致性比较放在正式探针之后。
- 探针写入是每臂一个 JSONL；大格（10 万包 × 4 方向）需要 `truth_probe_max` 上限保护，本轮取 40 000。
## 第一次 VM 探针（run `scpt-sc-probe-truth-s111-20261019-16`）暴露的抽样偏差

- 该 run 于 2026-10-19 18:32 发射、18:47 完成（rc=0，890 s）。探针写盘 **40 000 条**，
  但 `dropped_overflow = 151 023`——整格候选行是 **191 023**。
- 上限按入队顺序截断，因此这 40 000 条**只覆盖 2.110–2.165 s（约 55 ms）**，
  是**按时间的系统性偏样本**，不能代表整窗。
- 处置：探针增加**确定性 1/stride 抽样**（单调计数取模，不消费业务随机数），
  上限只作硬停止，summary 同时记录 `stride`、`skipped_by_stride`、`sampling_rule`。
  该 run 的探针数据**标记为时间偏样本，不作为结论证据**；正式证据用新 run-id 重跑。

## 第二次 VM 探针（重跑，stride=4）

见 `PROBE-RESULTS-20261019.md`（跑完后填）。


## 2026-10-29：truth-score replay 的抽样单位审查

旧版 TargetProbe 在候选行循环内部执行 stride，因此 stride=4 是“每四条候选记录取一条”，不是“每四个决策保留一个完整候选集”。每次决策会生成多条方向记录，而排名翻转需要同一决策下全部有限、非 fallback 候选的目标时刻真值。候选级抽样不能保证这一点；缺失候选必须判为 non-comparable，不能用剩下的一条或两条方向计算排名。

run 17 的预测误差统计仍可按其已报告的候选样本口径使用，但当前仓库没有该 run 的逐决策候选覆盖审计，也没有 truth-score replay 汇总，因此不得将其描述成已完成 ranking replay。新版已把 stride 改为按 (pid, decision_id) 决策组抽样，并让上限整组接收或整组丢弃；同时新增强制动作标记和资源、generation、目标时刻、采样时刻、服务率与分项加和检查。旧 run 使用旧代码身份，不能追溯性地变成新版抽样；需要用新代码身份重跑诊断格。
