# 四臂时间对齐 / 预测器 / 候选 ETA / 评分器 —— 代码就绪审计（2026-10-31 夜）

- 审计对象：`/Users/lge/Desktop/topic/.wt-night-20261031`，分支 `night/time-exp-20261031`，HEAD `63664f3adddedfb9e316397af29b16f9aab73a41`，审计开始时工作树干净（`git status --porcelain` 为空）。
- **并发写入说明（审计结束时实测，FACT）**：本 worktree 是多人共享写区。审计开始后出现了**不是我做的**改动：`CODE/experiment_platform/scene_trace_program.py` 变为 `M`（+71/−2，新增可选全局 `pulse` 处理，属另一位成员的未提交工作），并出现 3 个其他成员的未跟踪备忘录（`CODE-READINESS-PROBE-20261031.md`、`PILOT-SCENARIO-20261031.md`、`RUNBOOK-20261031.md`）。**HEAD 仍是 `63664f3` 未移动**；我本人只创建了本文件（`?? .../CODE-READINESS-ROUTER-20261031.md`），未编辑任何源码、未回退他人改动。§1–§6 的所有 file:line 结论针对 `63664f3` 的源码（`time_alignment.py`/`kernel.py`/`config.py`/`control.py` 均未出现在 `git diff` 中）；`scene_trace_program.py` 的未提交改动不在本审计的 6 项检查范围内。
- 审计方式：只读代码阅读 + 本机 pytest。**本机未跑 VM、未跑正式实验、未修改任何源码**；唯一写入是本文件。
- 证据标签：**[FACT]** = 本 HEAD 代码/测试直接可验证；**[INFER]** = 由代码结构推断、未由运行证明。
- 字段/行号均指本 HEAD，不引用旧文档（旧文档多处已过期，见 §9）。

---

## 0. 结论速览（详细证据见后文）

| # | 检查项 | 判定 | 一句话 |
|---|---|---|---|
| 1 | 四臂 stale/now/common/candidate，只有查询时刻不同；无 target_at 覆盖绕过；candidate 目标语义 | **PASS-WITH-CAVEAT** | 不变式在 `per_packet` 在线路径成立（arm 只进 8 处、其中 5 处是取时刻）；但 `NO_STRONG_COMMON` 是 common 专属回退，且 **candidate 默认仍是 legacy `arrival_plus_queue`（T_service），不是 T_arrival**，async_* 模式会整体绕过 arm 时刻 |
| 2 | bounded_linear：同资源近期接收历史、相邻斜率、中位数、外推到目标、hold_last 回退、无未来信息 | **PASS-WITH-CAVEAT** | 实现与描述一致；但 `history_limit` 默认/设计值均为 **8 不是 4**（全仓无 4），且中位数/切片/偶数取平均**无单测** |
| 3 | candidate ETA 组成 + 时间线记录（t_measure/t_receive/t_decision/candidate_target_hat 等） | **PASS-WITH-CAVEAT** | 8 个 ETA 项逐一存在且顺序/不重叠；`t_measure/t_receive/t_decision` 可恢复，`query_targets` 即 "candidate_target_hat"（无同名字段）；`t_target_resource_use` **没有**直接记录，只能由后继 decision 的 `t_local_queue_enter` 近似 |
| 4 | 评分器加性项集合、剩余传播（绕行）、queue_weight、边界 | **PASS-WITH-CAVEAT** | 用户要求的 8 类项全部在（ISL 目标另加 `resource_packet_tx_s`，下行用 `terminal_tx_s`）；`remaining_prop_s` 是"剩余跳数 × 单跳传播"的拓扑先验，**不是**逐跳传播求和；`resource_packet_tx_s` 未登记进 `TERM_KEYS`/docstring |
| 5 | config legality/determinism：arm/predictor/history_limit/advertise_interval_s/compute server/protocol version/vis_k | **PASS-WITH-CAVEAT** | 已强制：`time_alignment` 需要 `compute_servers_per_satellite <= 1`、async 模式与 async_routing 互锁、fixed_horizon 互锁；**未强制**：`advertisement_protocol_version=2`、`control_plane.enabled`、`execution_mode=per_packet`；同 trace/同 seed/仅 arm 差异有真实校验（`_arm_row` + acceptance） |
| 6 | 指定测试实跑 | **PASS-WITH-CAVEAT** | 5 个指定文件全绿（84 + 2 + 65 通过/1 skipped 等，详见 §7）；但 `test_target_probe.py` 在本 HEAD **2 failed**（stub 缺 `arrival_rate_bps`），且若干用户要求的不变式无对应测试 |

**给 pilot 的阻断项（VM 前必须处理）见 §8。最关键的 1 条：代码默认根本不按 `T_arrival` 查询 candidate，必须先改配置（或 `study_design`）把 `candidate_target_rule: arrival` 落下去，否则 pilot 测的是 `T_service` 且目标队列可能被重复计价。**

---

## 1. 四臂：定义、调用链、查询时刻解析、目标时刻覆盖（历史 bug）

### 1.1 四臂定义与"唯一差异"声明

[FACT] `CODE/leo_sim/time_alignment.py:44`

```python
ARMS = ("stale", "now", "common", "candidate")
```

[FACT] 模块 docstring 明确声明（`time_alignment.py:16-24`）：

```
The four online arms differ ONLY in the instant each arm queries:
  stale      the last received measurement, used as-is
  now        projected to snapshot_at (t0)
  common     projected to a horizon shared by every candidate (t0 + h)
  candidate  projected to that candidate own predicted resource-use instant
Everything else (candidate set, resource mapping, scorer, legality, fallback)
is identical across arms.
```

### 1.2 调用链（kernel → 纯函数核心）

[FACT] 在线打分路径唯一：

| 步骤 | 位置 |
|---|---|
| 决策入口 | `kernel.py:5265 _time_aligned_order` → `kernel.py:5283`（async 分流）/`5285`（per_flow）/`5316`（precomputed）/`5337 _score_order` |
| 读 arm | `kernel.py:5377 arm = str(self.cfg_ta["arm"])` |
| 建快照（arm 只在 `make_snapshot(arm=arm)` 出现） | `kernel.py:4958 _build_ta_snapshot`，调用点 `5219-5259`；arm 传入 `5237` |
| 决策 | `kernel.py:5413 plan = _ta.plan_decision(probe)` —— **无 target_at 参数** |
| 纯核心 | `time_alignment.py:1419 plan_decision` → `1335 build_predictions` → `844 arm_target_at` + `886 estimate_eta` + `646 predict_resource` → `1071 score_candidates` |

[FACT] `_score_order` 里 arm 只被读一次用于建快照（`kernel.py:5377`），没有任何按 arm 变化的候选集/映射/合法性分支。`time_alignment.py` 中 `arm` 的命中为 10 行 / 8 个逻辑分支（`grep -n "\.arm\b\|arm ==\|arm !="`；其中 `arm_target_at` 内 4 行）：

```
438  describe() 输出
847/851/853/857  arm_target_at：四个 arm 各返回一个时刻（唯一 arm 相关输入）
1091 snapshot.arm == "common"      # NO_STRONG_COMMON：common 专属回退
1364 snapshot.arm == "common"      # common 无 horizon 时的占位 target
1376 arm_target_at 调用
1389 allow_past=(snapshot.arm == "stale")   # 只允许 stale 目标时刻早于 t0
1396 snapshot.arm == "candidate"   # legacy 队列一步（可被 rule 关闭）
```

[FACT] `arm_target_at`（`time_alignment.py:844-861`）就是"唯一 arm 相关输入"：

```python
def arm_target_at(arm, *, snapshot_at, last_measured_at,
                  common_horizon_s, candidate_target_at) -> float:
    """The instant each arm queries.  This is the ONLY arm-dependent input."""
    if arm == "stale":
        if last_measured_at is None:
            raise TimeAlignmentError("stale arm requires at least one received sample")
        return last_measured_at
    if arm == "now":
        return snapshot_at
    if arm == "common":
        if common_horizon_s is None:
            raise TimeAlignmentError("common arm requires a common_horizon_s")
        return snapshot_at + common_horizon_s
    if arm == "candidate":
        if candidate_target_at < snapshot_at:
            raise TimeAlignmentError("candidate target is before the snapshot")
        return candidate_target_at
    raise TimeAlignmentError(f"unknown arm {arm!r}")
```

四个 arm 的 target 解析：
- **stale**：`last_measured_at` = 该候选资源在本节点"已收到"样本中的最大 `measured_at`（`time_alignment.py:1446-1453`，且 `received_at <= snapshot_at`），并配 `allow_past=True`（`:1389`、`predict_resource` 的 `:668-670`）。
- **now**：`snapshot_at`。
- **common**：`snapshot_at + common_horizon_s`；horizon 由 `resolve_common_horizon`（`:1309-1332`）从"同一估计器算出的各候选 ETA 偏移"取中位数/均值，`len(offsets) < 2` 时返回 `None`（→ `NO_STRONG_COMMON`）；或 config `fixed_horizon`。
- **candidate**：`eta.target_at`（`eta = snapshot_at + sum(8 个 ETA 项)`，见 §3），**默认再加一步** `+ predicted_bits / resource_service_rate`（legacy）。

### 1.3 candidate ARRIVAL 规则（commit 171c8ef）与"到底查 T_arrival 还是 T_service"

[FACT] 规则字段：`time_alignment.py:379-383`（`ObservationSnapshot.candidate_target_rule`，默认 `"arrival_plus_queue"`）、`:520`（`make_snapshot` 默认同）、`:564-567`（非法值 fail loud）；`config.py:299`（schema）、`:1284-1288`（校验，缺省按 legacy）；`kernel.py:5254-5258` 传入：

```python
candidate_target_rule=str(
    self.cfg_ta.get("candidate_target_rule")
    or "arrival_plus_queue"),
```

[FACT] 实际生效逻辑 `time_alignment.py:1396-1413`：

```python
prediction = predict_at(target)
if (snapshot.arm == "candidate" and explicit_target is None
        and last_measured is not None and resource is not None
        and snapshot.candidate_target_rule != "arrival"):
    # P1 (LEGACY, "arrival_plus_queue"): the CANDIDATE arm promises the
    # instant the packet actually USES the resource, i.e. arrival PLUS
    # the queueing it will find there.  estimate_eta deliberately
    # excludes future arrivals, so the wait is taken from the prediction
    # itself (one fixed-point step) instead of being silently dropped.
    # The explicit rule "arrival" skips this step: the query instant is
    # the ETA and the wait is whatever the prediction AT THAT INSTANT
    # reports.  Other arms keep their documented instants untouched.
    ...
    if (... rate > 0 ... bits > 0 ...):
        target = target + float(bits) / float(rate)
        prediction = predict_at(target)
```

- `candidate_target_rule == "arrival"` → candidate 查询时刻 **严格等于** `eta.target_at`（T_arrival，到达候选 peer 出口资源、尚未入队）。
- 缺省/`"arrival_plus_queue"` → 查询时刻 = `T_arrival + predicted_bits/rate`，即"该包开始被服务"的近似（T_service）。
- [FACT] commit `171c8ef` 是"新增可选 arrival 规则"，**没有把默认改掉**：`git show 171c8ef -- CODE/leo_sim/time_alignment.py` 只加了 rule 分支；commit message 明说 "absent keeps every pre-existing profile byte-identical；'arrival' is the contract's arrival-before-enqueue semantic"。
- [FACT] 全仓只有 `CODE/experiment_platform/ta_controlled.py:277` 一处**生产侧**设置 `candidate_target_rule="arrival"`；**所有 profile、`t1_suite` 的 `DEFAULT_EXPERIMENT_PARAMETERS`、冻结 study 契约都没有设置它**（grep 结果：仅 config/kernel/time_alignment/测试/ta_controlled）。
- [FACT] 冻结 study `CODE/work/WP-T1-COMPLETE/time_alignment_study.yaml:67` 声明 `candidate: frozen_decision_time_plus_candidate_eta`（即 T_arrival），但 `CODE/experiment_platform/study_design.py:61-91` 编译时只设 `enabled/predictor/advertise_interval_s/arm`，**没有设 `candidate_target_rule`** → 编译出的 16 个 cell 会跑 legacy T_service，与契约声明不一致。

[FACT] 因此"candidate 现在以 T_arrival 为目标"**在本 HEAD 的默认/冻结配置下为假**；只有显式配置才为真。这不是文档能证明的，必须看配置；而所有现存配置都没开。**这是 pilot 前第一阻断项（B1，§8）。**

### 1.4 显式 target_at 覆盖：在线路径不带，但 async 模式会整体绕过

[FACT] `build_predictions` 确实有显式旁路（`time_alignment.py:1335-1363`）：

```python
explicit_target = (None if target_at is None
                   else _finite("target_at", target_at))
if (explicit_target is not None
        and explicit_target < snapshot.snapshot_at):
    raise TimeAlignmentError(
        "explicit target_at may not precede the frozen snapshot")
...
    if explicit_target is not None:
        # Async window bins and offline fixed-instant comparisons ask the
        # SAME causal estimator about an explicit absolute instant.  The
        # normal online four-arm path passes no override and therefore
        # keeps its arm-specific target rule unchanged.
        target = explicit_target
```

[FACT] 在线四臂路径 `kernel.py:5413` 调 `_ta.plan_decision(probe)`，**没有**传 `target_at`（唯一调用点）。生产代码中带显式时刻的调用只有 `time_alignment.py:1479`（`build_schedule` 的每个 bin：`score_snapshot_at(snapshot, midpoint)`）。`score_snapshot_at`/`build_schedule` 的 docstring 也写明该入口供离线诊断与异步 bin 使用（`:1434-1443`、`:1456-1465`）。
- [FACT] 因此 `per_packet`/`per_flow`（miss 时）在线路径不会绕过 arm 自己的时刻；`per_flow` 命中缓存时不重算（`kernel.py:5285-5315`），也不涉及覆盖。
- [FACT] **`async_point`/`async_window` 会让四臂完全同值**：`_async_order` 只查表（`kernel.py:4842-4877`），表由 `AsyncScheduleManager` builder = `ta.build_schedule`（`async_routing.py:91-95`）在显式中点构建（`time_alignment.py:1479`），而 `build_predictions` 里 `explicit_target` 优先于 arm 时刻（`:1358-1363`），且 candidate 的 legacy 一步因 `explicit_target is None` 条件被跳过（`:1396`）。→ 四个 arm 的 prediction/score 全部相同。config 只要求 async 模式与 `async_routing.enabled` 互锁（`config.py:1376-1384`），**不禁止与四臂对照同用**。

[FACT] 历史 bug 的官方记录在离线对照器：`CODE/experiment_platform/time_alignment_compare.py:1246-1253`

```python
# NO explicit instant.  score_snapshot_at(snapshot, instant) scores
# at that ONE instant and overrides the arm instant, which made all
# four online arms numerically identical no matter what the history
# sloped like -- measured: h1_visible had median slopes +0.6356 and
# -0.982 Mbit/s and still produced identical predictions for every
# arm.  The arm's own query instant is the ONLY arm-dependent input
# this comparison is supposed to exercise, so it must not be
# overridden here.
scored = ta.score_snapshot_at(snap)
```

并有回归测试钉住：`CODE/experiment_platform/tests/test_time_alignment_compare.py:196-218 test_the_query_instant_is_the_only_arm_difference`（断言 stale→W、now/common/candidate→E，并注释"Passing an override here made all four arms identical by construction"）。

### 1.5 候选集 / 资源映射 / 合法性 / 物理模型 是否逐臂相同

[FACT]
- **候选集**：来自路由，早于 arm；零速率裁剪 `kernel.py:5386-5387 _split_zero_rate_directions`；未解析候选仍保留在 legal（`kernel.py:5008-5014`）。arm 不参与。
- **资源映射**：`kernel.py:4991-4992` 调 `_candidate_resource_map(pkt, sat, now, cands, force=True, dst=...)`，无 arm 参数；目标 = 候选 peer 的具名有向出口 `ResourceKey(peer, egress_direction, "isl", generation)`（`kernel.py:5023-5025`），下行目标 = `ResourceKey(peer, dst, "downlink")`（`5016-5017`）。`_candidate_resource_map` 本身（`5664-5874`）只读收到的广播与可见拓扑。
- **合法性**：合法集由路由 `choose_next_hop` 决定（`kernel.py:5773-5797`），打分只重排、不新增/删除合法方向（`kernel.py:5426-5427`：`order = [d for d in scored.ranking if d in scored_cands]` + 其余保留）。
- **物理模型**：四臂共用同一 kernel/同一 config（仅 arm 不同，`t1_tasks.py:1467-1474`），物理参数（queue/rate/propagation/GE/DRR）不读 arm；`_build_ta_snapshot` 中 arm 只出现在 `make_snapshot(arm=arm)`（`kernel.py:5237`）。
- **回退**：排序键 `(fallback, total, visible_order, direction)`（`time_alignment.py:1208-1220`），`visible_order` 来自 `snapshot.legal_directions`（同一候选顺序）→ 回退顺序逐臂相同。

[FACT] 唯一 arm 相关回退：`NO_STRONG_COMMON`（`time_alignment.py:1090-1093` + `1110-1114`）——common arm 且 `common_rule != fixed_horizon` 且 `common_horizon_s is None` 时，该 arm 的全部候选被标 `fallback=True, total=inf, missing=("NO_STRONG_COMMON",)`，其他 arm 正常打分。所以"fallback 跨臂完全相同"严格说**不成立**（仅此一种情况），但它是显式声明的"无可比公共时刻"保护，不是泄漏。
- [INFER] 该分支不改变候选集/合法性/物理模型，只改变 common arm 的排序退化为可见顺序；pilot 只要保证 `common_rule` 有 ≥2 个完整 ETA 候选（`_common_comparable_offsets`，`:1296-1306`），就不会触发。

### 1.6 判定（检查 1）

**PASS-WITH-CAVEAT。**
- 通过点（[FACT]）：在线路径 `plan_decision` 无显式 target；arm 只进 8 处且仅"取时刻"与一个显式保护分支；候选集/映射/评分器/合法性/物理模型逐臂相同（代码结构 + `test_all_four_arms_see_the_same_t0_input`）。
- 保留点：
  1. **candidate 默认 = legacy T_service 而不是 T_arrival**，且冻结 study 契约声明与编译结果不一致（阻断项 B1）。
  2. async_* 模式绕过 arm 时刻 → 四臂同值（不得用于 pilot）。
  3. `NO_STRONG_COMMON` 是 common 专属回退。
  4. `allow_past` 只对 stale 打开（必要，但确实是 arm 相关的 `predict_resource` 参数）。

---

## 2. bounded_linear 预测器

### 2.1 输入 = 同一资源的"已收到"历史

[FACT] 在线快照里的历史来自"本节点已收到的、来自该候选 peer 的广播"，逐资源方向+generation 过滤：`kernel.py:5081-5119`：

```python
for rec in self._advertisement_history(sat, int(cr["peer"]), now, force=True):
    ...
    generation = (rec.get("advertised_isl_generation") or {}).get(resource_direction)
    if generation != key.generation:
        continue
    advertised_bits = (rec.get("advertised_isl_work_ahead_bits_proxy") or {}).get(resource_direction)
    ...
    history.append(_ta.StateSample(
        key, float(rec["generated_at"]), float(rec["received_at"]),
        float(advertised_bits), sample_rate, sample_arrival))
```

- [FACT] 样本值 `queue_bits` 字段实际承载 **work-ahead 代理**（已排队 bits + 在服务包剩余 bits，`kernel.py:3826-3838`），不是裸队列；下行用 `downlink_resources[...].queue_bits`（`kernel.py:5083-5090`）。
- [FACT] `_advertisement_history` 只保留 `received_at <= now`（`kernel.py:4570`），按到达顺序；`StateSample` 构造强制 `received_at >= measured_at`（`time_alignment.py:274-276`）。
- [FACT] 纯核心再次净化：`order_history`（`time_alignment.py:617-634`）丢弃 `received_at > snapshot_at`，按资源+源时刻去重（保留最新到达），按 `(resource, measured_at)` 升序排序。

### 2.2 切片、斜率、中位数、外推、回退（逐行）

[FACT] 切片（`time_alignment.py:693-700`）：

```python
grouped = _latest_by_resource(ordered)
if len(grouped) > 1 and resource is None:
    raise TimeAlignmentError(...)
series = next(iter(grouped.values()))[-history_limit:]
last = series[-1]
```

[FACT] 斜率/中位数/外推（`time_alignment.py:814-834`，`else` 分支即 `bounded_linear`）：

```python
else:
    if len(series) < 2:
        value = last.queue_bits
        method = "hold_last"
    else:
        diffs = []
        for a, b in zip(series, series[1:]):
            dt = b.measured_at - a.measured_at
            if dt <= 0:
                raise TimeAlignmentError(
                    "non-increasing source time in history: "
                    f"{a.measured_at} -> {b.measured_at}")
            diffs.append((b.queue_bits - a.queue_bits) / dt)
        diffs.sort()
        mid = len(diffs) // 2
        slope = (diffs[mid] if len(diffs) % 2
                 else 0.5 * (diffs[mid - 1] + diffs[mid]))
        value = last.queue_bits + slope * (target - last.measured_at)
    value = max(0.0, value)
    if max_queue_bits is not None:
        value = min(value, max_queue_bits)
```

- 相邻斜率：`(W_k - W_{k-1}) / (t_k - t_{k-1})`；非正时间间隔 fail loud（不静默）。
- 中位数：奇数取中间，偶数取中间两个的平均；斜率排序后取。
- 外推锚点是**最后一个样本** `last.queue_bits + slope*(target - last.measured_at)`，不是均值。
- 下界 0；可选上界 `max_resource_queue_bits`（快照字段，kernel 传 `None` —— `make_snapshot` 默认，`kernel.py:5219-5259` 未传）。
- 回退：`len(series) < 2 → hold_last`（method 改成 `"hold_last"`，但 **missing_reason 仍为空**，即它被视为合法预测而非缺失）；0 样本 → `missing_reason="no_received_history"`（`:689-692`）；最近样本 `rate_bps <= 0` → `missing_reason="non_positive_rate"`（`:701-703`），不是零填充。

[FACT] 无未来信息：`order_history` 丢未来到达（`:627-628`）；`_last_measured_at` 同样过滤（`:1446-1453`）；`target` 可以大于 `snapshot_at`（外推）但数据只来自 t0 前到达者。`predict_resource` 的 `allow_past` 只影响"target 早于 snapshot 是否报错"（`:660-670`）。

### 2.3 history_limit 的默认值与配置位置 —— **不是 4**

[FACT]
- `config.py:593` `DEFAULTS["time_alignment"]["history_limit"] = 8`
- `time_alignment.py:507` `make_snapshot(..., history_limit: int = 8, ...)`
- `time_alignment.py:647` `predict_resource(..., history_limit: int = 8, ...)`
- `kernel.py:5238` `history_limit=int(self.cfg_ta["history_limit"])`
- 冻结 study：`CODE/work/WP-T1-COMPLETE/time_alignment_study.yaml:10` `time_alignment.history_limit: 8`；`information.prediction` 写明 "latest_8_distinct_source_times"
- `config.py:1276-1277` 校验 `>= 1`；`make_snapshot` 同（`:538-539`）。
- 仓库内 `history_limit: 4` / `=4` **不存在**（grep 全仓为 0）；所有 profile 是 8。旧 `AUDIT-SCENARIO.md:137` 也按 8 推算样本数。

→ 任务书里的 "history_limit=4" 与代码/设计均不符；**实际 8**。若 pilot 预注册写 4，必须改配置（否则与预注册不符），但代码支持任意 ≥1 的切片。

[FACT] 另有 `control.py:62 HISTORY_LIMIT = 64` —— 那是**广播到达缓存**（每 origin 保留多少条已到达广告，`LocalCache`，`control.py:59-90`），不是 `bounded_linear` 的窗口，不进入路由决策（注释明说 output-only）。

### 2.4 判定（检查 2）

**PASS-WITH-CAVEAT。**
- 通过点（[FACT]）：输入 = 同资源、已收到、按源时刻排序的历史；相邻斜率 → 排序 → 奇数中位/偶数两中位均值；锚定最后样本外推；`<2` 样本 hold_last；0 样本/非正速率显式 missing；无未来信息；`history_limit` 可配且默认 8。
- 保留点：`history_limit=4` 前提为假（应改口径为 8 或显式配置 4）；中位数/偶数规则/切片边界**无单测**（现有测试只覆盖 2 样本斜率、常量、1 样本回退，见 §7）。

---

## 3. candidate ETA 组成与时间线记录

### 3.1 `estimate_eta` 的全部项（顺序 = 代码顺序，语义上互不重叠）

[FACT] `time_alignment.py:886-967`，ETA = `snapshot_at + sum(terms)`；各项按代码顺序：

| # | term | 来源（快照字段 / 计算） | 是否区间不重叠的理由 |
|---|---|---|---|
| 1 | `compute_wait_s` | `snapshot.compute_wait_s`（kernel 的 compute 池 work-ahead 估计） | 起始区间 [t0, t0+wait] |
| 2 | `compute_service_s` | `snapshot.compute_service_s` | 紧接 1 |
| 3 | `query_wait_s` | `snapshot.query_wait_s`（kernel `_query_state_at(sat, now, compute_elapsed)` 投影，`kernel.py:5153/5234`） | 在 1+2 之后提交的 query 等待 |
| 4 | `query_service_s` | `snapshot.query_service_s` | 紧接 3 |
| 5 | `local_egress_wait_s` | `max(0, egress/rate + in_service - pre_egress_elapsed)`，其中 `pre_egress_elapsed = compute_wait+compute_service+query_wait+query_service`（`:910-913`、`:942-944`） | 已扣除 1-4 期间链路继续服务的时间，故不与 1-4 重叠 |
| 6 | `tx_s` | `pkt_bits / rate`（本地首跳速率；`rate is None` → unknown 不是 0） | 紧接 5，本包自己的串行化 |
| 7 | `prop_s` | `snapshot.link_propagation_s[d]`（本地首跳传播） | 紧接 6 |
| 8 | `peer_process_s` | `snapshot.peer_process_s[d]`，由 **已收到广播的 processing_resources** 投影到该包预计 peer 到达时刻（`time_alignment.py:970-1031`，`kernel.py:5159-5194`） | 到达 peer 后的 peer 计算/查询；在目标资源入队前 |

[FACT] 缺项处理（`:917-963`）：`rate is None` → `tx_s`/`local_egress_wait_s` 记 0 但 `known=False`＋`unknown_terms`；`egress is None` 或 `in_service is None` → `local_egress_wait_s` unknown；`prop`/`peer` 缺 → unknown。`method = default_peer_process if "peer_process_s" in unknown else exact_terms`（`:965`）。**未知不进总和（保持 0 但显式 unknown），由评分器转成 missing→fallback，而不是当 0 用**。

[FACT] `peer_process_s` 的投影本身（`project_advertised_peer_processing`，`:970-1031`）：从广告记录读 `advertised_processing.compute/query` 的 `work_ahead_s`、`service_s`、`servers`；`since_measurement = peer_arrival - generated_at`；`compute_wait = max(0, compute_work - since_measurement)`（0 服务器则 0）；`compute_done_elapsed = since + compute_wait + compute_service`；`query_wait = max(0, query_work - compute_done_elapsed)`；返回四者之和。`exec_mode in ("precomputed","async_point","async_window")` → compute_service=0；`exec_mode == "per_flow"` → **返回 None**（缓存存在性不在广告里）→ per_flow 下 `peer_process_s` 永远 unknown → **所有候选 fallback**（`time_alignment.py:1017-1021`）。[INFER] 这是"per_flow + time_alignment = 机制静默失效"的第二个陷阱（与多 server 同类）。

### 3.2 评分器复用的是同一批 ETA 项（不重复计价本地队列）

[FACT] `score_candidates` 从 `eta.terms` 原样取 8 项（`time_alignment.py:1123-1127`），并注释"re-deriving the egress wait here would both double-count it and leave the query instant untouched"（`:1120-1122`）。这一条有测试：`test_eta_terms.py:84-97`（`total_s == sum(terms)`；`ETA_TERM_KEYS` 与 eta 项一致）。

[FACT] `TERM_KEYS`/`ETA_TERM_KEYS`（`:137-164`）共 8 个 ETA 键 + `resource_work_s`/`remaining_prop_s`/`terminal_tx_s`/`terminal_prop_s`；**`resource_packet_tx_s`（ISL 目标串行化）两边都没登记**（只在 `:1155` 出现）。`test_ideal_state_time.py:304-315` 的注释也承认 `TERM_KEYS` 不能当作"每条路径都齐"的断言。

### 3.3 时间线/审计到底记录了什么、没记录什么

**决策 sink（`decision_sink`，每决策一行）** [FACT] `kernel.py:6075-6109`：`t`（commit）、`t_decision_start`、`decision_id`、`hop_index`、`pid/src/dst/sat/kind/policy/candidates/chosen/own_queue_bits`、`obs`、`info_audit`、`obs_mode`、`observation_at_start`、`four_direction_audit`、`estimate_at_start`、`truth_at_commit`。其中 `observation_at_start`（`kernel.py:4422-4560`，函数体见 `_observation_at_start`，def 在 `:4422`）含：
- `t_observed`（frozen=t_decision_start，refresh=commit；ledger 的 `t_measure` 由此恢复）
- `neighbours[origin]`：`generated_at`（**t_measure 的每邻居源时刻**）、`received_at`（**t_receive**）、`age_s`、`hops`、`advertised_isl_queue_bits/generation/rate_bps/arrival_rate_bps/work_ahead_bits_proxy`、`advertised_history`（全序样本，含每条的 `generated_at`/`received_at`）
- `candidate_resources`（每候选的 peer/egress_direction/generation/rate/prop/remaining_prop/remaining_hops 等，`kernel.py:5664-5874`）
- `time_alignment` 审计（`kernel.py:5489-5553`）：`arm`、`predictor`、`state_source`、`used_state`、`observation_age`、`common_rule/horizon/status`、`query_delay_s`、`snapshot_at`、`compute_state`、**`eta_terms`**、**`eta_targets`**、**`query_targets`**（`dict(plan.targets)` —— 这就是"candidate_target_hat"，但字段名不是它）、`eta_unknown_terms`、`ranking`、`fallback_directions`、`missing_directions`、`applied_order`、`history_samples`、`scores`（每方向 `total_s`、`terms`、`missing`、`fallback`、`predicted_bits`、`service_rate_bps`）、零速率诊断。

**时间线 sink（`timeline_sink`，里程碑）** [FACT] 可用里程碑：`decision_attempt`、`queue_enter`、`service_start`、`service_finish`、`peer_arrival`（带 `egress_snapshot`，`kernel.py:2221-2240`）、`redecision`、`satellite_ingress`、`node_process_start/end`、`commit_rejected`、`hold`、`frozen_inferred_hold`、`fail`、`forced_action`、`packet_fate`、`compute_*`、`query_*`、`ctrl_*`。`service_start/finish` 对数据包带 `stage`/`link_id`（ISL 为 `stage="isl"`、`link_id="isl:sat:peer"`，`kernel.py:2177-2207`、`:3210-3240`）。

**11 字段时间链（T1 ledger）** [FACT] `decision_ledger.py:44-57 TIMELINE_FIELDS`：`t_measure, t_control_rx, t_decision_start, t_decision_commit, t_local_queue_enter, t_service_start, t_service_finish, t_peer_arrival, t_peer_redecision, t_peer_target_egress_enter, t_peer_target_egress_service_start`。由 `build_ledger`（`:236-331`）从两个 sink 折叠；缺失记 `"missing"` 而非 0。

逐项回答任务书的问题：

| 任务书问题 | 结论 |
|---|---|
| `t_measure` | [FACT] 决策行本身**没有**该字段；ledger 由 `obs_mode` 推出（frozen→t_decision_start，refresh→commit，`decision_ledger.py:286`、`:35-38`）；每邻居源测量时刻在 `observation_at_start.neighbours[origin].generated_at`。 |
| `t_receive` | [FACT] 每邻居 `received_at` 在 `observation_at_start.neighbours`；ledger 的 `t_control_rx` = 最新一条（`_freshest_control_rx`，`:287`）。 |
| `t_decision` | [FACT] `decision.t`（commit）+ `t_decision_start` 都在决策行（`kernel.py:6076/6080`）。 |
| `candidate_target_hat` | [FACT] **全仓无此字段名**（grep 0 命中）。等价物：`time_alignment.query_targets[d]`（该 arm 实际查询时刻）与 `eta_targets[d]`（T_arrival）。opt-in 的 target probe 里叫 `algo_query_target_s` / `eval_target_s`（`kernel.py:6178-6179`）。 |
| `t_send_start` / `t_send_finish` | [FACT] **没有同名里程碑**；可由 `service_start`/`service_finish`（`stage="isl"`, `link_id="isl:sat:peer"`）恢复；仅当 timeline_sink 打开时存在。 |
| `t_peer_arrival` | [FACT] 有：`peer_arrival` 里程碑（传播到达 peer 卫星的物理时刻，`kernel.py:2221-2240`）+ `egress_snapshot`（到达瞬间 peer 四方向出口状态）。 |
| `t_target_resource_use` | [FACT] **没有**"目标资源被使用"的直接记录。最接近的两个：(a) ledger `t_peer_target_egress_enter` / `t_peer_target_egress_service_start` = **后继决策**的 `t_local_queue_enter`/`t_service_start`（`decision_ledger.py:313-319`）——即 peer 实际选择的出口（未必等于预测的 `egress_direction`）；(b) opt-in target probe 在 `eval_target_s` 采真值。 |

[FACT] opt-in 真值探针 `target_probe`（`target_probe.py`）：按 `eval_target_s` 采样（`:163`、`:197`），`eval_target_s` 来自 `eta_targets[direction]`（`kernel.py:6179`）；样本值用与广播同一函数 `_isl_resource_state_advertisement`（`target_probe.py:214-230`），记录 `queued_bits`/`in_service_remaining_bits`/`work_ahead_bits`，不干扰仿真（docstring `:15-33`）。
- [FACT] **探针在 `T_arrival` 采真值，不在 candidate 的 legacy 查询时刻采**；只有 `candidate_target_rule="arrival"` 时两者重合。legacy 下"预测时刻 ≠ 真值时刻"，预测精度会被低估/偏移。这是 B1 的连带影响（B4）。
- [FACT] `t1_tasks._four_direction_eta`（`t1_tasks.py:659-680`）用 `peer_arrival_at_s - eta_target_s` 算 ETA 误差；但 `eta_target_s` 含 `peer_process_s`（到达 peer 后还要 peer 计算/查询），`peer_arrival_at_s` 是 peer **卫星**到达（处理前）。[INFER] 已知 `peer_process_s` 时该误差带系统性正偏；不能直接当"ETA 精度"结论。

### 3.4 判定（检查 3）

**PASS-WITH-CAVEAT。** 8 个 ETA 项齐全、顺序即时间顺序、区间按构造不重叠、未知显式化；`eta_terms/eta_targets/query_targets` 在决策行可回放。保留：无 `candidate_target_hat` 字段名；无 `t_target_resource_use` 直接记录（只能近似/探针）；探针真值时刻与 legacy candidate 查询时刻不一致；`t_send_*` 只能由 `service_*` 恢复。

---

## 4. 评分器：加性项、剩余传播、queue_weight、边界

### 4.1 逐项（`time_alignment.py:1071-1226`）

[FACT] 加性项（秒），按写入顺序：

| term | 写入位置 | 含义 | 与任务书要求对应 |
|---|---|---|---|
| `compute_wait_s` | `:1123-1127`（来自 ETA） | 本地计算池等待 | 本地 compute wait ✔ |
| `compute_service_s` | 同上 | 本地计算服务 | 本地 compute ✔ |
| `query_wait_s` | 同上 | 查询池等待 | 本地 query wait ✔ |
| `query_service_s` | 同上 | 查询服务 | 本地 query ✔ |
| `local_egress_wait_s` | 同上 | 本地出口排队（已扣 pre-egress 时间） | 本地出口等待 ✔ |
| `tx_s` | 同上 | 本包本地首跳串行化 | 本地发送 ✔ |
| `prop_s` | 同上 | 本地首跳传播 | 本跳传播 ✔ |
| `peer_process_s` | 同上 | peer 计算/查询预测 | peer 处理 ✔ |
| `resource_work_s` | `:1131-1145` | `predicted_bits / resource_service_rate`（预测的目标资源工作量 ÷ 其服务率） | 预测目标负载/服务率 ✔ |
| `resource_packet_tx_s` | `:1146-1155` | `pkt_bits / service_rate`，**仅 ISL 目标** | 目标资源上的包串行化 ✔ |
| `terminal_tx_s` | `:1156-1168` | `pkt_bits / GSL_rate`，仅下行目标 | 下行终端串行化 |
| `terminal_prop_s` | `:1169-1173` | 下行终端传播 | 终端传播 |
| `remaining_prop_s` | `:1174-1178` | 可见拓扑剩余传播成本 | 剩余传播 ✔ |

[FACT] 缺项 → `fallback=True, total=inf`，`missing` 列出原因（`:1102-1119`、`:1179-1187`）；排序键把 fallback 放最后且用 `visible_order`（`:1208-1220`）。**永不把缺失当 0**。

### 4.2 剩余传播 / 绕行

[FACT] `kernel.py:5835-5872`（`_candidate_resource_map`）：

```python
# fixed visible-topology remaining propagation cost: remaining
# hop count from the peer to its nearest VISIBLE serving node, at
# a representative per-hop propagation (the known link value).
...
safe_reverse = routing.without_nodes(self._routing_reverse_adj, forbidden_nodes)
hops_map = routing._multi_source_bfs(safe_reverse, safe_serving)
remaining_hops = hops_map.get(peer)
...
remaining_prop = (None if (remaining_hops is None or propagation is None)
                  else float(remaining_hops) * float(propagation))
```

- 剩余跳数 = 从该候选 peer 出发、在多源 BFS（源 = 可见 serving 集、去掉本包已访问节点）到最近 serving 节点的跳数；`propagation` = **当前候选首跳**的链路传播（`model.propagation_delay_s(isl_range_km(sat, peer, now))`），被当作"代表单跳值"。
- 下行候选：`remaining_prop_s = 0.0`（`kernel.py:5762`）。
- 规则标签：`rule="visible_topology_shortest_remaining_path"`，basis 注释 `kernel.py:5868-5872`。
- 因此**绕行候选的额外剩余传播确实进总分**（跳数越多越大），但它是"跳数 × 单跳传播"的**拓扑先验**，不是沿真实路径逐跳求和；对传播时延沿路径差异大的场景会低估/高估。文档亦如此标注（`ANALYSIS/.../FINDINGS-20261030.md:199` 指出 5 跳×单跳低估 14 跳绕行）。
- [FACT] `remaining_prop_s` 在评分器只加一次（`time_alignment.py:1174-1178`），且不进入 ETA（ETA 只到目标资源入队前）。

### 4.3 queue_weight 与单位

[FACT] `time_alignment.py:1188-1201`：

```python
queue_weight = (_QUEUE_WEIGHT if snapshot.queue_weight is None
                else _finite("queue_weight", snapshot.queue_weight))
if queue_weight <= 0:
    raise TimeAlignmentError(...)
if queue_weight != 1.0 and "resource_work_s" in terms:
    terms = dict(terms)
    terms["resource_work_s"] = terms["resource_work_s"] * queue_weight
total = float(sum(terms.values()))
```

- 优先级：config `time_alignment.queue_weight`（进 `config_sha256`）> 环境变量 `T1_QUEUE_WEIGHT`（`time_alignment.py:131`）> 历史 1.0。
- 单位：无量纲乘子，只乘 `resource_work_s`（秒），其他项不动；>0 校验。
- 其余项单位均为秒；`predicted_bits` 为 bit；`service_rate_bps` 为 bit/s。

### 4.4 评分器边界

[FACT] 只对"当前候选的目标资源"的队列工作量定价（`resource_work_s` + `resource_packet_tx_s`/`terminal_tx_s`）；下游**只**加 `remaining_prop_s`，不建模下游任何队列/竞争。上游本地队列只通过 ETA 的 `local_egress_wait_s` 计入一次。
[FACT] 目标资源 = 候选 peer 的具名有向出口（`ResourceKey`），不是整颗卫星；generation 必须匹配（重匹配后旧广播样本不进历史，`kernel.py:5095-5096`；测试 `test_physical_resource_generation_never_shares_predictor_history`）。
[FACT] 排序确定性：`(fallback, total, visible_order, direction)`；`tie_sig_digits` 默认 `None` 时排序键与历史逐字节一致（`:1208-1218`）。
[INFER] 因此跨候选比较共享同一 `remaining_prop` 先验（逐候选不同、逐臂相同），不会偏向任一臂；但"下游拥塞"整体不在评分器内，研究问题中的"queue/congestion amplification"只能作为**观测后果**，不能声称评分器已建模。

### 4.5 判定（检查 4）

**PASS-WITH-CAVEAT。** 用户列出的 8 类加性项全部存在且只加一次；绕行额外剩余传播在（拓扑先验形式）；queue_weight 语义/单位/优先级清楚；边界明确（只当前目标资源队列 + 剩余传播）。保留：`resource_packet_tx_s` 未进 `TERM_KEYS`/docstring（声明不全）；`remaining_prop` 是跳数×代表单跳，不是逐跳求和；下游队列不建模。

---

## 5. 配置合法性、协议版本、确定性

### 5.1 `config.py` 的 `time_alignment` 段

[FACT] schema（`config.py:292-354`）与默认（`589-610`）：

```
enabled: False            arm: "candidate"       predictor: "bounded_linear"
history_limit: 8          common_rule: "median_eta"   common_horizon_s: None
execution_mode: "per_packet"  per_flow_ttl_s: 0.5     query_delay_s: 0.000001
min_observation_age_s: None    state_source: "advertised"    tie_sig_digits: None
（candidate_target_rule / ou_tau_s / queue_weight / arrival_window_s / drift_alpha / state_age_tau_s / rate_ahead_s 只在 SCHEMA，不给默认值——避免改变既有 profile 的 config_sha256）
```

- [FACT] `advertise_interval_s` 与 `vis_k` **不在** `time_alignment`，而在 `control_plane`：`config.py:526 vis_k: 2`、`528 advertise_interval_s: 1.0`；`1030-1032` 只校验 protocol ∈ {1,2}，`1026-1028` 校验 vis_k≥0、间隔/ttl/packet_bits。冻结 study `time_alignment_study.yaml` 把 `control_plane.advertisement_protocol_version: 2` 放进 `fixed`；base profile `CODE/leo_sim/profiles/t1_population_region_cost_smoke.yaml:55-64` 也是 v2、vis_k=17、servers=1、frozen。
- [FACT] `compute_servers_per_satellite` 在 `execution`（`config.py:286`，默认 0，`587`）。

### 5.2 已存在的合法性规则（fail loud）

[FACT]
- `time_alignment.enabled and compute_servers_per_satellite > 1` → `ConfigError`（`config.py:1241-1257`，理由：多服务器 work-ahead 广告编码不了 → 全 fallback；测试 `CODE/leo_sim/tests/test_config.py:40-62`）。
- arm ∈ {stale,now,common,candidate}（`:1268-1271`）；predictor ∈ 9 种（`:1272-1275`）；`history_limit >= 1`（`:1276-1277`）；`candidate_target_rule ∈ {arrival,arrival_plus_queue}`（`:1284-1288`）；`state_source ∈ {advertised, ideal_current}`（`:1289-1293`）；`common_rule ∈ {median_eta,mean_eta,fixed_horizon}`（`:1301-1304`）；`fixed_horizon` ↔ `common_horizon_s` 互锁（`:1359-1370`）；`execution_mode ∈ 5 种`（`:1371-1375`）；async 模式 ↔ `async_routing.enabled` 双向互锁（`:1376-1384`）；`per_flow_ttl_s > 0`、`query_delay_s >= 0`（`:1385-1393`）；`ou_tau_s` 仅对 `ou_shrink` 有意义（`:1354-1358`）等。

### 5.3 缺失的合法性规则（**静默失效风险**，FACT）

没有任何校验把 `time_alignment.enabled` 绑定到：
1. `control_plane.advertisement_protocol_version == 2`（v1 会把 `downlink_resources`/`processing_resources` 删掉、`isl_queue_bits` 只剩 `{peer,value}`，`kernel.py:3741-3750`）→ 在线快照拿不到 generation/rate/work_ahead → 全部候选 `missing`/fallback，四臂退化同值。
2. `control_plane.enabled == True`（关掉控制面 → 无广播 → `no_received_history` → 全 fallback）。
3. `execution_mode == "per_packet"`（per_flow 的 `peer_process_s` 恒 None → 全 fallback，`time_alignment.py:1017-1021`；precomputed 根本不打分 `kernel.py:5316-5336`；async 见 §1.4 四臂同值）。
4. `scenario.seed` 一致（由 driver 保证，非 config 校验）。
- [FACT] 现有测试覆盖了 1 的**后果**（`test_time_alignment_online.py:285-310`：legacy 广告 → `no_received_history` + fallback，不零填充），但**没有**在 config 层拒绝 v1+ta，也没有拒绝 per_flow/async+ta 的测试。
- [INFER] 只要 pilot 的 profile 沿用冻结 study/base profile（v2、frozen、servers=1、per_packet 默认），就不会踩 1-3；但这是"profile 恰好正确"，不是"代码保证"。

### 5.4 协议版本携带什么

[FACT]
- v2 分支（`kernel.py:3727-3740`）在广告里加资源遥测，并保留 `downlink_resources`/`processing_resources`；v1 分支（`:3741-3750`）删除二者并把 `isl_queue_bits` 缩成 `{peer, value}`。
- v2 的每方向 ISL 记录（`kernel.py:3828-3838`）：`peer`、`value`（queued data+ctrl）、`generation`、`rate_bps`、`work_ahead_bits_proxy`（queued+在服务剩余）、`arrival_rate_bps`（本机 EWMA）。→ 资源身份/世代/工作量/速率只在 v2 有。
- `receipt.py:1948-1988` 对 protocol==2 强制校验 wire_cost 描述符（ISL 192 bit/记录、GSL 200 bit、processing 416 bit）；v1 不允许 wire_cost 描述符。
- [FACT] 没有任何代码要求 "time_alignment enabled ⇒ protocol v2"。

### 5.5 同 trace / 同 seed / 仅 arm 差异 的支持

[FACT]
- 四臂整网运行：`t1_tasks.py:1456-1474 _arm_row` 复制 resolved config，强制 `enabled=True`、只改 `arm`，再 `resolve_config` → 每臂一个 config 哈希；trace/geometry/seed 由同一份 `rows` 输入。
- `t1_tasks.py:1861` 只算一次 `rows_digest`；`network_alignment` 的 fairness 块（`:1955-1962`）记录 `rows_digest/same_seed/same_predictor/arms_differ_only_in = "time_alignment.arm (the query instant...)"`。其中 `same_trace: True` 是**声明**，但结构上成立（所有 arm 共享同一 `rows` 列表且只算一次 digest）。
- 校验器 `CODE/experiment_platform/network_arm_acceptance.py:355-395` 对四个 run 目录做**真实**检查：
  - `:368 _require(all(c == configs[0] for c in configs), "configs differ beyond arm")`（先 `config["time_alignment"].pop("arm")`，`:350-352`）
  - `:369 _require(all(rows == inputs[0] ...), "offered inputs differ between arms")`
  - `:384` 才写 `"configs_differ_only_in_arm": True, "offered_rows_identical": True`；`:385` 诚实标注 `"scientific_source_equivalence": "NOT_CHECKED: external exact source diff review is required"`。
- 冻结 study 编译：`study_design.py:78-85` 对每个 arm 重新 resolve，并把 `_without_arm(config)` 与 block 内参考比较，不等即报错。

[FACT] `config_sha256` = `sha256(canonical_json(DEFAULTS+profile+user+overrides))`（`config.py:1575-1582`）→ arm 在 canonical json 内，四臂四个哈希；同 arm 同 seed 同配置 → 同哈希。

### 5.6 判定（检查 5）

**PASS-WITH-CAVEAT。**
- 通过（[FACT]）：compute server ≤1、async/fixed_horizon 互锁、arm/predictor/history_limit/rule 校验、config_sha256 覆盖 arm、同 trace/同 seed/仅 arm 差异有 driver + acceptance 双重保证。
- 保留：**协议 v2 / control_plane 开 / per_packet 三个必要组合无校验**（§5.3），错了会静默全 fallback 或四臂同值；`candidate_target_rule` 不在默认值里，导致 `study_design._set` 无法在 `fixed` 段添加它（`study_design.py:26-32` 会抛 `unknown resolved configuration key`）→ B1 不是"改个 yaml 就行"。

---

## 6. 代码路径层面的"物理模型相同"补充

[FACT] 四臂共用 `Kernel`、同一 `config`（除 arm）、同一 trace/geometry；`_build_ta_snapshot` 只读本节点自己的队列、已收到广播、可见拓扑与配置代价（docstring `kernel.py:4961-4965`）。`_candidate_resource_map` 不读 peer 真实队列（docstring `:5669-5674`）。状态源 `state_source=ideal_current` 是显式干预（换成同一资源的真值，`kernel.py:5045-5080`），默认 `advertised`；它不是 arm 差异。
[INFER] 因此"物理模型逐臂相同"成立；唯一由 arm 改变的是预测所问的时刻，以及该时刻下的预测值/排序。

---

## 7. 实跑的定向测试（精确命令与计数）

环境：`Python 3.14.2`，`pytest 9.1.1`，`simpy 4.1.1`，`numpy 2.3.5`。命令均加 `PYTHONDONTWRITEBYTECODE=1 -p no:cacheprovider`（不写缓存，不修改工作树）。
**注意（FACT）**：本机依赖版本与 CI 钉住的不一致——`.github/workflows/test.yml:26` 是 `simpy==4.0.1 numpy==1.24.3 pyyaml pillow==12.0.0`，CI 的 Python 版本未在本机复现。因此下面的"通过"是本机证据，不等于远端 CI 已通过；下面的 "2 failed" 与依赖版本无关（是测试桩缺少真实类已有的方法，见 #6 说明），在任何环境都会失败。

| # | 命令 | 结果 |
|---|---|---|
| 1 | `cd CODE && python3 -m pytest -q leo_sim/tests/test_time_alignment.py leo_sim/tests/test_time_alignment_online.py leo_sim/tests/test_eta_terms.py leo_sim/tests/test_control.py` | **84 passed**（0.64s） |
| 2 | `cd repo-root && python3 -m pytest -q CODE/experiment_platform/tests/test_four_direction_eta.py` | **2 passed**（0.44s）（必须从 repo root 跑，文件 `from CODE.experiment_platform import t1_tasks`） |
| 3 | `cd repo-root && python3 -m pytest -q CODE/experiment_platform/tests/test_time_alignment_compare.py CODE/experiment_platform/tests/test_ideal_state_time.py CODE/experiment_platform/tests/test_benchmark_decision.py CODE/experiment_platform/tests/test_ta_controlled.py` | **65 passed, 1 skipped**（22.11s） |
| 4 | `cd CODE && python3 -m pytest -q leo_sim/tests/test_config.py` | **31 passed** |
| 5 | `cd CODE && python3 -m pytest -q leo_sim/tests/test_decision_ledger.py` | **18 passed** |
| 6 | `cd repo-root && python3 -m pytest -q CODE/leo_sim/tests/test_target_probe.py` | **2 failed, 6 passed**（见下） |
| 7 | `cd repo-root && python3 -m pytest -q CODE/experiment_platform/tests/test_prediction_accuracy.py CODE/experiment_platform/tests/test_network_arm_acceptance.py CODE/experiment_platform/tests/test_execution_compare.py` | **48 passed** |

单文件计数（#1 拆分）：`test_time_alignment.py` **42**、`test_time_alignment_online.py` **13**、`test_eta_terms.py` **14**、`test_control.py` **15**。

**#6 的失败（FACT，本 HEAD 真实红）**：

```
FAILED leo_sim/tests/test_target_probe.py::test_probe_reads_the_kernel_own_work_ahead_definition
FAILED leo_sim/tests/test_target_probe.py::test_out_of_order_targets_are_each_read_at_their_own_instant
E   AttributeError: 'Link' object has no attribute 'arrival_rate_bps'
    at CODE/leo_sim/kernel.py:3837  ->  link.arrival_rate_bps(float(now))
```

- 原因定位 [FACT]：真实 `ISLLink` 定义了 `arrival_rate_bps`（`kernel.py:978`），但这两个测试用**桩 Link**（`test_target_probe.py:90-103`、`:140-151`）没有该方法；`arrival_rate_bps` 字段是 commit `7e31c79` 加到广播里的，桩未同步。属于**测试桩过期**，不是产品行为缺陷；但 CI 在 HEAD 是红的（`python -m pytest CODE/...` 与 CI 命令一致），且 target probe 是 ETA 真值仪器，pilot 前应修/知会。
- 说明 [FACT]：我另起过 (a) 全量 CI 命令（`CODE/leo_sim/tests CODE/experiment_platform/tests CODE/tests CODE/scripts/remote/tests ANALYSIS/tests`）与 (b) 仅 `CODE/leo_sim/tests` 的整目录运行：两者分别超过 45 min / 40 min 仍未结束，均已在审计窗口内终止，**没有全量结论**。本备忘录只对 §7 表中定向文件给出精确计数；全量结果未知，不作为结论。
- [FACT] §7 表中被审计的定向文件本身很快（0.11–22.11 s），慢的是同目录中**其他**文件；[INFER] 这与本机 Python 3.14/simpy 4.1.1 未钉 CI 版本可能有关，**不能**据此断言仓库有慢测试/挂起缺陷，只说明"本机未取得全量绿"。pilot 前的可信门仍是远端 CI 的 exact-head 结果（本机复现不了 CI 依赖）。

**测试与 6 项检查的覆盖映射（存在 / 缺失）**

| 检查 | 已有测试（FACT） | 缺什么 |
|---|---|---|
| 1 四臂仅时刻不同 | `test_time_alignment.py:195 test_only_the_query_instant_differs_between_arms`（只逐臂调 `arm_target_at` 断言时刻）；`:187 test_tie_break_is_arm_independent`；`test_time_alignment_online.py:83 test_all_four_arms_see_the_same_t0_input`（候选集/资源映射相同）；`:354 test_a_constant_state_makes_every_arm_choose_the_same_action`；`test_time_alignment_compare.py:196 test_the_query_instant_is_the_only_arm_difference`；`:220 test_a_constant_resource_predicts_the_same_for_every_arm` | **没有** kernel 端到端"除时刻之外所有 score terms/fallback/legality 逐臂相同"的断言；**没有**"在线路径从不传显式 target_at"的守护测试；**没有**"四臂在非恒常量状态下 query_targets 互不相同"的断言 |
| 2 bounded_linear | `test_time_alignment.py:42 test_projection_keeps_source_timestamp`（2 样本斜率 800/s → 2400）；`:69` 常量不变；`:79` 单样本→hold_last；`:86` 未到达样本不算；`:92` 旧源不回灌新源；`:101` 负 span 报错；`:106` 非正速率 missing；`:54` generation 隔离 | **没有** ≥3 样本的中位数（奇/偶）断言；**没有** `history_limit` 切片断言（例如给 10 条只取后 4/8）；**没有**非均匀 dt 的相邻斜率断言 |
| 3 ETA 项/时间线 | `test_eta_terms.py:41/50/59` 三项局部延迟各进 ETA 且移动 target；`:84` score 复用 ETA 项、不重复；`:100` 缺 rate → missing 非 0；`:151/164` 下行共享资源；`test_time_alignment_online.py:103/313` 在线快照打分；`test_decision_ledger.py` 18 passed（11 字段折叠）；`test_four_direction_eta.py` 2 passed（发布 eta_target/peer_arrival） | **没有**"`candidate_target_rule=arrival` 下 probe 真值时刻 == 查询时刻"的测试；**没有** candidate legacy 下"目标队列是否重复计价"的判别测试（当前只有语义文档） |
| 4 评分器/剩余传播 | `test_eta_terms.py:164 test_busy_downlink_can_lose_to_a_finite_detour_and_unknown_stays_unknown`（绕行/未知）；`test_time_alignment.py:150 test_an_empty_target_egress_is_not_charged_the_peer_total_backlog`；`test_ideal_state_time.py:290` 项集合一致 | **没有** kernel 层"`remaining_prop_s` 逐候选不同、逐臂相同"的断言；**没有** `resource_packet_tx_s` 在 TERM_KEYS 的声明测试 |
| 5 config | `test_config.py:40-62` 多 server 拒绝；`test_time_alignment.py:295-360` 各字段拒绝/互锁；`:364` 开启 ta 改变 config identity；`test_study_design.py` 断言 study cell 的 v2/seed/arm 组内一致 | **没有** v1+ta、per_flow+ta、async+四臂对照的拒绝/告警测试；**没有** `candidate_target_rule` 在生产 profile/study 中生效的测试 |
| 6 确定性/同 trace | `test_t1_tasks.py`/`test_network_arm_acceptance.py`（48 passed 组内）；acceptance `:368-369` 真实校验 | **没有** 面向 pilot 的"四臂全 fallback 诊断门"（reordered>0 / fallback<100%），虽有审计计数器（`t1_tasks.py:1541-1562`） |

---

## 8. 给 pilot 的阻断项（VM 前）

| ID | 级别 | 问题 | 证据 | 最小处置（需 Lead 决策/授权，我未改） |
|---|---|---|---|---|
| **B1** | **BLOCKER** | candidate 臂实际查 `T_arrival + predicted_bits/rate`（legacy T_service），不是任务书要求的 `T_arrival`；冻结 study 契约声明 T_arrival 但编译结果不是 | 默认 `candidate_target_rule="arrival_plus_queue"`：`time_alignment.py:383/520/1396-1413`、`kernel.py:5254-5258`、`config.py:1284-1288`；无任何 profile/study 设置它（grep）；`study_design.py:61-91` 不设；且 `study_design._set`（`:26-32`）拒绝未在 resolved config 出现的键，故 study yaml 的 `fixed` 无法补 | 二选一：把 `candidate_target_rule: arrival` 写进 base profile（`CODE/leo_sim/profiles/t1_population_region_cost_smoke.yaml` 的 `time_alignment` 段，随后 study 编译自动继承）或改 `study_design.py` 默认。改后重跑 `test_study_design.py`/`test_time_alignment_compare.py`，并在 4 臂 run 的 audit 里核对 `query_targets == eta_targets`（candidate 行） |
| **B2** | **BLOCKER(CI)** | HEAD 上 `test_target_probe.py` 2 failed：桩 `Link` 缺 `arrival_rate_bps` | `kernel.py:3837`、`kernel.py:978`（真类有）、`test_target_probe.py:90-103/140-151`（桩无） | 修测试桩（加 `arrival_rate_bps` 或改用真实 `ISLLink`）；不改产品代码。pilot 若用 `target_probe` 采真值，必须让该仪器绿 |
| **B3** | **HIGH（静默失效）** | 无校验 `time_alignment ⇒ protocol v2 / control_plane on / per_packet`；错了会全 fallback 或四臂同值而无错误 | §5.3；`time_alignment.py:1017-1021`、`kernel.py:3741-3750`、`:5316-5336`、`:4842-4877` | pilot 的 profile 明确写 `advertisement_protocol_version: 2`、`execution_mode: per_packet`、`control_plane.enabled: true`，并在起飞前断言 audit `fallbacks < 100%` 且 `arms_seen_in_the_audit == 四臂`；最好再补 config 校验（源改动） |
| **B4** | **MEDIUM（测量口径）** | target probe 在 `eta_targets`（T_arrival）采真值，legacy candidate 查询时刻更晚；`t1_tasks._four_direction_eta` 用 `peer_arrival − eta_target`，两者语义差一个 `peer_process_s` | `kernel.py:6178-6179`、`target_probe.py:163/197`、`t1_tasks.py:659-680` | B1 修好后 candidate 的预测/真值时刻自动对齐；ETA 误差诊断若继续用 `_four_direction_eta`，必须扣掉 `peer_process_s` 或改口径 |
| **B5** | **MEDIUM（诊断门缺失）** | 没有自动门禁止"四臂退化为同一 run"；目前只有审计计数器 | `t1_tasks.py:1537-1562`（reordered/fallbacks/distinct_query_targets）；`network_arm_acceptance.py:368-369`（只查 config/输入，不查臂是否真的不同） | pilot 起飞前手工/脚本检查每臂 audit：`decisions_where_the_arm_reordered_candidates > 0`、`fallbacks < decisions`、`distinct_query_targets` 非空且四臂不全同；把它写进 launch 检查单（不改代码） |

补充（非阻断但会误导结论）：
- `remaining_prop_s` 是跳数×代表单跳的拓扑先验，**不能**当作"物理剩余路径时延"来声称（`kernel.py:5835-5872`）；`FINDINGS-20261030.md:199` 已记录该低估。
- `candidate_target_rule` 不进 DEFAULTS 是为了不改变历史 `config_sha256`（`config.py:328-333` 注释），因此任何"补默认值"的改动都会移动所有 profile 的 hash —— 这也是 B1 处置时要留意的身份影响。
- 研究问题中的"queue/congestion amplification"不在评分器内（§4.4）：评分器只对当前候选目标资源队列定价；下游拥塞只能作为观测后果，不能作为模型能力声称。

---

## 9. 旧文档 vs 本 HEAD（明确哪些前提是错的）

| 旧前提 | 本 HEAD 事实 | 证据 |
|---|---|---|
| `history_limit=4` | 全部为 **8**（config 默认、make_snapshot/predict_resource 默认、study 契约、全部 profile）；仓内无 4 | §2.3 |
| candidate 臂已按 `T_arrival` 查询 | **默认仍是 `arrival_plus_queue`（T_service）**；arrival 规则存在但只有 `ta_controlled.py` 与测试在用；冻结 study 契约声明与编译不一致 | §1.3、B1 |
| `test_time_alignment_online.py` 8 passed（`CODE/work/WP-T1-COMPLETE/REPORT.md:74`） | 现在 **13 passed** | §7 |
| 四臂 fallback 完全一致 | `NO_STRONG_COMMON` 是 common 专属回退 | §1.5 |
| candidate 目标就是"预测到达 peer 出口资源的时刻" | 只有 `rule="arrival"` 时成立；`arrival_plus_queue` 是"到达 + 预测队列等待"（使用时刻） | `time_alignment.py:1396-1413`、测试 `test_time_alignment.py:450/468` |

---

## 10. FACT / INFERENCE 清单（本备忘录的自证）

- FACT：所有 `file:line` 引用的代码/测试内容；所有 pytest 计数（命令见 §7）；「仓内不存在 history_limit=4 / candidate_target_hat」的 grep 结果；commit `171c8ef` 只新增规则未改默认（`git show`）。
- INFER（未由运行证明）：四臂"唯一差异"在 kernel 端到端（测试只覆盖到快照/排序/时刻层）；`per_flow`/`v1`/`async` 配置会导致机制静默失效（由代码分支推断，未跑该组合）；`_four_direction_eta` 的偏差方向（由两个时刻的语义差推断）。
- 未验证：VM 上任何配置/数据上的行为；全量 CI（审计时后台仍未结束）；96 星整网性能；`R_phys` 一词（`ta_controlled.py:12` 提到 "not implemented"）在本 HEAD 的具体所指（本 HEAD 只有 `remaining_prop_s` 拓扑先验，未找到 `R_phys` 符号）。
