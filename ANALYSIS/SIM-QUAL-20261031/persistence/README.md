# persistence/ — 方向性持续偏差（δ_r / ε_r / z）与队列松弛

> 性质：**DIAGNOSTIC / mechanism pilot**（单 seed=7、单 trace、合成 140-sat Walker）。
> 不是 formal run，不进论文主表；结论不得外推为真实 Starlink 流量。
> 分析器：`../sq_persistence_analyze.py`（本目录所有 JSON/MD 由它生成，确定性、无时间戳）。
> 评测窗：**4–14 s**（PROTOCOL §2），δ/ε 的 z 归因用 0.5 s 细窗，**burst 8.0–8.5 s 单列**。
> 证据根（只读）：`三端文件与同步治理/evidence/t1/sq-<cell>-20261031-01/<cell>/`。

## 0. 一句话结论（当前 Round-1 数据）

**action flip 率几乎不随 regime 变化，但"方向性持续偏差相对剩余容量"变化两个数量级**：
p1-high / p1-low / p2-sync 的 greedy flip 率是 0.278 / 0.277 / 0.224，
而 z≥1 的占比是 **0.538 / 0.019 / 0.000**。
⇒ 单看 flip rate 无法回答"是否存在持续定向偏流把关键 corridor 推向容量"；必须看 δ_r/ε_r。

## 1. 口径（三条必须与 Lead 修正一致，已实现）

| # | 修正 | 实现 |
|---|---|---|
| F1 | `load_calibration._project_decision` **丢掉 dst**，用 decision row 的 dst 会把 (sat,dst) 流退化成分 sat | `dst` 与 `bits` 一律从 cell 的 `trace.jsonl.gz` 按 `pid` join（与 `sq_policy_analyze.load_trace_map` 同法）。`persistence.flip_runs_by_sat_only` 只作**旧口径对照**并标 `superseded_round1_lens`；报告正文以 `flip_runs_by_stream`（per-(sat,dst)）为准 |
| F2 | 队列重建若在 `service_start` 扣 bits，等于"dequeue 即排空" | backlog = **queued bits + in-service 剩余 bits**：`[queue_enter, service_start)` 计满量，`[service_start, service_end)` 线性衰减到 0，`service_end` 取 `link_service_windows[*].end`（本数据 100% 匹配：23025/23025，fallback 0，服务时隙 p50=10 ms） |
| F3 | τ_error 不能用 0.5 s 分箱宽度当 duration | 两处都用**决策时间戳**：① (sat,dst) 流内"连续 flip 且 chosen 方向相同"的 `t_last − t_first`；② 每 link 的持续超载段 `t_last − t_first`（分箱只用于判定某决策是否落入 `|δ_r| > 阈值` 的 bin）。0.5 s 分箱版本单独放在 `persistence.binned_bias_runs` 并显式标注"durations quantized, 不是 τ_error" |
| F4 | 小 ε_r 会让 z 虚高 | z 同时给 **raw**（`z_bins.flip_decisions`）与 **valid**（`z_bins.flip_decisions_valid`，仅 `ε_r ≥ max(1 bps, 0.01·μ)` = **1 Mbps**）。两者都报，并给 `n_flips_excluded_by_floor` / `n_flips_without_resource` |
| F5 | δ 的口径容易被误读 | `directional_bias.accounting` 同时给 `Σ|δ_r|`（每条 flip 计进+出，双计）、`Σ|δ_r|/2`（**推荐引用的一侧搬运量**）、`Σ positive excess`、`Σ negative deficit`、`net Σδ_r`（构造上=0，实测 0.00 Mbps）、top-5 excess |

## 2. 定义（自描述，JSON 的 `definitions` 字段同义）

- **flip**：truth-substituted same-decision re-ranking，与 `sq_policy_analyze.py` **逐决策完全一致**（见 §5 自检）。
  `J_truth = j_used − resource_work_used + truth.work_ahead_bits/rate`（queue-aware）；
  geometry 的 J 无状态项 ⇒ `J_truth = j_used`，**flip≡0、δ≡0**（负对照，已断言）。
  hysteresis 用记录的 `last_direction` + `eta` 重新套用到 truth J 上。
  `target_probe` 只用 `identity_source == "used_state"` 且 `arm == "now"` 的行（greedy cell：用 46,577 行，跳过 1,260 行）。
- **δ_r**（bit/s）= (被错误送进 r 的 bits − 本应送进 r 却送去别处的 bits)/窗口。
  每条 flip 是"一次 +、一次 −"；两端 resource 未知的 flip 丢弃并计数（`n_flips_unattributed`，p1-high-greedy 为 25/4037），
  因此 `Σ_r δ_r = 0` 严格成立。
  **它是逐决策的"改送"计量，不是端到端重路由净流量**（同一包的后续 hop 可能再改回来）。
- **ε_r**（bit/s）= `μ_r − served_bits_r/窗口`，由 `link_service_windows`（`stage == "isl"`）按窗口精确裁剪累加；
  `μ_r` = 窗口的 `rate_bps`（本数据恒 100 Mbps）。可行调度下 `served ≤ μ·Δ` 恒成立 ⇒ **ε ≥ 0**（实测无负值）；
  ε=0 = 该窗口饱和，z 记为 inf 并单独计数。另给 **offered** 诊断（`queue_enter` 到达率，**可以 > μ**），
  因为 0.5 s 平均值会掩盖 sub-bin 饱和：p1-high-greedy 有 55/2400 个 0.1 s 子窗 `served util = 1.00`，
  geometry 有 497/2400（20.7%），offered util 最高 1.90。
- **z = |δ_r| / ε_r**：用该决策所在 **0.5 s bin** 的 δ_r 与 ε_r。
  ⚠️ **z 是"归一化偏差幅度"，不是过载指标**：ε 是"扣掉实际流过（含被改送的）流量后的剩余容量"，
  δ 是反事实账目摆动，两者计账基础不同。z≥1 的含义是"这次定向偏差与/大于该链路此刻的剩余容量"，
  而不是"链路被压垮"。是否真的排队，看 `queue_growth` / `backlog@t` / `frac_relaxed_at_2s` 三列（逐 bin 实测）。
- **backlog**（bit，本次修正后的 τ_relax 基准）= queued + in-service 剩余（见 F2）。
- **τ_relax**：corridor link 上 backlog 的峰值（≥2 个服务时隙 = 2 Mbit，避免把"正在服务的那个包"当拥塞）
  回到 **峰值 50%** 的时间 `tau_50`，以及回到**前 2 s 的 p10–p90 band** 的时间 `tau_band`。
  backlog 是分段线性函数，交点**解析求解**（微秒级），没有重采样栅格下限。
- **τ_error**：见 F3；两条口径都报：(sat,dst) 流内的同向连续 flip run（`flip_runs_by_stream.duration_ms_ge2`）
  与每 link 的持续超载段（`persistence.error_runs_exact.tau_error_s`，阈值 `|δ_r| > 5% μ = 5 Mbps`，
  相邻决策间隔 ≤1 s；敏感性 1%/10% 也报）。
- **corridor**：按 4–14 s 窗口内 served rate 排序的 top-ρ 有向 ISL（ρ 主值 0.10；0.05/0.20 也报）。

## 3. 命令

```bash
cd /Users/lge/Desktop/topic/.wt-qual-20261031
E=/Users/lge/Desktop/topic/三端文件与同步治理/evidence/t1
P=ANALYSIS/SIM-QUAL-20261031/persistence

# 单 cell（冒烟）：stdout 打印 flip/directional_bias/epsilon 摘要
python3 -B ANALYSIS/SIM-QUAL-20261031/sq_persistence_analyze.py cell \
  $E/sq-p1-high-greedy-20261031-01/p1-high-greedy --json /tmp/g.json --md /tmp/g.md

# 一组 cell → <cell>.json/.md + <label>-persistence.json/.md
for grp in p1-high p1-low p2-sync; do
  cells=""; for pol in geom greedy hyst ewma; do cells="$cells $E/sq-$grp-$pol-20261031-01/$grp-$pol"; done
  python3 -B ANALYSIS/SIM-QUAL-20261031/sq_persistence_analyze.py matrix $cells \
    --out-dir $P --label $grp --quiet
done

# 交叉自检：逐 flip 决策与 sq_policy_analyze.py 比对（12 个 cell 全 identical）
python3 -B ANALYSIS/SIM-QUAL-20261031/sq_persistence_analyze.py selfcheck <cell-dir>... \
  --check-json $P/selfcheck-20261031.json --quiet
```

Round-2 新 cell 只要目录结构相同，直接把 cell 目录加进 `matrix` 参数即可（脚本不写死 cell 名）。

## 4. 输出清单

| 文件 | 内容 |
|---|---|
| `<cell>.json` | 单 cell 全量聚合：`flip` / `directional_bias(含 accounting)` / `epsilon(含 subbin_resolution)` / `z_bins(raw+valid)` / `persistence(streams, error_runs_exact, binned_bias_runs)` / `queue_relaxation(per_link + episodes×2)` / `tau_error_vs_relax` / `per_bin(20×0.5 s)` |
| `<cell>.md` | 同内容人读版（含口径行、z raw/valid 两张分箱表、τ_error top runs） |
| `<label>-persistence.json` | 4 cell 的**摘要 digest**（`per_bin` 不重复，见 `<cell>.json`） |
| `<label>-persistence.md` | 组内对照表（headline、z 分箱占比 raw/valid、δ/持久性表） |
| `selfcheck-20261031.json` | 逐 cell 的 flip 集合与参考分析器一致性 |

无逐决策明细 dump（12 cell 全量决策 dump 会到 10^8 字节量级）；本目录 JSON 合计约 1.1 MB。

## 5. 自检（`selfcheck` 命令，可复算）

**(a) 逐决策一致性**：`sq_policy_analyze.load_decisions` + `_fresh_action` 重算的 flip 决策集合 vs 本脚本，
**12/12 cell 完全相同**（`selfcheck-20261031.json` → `all_identical: true`；
如 p1-high-greedy 4037、p1-high-hyst 2695、p1-high-ewma 4078、p2-sync-greedy 3307、p1-low-greedy 1371）。

**(b) 与已发布 RESULTS 的数值门**（whole-run flip rate，口径与 `RESULTS-20261031.md` 表一致）：

| cell | RESULTS | 复算 | \|diff\| | flips/decisions |
|---|---|---|---|---|
| p1-high-geom | 0.0000 | 0.000000 | 0.000000 | 0/14725 |
| p1-high-greedy | 0.2778 | 0.277793 | 0.000007 | 4098/14752 |
| p1-high-hyst | 0.1815 | 0.181493 | 0.000007 | 2740/15097 |
| p1-high-ewma | 0.2849 | 0.284928 | 0.000028 | 4144/14544 |
| p1-low-geom | 0.0000 | 0.000000 | 0.000000 | 0/4933 |
| p1-low-greedy | 0.2764 | 0.276400 | 0.000000 | 1382/5000 |
| p1-low-hyst | 0.1445 | 0.144524 | 0.000024 | 706/4885 |
| p1-low-ewma | 0.2807 | 0.280716 | 0.000016 | 1396/4973 |
| p2-sync-geom | 0.0000 | 0.000000 | 0.000000 | 0/14727 |
| p2-sync-greedy | 0.2233 | 0.223342 | 0.000042 | 3345/14977 |
| p2-sync-hyst | 0.1642 | 0.164215 | 0.000015 | 2456/14956 |
| p2-sync-ewma | 0.1961 | 0.196096 | 0.000004 | 2883/14702 |

最大偏差 4.2e-5（= RESULTS 四位小数的舍入量级）。

**(c) 确定性**：同一 cell 连跑两次，JSON 与 MD 逐字节相同；脚本无时间戳、无 wall-clock、所有排序显式。
`--quiet` 只关进度输出。

## 6. 结果（Round-1，eval 4–14 s）

单位：moved = `Σ|δ_r|/2` Mbps；ε = corridor top-10% 的 p50/min Mbps；τ 单位 s（τ_relax 为 ms 已标注）。
`z≥1` 为该 cell 内 flip 决策落在 z∈[1,∞) 的占比。

### 6.1 p1-high（120 Mbps + 8.0–8.5 s ×1.6 pulse，adv=1.0 s）

| cell | flip | moved | ε p50/min | z≥1 raw | z≥1 valid | z p50 | 最长同向 flip run | run 时长 max | τ_error p50 | τ_error max | τ_relax p50/p90 (ms) | 0.1 s 饱和子窗 (eval/burst) |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| p1-high-geom | **0** | **0** | 11.21/8.03 | – | – | – | – | – | – | – | **151.2 / 2121** | **497/2400, 22/120** |
| p1-high-greedy | 0.2779 | 265.6 | 51.20/25.62 | **0.538** | 0.541 | 1.32 | 163 | 9.74 s | **0.93 s** | 9.74 s | 12.35 / 31.9 | 55/2400, 12/120 |
| p1-high-hyst | 0.1816 | 112.9 | 48.00/23.52 | 0.462 | 0.467 | 0.79 | 138 | 9.88 s | 0.80 s | 9.66 s | 13.85 / 40.2 | 77/2400, 9/120 |
| p1-high-ewma | 0.2850 | 327.5 | 30.40/25.22 | 0.658 | 0.663 | 1.79 | 211 | 9.74 s | 0.93 s | 9.91 s | 12.67 / 31.9 | 59/2400, 8/120 |

- 净 δ（`Σδ_r`）：全部 0.00 Mbps（构造）。top excess：greedy `isl:44:43 +50.9`（该 link ε≈38 Mbps ⇒ z=1.33）、
  ewma `isl:6:26 +68.1`（z=2.33）、hyst 最大仅 `+13.8`。
- 同号 δ 的最长连续段（0.5 s bins）：greedy +9.0 s / −9.0 s，hyst +7.0 s，ewma +10.0 s。
- 每 link 持续超载段（>5 Mbps）：greedy 77 段（19 段 ≥1 s）、hyst 75、ewma 51；最长 9.74–9.91 s（在窗边**censored**）。
- **τ_error ≫ τ_relax**：可比较片段的 `τ_error/τ_relax ≥ 1` 在 12 个 cell 里 **100% 成立**
  （中位比值 11–330），因此"比值 ≥1 是否带来更大 E2E/queue growth"的按比值分组的对照在本 regime **退化**：
  `<1` 组恒为 0 段。改为按 `|δ|` 中位数分组后（仍有 1–9 段/组，n 很小）：
  **6/6 个可比 cell 里高 |δ| 段的"相对该 link 基线 E2E"都比低 |δ| 段更高**
  （p1-high-greedy −5.2 vs −13.9 ms、hyst +13.1 vs +4.5、ewma +9.2 vs −3.5；
  p2-sync-greedy +5.7 vs −1.0、hyst **+54.9 vs +23.2**、ewma +4.7 vs +0.4 ms），
  而同期 backlog 斜率 ≈ 0（0.1–0.9 Mbit/s，不累积）。
  ⇒ **方向一致但幅度不大（差 3.5–32 ms，基线 E2E ≈ 300 ms）且无队列放大**；
  这是描述性关联（段数少、无检验），**不能**写成"偏流造成性能损失"的因果结论。

### 6.2 p1-low（40 Mbps，无 pulse，adv=1.0 s）

| cell | flip | moved | ε p50/min | z≥1 raw/valid | 最长 run | τ_error p50 | τ_relax p50/p90 (ms) |
|---|---|---|---|---|---|---|---|
| p1-low-geom | 0 | 0 | 70.13/69.2 | – | – | – | 14.36 / 30.7 |
| p1-low-greedy | 0.2774 | 107.9 | 76.83/70.0 | **0.019** / 0.019 | 139 | 0.96 s | 10.71 / 10.8 |
| p1-low-hyst | 0.1453 | 47.6 | 71.00/69.4 | 0.074 / 0.075 | 110 | 0.74 s | 13.48 / 23.7 |
| p1-low-ewma | 0.2816 | 126.7 | 73.90/70.0 | 0.019 / 0.019 | 139 | 0.76 s | 10.71 / 10.8 |

低负载：flip 率与 high **几乎相同**（0.277 vs 0.278），但 ε 变宽 ⇒ z≥1 从 0.54 掉到 0.02。

### 6.3 p2-sync（120 Mbps，adv=0.1 s，AoI p50 487.7→56.3 ms）

| cell | flip | moved | ε p50/min | z≥1 raw/valid | z p50 | 最长 run | τ_error p50 | τ_relax p50/p90 (ms) |
|---|---|---|---|---|---|---|---|---|
| p2-sync-geom | 0 | 0 | 11.31/8.03 | – | – | – | – | 40.11 / 1831 |
| p2-sync-greedy | 0.2243 | 145.2 | 61.60/25.62 | **0.000 / 0.000** | 0.136 | 64 | 0.47 s | 11.41 / 30.3 |
| p2-sync-hyst | 0.1645 | 77.1 | 51.20/23.32 | 0.039 / 0.040 | 0.123 | 52 | 0.39 s | 13.92 / 40.2 |
| p2-sync-ewma | 0.1957 | 180.4 | 33.10/25.32 | 0.116 / 0.118 | 0.220 | 162 | 0.61 s | 12.26 / 30.3 |

**这是本轮最干净的分裂**：AoI 降 8.7× 后 flip 率只从 0.278→0.224（−19%），
但 z≥1 从 0.538→**0.000**、一侧搬运量 266→145 Mbps、τ_error 0.93→0.47 s。
即"更鲜的状态"没有消掉 action flip，却基本消掉了"定向偏差压过剩余容量"。

## 7. 已知限制（必须随结果一起读）

1. **flip/δ 是逐决策、单跳归因**：只记账"本该走 A 却走了 B"的即时下一跳，同一包后续 hop 可能改回来；
   δ 不是端到端重路由净流量。E2E 归因到"该 link 上的包"也是粗归因（整条路径的 E2E）。
2. **ε 由 served bits 算**，可行调度下服务率不可能超过 μ，所以 ε≥0 恒成立、z=∞ 只在精确饱和时出现，
   因此 ε 是"剩余容量"而非"到达率余量"：后者看 `subbin_resolution` 里的 offered（可 >μ，p1-high 实测最高 1.90）。
3. **`packet_events` 只含数据包**（pid 1..1255），控制面队列不在其中，故 backlog 只反映数据 backlog。
   控制面占 ISL 忙时比例实测：p1-high 1.1–1.3%、p1-low 3.7–3.9%、p2-sync 4.1–4.3%（`inputs.control_share_of_isl_busy_time`），
   这部分容量被算进了数据面 ε，即 **ε 系统性偏高该比例**；控制面为非抢占优先且占比小，未做逐 link 扣除。
4. **backlog 重建依赖 `queue_id` 一一配对**（1:1；12 cell 的未匹配数均为 0，逐 cell 见 `inputs.backlog_rebuild`）
   与 `link_service_windows.end`（12 cell 全部 100% 匹配、`finish_from_rate_fallback = 0`）；
   若后续数据出现"已入队但从未服务"的包，其 bits 会永久留在 backlog（当前数据无此情况：`ISL_QUEUE_OVERFLOW=0`）。
5. **τ_error 的 `max` 大多在 4 s / 14 s 窗边被 censored**（run 一直跨到窗末），不能读成"自然结束"；
   `error_runs_exact.n_runs_censored` 给出计数。
6. **τ_error/τ_relax 对照表覆盖率有限**：只统计"corridor link 且该 link 有松弛事件"的段
   （p1-high-greedy 7/77、p1-low-greedy 0/28 —— 低负载下 corridor link 没有 ≥2 时隙的 backlog 峰），
   见 `persistence.error_runs_exact.n_runs_with_relax`；每组 n=1–9，只作描述，不做检验。
7. **z 的 0.5 s 归因窗**是选择：更细的窗会让 δ 更稀疏、z 更噪；更粗的窗会平均掉 burst。
   burst 窗恰好等于一个 bin（8.0–8.5 s）是有意为之；同时给 0.1 s 子窗饱和/offered 诊断以暴露被平均值掩盖的饱和。
8. **corridor 用 served rate 排序的 top-ρ 操作化**，不是"拓扑意义上的走廊"；ρ 换值会换集合（0.05/0.20 已给）。
9. 单 seed、单 trace、单 horizon 20 s；低负载与 high 复用同一 trace 程序的不同负载档（source_trace_sha256 不同）。
10. hysteresis 的 flip 是 **rule-relative**（把"同规则 + truth 状态"当 fresh），不等于"离 truth 最优的距离"；
    因此 hyst 的 flip/δ 低不等于偏差小（RESULTS §2.2 的口径警告同样适用）。

## 8. 不主张（non-claims）

- 不主张"stale state 在本 regime 造成可测性能损失"：高 |δ| 段的 E2E 相对基线只高 3.5–32 ms
  且无队列累积，方向不一致的正负例都在（greedy p1-high 为负、hyst p2-sync 为 +55 ms），样本少且无检验。
- 不主张 flip rate 是方向性偏差的代理（§6 三者已分裂）。
- 不主张 z 是拥塞或丢包的预测器；它只描述"偏差相对剩余容量"。
- 不把 DIAGNOSTIC 结果写成 formal 判据通过，也不外推真实流量。
