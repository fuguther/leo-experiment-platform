# TRAFFIC-MODEL-SPEC — 当前业务模型与 M-Lab 数据可声明边界

| 字段 | 值 |
| --- | --- |
| MAIN_SHA | `549acd8b7cb7a1bb573e7146653f55273999086d`（traffic 相关文件在 `fb6b1a6` 复核未变） |
| 日期 | 2026-09-24 |
| 取证 | 逐行读码 + 实跑。凡标 **[EXEC]** 者为实际执行所得，**[READ]** 为读码所得 |
| 权威文件 | `CODE/leo_sim/trace.py`、`trace_family.py`、`population.py`、`config.py`、`scene_check.py`、`rng.py`、`CODE/data/traffic/` |

> 本文件回答一个问题：**当前平台的"业务/流量"到底是什么，M-Lab 数据到底代表什么，因此哪些论文声明成立、哪些不成立。**

---

## 1. 流量模型：只有两族，没有第三族

### 1.1 合成非齐次泊松（默认族）

| 环节 | 公式 | 代码 |
| --- | --- | --- |
| 每小区基率 | `λ_i = (offered_mbps × 1e6 / packet_bits) × w_i / Σw` | `trace.py:733-737`、`750-753` |
| 抽样 | `exponential(1/(λ_i·max_mult))` 然后按 `m(t)/max_mult` 稀释 | `trace.py:765-770` |
| burst 变换 | 阶跃窗口 | `trace.py:302-307` |
| legacy diurnal | `local_h = (t/3600 + lon/15) mod 24` 余弦 | `trace.py:308-312` |
| population diurnal | 增加 `utc_start_hour` | `trace.py:313-320` |
| 目的采样 uniform | 均匀 | `trace.py:253-254` |
| 目的采样 gravity | `P(j\|i) ∝ w_j^γ / max(d, d_floor)^α` | `trace.py:255-272` |
| 目的采样 population | alias + 拒绝采样（精确） | `trace.py:163-185` |
| 目的采样 hotspot | 位置热区集 | `trace.py:273-282` |
| 目的采样 mlab | M-Lab 导出的有向 OD 权重 | `trace.py:283-298` |
| 嵌套 master/child | 主率生成候选，按 `p = offered/master` 独立保留 | `trace.py:699-708`、`785-799` |

**不存在**：流体模型、OD 矩阵对象、实测到达过程回放。

### 1.2 csv 逐包回放（第二族）

`trace.py:588-672`：逐字使用 `packet_id / emit_time_s / bits / deadline_at_s`，**不消耗 RNG**。
**[EXEC]** 回放 `t1_step5_micro_ab.csv` → 6 包，`load_mode=observed_trace`，`packet_id=99` 保持原顺序（可在时间上乱序）。

### 1.3 RNG 缺陷（本次审查新增，可核验）

`rng.py:15-29` 声明 8 个位置化 `SeedSequence` 子流。**实际只消耗 2 个**：

```
[EXEC] grep: 除 rng.py 自身外，ge_gsl / ge_isl / association /
              routing / control / monitor 六个名字在 CODE/ 内零引用
[READ] trace.py:694   gen = generators["demand"]
[READ] trace.py:703   filter_gen = generators["nested_filter"]
[READ] kernel.py:789  rngmod.link_stream(seed, f"isl:{sat}:{direction}")
[READ] kernel.py:2223 rngmod.link_stream(seed, f"gsl:{sat}:{cell}")
```

GE 随机性走的是 `link_stream`（按链路身份 keyed），**不是** `ge_gsl`/`ge_isl` 子流。
因此 `rng.py:1-8` 的 docstring「每个机制从自己的流抽取，因此开关某机制不会扰动其他机制的随机性」
对那六个流**是过度声明**。学习臂另有 `np.random.default_rng(seed)`（`learning.py:524`、`985`），
不属于这 8 个流。

**同种子 ⇒ 同一次 run：已由执行验证。** 同一 config 跑两次 `diff -r` 全部产物逐字节相同。
但要复现必须同时钉死：`scenario.seed` 与 `learning.seed`、`demand`/`endpoints` 身份载荷
（`config.py:900-917`、`974-980`）、trace 输入字节、`code_sha256` 与 numpy 版本，
以及 `geometry_epoch_s`（**被刻意排除在 trace identity 之外**，`config.py:942-943`）。

---

## 2. M-Lab 数据到底代表什么

### 2.1 事实

| 项 | 值 | 来源 |
| --- | --- | --- |
| 文件 | `CODE/data/traffic/mlab_2026-05-27.csv`，路径是**硬编码常量** | `trace.py:34` |
| 列 | `client_city, client_lat, client_lon, server_city, server_lat, server_lon, hour_utc, sample_count, mean_throughput_mbps` | **[EXEC]** `head -1` |
| 行数 | 44 929（不含表头） | **[EXEC]** `wc -l` |
| 粒度 | **逐小时聚合**，小时 0–23 | **[EXEC]** |
| 空间不对称 | 18 765 个 client 坐标 → **75** 个 server 坐标 | **[EXEC]** |
| 权重 | `W(s,d) = Σ mean_throughput_mbps × sample_count` | `trace.py:373` |
| 语义 | M-Lab 是 **Speedtest 式 client→server 测量**，不是端到端 OD 需求矩阵 | `README.md` |

### 2.2 仓库自身的声明（与代码互相印证）

`CODE/data/traffic/README.md:3-7` 原文：

> "`mlab_2026-05-27.csv` is a checked-in M-Lab-derived **measurement proxy**. It is
> an hourly city-to-city summary, **not a packet capture and not a calibrated user
> demand trace**. … it does not claim that these measurements are the offered load
> of a real satellite operator."

`README.md:22-23`：**"Raw client identifiers and packet-level records are not present."**

**并且在 receipt 层强制**（`receipt.py:334-340`）：`mode == "mlab"` 时，
manifest 必须带 `not_calibrated_user_demand: True` 与逐字固定的 `provenance_note`，
否则 receipt 校验**失败**。即该免责声明是被机器强制的，不是仅写在文档里。

---

## 3. 结论：可以声明 / 不能声明

### 3.1 可以用于论文 claim ✅

1. 「M-Lab 导出的**逐小时城市对吞吐/计数聚合**，仅作为**有向 OD 权重先验**」
   （44 929 行；18 765 client 坐标 → 75 server 坐标；UTC 0–23；`trace.py:373`、`283-298`）。
2. 「offered load 是按声明 `offered_mbps` 生成的**合成非齐次泊松过程**，
   叠加的 burst/diurnal 是**显式记录在 manifest 里的变换**」（`trace.py:733-770`、`302-321`）。
3. 「生成的逐包 trace 是**合成的、不可变的、内容寻址的、可逐字节复现的**」
   （`trace.py:827-834`；`diff -r` 逐字节相同）。
4. 「逐包时间性是**模型输出**」，标签为 `measurement_proxy` 并带机器强制的
   `not_calibrated_user_demand` 免责（`trace.py:945-950`、`receipt.py:334-340`）。
5. 分析粒度：**per-packet / per-link / per-satellite / per-cell**（`scene_check.py:852-865`）。

### 3.2 只能作为工程能力（不能作为科学 claim）⚠️

- 同种子可复现、trace 内容寻址、编译产物 sha256 绑定 —— 是**工程可复现性能力**，
  不等于「模型已被真实数据校准」。
- csv 逐包回放通道存在且忠实，但**库里唯一被引用的回放文件是自造的微场景 trace**，
  不是外部真实测量。

### 3.3 当前不能使用 ❌

| 不能声明 | 依据 |
| --- | --- |
| **「真实逐包时间序列」** | 仓库内**不存在**任何逐包时间记录：`git ls-files` 无 pcap/parquet/h5；9 列全是小时聚合；`README.md:22-23` 明文否认 |
| 端到端 / 城际 **OD 需求矩阵** | 18 765 : 75 的 client→server 不对称，是 Speedtest 式测量；`mlab_auto` 只保留 56/2604 小区（`trace.py:459-469`） |
| **实测** burst 或 diurnal profile | `README.md:14-17`：「measured OD weights plus a reproducible stress transform, **not a measured burst**」 |
| 「已校准的运营商 offered load」 | `trace.py:947-950`、`receipt.py:335-340` |
| 「offered load 等于 `offered_mbps`」（burst/diurnal 开启时） | **[EXEC]** `mlab_multiod_burst_t0`：目标 50.0 Mbps，实测 64.95 Mbps（burst 8s/20s ×2，理论上界 70） |
| **flow-level / 每流结果** | `grep -rn "flow_id\|flowid" CODE/` = **0**；`DataPacket` 全字段 per-packet（`kernel.py:67-97`）；ledger 按 `pid` |
| 「经当前 lane 对 M-Lab 场景做 scene-check 分类」 | `scene_check.py:278-291` 硬要求 `provenance == "population_proxy"` 且 `temporal_model == "local_diurnal_cosine"`；`:250-251`、`:919` 硬编码 `scope: global_populated_land` |

---

## 4. 流量域的研究可用性阻塞项

**T3（最高影响）**：`scene_check.verify_decision_contract` 硬绑定
`traffic.provenance == "population_proxy"` 与 `temporal_model == "local_diurnal_cosine"`
（`scene_check.py:278-291`），而 `scope` 硬编码 `global_populated_land`（`:250-251`、`:919`）。
库里唯一入库的 scene-check lane（`CODE/work/WP-LEO-V2-GLOBAL-PRESSURE-BRACKET/R01/scene-decision.yaml:11-13`）
正好满足这对耦合 —— 也就是说**当前唯一能通过场景检查的业务配置是被硬编码允许的那一种**，
M-Lab 场景或合成场景**无法**通过该 lane 获得分类通过。

→ 后果：把 M-Lab 场景用于正式实验时，scene-check 这一层要么不可用，要么必须换 profile；
在修复前**不得**声明「本平台的场景合法性门覆盖了 M-Lab 业务模型」。

次要项（记录，不阻塞）：
- `CODE/data/traffic/` 内 `mlab_sample.csv`、`diurnal_mlab_2026-05-27.json`、
  `diurnal_smoke_sinusoidal.json` **零代码引用**（孤儿 legacy 夹具），
  且 `diurnal_mlab_2026-05-27.json:2` 记录了一个本库不存在的
  `DiurnalSchedule.from_mlab_csv()` 与「回退到正弦默认」的旧语义 —— 误引风险最高。
- `demand.mode` 缺省静默解析为 `"uniform"`（`config.py:278`）：是有文档的默认值且计入
  resolved config 哈希，属清单风险而非 fail-loud 违规。
- `_dst_choices` 保留 `return others[-1]` 兜底（`trace.py:272`、`:298`），
  比精确 population 采样器（`:163-166`、`:183-185` 明确禁止 "last"）宽松 —— 可达性 **UNDETERMINED**。
- `load_trace` 默认 `horizon=inf`、`max_packets=2^62`（`trace.py:1046-1049`），
  `scene_check.py:347` 以无界方式调用。
- Web UI（`experiment_platform/app.js:68`）渲染的是 **legacy** `parameter-catalog.json`，
  含 `traffic.hourly` 等 V2 无对应物的参数 —— 文档口径正确
  （`experiment_platform/AGENT_EXPERIMENT_PROTOCOL.md:25-31`），但 GUI 会误导。

---

## 5. 复现命令

```bash
cd <repo>   # MAIN_SHA 549acd8b7cb7a1bb573e7146653f55273999086d
head -1 CODE/data/traffic/mlab_2026-05-27.csv && wc -l CODE/data/traffic/mlab_2026-05-27.csv
grep -rn "flow_id\|flowid" CODE/ | wc -l                       # -> 0
sed -n '690,710p' CODE/leo_sim/trace.py                        # RNG 实际消耗的两条流
sed -n '1,25p' CODE/data/traffic/README.md                     # 数据自身声明
grep -n "not_calibrated_user_demand" -A 6 CODE/leo_sim/receipt.py
python3 -m CODE.leo_sim trace compile --config CODE/leo_sim/profiles/smoke.yaml --out /tmp/e2_smoke
```
