# 正式实验前可信性缺口修复 · 验收报告

| 字段 | 值 |
|---|---|
| 工作包 | `CODE/work/WP-LEO-V2-PRE-EXPERIMENT-TRUST/` |
| 日期 | 2026-09-25 |
| 起点 HEAD | `98c858f642aeca8056d215224b20783b00354026`（分支 `chore/retire-dead-lines-q0-tiny`） |
| 隔离基线 | worktree `/Users/lge/Desktop/topic/leo-prefix-check`（detached `98c858f` + 开工前未提交改动，6 文件 sha256 逐一核对一致） |
| 本工作包改动 | 7 个已跟踪文件（+515/−39）+ 3 个新代码/测试文件 + 本工作包；**未提交、未推送、未建 PR** |
| 是否启动正式实验 | **否**（见 §7 放行表） |

> 本轮只修实验可信性。**未训练模型、未运行正式研究实验、未据此判断流量调度与分布式逐包路由哪条更优。**

---

## 0. 开工核对：未提交改动的影响面（未覆盖、未清理、未忽略）

进入本轮时工作树携带 6 个已修改文件与 4 组未跟踪材料。开工前逐一记录 sha256；本轮**只读、不动**其中任何一项，并在此写明它们与本任务的关系：

| 未提交项 | 是否影响本任务 | 处理 |
|---|---|---|
| `CODE/leo_sim/__main__.py`（空决策流 V6→V5 降级） | **是（间接）**：它决定了 §6 中"V5 回执 + 无条件流校验"互斥缺陷的存在 | 未回退；本轮的 `recompute` 子命令追加在同一文件，hunk 不重叠 |
| `CODE/leo_sim/receipt.py`（verify 非 dict 守卫） | 否 | 未触碰 |
| `test_pairing_trace_identity.py`、`test_review_residuals.py`（测量数字与正则修正） | 否 | 未触碰；两文件在本轮全量测试中通过 |
| `CODE/scripts/remote/remote_job.py`、`pull-results-remote.sh`（正式链路改动） | **是**：§6 的盘点对象 | **未修改**；缺陷以"未应用的补丁提案"形式给出 |
| `CODE/leo_sim/profiles/{linkgen_generic_rf_mcs_1g,linkgen_published_optical_100g,t1_pressure_corridor_v2}.yaml` | 否 | 未触碰 |
| `ANALYSIS/ARRIVAL-TIME-T1-20260923/`、`CODE/work/WP-LEO-V2-T1-TRUST-CHAIN-V6/round3/` | 否（唯一研究材料） | 未触碰；未纳入基线 worktree，也不依赖 |

**隔离基线如何建立**：`git worktree add --detach <dir> 98c858f`，再 `git apply` 一份**只含开工前 6 个文件**的补丁（`baseline-uncommitted.diff`，见本目录）。落地后 6 个文件 sha256 与开工记录**逐一相同**。所有"修改前"证据都在该 worktree 内产生，因此 before 不是对本轮代码的推测，而是可复算的实测。

**冲突清单：无。** 6 个未提交文件里唯一与会话改动重叠的是 `__main__.py`：本轮改动落在 `_cmd_recompute` 与子命令注册处，开工改动落在 `_cmd_run` 的空决策分支，无同一 hunk。

---

## 1. 最小改动清单

`minimal-change.diff`（本目录）= 隔离基线与当前工作树的逐文件 `diff -u`，**只含本轮改动**。

| 文件 | +/- | 改了什么 |
|---|---|---|
| `CODE/leo_sim/config.py` | +32/−7 | burst 声明在非 burst/mlab 模式下拒绝；burst 窗口必须整体落在**实际发包窗口** `[0, emission_end]` 内 |
| `CODE/leo_sim/trace.py` | +133/−6 | 新增 `materialization_report()`：对**写盘前**的逐包行机械核对包长/注入量/源目的地/发包窗口/突发落点，违规即 `TraceError` |
| `CODE/experiment_platform/primary_metrics.py` | 新 +62 | 主指标词表单一真源（编译期与分析器共用） |
| `CODE/experiment_platform/v2_analysis.py` | +11/−0 | 分析器入口按同一词表做前置判定 |
| `CODE/leo_sim/matrix.py` | +214/−10 | 编译期：主指标必须在词表内；利用率类主指标必须已配置容量采样；新增可选 `design` 块，按**已解析 cell** 机械核对单因素声明；`design_accounting` 仅在声明时追加键 |
| `CODE/leo_sim/metrics_independent.py` | +29/−9 | 链路无可用容量覆盖时不再发布 `status=OK / utilization=0.0`，改为 `DEGENERATE_DENOMINATOR / None`（有服务量时仍硬报错） |
| `CODE/leo_sim/recompute.py` | 新 +283 | 持久化 run 目录 → 独立重算的可运行入口，含 F2 混淆与分母缺失两道 fail-loud |
| `CODE/leo_sim/__main__.py` | +28/−1 | `python3 -m CODE.leo_sim recompute --run-dir DIR` |
| `CODE/leo_sim/tests/test_config.py` | +68/−6 | 两条旧断言（"必须相交"）升级为更强的"必须落在发包窗口内"，并补两条新反例 |
| `CODE/leo_sim/tests/test_pre_experiment_trust.py` | 新 +455 | 13 条验收测试（见 §2） |

**刻意未做**：未改 `metrics.py`（生产数值不动，见 §5）、未改 manifest/receipt 键集（见 §5）、未改远端链路文件（见 §6）、未删任何测试、未放宽任何 tolerance。

---

## 2. 证据怎么读

- `evidence/pre-fix-new-tests.txt`：新测试在**隔离基线**上的结果 —— **11 failed, 2 passed**。
- `after.json` / `evidence/before-isolated.json`：同一支探针脚本在两棵树上的完整输出。
- `probe_pre_experiment_trust.py`：11 个探针，只调用公开入口，同一份脚本在两棵树上都能跑。
- `release-table.json`：§7 放行表的机器可读版本（14 项，唯一 FAIL = R11）。
- `evidence/run-dirs/{sampled-run,unsampled-run}`：探针 D1/D3 实际读取的持久化 run 目录，可直接复跑
  `python3 -m CODE.leo_sim recompute --run-dir <dir>`（sampled → exit 0 / 均值 6.0e-4；unsampled → exit 10 / 均值 null）。
- `PROPOSED-remote_job-stream-binding.patch`：§6.2 的提案，**未应用**（已确认可 apply、可编译）。
- 全量测试：基线 **940 passed / 1 skipped / 0 failed**；本轮后 **955 passed / 1 skipped / 0 failed**。

那 2 个"两边都过"的测试是**兼容性控制**，本来就该如此：`test_a_request_without_a_design_block_derives_exactly_what_it_did_before`，以及词表一致性测试（只依赖纯数据模块，隔离基线上临时补入该模块才能收集用例）。

---

## 3. 四项逐条

### 3.1 输入落实核验

**问题**：manifest 记录的是**声明**（目标负载、突发窗口、包长），此前没有任何东西把它与**实际写出的行**比对。

| 探针 | 修改前实测 | 修改后实测 |
|---|---|---|
| A1 `demand.mode=uniform` + 声明 burst | `resolve_config` 通过；manifest `traffic_transform.burst = null`；无任何诊断 | `ConfigError: ... never applies the burst transform` |
| A2 `emission_end_s=20`、窗口 `[30,40)`、`duration_s=60` | 通过；**100 个包，窗内 0 个**；realized = target = 40.0 Mbps（等价纯基线） | `ConfigError: ... must lie inside the emission window [0, 20.0]` |
| A3 `emission_end_s=20`、窗口 `[15,25)` | 通过；窗内 123 个包，声明窗口的 `[20,25)` 段**永不可达** | `ConfigError: ... a window that extends past it is only partially applied` |
| 新增 `materialization_report()` | 不存在 | 对包长、自环、越界 endpoint、越窗发包、**窗内零包**逐条 `TraceError`；拒绝时不落任何产物（`trace.csv` / `manifest.json` 均不存在） |

**正向对照**：`test_a_real_burst_trace_reports_where_its_packets_actually_land` 要求真 burst 的窗内包数 > 0，证明该检查非空洞。

**仍未解决的限制**
- 生成器是**随机稀疏过程**：窗内 ≥1 个包是必要条件，不是"突发强度等于配置倍数"的充分证据。realized/target 比值仍只写进 manifest，不构成断言（finding L3 不变）。
- `mode=csv` 的包长来自输入文件，不参与"包长与声明一致"检查（那里的声明就是输入哈希）。
- 只校验源/目的落在已解析 endpoint 集合内，**不**校验几何可达性。

### 3.2 编译前拦截无效实验

**问题**：编译期只要求 `primary_metric` 是**非空字符串**（`matrix.py:181`），与分析器白名单零交叉校验。

| 探针 | 修改前实测 | 修改后实测 |
|---|---|---|
| B1 `primary_metric=p95_e2e_latency`（catalog 标 `eligible_as_primary: true`） | 编译 `COMPILED_REVIEW_REQUIRED`、`errors=[]`、`execution_authorized=false`；随后分析器抛 `unsupported V2 primary metric` | `MatrixError: ... is not one the V2 analyzer can compute; supported: [...]` |
| B2 `primary_metric=link_utilization_mean` 且 `available_capacity_interval_s=null` | 编译 `errors=[]`；两臂 resolved 均为 null | `MatrixError: ... is a link-utilization ratio, which does not exist unless available capacity is sampled` |
| B3 两因素干预 + 声称"单因素" | **无处可声明**：V2 请求契约顶层键白名单直接拒绝 `design`；不声明时两因素干预 `errors=[]` 静默编译 | 声明 `one_change_policy=strict` 时按**已解析 cell** 计数并拒绝；`exploratory_multi_factor` 则如实记录实际变化路径 |

**单因素机械核对的定义**（`matrix._design_check`）：对每条 `planned_contrast`，取两臂**已解析配置**的叶子路径差集，去掉 `STOCHASTIC_IDENTITY_PATHS`（重放身份，与 `controlled_signature` 同口径）；strict 要求该集合恰等于 `factor_changed`。因此 **cell 级 override 也被计入**；而 cell 想引入非随机身份路径时会先被既有契约拒绝（`undeclared intervention path`），两道门互补。

**仍未解决的限制**
- 词表**只有 14 个**主指标；catalog 里被标 `eligible_as_primary` 的 10 个中多数仍不可作主指标。本轮**不新增**分析能力，只把"能不能算"变成编译期事实。`test_the_canonical_vocabulary_matches_what_the_analyzer_dispatches` 逐条驱动分析器，词表与分析器不能各说各话。
- 容量采样只校验**非 null 且在配置合法域内**；不校验采样步长与被测窗口是否匹配（例如窗口 20 ms 却按 1 s 采样）。该量化风险仍 open。
- `design` 是**可选**字段：不声明就不检查。不声明时编译产物不会声称单因素，但也**不会主动警告**多因素 —— 这是为兼容历史产物付出的代价（见 §5C）。

### 3.3 关键指标独立核验

**问题**：为抓伪造分母而建的 `metrics_independent` 自己伪造了一个分母；且没有任何平台入口能从持久化产物跑独立重算。

| 探针 | 修改前实测 | 修改后实测 |
|---|---|---|
| C1（可用性只覆盖 `isl:0:1`；`isl:1:2` 只有一条 stalled 服务窗） | `isl:1:2` → `status=OK`、`utilization=0.0`、`available_capacity_bits=0.0`、`available_samples=0` —— **分母根本不存在却发布了数值**，且生产实现给出同样的 0.0，交叉校验对这一情形失明 | `status=DEGENERATE_DENOMINATOR`、`utilization=null`、`available_capacity_bits=null`；有服务量却无覆盖的链路仍硬报错（原 `test_negative_served_link_without_denominator_coverage_is_rejected` 保持通过） |
| C2（`available_capacity_interval_s=null` 的真实 run） | 生产读数 `utilization=0.9999999999999992`、`available_samples=0` | **不变**（刻意；见 §5） |
| D1 `python3 -m CODE.leo_sim recompute --run-dir <C2>` | `error: argument cmd: invalid choice: 'recompute'`（能力不存在） | exit 10；`link_utilization_mean = null`、`status=DEGENERATE_DENOMINATOR`、R2=false。**拒绝把 0/1 当测量发布** |
| D2 配置启用 F2 但 run 目录无 timeline span | 同上（命令不存在） | exit 10，`RECOMPUTE REFUSED: execution.node_process_delay_s > 0 requires the timeline stream...` |
| D3（正对照：`available_capacity_interval_s=0.1` 的同类 run） | 命令不存在 | exit 0，R1–R6 全 true，`link_utilization_mean=6.0e-4`，与生产读数 **0 处不一致** |

**独立重算入口读什么、拒绝什么**（`CODE/leo_sim/recompute.py`）：只读持久化的 `ledgers.json`（逐包事件 + 链路服务窗 + 可用性窗）、`resolved_config.json`、`receipt.json`、`timeline.jsonl`；调用 `metrics_independent`（该模块用 AST 守卫禁止引用生产 `metrics`）；与 `ledgers.congestion_metrics` 的**数据**逐字段对账；timeline 在场时同时给出"标注/未标注"两个 `decision_compute_s`，把 F2 泄漏量写成显式数字。

**仍未解决的限制**
- 生产实现 `metrics.py:334-340` 的分母回退**未改**：老 run 的 `utilization≈1.0` 仍是历史事实。本轮的补救是**不让它成为主指标**（编译期）与**不让它变成独立读数**（recompute），不是改写历史。
- `recompute` 只覆盖"利用率 + 时延分解"，不覆盖 receipt 全部字段；它自己也**不是**收据校验，报告里带 `scope` 字段说明这一点。
- R6（流哈希与回执绑定一致）只对 v6 回执生效；v5 回执没有可比的流身份。

### 3.4 正式运行链路盘点

见 §6。结论：**未通过**。

---

## 4. 三个必需反例（修改前 → 修改后）

### 反例一：配置了突发却未发生

```text
配置  scenario.duration_s=60, demand.mode=burst, emission_end_s=20,
      burst_start_s=30, burst_duration_s=10, burst_multiplier=5, offered_mbps=40
改前  resolve_config -> OK ; compile_trace -> OK
      manifest.provenance_contract.traffic_transform.burst
        = {"start_s": 30.0, "duration_s": 10.0, "multiplier": 5.0}
      offered_packets = 100 ; packets_inside_burst_window = 0
      realized_offered_mbps = 40.0  == target_offered_mbps = 40.0   # 等价纯基线
      -> 回执仍然宣称这是一次 burst 实验
改后  ConfigError: burst window [burst_start_s, burst_start_s+burst_duration_s)
      = [30.0, 40.0) must lie inside the emission window [0, 20.0]
      (demand.emission_end_s, or scenario.duration_s when unset);
      a window that extends past it is only partially applied
```

复现：探针 A2（`after.json` / `evidence/before-isolated.json`）。
测试：`test_burst_window_is_measured_against_the_emission_end_not_the_horizon`、`test_a_burst_window_under_a_mode_that_ignores_it_is_refused`、`test_a_declared_burst_that_no_packet_observes_is_refused_by_compile`。

### 反例二：主指标运行完才报不支持

```text
配置  leo_sim_v2 matrix 请求, analysis.primary_metric = "p95_e2e_latency"
      （该 id 在 metric-catalog.json 中且 eligible_as_primary = true）
改前  compile-report.json: status=COMPILED_REVIEW_REQUIRED, errors=[],
      execution_authorized=false   -> 可以走完审阅/授权/部署/全部 cell
      之后才: V2AnalysisError: unsupported V2 primary metric: p95_e2e_latency
改后  MatrixError: analysis.primary_metric 'p95_e2e_latency' is not one the V2
      analyzer can compute; supported: [... 14 ids ...]
```

复现：探针 B1。测试：`test_a_primary_metric_the_analyzer_cannot_compute_never_compiles`、`test_the_canonical_vocabulary_matches_what_the_analyzer_dispatches`。
量化背景：catalog 23 个 id 中分析器只接受 3 个；catalog 自己把其中 10 个标为可作主指标。

### 反例三：容量分母缺失却产出有效利用率

```text
配置  可用性窗口只覆盖 isl:0:1；isl:1:2 有一条 stalled 服务窗（served_bits = 0）
改前  metrics_independent.recompute_link_utilization ->
        isl:0:1: status=OK, utilization=1.0,  available_capacity_bits=1000.0
        isl:1:2: status=OK, utilization=0.0,  available_capacity_bits=0.0,
                 available_samples=0        # 分母不存在, 却发布了 0.0
      生产实现给出同样的 0.0 -> 两个实现"一致"，交叉校验对这一情形失明
      另一条路径: available_capacity_interval_s=null 的真实 run ->
        production utilization = 0.9999999999999992, available_samples = 0
        而矩阵编译期对 primary_metric=link_utilization_mean 一声不响
改后  isl:1:2: status=DEGENERATE_DENOMINATOR, utilization=null,
               available_capacity_bits=null
      matrix: MatrixError ... does not exist unless available capacity is sampled
      recompute: link_utilization_mean = null, ok=false (R2)
      生产实现数值不变（历史回执不被改写）
```

复现：探针 C1 / C2 / B2 / D1。测试：`test_a_link_without_a_denominator_publishes_no_utilization`、`test_recompute_publishes_utilization_only_with_a_denominator`、`test_a_utilization_endpoint_without_capacity_sampling_never_compiles`。

---

## 5. 版本兼容方案（不得静默改写历史结果）

本轮每一处改动都按"**新 run 更强，旧回执原样可验**"设计，且有实测支撑。

**A. 数值与语义：生产实现零改动。**
`metrics.py` 未触碰。实测：同一 run 在改前/改后 `congestion_metrics.links` 逐字段相同（探针 C2：`utilization=0.9999999999999992`、`available_samples=0` 两边一致）。**没有任何历史回执的数值或语义被改写。**

**B. manifest / receipt 键集不动。**
`receipt._validate_manifest` 对 manifest 顶层键做**闭集**校验，`provenance_contract` 同样是闭集。因此材料化核对**刻意不写进 manifest**，只作为编译期 fail-loud 门 + 可被测试与探针调用的纯函数 `trace.materialization_report()`。新增的 `recompute` 报告是**新文件**，不进任何既有 schema。

**C. 派生文档只在声明了新字段时才变化。**
`matrix._design_accounting(rows, design_check=None)` 仅在请求带 `design` 时追加 `one_change_policy_check` 键；`_render_runbook` 同理。**不带 `design` 的请求**（包括库内全部已编译矩阵与 `CODE/work/WP-LEO-V2-E0-LOAD-FORMAL` 的两份 request-source）派生的 `design_accounting` / `RUNBOOK.md` / `analysis-request.json` **逐字节不变**，`verify_compiled_matrix` 的重算路径因此对历史产物仍然 PASS。已由 `test_a_request_without_a_design_block_derives_exactly_what_it_did_before` 与 `test_matrix_contract.py` 全量通过钉死。

**D. 拒绝性变化会被看见，不会被吞掉。**
新增的三类 `MatrixError` / `ConfigError` / `TraceError` 都是**编译期**拒绝：影响的是"下一个实验能否开工"，不是"上一个实验的结论是什么"。且被拒绝的配置在库内并不存在 —— 库内全部 burst 声明均为 `mode: mlab`，全部窗口都在发包窗口内，全部 `burst_multiplier = 2`。

**E. 必须提前声明的爆炸半径：`code_sha256` 漂移。**
`receipt.code_sha256()` 覆盖 `CODE/leo_sim/*.py`（非递归；`receipt.py:160-167`）。实测三个身份：

| 树 | code_sha256 |
|---|---|
| 提交态 `98c858f` 干净树 | `2a114890e8b11e429fec45a6b1efcb81c1d80a8e195a9c9407a250d218f16dbe` |
| `98c858f` + 开工前未提交改动 | `50f4c17b41850f254ea915356392e48cd25ae97c10558d8d510eca8f7a938a0a` |
| 再加本轮改动 | `6acc2c127215396e81c602fd13cacdfc286238a301cbc786e13bd7031ab149e7` |

**含义**：任何 `CODE/leo_sim/*.py` 改动都会作废该 checkout 上全部已编译矩阵与已发授权（finding B2）。这不是本轮引入的 —— **开工前的未提交改动已经发生了这件事**；本轮只是把它又推进一步。因此顺序纪律必须重述：**冻结 `CODE/leo_sim` → 编译 → 审阅 → 授权 → 运行**。"改了再补审"与"审了再改"在哈希绑定下互斥。

**F. 若要把拒绝性下沉到历史产物**（例如回填式重编译老矩阵）：**本轮不做**，也不建议在本轮做。那需要显式的版本迁移记录 + 冷启动复核，属独立任务。

---

## 6. 正式运行链路盘点（未通过）

盘点对象是开工前就存在的两处未提交改动。**本轮未修改它们**，只做机械核查。

### 6.1 已核对为正确的部分

| 项 | 结论 |
|---|---|
| `remote_job.formal_command` 是否为 V2 正式 run 请求两条证据流 | **是**：`--decision-log <run>/decisions.jsonl`、`--timeline-log <run>/timeline.jsonl`（`remote_job.py:328-337`），且 `--out` 目录先建为空 |
| 流是否随 run 目录走 | **是**：两条流与其侧车 manifest 都在 run 目录内，`pull-results-remote.sh` 新增的 4 个条目与 `__main__._decision_manifest_target / _timeline_manifest_target` 的命名**逐字一致** |
| 拉回脚本对旧 run 是否安全 | **是**：`add_file` 对不存在/符号链接文件静默跳过（`:208-213`），legacy Gateway run 不会因此报错 |

### 6.2 缺陷（附实测复现）

**F4-1（阻塞）两处未提交改动互相矛盾：0 决策行的正式 run 会在 VM 上被判失败。**

```text
配置  micro fixture（csv 6 包）+ available_capacity_interval_s=0.1,
      compute_delay_s=0, node_process_delay_s=0
命令  python3 -m CODE.leo_sim run --config … --out OUT \
        --decision-log OUT/decisions.jsonl --timeline-log OUT/timeline.jsonl
实测  exit 0
      decisions.jsonl 行数 = 0     （该配置不产生任何契约合法决策行）
      timeline.jsonl  行数 = 36
      receipt.json schema = leo-sim-receipt/v5    （空决策流 -> 降级，这是开工前
                                                   __main__.py 改动的既定语义）
      verify_receipt_dir(OUT)                                 -> []            # 通过
      verify_receipt_dir(OUT, decision_log=…, timeline_log=…) -> ["stream paths
        were supplied but this receipt is leo-sim-receipt/v5, which carries no
        stream binding"]                                                   # 失败
```

`remote_job.py:697-700` **无条件**把两条流交给 `verify_receipt_dir`。于是：一个合法、exit 0、bare verify 通过的正式 run，会因为"回执是 v5"被判 `failure_stage=receipt_verification`、`rc=2`。两个改动各自都合理，合起来把 fail-loud 变成了 false negative —— 这类门在真实运行压力下最容易被绕过。

**提案（已生成、已确认可应用可编译、本轮未应用）**：`PROPOSED-remote_job-stream-binding.patch` —— 先读回执，**仅当 schema 是 v6 时**交付两条流；同时去掉重复的那次 `receipt.json` 读取。为什么只是提案而不是本轮修掉：(a) 改动落在授权/部署链上，按平台自身规则需独立冷启动复核；(b) 本环境没有 VM，改完无法验证真实路径；(c) 它属于他人在飞的未提交工作。按任务书"无法安全纳入时停止修改，提交冲突清单"，此处提交冲突清单而非改动。

**F4-2（缺口）拉回之后没有本地哈希重验。**

`verify_receipt_dir` 的全部非测试调用点里，只有 `remote_job.py:697` 传了流路径 —— 那是在 **VM 上、run 刚结束时**。`pull-results-remote.sh` 只负责把文件塞进 tar（420 行里没有任何哈希校验），`v2_analysis` 走的是 `_verify_receipt_dir_artifacts_unbound`（不带流）。因此：**流被拉回来了，但没有任何本地步骤把回执里记录的 `decision_log_sha256` / `timeline_log_sha256` 与拉回的文件重新比对**。一旦 VM 那次检查被跳过（或拉回不完整），本地没有任何独立复核能发现。本轮新增的 `recompute` 的 **R6** 正是补这一块，但它只覆盖 `recompute` 这一条路径，且不构成链路验收。

**F4-3（缺口）两处改动没有任何测试覆盖。**

在 `CODE/experiment_platform/tests/test_remote_launcher_environment.py` 与 `CODE/tests/test_remote_gate.py` 中，`grep -n "decision_log\|timeline_log\|jsonl\|verify_receipt_dir"` **零命中**。F4-1 就是这个缺口的直接产物。

### 6.3 缺失的独立复核与 VM 证据（明确清单）

| # | 缺什么 | 为什么不能由本轮补 |
|---|---|---|
| 1 | 至少一份**真实 VM 正式 run 回执**（`governance_receipt.json` + 外部启动证人 + 两条流） | 本环境无 VM；本地"预演"不能代替 |
| 2 | 端到端 VM 实测：部署 → 授权 → 运行 → 回执 → 拉回 → 本地重验 | 同上 |
| 3 | 对 `remote_job.py` / `pull-results-remote.sh` 改动的**独立冷启动复核** | 属授权/部署链，需独立审阅者 |
| 4 | 拉回后的**本地**哈希重验步骤（F4-2） | 需先定"谁来验、在哪一步失败" |
| 5 | `code_sha256` 漂移下的授权重签流程（§5E） | finding B2，独立任务 |

**结论：不得报告"正式链路已打通"。** 本轮可声明范围仅限：入口层两处改动**在静态上自洽**（F4-1 除外），以及"本地可以独立重算一份已拉回的 run"。

### 6.4 候选到达时刻状态 O3（后续工作，本轮不触碰）

按 `ANALYSIS/ARRIVAL-TIME-T1-20260923/06-decision-gate.md` §9.2 与 `08-recording-gap-proposal.md`：**扩展 C（各候选到达时刻真值）未实现**，O3 的**可用性通道**（inf → 有限）因此未被完全排除。该能力**单列为后续工作**。本轮：

- 未实现、未假装实现、未用任何替代信号顶替；
- **未**为了让 O3 相关对照"看起来完整"而注入未来信息；
- 也**未**据此对 H1 的既有阴性判定做任何改判（该判定不在本轮范围）。

---

## 7. 平台放行表

判定口径：**通过** = 有本轮实测的修改前失败样例 + 修改后实测结果 + 已知边界；**未通过** = 缺独立复核或 VM 证据，或本就不属于本轮可达成范围。**不以单元测试通过替代实测。**

| # | 放行项 | 判定 | 依据 | 仍存在的限制 |
|---|---|---|---|---|
| R1 | trace 生成后机械核对包长/注入量/源目的地/发包窗口 | **通过** | §3.1；探针 A1–A3、C 组；`materialization_report` 正/负对照 | realized/target 比值非断言；csv 包长不参与 |
| R2 | 不支持突发的模式必须拒绝或显式标不生效 | **通过** | A1；`test_a_burst_window_under_a_mode_that_ignores_it_is_refused` | 仅覆盖 burst 变换；其他"声明了但模式忽略"的旋钮未逐一排查 |
| R3 | 突发窗口必须落在实际发包窗口内 | **通过** | A2/A3；两条升级后的 config 测试 | 未检查窗内**强度**是否等于声明倍数 |
| R4 | 主指标必须是分析器真正支持的指标（编译前） | **通过** | B1；词表一致性测试逐条驱动分析器 | 词表仅 14 个；catalog 的 10 个"可作主指标"多数仍不可用 |
| R5 | 需要链路利用率时必须先配置容量采样（编译前） | **通过** | B2；`test_a_utilization_endpoint_without_capacity_sampling_never_compiles` | 不校验采样步长与窗口匹配 |
| R6 | "单因素"声明必须机械核对实际改变因素 | **通过（可选门）** | B3；`_design_check` 按已解析 cell 计数 | 不声明即不检查；不主动警告多因素（兼容约束所致） |
| R7 | 容量分母缺失不得输出 0 或 1 利用率 | **通过（独立实现）** | C1；`test_a_link_without_a_denominator_publishes_no_utilization` | 生产 `metrics.py` 的分母回退未改（历史数值不动） |
| R8 | 用持久化逐包/链路事件独立重算关键结果 | **通过** | D1/D3；`recompute` R1–R6 | 只覆盖利用率与时延分解，非全字段 |
| R9 | 节点处理时间与决策计算时间不混淆 | **通过** | D2 + D3 的 `f2_separation` 恒等式 | 依赖 timeline 流存在；v1 限制（无 learning 臂 / forced_actions）不变 |
| R10 | 修复不得静默改写历史回执数值/语义 | **通过** | §5 A–D；C2 前后逐字段相同；不带 `design` 的派生文档逐字节不变 | `code_sha256` 漂移会作废授权（§5E），需独立重签流程 |
| R11 | 远端运行 → 结果拉回 → 哈希重验链路 | **未通过** | §6；F4-1 未提交改动互斥、F4-2 无本地重验、F4-3 无测试覆盖 | 需 VM 实测 + 独立复核 + 先定本地重验归属 |
| R12 | 无 VM 实测回执不得声称正式链路已打通 | **通过（纪律项）** | §6.3 明确列为阻塞 | — |
| R13 | 候选到达时刻 O3 能力单列后续工作、不伪造未来信息 | **通过（纪律项）** | §6.4 | O3 能力本身仍缺失 |
| R14 | 不以单元测试通过宣布平台可信 | **通过（纪律项）** | 本表依据探针 + 隔离基线对照 + 限制清单，测试只是其中一项 | — |

### 是否具备启动正式实验的条件

**否。** 三条独立理由，任一条都足以阻止：

1. **R11 未通过**：正式链路存在一个已实测的 false negative（F4-1），且拉回后没有本地哈希重验（F4-2）、没有测试覆盖（F4-3）。在真实运行压力下，这种门最可能被绕过而不是被修好。
2. **没有任何 VM 正式回执**：`governance_receipt.json`、外部启动证人、两条证据流都只存在于本地预演中。按 §6.3，这不构成"链路已打通"。
3. **`code_sha256` 已漂移**：开工前的未提交改动就已使该 checkout 上全部已编译矩阵/已发授权失效（`50f4c17b…`），本轮又推到 `6acc2c12…`。在任何授权被重签、任何矩阵被重编译之前，正式路径本来就不可启动。

**可以开始的**（不依赖上述三项）：本地诊断与快照级复算；用新门禁重新编译候选矩阵并走审阅；以及在**不启动正式实验**的前提下继续补齐 R11 的证据。

---

## 8. 明确未做的事（范围自律）

1. 未训练任何模型、未运行任何正式研究实验。
2. 未就"流量调度 vs 分布式逐包路由"作任何优劣判断；本轮不产生该问题的证据。
3. 未改 `metrics.py`、未改 manifest/receipt 键集、未改任何既有 schema。
4. 未删除任何测试、未放宽任何 tolerance、未降低任何 fail-loud 条件。
5. 未修改开工前存在的 6 个未提交文件与 4 组未跟踪材料；对 `remote_job.py` 只给**未应用的补丁提案**。
6. 未提交、未推送、未创建 PR。
7. 未实现 O3 / 前向投影能力，未用任何替代信号顶替。
8. 未把"单元测试全绿"当作平台可信的证据；放行表逐项区分"实测通过"与"纪律项"。
