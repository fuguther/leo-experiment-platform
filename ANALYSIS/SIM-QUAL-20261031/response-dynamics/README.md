# response-dynamics/ — flip 后果、切换、run 长度与 action entropy

> 性质：**DIAGNOSTIC**。输入为 Round-2 证据（post-fix release、校准负载），只读。
> 生成器：`../sq_response_dynamics.py`（flip 语义直接 import `sq_persistence_analyze`，
> 后者已由 selfcheck 逐决策对齐 `sq_policy_analyze.py`）。
> 评估窗 **4–14 s**；stream key = 每 `(sat,dst)`（`dst` 由 `trace.jsonl.gz` 按 pid join）。

## 0. 一句话结论

**flip 不是孤立抖动**：9 个可统计 cell 里，**70.6–81.8%** 的 flip 落在"≥3 条同方向连续 flip run"内，
只有 **8.6–15.6%** 在下一条决策就 remerge；按 truth 代理代价，**P(flip 改变代价) = 94.9–99.0%**，
但每条 flip 的代价很小（dJ p50 ≈ 5.7–8.1 ms、p90 ≈ 11–18 ms），
与"proxy regret 每条**决策** 1.6–2.1 ms"相容（0.26 × 6.5 ≈ 1.7）。

## 1. 口径

| 指标 | 定义 | 注意 |
|---|---|---|
| flip | truth-substituted same-decision re-ranking（与 `sq_policy_analyze` 逐决策一致） | 不是 re-simulated counterfactual |
| switch rate | 同一 `(sat,dst)` 流内相邻决策 committed 方向不同 | 与 RESULTS 的 switch 口径一致（per-(sat,dst)） |
| action run | 同一流内 committed 方向不变的连续段；长度=决策条数，时长=首末决策时间戳差 | 时长用决策时间戳，不用 bin 宽度 |
| **action entropy** | **仅"实际选择"的经验熵** H=−Σ p_d log₂ p_d（每流内方向分布） | **策略 softmin 不存在**：`CODE/leo_sim` 无 `softmin` 记号，决策日志 `state_policy` 无概率字段。因此**没有**策略内部熵可报，本项只是结果侧观测 |
| dJ | `J_truth(chosen) − J_truth(fresh)`（ms） | 正值=stale 的选择在 truth 下更差；`fresh` 是 truth 代价的 argmin（hysteresis 例外，见 §3） |
| P(consequential\|flip) | 翻 flip 中 \|dJ\| > tol 的占比 | **proxy 定义**，tol 主值 0.5 ms，另给 0.1/1/5 ms |
| P(harmful\|flip) | dJ > tol 的占比 | 同上；非因果 |
| partition | `proxy_gap_tiny` / `fresh_better`(细分为 `quick_remerge`、`persistent_bias`、`fresh_better_other`) / `stale_better` | `persistent_bias` = 该 flip 属于长度 ≥3 的同向 flip run；`quick_remerge` = 该流下一条决策已回到 fresh-optimal |

## 2. 用法

```bash
cd /Users/lge/Desktop/topic/.wt-qual-20261031/ANALYSIS/SIM-QUAL-20261031
E=/Users/lge/Desktop/topic/三端文件与同步治理/evidence/t1
# 单 cell（冒烟）
python3 -B sq_response_dynamics.py cell "$E/sq4-m2-c2-greedy-s7-20261031-01/m2-c2-greedy-s7" \
  --json /tmp/rd.json --md /tmp/rd.md
# 一组 cell
python3 -B sq_response_dynamics.py matrix \
  "$E/sq4-m2-c2-geom-s7-20261031-01/m2-c2-geom-s7" \
  "$E/sq4-m2-c2-greedy-s7-20261031-01/m2-c2-greedy-s7" \
  "$E/sq4-m2-c2-ewma-s7-20261031-01/m2-c2-ewma-s7" \
  --out-dir response-dynamics --label m2-c2-s7
```

输出：`<cell>.json` / `<cell>.md`（单 cell 全量）、`<label>-response.json` / `.md`（组内对照）。

## 3. 结果（Round-2 C/B regime，eval 4–14 s）

| cell | policy | flips | flip rate | switch rate | H (bit) | modal share | dJ p50 (ms) | dJ p90 | P(cons) | P(harm) |
|---|---|---|---|---|---|---|---|---|---|---|
| m2-c2-geom-s7 | geometry | 0 | 0 | 0.221 | 0.404 | 1.000 | – | – | – | – |
| m2-c2-greedy-s7 | queue_greedy | 6592 | 0.2638 | 0.297 | 0.863 | 0.857 | 6.46 | 13.22 | 0.977 | 0.977 |
| m2-c2-greedy-s8 | queue_greedy | 6245 | 0.2566 | 0.255 | 0.797 | 0.833 | 6.13 | 11.84 | 0.971 | 0.971 |
| m2-c2-greedy-s9 | queue_greedy | 6250 | 0.2570 | 0.273 | 0.810 | 0.867 | 6.30 | 12.77 | 0.976 | 0.976 |
| m2-c2-ewma-s7 | queue_greedy_ewma | 6462 | 0.2604 | 0.158 | 0.503 | 0.948 | 5.72 | 11.72 | 0.949 | 0.949 |
| m2-c2-ewma-s8 | queue_greedy_ewma | 6246 | 0.2640 | 0.217 | 0.695 | 0.936 | 6.16 | 11.54 | 0.967 | 0.967 |
| m2-c2-ewma-s9 | queue_greedy_ewma | 6492 | 0.2648 | 0.207 | 0.731 | 0.931 | 6.52 | 11.89 | 0.959 | 0.959 |
| m2-c2-hyst-s7 | hysteresis | 4915 | 0.1887 | 0.191 | 0.805 | 0.857 | **8.07** | **17.54** | **0.990** | **0.954** |
| m2-b-geom-s7 | geometry | 0 | 0 | 0.213 | 0.404 | 1.000 | – | – | – | – |
| m2-b-greedy-s7 | queue_greedy | 5284 | 0.2691 | 0.241 | 0.823 | 0.896 | 6.16 | 11.58 | 0.982 | 0.982 |
| m2-b-ewma-s7 | queue_greedy_ewma | 5186 | 0.2622 | 0.138 | 0.445 | 0.958 | 5.94 | 10.80 | 0.951 | 0.951 |

（tol = 0.5 ms；`P(cons)`/`P(harm)` 都是 proxy 定义。）

### 3.1 结构事实：对 argmin 策略 P(harmful) ≡ P(consequential)

greedy/EWMA 的 `fresh` 就是 `J_truth` 的 argmin，所以 `dJ ≥ 0` 恒成立，
**不存在"stale 选择更好"的 flip**，于是 `P(harmful) = P(consequential)`（表里两列相同不是 bug；
实测范围 0.949–0.982）。
只有 hysteresis 可以出现 `stale_better`（它把 `last_direction` 也当候选）：C regime 下 3.6%
（P(cons)=0.990 vs P(harm=0.954)），且它的 dJ 分布最重（p50 8.07 / p90 17.54 ms）。

### 3.2 flip 的后果分类（C regime s7，tol=0.5 ms）

| cell | proxy_gap_tiny | quick_remerge | persistent_bias | fresh_better_other |
|---|---|---|---|---|
| m2-c2-greedy-s7 | 149 (2.4%) | 953 (15.2%) | **4779 (76.5%)** | 369 (5.9%) |
| m2-c2-greedy-s9 | 149 (2.4%) | 946 (15.1%) | 4779 (76.5%) | 369 (5.9%) |
| m2-c2-ewma-s9 | 265 (4.1%) | 602 (9.3%) | **5297 (81.6%)** | 328 (5.1%) |
| m2-b-ewma-s7 | 255 (4.9%) | 448 (8.6%) | **4243 (81.8%)** | 240 (4.6%) |
| m2-b-greedy-s7 | 96 (1.8%) | 610 (11.5%) | **4264 (80.7%)** | 314 (5.9%) |

**flip 是"成串"的**：70.6–81.8% 的 flip 处在 ≥3 条同向连续 flip run 内（与 persistence 报告里
最长同向 run 163–211 条一致），下一条决策就纠正的比例只有 8.6–15.6%。
这与"W p50 只有 29–159 ms"并不矛盾：run 是按决策条数/时间戳算的，
同一条 run 内可以跨越多个不同 packet 的同一跳。

### 3.3 action entropy（结果侧）

- geometry：modal share 1.000、H≈0.40 bit —— 每条 stream 在多数情况下只有一个方向，
  H 的均值被少数出现 2 个方向的 stream 拉高（权重按 stream 决策数）。
- greedy：H 0.80–0.86 bit、modal share 0.83–0.90 —— 方向分布明显更散。
- EWMA：H 0.45–0.73、modal share 0.93–0.96，且 **switch rate 最低（0.138–0.217）**
  —— 平滑让"选择集中在少数方向 + 少切换"，与 D5（EWMA E2E 最低）方向一致。
- hysteresis：switch 0.191（低）但 H 0.805（高）、dJ 最重 —— "降抖动不降离最优距离"再次出现。

## 4. 与其它分析的关系

- 与 `persistence/`：那里的 z/τ_error 回答"偏差落在哪、持续多久"；这里回答"一次 flip 值多少代价、
  是否成串、切换有多碎"。两者共用同一 flip 定义与同一 (sat,dst) stream key。
- 与 `RESULTS-ROUND2` D1/D2：本节把"flip 率 26%"细化成"95%+ 的 flip 越过 0.5 ms 代理门槛，
  但单条代价 6 ms 量级、成串发生"——即**动作层显著、代价层小**的原因。

## 5. 限制

1. `dJ` 是 **truth-substituted proxy**，不是因果反事实；因果度量见 fork 研究（D6），两者不可互换。
2. 没有 softmin/概率数据，故**没有**策略熵；`H` 是选择结果的熵，不能读成"策略随机性"。
3. P(consequential)/P(harmful) 依赖 tol：0.1/1/5 ms 的敏感性在每 cell JSON 的 `thresholds` 里。
4. `persistent_bias` 依赖 run 长度阈值（≥3 条）与 stream 定义；换 stream key（per-sat）会变大。
5. geometry 的 H/switch 非零只反映"不同时间/不同候选集下方向变化"，其 flip 恒为 0。
6. 只跑了 C（m2-c2，3 seeds）与 B（m2-b，s7）；A/D/强同步 sweep 未跑本节。
7. 单 trace 族、合成 30/70 hotspot；不外推真实 Starlink。
