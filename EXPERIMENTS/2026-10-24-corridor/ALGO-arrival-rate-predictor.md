# 候选算法：带到达率的状态表示 + drift 预测器（下一轮执行计划，2026-10-24）

> 依据：今晚诊断链的结论——**100–200 ms 尺度上 `hold_last` 最优**（离线 MAE 1.02–1.28 Mbit；drain/slope 更差），
> `bounded_linear/ou_shrink` 外推比 hold_last 差 **+11.0%/+15.5%/+19.4%**（CI 不含 0）；
> 四臂 k=6、oracle k=4、H_load k=2、H_adv k=3 全部"未建立"，且 trace 间 SD（3.3–7.5）≥ 观测效应。
> ⇒ **不是"时间对齐没价值"，而是"在当前状态表示下，未来到达过程不可观测"**（`time_alignment.py:706` 自己写明
> "Future arrivals are not available online and are assumed absent"）。本算法直接补这个信息缺口。

## 1. 数学定义（实现前先钉死语义）

- **对象**：有向 ISL 出口队列 `Q_d(i,t)`（bit），服务率 `μ`（bit/s，取自广告 `rate_bps`）。
- **新增状态量**：到达率估计 `λ̂(t)`（bit/s）——**发送端本地可测**：该出口队列在窗口
  `W = min(2·T_adv, 0.2 s)` 内实际入队比特数 / W；用 EWMA 平滑（α=0.5）或滑动窗口均值（二者都记录到广告，取哪一个由 config 定）。
- **预测**：`Q̂(t+Δ) = clip( Q(t) + (λ̂ − μ)·Δ , 0, B )`，`Δ = target_at − measured_at ≥ 0`，`B` = 广告缓冲上限。
- **不确定度**：`u = |ΔQ/Δ|` 的历史残差 p90（或解析上界 `r_max·age + ε_model`），供门控（专利 A）使用。
- **禁止**：读任何 `t > measured_at` 的到达；`λ̂` 必须与 `Q`、`μ` 共用同一 `measured_at` 时间戳；单位 bit/s。

## 2. 实现清单（file:line，全部为最小改动）

| # | 位置 | 改动 |
|---|---|---|
| 1 | `CODE/leo_sim/kernel.py:3719` `_isl_resource_state_advertisement()` | 返回 dict 增加 `arrival_rate_bps`（本地 EWMA/窗口均值）；窗口边界只用 `<= now` 的入队事件 |
| 2 | `CODE/leo_sim/kernel.py:3589`（广播构建调用处） | 字段随广告自动带上；确认 payload 大小与 `control.bits` 记账一致 |
| 3 | `CODE/leo_sim/kernel.py:4404/4522`（接收端消费 `work_ahead_bits_proxy`） | 把 `arrival_rate_bps` 存入该资源的 received-history 样本（同一 `measured_at`） |
| 4 | `CODE/leo_sim/time_alignment.py:51` `PREDICTORS` | 增加 `"drift"` |
| 5 | `CODE/leo_sim/time_alignment.py:257` `ObservationSnapshot` + 样本 dataclass | 样本增加 `arrival_rate_bps: float|None`；`make_snapshot`(:421) 透传；旧数据缺字段时显式 `None`（回退 hold_last） |
| 6 | `CODE/leo_sim/time_alignment.py:545` `predict_resource()` | 增加 `drift` 分支（公式见 §1）；`arrival_rate_bps is None` → `missing_reason="no_arrival_rate"`，不静默置 0 |
| 7 | `CODE/leo_sim/config.py:1228`（`_validate_time_alignment`） | predictor 白名单加 `drift`；保持 config_sha256 记录 |
| 8 | `CODE/leo_sim/tests/` | 新增 test：①λ̂ 计算只用过去；②drift 在 λ<μ 时下降、λ>μ 时上升、clip 边界；③缺字段回退 hold_last；④同一 `(Q,μ,λ,Δ)` 确定性输出 |

## 3. 验收（先离线，后小 run；不通过不进四臂）

1. **离线 MAE（probe，同 run 真值）**：在 h=100/200 ms，`drift` 的 MAE **< hold_last**（当前 hold_last 1.02–1.28 Mbit）。
2. **臂间分辨力**：`now/common/candidate` 的臂间 `|ΔQ̂|` p99 **从 0.31 ms 提升到 ≥5 ms**（≈真实漂移 9–10 ms 的 50%）。
3. **决策层**：删预测项改判比例与 regret 不得恶化；`flip` 率与 `|K|` 同步上升。
4. **代价**：控制面 bits 增幅 ≤ ~5%（一个 float32/序列化字段，相对现有广告 payload）。
5. **falsifier**：若 drift 的 MAE 仍 ≥ hold_last ⇒ "在本抽象里补 λ̂ 也不可行"，则转 §4 方向 2/3，不再做预测类干预。

## 4. 若 drift 仍失败的三条退路（按证据排序）

1. **改 ETA 构建**：现在 `common_horizon` 只有 0/7.88 ms 两档、38% 决策两臂问同一时刻 → 让候选 ETA 真正分散（多跳投影/排队投影），才可能让 R2 可测。
2. **换场景族**：`τ_ser/τ_prop` 或 `τ_prop` 量级不同的星座（如 14×10 薄星座），使队列动力学与广告周期可分辨；
   `theory/04` 的三角（H3∧M2∧H5 排空/adv）给出可行域约束。
3. **existence witness**：复现文献环境的"长广告周期 + 高负载 + 深缓冲"作为机制存在性诊断，明确标注"非现实设置"，
   再逐步迁移（这是用户目标允许的最后手段，必须在报告里单列）。

## 5. 与论文/专利的对应

- 论文 §5（Freshness/load 之后的第三条 follow-up）与 §6.4（机制解释）：本算法是"状态表示缺 arrival/load 信息"论断的直接检验。
- 专利 **B**：权利要求"广告消息新增到达率字段 + 接收端按 `Q+(λ̂−μ)Δ` 外推 + 与门控（专利 A）结合"；
  §1 即其说明书实施例的最小实现。
