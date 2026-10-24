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

## 2b. 已落地（分支 `research/arrival-rate-20261024`，从 `5e52ba3` 切出；2026-10-24 11:38）

- ✅ `time_alignment.py`：`PREDICTORS += "drift"`；`StateSample.arrival_rate_bps`（默认 None、负值报错）；
  `predict_resource(method="drift")`：`Q+(λ̂−μ)Δ`、clip [0,B]、缺 λ̂ → `missing_reason="no_arrival_rate"`（不静默置 0）。
- ✅ `config.py`：`VALID_PREDICTORS += drift`。
- ✅ `CODE/leo_sim/tests/test_drift_predictor.py`：7 条全过；既有 `test_time_alignment.py` 39 条全过（无回归）。
- ⏳ 未做：kernel 侧本地 λ̂ 测量与广告携带（§2 第 1–3 项）、接收端写入 history、离线 MAE 验收、VM 小 run；
  下一轮第一件事（验收门见 §3）。
- commit：`d1a45f4`（已推 `origin/research/arrival-rate-20261024`）。

## 2c. 端到端接通 + 冒烟（2026-10-24 11:40–11:46）

- **kernel 侧 λ̂ 已接通**（commit `7e31c79`）：`ISLLink` 记录"实际入队比特"的连续时间 EWMA（只有新入队计数，服务停滞后的 requeue 不重复计），
  广告新增 `arrival_rate_bps`；接收端 `advertised_isl_arrival_rate_bps` 在 observation view 与 recorded history 两处提取；
  `StateSample` 携带该值；`ideal_current` 臂也带真值到达率；`time_alignment.arrival_window_s`（仅 schema/校验，无默认值 ⇒ 旧 profile 的 config_sha256 不变）。
- **VM 代码身份**：`CODE/leo_sim/{kernel,time_alignment,config}.py` 覆盖为 `7e31c79` 版本，sha256 前缀
  `3cd4daa0 / 3170fbe7 / cebafba2`；VM 树其余文件仍为 `5e52ba3`。
- **冒烟 1**（`arr_smoke`：T=0.3s、2000 Mbps、arm=now、drift、probe stride=1/max=2000）：rc=0、status ok；
  `fallback_reason_counts` 出现 **`unknown_term:no_arrival_rate` 2048 次** ⇒ drift 分支被调用且按契约显式报缺（不静默 0）；
  probe 只有前 2000 候选行（早决策、唯一广告在 t=0 无到达）⇒ coverage 0，符合预期。
- **冒烟 2**（`arr_smoke2`：T=0.6s、adv=0.1、emission 0.1–0.5、probe stride=1/max=30000/h=20ms）：**29118 条有标签**，
  `pred!=state` **28.2%** ⇒ 到达率确实进入预测；MAE：drift **0.1209 Mbit** vs hold-last 代理 **0.1181 Mbit**（ratio **1.024**，低负载微劣）；
  分时段 1.05→1.00。⇒ **接线已验证；收益在低负载小场景未出现（预期：队列浅、drift 项小）**。
- **诊断已发射**（11:45，e2 档：6Gbps/140 星/T=1.2/adv=0.2，arm=now 与 candidate，seed 7，probe stride=25/max=8000/h=0，D=0.1928）：
  `runs/drift_s7_{now,candidate}.json`。验收：**h=0 的 drift MAE < hold-last 代理（0.741 ms）且 >5% 改善**，才推进四臂。

## 2d. 全量 drift 诊断结果（e2 档，seed 7，arm=now/common/candidate，12:18–12:20 落盘）——**负面，需调参**

- **MAE（h=0，drift vs hold-last 代理）**：now 分时段 ratio **0.75 / 0.75 / 1.90**；common 总体 **1.112**（分时段 0.87/0.92/1.79）；
  candidate 总体 **0.975**（分时段 0.77/0.79/1.77）。
  ⇒ **早/中段 drift 明显更准（−13%~−25%），晚段（拥塞起来后）明显更差（+77%~+90%）**。
- **业务（vs 同 seed 的 bounded_linear 基线，逐包配对）**：now **+11.42 ms / +6.84 pp**、
  common **+19.06 / +13.75**、candidate **+19.83 / +13.20** ⇒ **全量 drift 让路由显著变差**。
- **机制判读**：EWMA(λ̂, W=0.1s) 在拥塞期把 λ 估低（到达被服务吸收后队列净增长≈0，λ̂−μ 波动下行），
  drift 项预测"队列在排空"，而真值维持高位 ⇒ 晚段系统性低估；且 200ms 广告间隔下 λ̂ 从测量到使用又衰减一次。
  ⇒ 不是"arrival-rate 无信息"，而是**估计器/外推幅度需要收缩与平滑**。
- **调参实验（12:29 发射，arm=now，seed 7，各 1 run）**：
  `A` alpha=0.5, W=0.1；`B` alpha=0.5, W=0.5；`C` alpha=0.25, W=0.1（`runs/tune{A,B,C}_now.json`）。
  验收：h=0 的 MAE **< 0.741 ms（hold-last）且比 0.696 ms（现役 now）好 ≥5%**，且业务 `now−stale` 不劣于基线；
  未过则再改（饱和感知门控：仅当 |λ̂−μ|/μ > 阈值时才开 drift）。
