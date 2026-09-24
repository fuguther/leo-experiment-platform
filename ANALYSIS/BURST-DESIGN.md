# BURST-DESIGN — Burstiness 实验合法性设计与两个模式

> 本文件即任务书第六阶段要求的 `burst_experiment_design.md`（验收标准所列文件名为 `BURST-DESIGN.md`）。
>
> **修订记录**：rev 1 用 **Fano 因子**作为突发度统计量。独立复核（`leo-runs/audit-notes/E4-burst.md`）
> 实测证明 **Fano 是负载相关的**（同一形状下负载 ×16 使 Fano ×11），用它在 **Mode B** 里会把
> "负载更高" 误读成 "更突发"。rev 2 因此改用**无量纲、尺度不变**的 **CV²**作为主统计量，
> 并补入 `emission_end_s` 截断、`mode=csv` 静默失效、编译路径三处更正。**E4 的复核意见正确。**

| 字段 | 值 |
| --- | --- |
| MAIN_SHA | `549acd8b7cb7a1bb573e7146653f55273999086d` |
| 日期 | 2026-09-24 |
| 取证 | 机制与代数 **[READ]**；数字 **[EXEC]**，§6 给出命令 |
| 涉及代码 | `CODE/leo_sim/trace.py`（`_rate_multiplier` 302-321、生成 733-770、manifest 871-892）、`CODE/leo_sim/config.py`（83-98、291-293、570-597） |

---

## 1. 机制：两级阶跃速率的非齐次泊松（精确稀释）

```
_rate_multiplier(mode, t, src_lon, dm)                     trace.py:302-307
    if mode in ("burst","mlab") and burst_start_s is not None:
        if start <= t < start + dur:  return burst_multiplier      # B
        return 1.0
生成（精确稀释 / exact thinning）
    total_rate = generation_mbps * 1e6 / bits_per_pkt        trace.py:733
    base_rate  = total_rate * weights[i] / wsum              trace.py:751
    max_mult   = max(1.0, burst_multiplier)                  trace.py:757
    t += exponential(1 / (base_rate * max_mult))             trace.py:766
    if gen.random() > _rate_multiplier(...) / max_mult: continue   trace.py:769
```

瞬时到达率 `λ_i · m(t)`，`m(t) ∈ {1, B}`，突发窗 `[s, s+W)`。

### 1.1 精确代数（**取代 rev 1 的近似式**）

令突发窗在地平线上的**有效占比** `f`（`T_e = emission_end_s`，未设时为 `scenario.duration_s`）：

```
f         = max(0, min(s+W, T_e) − max(s, 0)) / T_e
mean_mult = 1 + (B − 1) · f                  ⇒  平均 offered load = offered_mbps × mean_mult
CV²_int   = (B − 1)² · f · (1 − f) / mean_mult²      （到达间隔的平方变异系数）
```

`f` 依赖 `T_e` 是**关键**：窗口可以因为 `emission_end_s` 被截断而**完全落在观测区间之外**（§4 F4）。

---

## 2. 混淆：只用**一个** burst 旋钮无法归因

由 §1.1，"只动一个旋钮"必然同时改变 `mean_mult` 与 `CV²_int`：

| 旋钮 | 改平均负载？ | 改突发度？ | 判决 |
| --- | --- | --- | --- |
| `burst_multiplier` B | **是**，×`1+(B−1)f` | **是** | **(c) 两者都改 → 混淆** |
| `burst_duration_s` W | **是**，×`1+(B−1)W/T_e` | **是** | **(c) 两者都改 → 混淆** |
| `burst_start_s`（窗口完全在地平线内） | 否 | 否 | 仅相位 |
| `burst_start_s`（被截断）/ `emission_end_s` / `duration_s` | **是** | **是** | **(c) 混淆** |
| `diurnal_amplitude` | **是**（有限地平线） | **是** | **(c) 混淆** |
| `hotspot_concentration` / `hotspot_fraction` | 否 | 仅**空间**集中度 | 保均值，但**非时间**突发 |
| `nested_master_offered_mbps` | 否（子 trace 不变） | 否 | 仅稀释分辨率 |
| `packet_bits` | 否（bits/s 不变） | 否（无量纲）；**但改计数 Fano** | 陷阱 |

**代码中不存在任何 cv / cv² / scv / Hurst / Pareto / on-off 参数化**
（`grep -rniE "burstiness|fano|cv2|scv|coefficient of variation|hurst|self-similar|pareto|on-off" CODE/` → 0）。

---

## 3. 实测证据

### 3.1 单旋钮混淆（**[EXEC]**，T=600 s，`packet_bits=1e5`，窗 [240,360) s，seed 7，`offered=1.0`）

| arm | 实测 Mbps | 预测 `offered×mean_mult` | **CV²** | Fano(1 s) |
| --- | --- | --- | --- | --- |
| uniform（负对照） | 0.9862 | 1.0000 | **1.048** | 1.115 |
| B=2 | **1.1683** | 1.2000 | **1.184** | 2.239 |
| B=4 | **1.5993** | 1.6000 | **1.720** | 9.814 |
| B=8 | **2.3845** | 2.4000 | **2.993** | 33.485 |
| B=0.5 | 0.8880 | 0.9000 | **1.161** | 1.419 |

只改 B：平均负载 **+19% / +60% / +138%**，CV² 同时上升。**混淆成立。**
负对照 CV² = 1.048 ≈ 1，与泊松一致 —— 统计量无系统性偏差。
仅改 W（B=2，W=30/120/300/600）：1.0298 / 1.1683 / 1.4807 / 1.5820（预测 1.05/1.20/1.50/1.60）。
仅改 `burst_start_s`（B=2，W=120，s=0/60/240/480）：1.1642 / 1.1658 / 1.1683 / 1.1828 —— **均值中性**。

### 3.2 ★ Fano 是负载相关的，CV² 不是（rev 2 的核心更正）

Mode B 形状固定（B=4，W=120），只扫 `offered_mbps`：

| offered | 实测 Mbps | **CV²** | Fano(1 s) |
| --- | --- | --- | --- |
| 0.25 | 0.4012 | **1.713** | 3.37 |
| 0.5 | 0.7852 | **1.728** | 5.28 |
| 1.0 | 1.5993 | **1.720** | 9.81 |
| 2.0 | 3.1923 | **1.747** | 19.33 |
| 4.0 | 6.4127 | **1.720** | 37.25 |

**CV² 基本恒定（1.713–1.747，极差 2%），Fano 却从 3.37 涨到 37.25（×11）。**

> → **报告纪律（强制）**：突发度主统计量用 **CV²**（到达间隔平方变异系数）或 `CV²_int`。
> 若必须用 Fano，**必须同时给出分箱宽度，且禁止跨不同负载水平的 arm 比较 Fano 数值。**

### 3.3 Mode A 构造成功（固定平均负载，变突发度）

沿等均值双曲线 `W·(B−1) = C`，并把 `offered_mbps` 反向缩放以抵消 `mean_mult`：

| arm | B | W | offered | 实测 Mbps（4 seed 均值±sd） | **CV²** |
| --- | --- | --- | --- | --- | --- |
| A0（对照 uniform） | 1 | — | — | 0.9961 ± 0.012 | 1.048 |
| A1 | 2 | 300 | 0.6667 | 0.9820 | **1.214** |
| A2 | 4 | 150 | 0.5714 | 0.9938 | **1.828** |
| A3 | 8 | 75 | 0.5333 | 0.9902 | **2.319** |

**平均负载一致到 ±1%，CV² 从 1.21 单调升到 2.32（≈1.9×）。** 对照的混淆 arm 跨 4 seed 为
1.1940 ± 0.019 / 1.6018 ± 0.017 / 2.4033 ± 0.026 —— **混淆幅度远大于噪声**。

### 3.4 rev 1 §2 数字的独立复算（同一结构，不同装置）

| (B,W) | rev 1（我的装置） | E4 独立复算 |
| --- | --- | --- |
| (1,8) | 49.150 Mbps / Fano 0.808 | 48.450 / 1.217 |
| (2,8) | 58.975 / 9.438 | 58.600 / 8.275 |
| (3,8) | 70.100 / 27.160 | 68.625 / 25.646 |
| (2,16) | 69.025 / 10.838 | 69.800 / 10.933 |
| (5,4) | 69.475 / 56.138 | 69.500 / 59.782 |

**结构被独立复现**（等均值三点负载极差 ≈1.7%、Fano 比 ×5.47 vs ×5.18）；
**具体数字是单次运行、装置特定的**（rev 1 的探针装置不在库内），不得当作可引用常数。

---

## 4. 三个新发现的陷阱

| # | 陷阱 | 事实 |
| --- | --- | --- |
| **F4** | **声明了但从未观测的处理** | burst 窗可以完全落在 `emission_end_s` 之外。**[EXEC]** `emission_end_s=20`、窗 `[30,40)` → 实测 0.9800（= 无突发），**而 manifest 仍记录 `traffic_transform.burst`**。`config.py:585-589` 的窗口校验只比对 `scenario.duration_s`，**不比对 `emission_end_s`**。→ 任何 burst 实验必须报告**实测**负载与窗内有效占比 `f`，不能只报配置。 |
| **F5** | **`mode=csv/uniform` 下 burst 旋钮静默失效** | `trace.py:303` 只在 `mode ∈ {burst, mlab}` 时应用。**[EXEC]** csv + `burst_multiplier=5` → `traffic_transform.burst = None`、`load_mode=observed_trace`。本库 **126 份 resolved config 中 55 份**携带 `burst_multiplier` 而其 mode 永不生效（`population_gravity`/`uniform` 等）。 |
| **F6** | **编译路径** | `CODE/experiment_platform/parameter-catalog.json` **不含** V2 的 `demand.burst_*` 键，而 `compile_experiment.py:469-472` 拒绝目录外的因子 → burst 因子**必须**走 V2 matrix 编译器（`compile_matrix_experiment.py`）。 |

**F3 的推论**：由于 `demand.burst_*` 会进入 `trace_identity_sha256`（`config.py:900-917`），
**同一配对内的两个 arm 不能共享 trace identity**；V2 配对靠 `controlled_signature`
（`matrix.py:410-414` 会剥离已声明的 intervention paths）。这不影响合法性，但会影响配对实现方式。

---

## 5. 两个模式（设计定义）

### Mode A —— 固定平均负载，改变突发度

**做法**：令 `C = W·(B − 1)` 恒定（`mean_mult = 1 + C/T_e` 恒定），沿超曲线取点，
并相应缩放 `offered_mbps`。

**arm 骨架**（V2 matrix request，共享 `common_config.demand = {mode: burst, packet_bits: 1e6, burst_start_s: 8}`，
`scenario.duration_s = 40`，`C = W(B−1) = 16` ⇒ `mean_mult = 1.40`）：

| arm | B | W | offered_mbps | 预期平均 Mbps | CV²（预期） |
| --- | --- | --- | --- | --- | --- |
| A0（control） | 1 | 8 | 35.71 | 50 | ≈1 |
| A1 | 3 | 8 | 35.71 | 50 | 中 |
| A2 | 2 | 16 | 35.71 | 50 | 低 |
| A3 | 5 | 4 | 35.71 | 50 | 高 |

（更宽动态范围可用 T=600、`offered=1.0`、C=300 的 `(B,W) = (2,300)/(4,150)/(8,75)` 组合，见 §3.3。）

**每条 A arm 必须声明两条路径**：

```json
{"arm_id": "A1",
 "config_overrides": {"demand": {"burst_multiplier": 3, "burst_duration_s": 8}},
 "intervention_paths": ["demand.burst_multiplier", "demand.burst_duration_s"]}
```

**⚠️ 设计规则（rev 2 更正）**：Mode A 改**两条**参数路径。
- `AGENT_EXPERIMENT_PROTOCOL.md`「设计规则」要求严格设计恰有一个变化因素 →
  **Mode A 必须声明 `one_change_policy = exploratory_multi_factor`，且不得声称单因素因果。**
- **但（rev 2 新增缺陷）**：**V2 matrix 编译器并不机械强制这一条** —— `matrix.py` 内
  `grep "one_change_policy|single_factor|exactly one"` 只命中配对键检查（`:474`/`:500`），
  **没有任何"恰一个变化因素"的机械门**。因此"不得声称单因素因果"目前**只是协议纪律，
  不是可执行门禁**。→ 记为缺陷；在修复前，Mode A 的单因素声明风险由人工复核承担。
- 要求一维化等于要求新增 `demand.burst_cv2` 之类的旋钮 —— 属**新增机制**，任务书明确禁止。

### Mode B —— 固定 burst 形状，改变平均 offered load

**做法**：固定 `burst_start_s` / `burst_duration_s` / `burst_multiplier` **三个字段完全不动**，
只扫 `demand.offered_mbps` → **一条路径 → 严格单因素，可声明**。

**库内既有先例**：`EXPERIMENTS/EXP-20260829-GLOBAL-PRESSURE-BRACKET-R02/request.json`
已经只用 `demand.offered_mbps`（10/20/40/80）扫负载、且共享 `common_config` —— Mode B 的形状已经在库内成立。

**带 burst 的骨架**（T=40，start=8，W=8，B=2 ⇒ `mean_mult=1.20`）：

| arm | offered_mbps | 预期平均 Mbps |
| --- | --- | --- |
| B1 | 25 | 30 |
| B2 | 50 | 60 |
| B3 | 100 | 120 |

建议把 `nested_master_offered_mbps` 固定在最高负载以取得共同随机数（实测子 trace 0.948–1.018、CV² 0.95–1.12）。

> **Mode B 的声明边界**：平均负载与**瞬时峰值率同比例**变化（峰值 ≈ `offered×B`），
> 因此 Mode B 建立的是**负载水平效应**，**不是**突发度效应。
> **并且严禁跨 Mode B arm 比较 Fano**（§3.2）。

---

## 6. 归因矩阵与汇报纪律

| 想要的声明 | 用哪个模式 | 允许？ |
| --- | --- | --- |
| 「固定平均负载下，突发度变化导致 X」 | **Mode A** | ✅ 可运行；声明 `exploratory_multi_factor`，**不得**声称单因素因果 |
| 「负载水平变化导致 X」 | **Mode B** | ✅ 严格单因素，可声明 |
| 「burst 导致 X」（只动 `burst_multiplier`） | 都不是 | ❌ **禁止** —— 实测该旋钮同时改变平均负载（+19%/+60%/+138%） |
| 「突发度与负载的交互」 | A×B 因子设计 | ⚠️ 需显式多因素预注册；当前平台无此模板 |

**汇报纪律（写进论文前必须遵守）**：

1. **报告实测负载，不报告配置值**。burst 开启时 `offered_mbps` **不等于**实际负载
   （§3.1：配置 1.0 vs 实测 1.1683）。从 manifest 的 `offered_bits`/`offered_packets` 反算。
2. **主统计量用 CV²**；用 Fano 必须给出分箱宽度，且**禁止跨负载水平比较**。
3. **报告有效窗占比 `f`**，并显式确认 burst 窗落在 `emission_end_s` 之内（F4）。
4. **Mode A 必须写明"平均负载在 ±x% 内固定"**并给出各 arm 实测值。
5. **不得**用 `t1_pressure_corridor.yaml:69-73` 的 "duty cycle" 措辞描述比特比 —— 与 goodput 利用率混淆
   （见 `PLATFORM-AUDIT-REPORT.md` §2 L6 / §3 B5）。

---

## 7. 与既有测试的关系

- `test_micro_mechanism.py:327` 的 `test_m2_burst_reverses_candidate_order_on_equal_cost_branches`
  **并不使用** `demand.burst_*`：它把手工排定的 50 ms 包列直接注入 kernel（`:335-337`），
  断言的是**路由队列排序**（`:359`/`:363`/`:369`/`:392`）。对混淆而言它是干净的，
  但它**不覆盖真实 burst 旋钮**。真实旋钮的覆盖目前只是元数据回显
  （`test_trace.py:277-281`/`:332`/`:336-339`）与配置门（`test_config.py:78-107`）；
  **没有任何测试断言实测负载、`mean_mult` 或任何突发度统计量**。
- `config.py:570-597` 的 fail-loud 校验（`mode=burst` 必须给出 start/duration；窗口必须与
  `scenario.duration_s` 相交；`burst_multiplier > 0`）**不得放宽**；F4 建议的
  `emission_end_s` 校验属新增门禁，不在本审计授权范围内。
- 本文件**不新增任何代码**。

---

## 8. 复现命令

```bash
cd <repo>   # MAIN_SHA 549acd8b7cb7a1bb573e7146653f55273999086d
python3 /Users/lge/Desktop/topic/leo-runs/probe_burst.py     # §3.4 rev 1 数字
# 完整实测（§3.1–§3.3 的 4-seed 版本）见 /Users/lge/Desktop/topic/leo-runs/audit-notes/E4-burst.md

# 陷阱 F5 复现（csv 下 burst 旋钮静默失效）
grep -n "burst_multiplier" CODE/leo_sim/trace.py            # :303 只在 burst/mlab 生效
grep -rn "burst_multiplier" CODE/leo_sim/config.py          # :291-293 默认 2.0 恒写入 resolved
```
