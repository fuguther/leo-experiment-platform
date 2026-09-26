# 00 — 仓库与来源取证（E0 交付物）

**对应 commit**：`98c858f642aeca8056d215224b20783b00354026`（分支 `chore/retire-dead-lines-q0-tiny`）

**clean 树 `code_sha256`**：`2a114890e8b11e429fec45a6b1efcb81c1d80a8e195a9c9407a250d218f16dbe`

> **冻结有效性声明**：上述 `code_sha256` 只对 commit `98c858f` 的 clean checkout 成立。取证期间存在并发写入者（见 §2.3），该值会随他人提交或工作树改动失效；任何引用它的下游文件必须先重新采样 `git status` 与 `code_sha256`。

**本文件范围**：任务书 v1.4 §8 对 `00-repository-and-source-audit.md` 的规定范围——读取的 exact SHA、功能/证据/文献对照、信息白名单、当前阻塞。字段级信息权限与时间语义另见 `00e-field-time-and-information-permission-table.md`。

**唯一事实来源**：本仓 `leo-experiment-platform`（只读）。源仓库 `leo-direct-sim` 仅用于治理机制对照，不作为本仓证据。

---

## 1. 冻结身份

### 1.1 本地 git 状态

| 项 | 值 | 获取命令 |
|---|---|---|
| 分支 | `chore/retire-dead-lines-q0-tiny` | `git rev-parse --abbrev-ref HEAD` |
| HEAD（exact SHA） | `98c858f642aeca8056d215224b20783b00354026` | `git rev-parse HEAD` |
| 提交时间 / 作者 / 主题 | `2026-09-24T23:48:19+08:00` / `fuguther` / `docs: 冻结 round3 判据（12 条，按 S-1 回退后的最终状态）` | `git log -1 --format='%H %cI %an %s'` |
| 上游与同步状态 | `origin/chore/retire-dead-lines-q0-tiny`；`git rev-list --left-right --count @{u}...HEAD` = `0 0` | 同上 |
| 已跟踪文件改动（clean 采样，2026-09-25T00:00:59） | 0 行（`git status --porcelain` 为空） | `git status --porcelain` |
| 忽略但存在 | `out/`、`__pycache__/`、`.pytest_cache/`、`CODE/scripts/remote/remote.env` | `git status --porcelain --ignored` |
| 未跟踪目录 | `CODE/work/WP-LEO-V2-T1-TRUST-CHAIN-V6/round3/`（并发写入者于 00:02:01 新增） | 同上 |

**与任务前提的偏差（须并列报告）**：任务前提给出的 HEAD 是 `3aa2890`；实测 HEAD 是 `98c858f`，二者关系为 `3aa2890` 是 HEAD 的祖父提交（HEAD~2）。提交顺序为 `98c858f ← c2e18a8 ← 3aa2890`。`git diff --stat 3aa2890..HEAD` = 5 文件 / +174 −69：`CODE/experiment_platform/isl_pressure.py`、`CODE/experiment_platform/tests/test_capacity_sampling_diagnostic.py`、`CODE/leo_sim/tests/test_matrix_contract.py`、`CODE/leo_sim/tests/test_pairing_trace_identity.py`、`CODE/work/WP-LEO-V2-T1-TRUST-CHAIN-V6/criteria-r3.json`。

**冻结取值以 `98c858f` 为准。** 若下游要求回到 `3aa2890`，必须先重新计算 `code_sha256` 与 `config_sha256`，因为 `isl_pressure.py` 属于 `CODE/leo_sim` 之外、但 `ANALYZER_FILES` 之内的语义文件。

### 1.2 code_sha256：算法、取值与漂移证据

算法（`CODE/leo_sim/receipt.py:152-159`）：

```python
def code_sha256() -> str:
    """SHA256 over the leo_sim package sources (sorted, path-bound)."""
    pkg = Path(__file__).resolve().parent          # CODE/leo_sim/
    h = hashlib.sha256()
    for p in sorted(pkg.glob("*.py")):             # 非递归，仅 *.py，含 __init__.py
        h.update(p.name.encode())                  # 先喂文件名（路径绑定）
        h.update(hashlib.sha256(p.read_bytes()).digest())  # 再喂该文件内容摘要
    return h.hexdigest()
```

要点：非递归（不含 `tests/`、`profiles/`）；按文件名排序；先喂 `name` 再喂 `sha256(bytes).digest()`。任何 `CODE/leo_sim/*.py` 的字节变化都会改变全部回执与授权的身份。

取值命令：`PYTHONDONTWRITEBYTECODE=1 python3 -B -c "from CODE.leo_sim import receipt; print(receipt.code_sha256())"`

| 采样时刻 | 树状态 | `code_sha256` |
|---|---|---|
| 2026-09-25T00:00:59 | 已跟踪文件 clean，HEAD = `98c858f` | `2a114890e8b11e429fec45a6b1efcb81c1d80a8e195a9c9407a250d218f16dbe` |
| 2026-09-25T00:08:45 | 6 个已跟踪文件被并发写入者修改 | `57e86d4471af7a94b584202cb8b2418a92f6d2a7850a46b7e6c4e0696e489b7c` |

历史 run 回执中读出的 `code_sha256`（用于证明身份绑定确实生效）：

| run 目录 | 采样时刻 | `code_sha256`（截断） | `receipt.schema` | 键数 |
|---|---|---|---|---|
| `out/smoke` | 2026-09-24 20:25 | `ffcad9fca16136cd…` | `leo-sim-receipt/v5` | 28 |
| `out/v6` | 2026-09-24 21:48 | `469924bb82c14ffd…` | `leo-sim-receipt/v6` | 31 |
| `out/e0-smoke-20260925` | 2026-09-24 23:59 | `2a114890e8b11e42…` | `leo-sim-receipt/v5` | 28 |
| `out/e2-corridor-diag-20260925` | 2026-09-25 00:01 | `2a114890e8b11e42…` | `leo-sim-receipt/v5` | 28 |

可复现验证：`python3 -m CODE.leo_sim receipt verify out/smoke` 返回 `{"status":"FAILED","errors":["leo_sim code sha mismatch (sources changed since the run)"]}`，因为该 run 的代码身份与当前树不同。

**文档漂移**：`README.md:99-102` 记载"退役旧线之后本仓库的代码身份已分叉为 `4402081f…`"，与实测 `2a114890…` 不一致；该处为过期快照，不得引用。
### 1.3 config_sha256：算法、命令与全部 profile 实测值

算法（`CODE/leo_sim/config.py:1024-1031`）：defaults → profile → user → overrides 深合并后，对 `json.dumps(merged, sort_keys=True, separators=(",",":"))` 取 SHA256。

```bash
python3 -B -c "from CODE.leo_sim import config; r=config.load_config_file('<path>.yaml'); print(r['sha256'], r['version'])"
# 等价 CLI：python3 -m CODE.leo_sim config validate <path>.yaml
```

本仓 `EXPERIMENTS/` 下不存在任何已编译的 `resolved/*.yaml`（只有 `EXP-20260824-ISL-BANDWIDTH-PILOT-R03/RUNBOOK.md` 一个被测试引用的 fixture），因此"当前正式 config"只能取 `CODE/leo_sim/profiles/` 下的 profile。全部 profile 实测 SHA（version 均为 `leo-sim-config/v1`）：

| profile 路径 | config_sha256 | 定位 |
|---|---|---|
| `CODE/leo_sim/profiles/smoke.yaml` | `934315391f9572ca2f03a641a3b2a800889330e8f0579fccbb7ee29379c68816` | 最小闭环；与 `criteria-r3.json` T12 记录一致 |
| `CODE/leo_sim/profiles/mlab_multiod_burst_t0_queue_280x14_e25.yaml` | `c2c59497e2444c981cb37449a60cdb5a75f4c96a46f35827b416c15e240891a4` | E0 工程基线 / provisional VM candidate |
| `CODE/leo_sim/profiles/mlab_multiod_burst_t0_queue_280x14_e25_10mbps.yaml` | `9417783a78c27052b0d7e939491c1f4dba6c6375b94a40b0dd96ccf3ad034e82` | E0 负载臂 10 Mbps |
| `CODE/leo_sim/profiles/mlab_multiod_burst_t0_queue_280x14_e25_25mbps.yaml` | `2aa0df0d7b7f45d5a7fddae6b78440c6de08d60d64458ae3cbd310a0dcd00d4e` | E0 负载臂 25 Mbps |
| `CODE/leo_sim/profiles/t1_pressure_corridor.yaml` | `52dfe3d92c1e0e6cbd7281c8b4725d9889c5c2376848a22a5b25a980ed4c47a3` | T1 可解析 ISL 压力窗口 |
| `CODE/leo_sim/profiles/mlab_e0_calibration.yaml` | `1466ec9565f54dc2fb86c2a778b4a73000aa308bc2fc5ebb4dd28ec8da388b45` | 旧 E0 标定 |
| `CODE/leo_sim/profiles/e0_load_scan.yaml` | `738ddb1aa4925d45d243747006b1309c010ee0bc7bb6d9ed70bf7ed41b69641b` | E0 负载扫描 |
| `CODE/leo_sim/profiles/formal_exp1.yaml` | `473564ae6a96e326356bacee5cdf8798ad1e28251a96a2b732093f13d1d0ac31` | 正式 EXP1 |
| `CODE/leo_sim/profiles/comparison.yaml` | `07739f8786d2619db15a5b16966863ac5c5d4bbc61a58acf8cd37f9dd3460df9` | 平台自检 |
| `CODE/leo_sim/profiles/experiment_base.yaml` | `36bc423b977d30f0bae12259224e391be2b4bc698fdc142c823c8b6a1c68956b` | 基线 |
| `CODE/leo_sim/profiles/population_gravity.yaml` | `e48e4ac833a6e5ca022a4071eb91680c5583b75103f5e7eb2328d2cd98569343` | 人口代理 |
| `CODE/leo_sim/profiles/population_global_1deg_diagnostic.yaml` | `ddec4a35f84a65e91c12dcb21ca7d46ea4c3a2bc9fa7e3ba10512669779de845` | 诊断 |
| `CODE/leo_sim/profiles/mlab_measured_od_burst.yaml` | `17a9eebfba455f61f8b48dc4ae69fb4e8061a35602b108101c08d94d079d7682` | M-Lab 单值 |
| `CODE/leo_sim/profiles/mlab_multiod_burst_t0.yaml` | `cc6200bdb6f45dd1b044cd8d5a84cee470e8c69356e5fc19c999f234b2b54167` | M-Lab 多 OD |
| `CODE/leo_sim/profiles/mlab_multiod_burst_t0_queue.yaml` | `eab90ebc5d4886c8fa6abf1cf0ad22ac9730b2baa23965eb3b40470ed72daa1d` | M-Lab 多 OD 加队列 |
| `CODE/leo_sim/profiles/acceptance/bbm.yaml` | `14214b8e5a3b8fc454dae412a1518439e2e29b92841b119ceb6824658cbb9ded` | 验收臂 |
| `CODE/leo_sim/profiles/acceptance/ddqn.yaml` | `913dc916f74201b555218d0bba2d937a39edb573347ef4a85f0d716449ae832d` | 验收臂 |
| `CODE/leo_sim/profiles/acceptance/direct.yaml` | `8b5eef6947eb7434d0e6634f562541597cef16eee0e7e6edb17b314226225281` | 验收臂 |
| `CODE/leo_sim/profiles/acceptance/gat.yaml` | `26c8492c7778256953f7579beb883de8e0db679213767e65f5118b00afb07527` | 验收臂 |
| `CODE/leo_sim/profiles/acceptance/ge.yaml` | `c355d94bde314b0b15ce35fd5b035bc242412b14f756863133bc27bc494b84b0` | 验收臂 |
| `CODE/leo_sim/profiles/acceptance/k1.yaml` | `502d083ea1b578b92bb6304f79d515f29ebe3f57ae05564793803c95f85ec198` | 验收臂 |
| `CODE/leo_sim/profiles/acceptance/mbb.yaml` | `24ae01864593a061ed606af868a265a6769dde60c8e85797196db3dd529cf15b` | 验收臂 |
| `CODE/leo_sim/profiles/acceptance/mpnn.yaml` | `9760b937a5d8b2f9e26c6b3949603981632deb7c9d6634ed7cd7356c1a727f49` | 验收臂 |

**E0 基线的关键默认值**（profile 未声明这些键，全部取默认）：`execution.compute_delay_s = 0.0`（`config.py:411`）、`execution.decision_observation_mode = "refresh"`（`config.py:413`）、`execution.node_process_delay_s = 0.0`（`config.py:416`，F2 关闭）、`execution.available_capacity_interval_s = None`（`config.py:409`，但 E0 基线 profile 内显式设为 1）、`links.isl_rate_mbps = 1000.0`（`config.py:345`）、`links.rate_model = "constant"`（`config.py:322`）、`links.max_isl_km = 6000.0`（`config.py:348`）、`links.geometry_loss = True`（`config.py:349`）、`demand.packet_bits = 8_000_000`（`config.py:281`，E0 基线 profile 覆盖为 1_000_000）。

独立旁证：`EXPERIMENTS/experiment-program.yaml:149-154` 记录四个正式 run 的 resolved config 实测 `execution.node_process_delay_s = 0.0`，并注明该部署"contains F2 code with F2 DISABLED"。

### 1.4 trace：来源、SHA 与四个层次

trace 身份分四层，引用时必须写清是哪一层。

| 层次 | 内容 | 位置 / 取值 |
|---|---|---|
| L1 真实来源 | M-Lab 城市对逐小时聚合快照 | `CODE/data/traffic/mlab_2026-05-27.csv`，SHA256 `f15cf8b9845c195046a4566d31ab9eb0137e16270ea98ee6a06b871a6f578437` |
| L2 编译态 | `trace.csv` 与 `manifest.json` | `python3 -m CODE.leo_sim trace compile --config <yaml> --out <dir>` |
| L3 本地已有工件 | `out/smoke/trace.csv`（含 c1、f-v5、f-v6、probe、r2-probe、rep-*、s3、v6、tr0-*、tr1-*、e0-smoke-20260925 共 14 份同值） | SHA256 `47a4af9fcf7fc58dd1ec3c5f8ce0961296fff2344f711bac5b02889ceff18ecd` |
| L3 本地已有工件 | `out/e2-corridor-diag-20260925/trace.csv` | SHA256 `d7a0c67bb0eb35a71c54b018d50b5dd50df793fdc804cc81f883a7fbc01f3d65` |
| L4 正式 VM 登记值 | `CODE/Results/` 本地不存在（`.gitignore:4` 忽略 `Results/`），只能引 `experiment-program.yaml` 登记值 | 50 Mbps 臂 `f6981c327f4c36e659d3f7b5ef66128f94a199d0203591401c88ed0e8ab22de4`（`:433`、`:364`）；10 Mbps 臂 `e2b469b984a7fc677f4ae8a61621f7e1e9c93ff3b3ade097461a68f365a2d23`（`:461`）；25 Mbps 臂 `22d341d9a5f8028f91ca8bb32f6a580a4a28a08dd6409a774e76690e31937193`（`:460`） |

**L1 的性质（不得外推）**：该 CSV 是 44 929 行逐小时城市对聚合（18 765 client 坐标 : 75 server 坐标），不是逐包 trace（`ANALYSIS/PLATFORM-AUDIT-REPORT.md:87` 的 L1；数据自身声明见 `CODE/data/traffic/README.md`）。同目录的 `mlab_sample.csv`、`diurnal_mlab_2026-05-27.json`、`diurnal_smoke_sinusoidal.json` 为零代码引用的孤儿夹具（`ANALYSIS/TRAFFIC-MODEL-SPEC.md:143-146`）。

**trace SHA 的计算方式**（`__main__.py:249-259` `_compile()`）：

```bash
shasum -a 256 <dir>/trace.csv   # == manifest.__trace_sha256 == receipt.trace_sha256
shasum -a 256 <dir>/manifest.json  # == manifest.__sha256 == receipt.trace_manifest_sha256
```

**manifest 与 identity 契约**：`manifest` schema `leo-sim-trace-manifest/v2`（`CODE/leo_sim/trace.py:28`）；`provenance` schema `leo-sim-trace-provenance/v2`（`trace.py:30`）；manifest 内自带 `input_sha256`（`trace.py:926`，`mode=mlab` 时为 `sha256(REPO_MLAB_CSV bytes)`，`trace.py:709-716`）。trace identity 版本 `leo-sim-trace-identity/v3`（`config.TRACE_IDENTITY_VERSION`），算法 `config.py:974-980`，覆盖 `config_version`、`scenario{duration_s, seed}`、`endpoints`、`demand`（去掉 `emission_end_s`）、`execution.max_packets` 与 `input_sha256`；`geometry_epoch_s` 被刻意排除在 trace identity 之外。

### 1.5 事件定义与产物契约（冻结面）

| 契约 | 取值 | 源码位置 |
|---|---|---|
| packet_events 封闭词表 | 7 种 kind：`packet_emitted`、`queue_enter`、`satellite_ingress`、`service_start`、`propagation_start`、`propagation_arrival`、`delivered`；未知 kind 直接抛 `MetricsError` | `CODE/leo_sim/metrics.py:90-218`（未知 kind 在 `:217-218`） |
| timeline 里程碑 | 13 种取值、15 处调用点（枚举全部 `_timeline(` 调用点得到；详见 00e 表 C2） | `kernel.py:1510-1529` `_timeline()` |
| decision 行契约 | 19 键，双向 fail-loud；流契约名 `decision-rows/v1` | `decision_ledger.py:75-80`、`:83`、`:90-110` |
| receipt 键集 | V3 = 25、V4 = 26、V5 = 28、V6 = 31 键（代码 `len()` 实测） | `receipt.py:45-64` |
| 字段权威分级 | `recomputed` / `ledger_consistency` / `diagnostic` 三类 | `receipt.py:132-149` |
| ledger 顶层键 | 17 个 | `receipt.py:97-104` |
| 配置 schema | `leo-sim-config/v1` | `config.CONFIG_SCHEMA_VERSION` |

产物族与门（`EXPERIMENTS/contracts/run-artifact-contract.md`，64 行）：三族分离（`:5-7`）；两层门"运行准入 ≠ 科研充分"（`:12-19`）；V5/V6 形态（`:37-40`）；正式准入四条件（`:59-63`：自然结束且守恒、无 verification errors、身份一致、`v2_analysis` 重算持久化 manifest）。

**本地回执永远不具备科研资格**：`receipt.expected_research_eligible(...)` 直接 `return False`（`receipt.py:224-232`），注释写明正式资格属于外部 governance receipt。实测 `out/*/receipt.json` 全部为 `research_eligible=false`。
---

## 2. 治理现状

### 2.1 本仓不存在的治理文件（须显式声明）

| 文件 | 本仓 | 源仓库 leo-direct-sim | 影响 |
|---|---|---|---|
| `AGENTS.md` | 不存在（本仓 git 历史中从未出现） | 存在（含 rule 20 承重改动仲裁） | 任何引用"AGENTS.md 规则 N"的陈述在本仓无落点 |
| `CLAUDE.md` | 不存在 | 不存在 | 无 |
| `AGENT-START-HERE.md` | 不存在 | 存在 | `EXPERIMENTS/README.md:3`、`CODE/work/README.md:3` 的入口指引悬空 |
| `ANALYSIS/DOCUMENT-STATUS.json` | 不存在 | 存在（schema `leo-document-governance/v1`，18 条 coverage） | 本仓无文档登记机制 |
| `ANALYSIS/EXPERIMENT-PROGRAM.md` | 不存在 | 存在 | `experiment-program.yaml:72-75` 的 `authority.human_plan` 悬空 |
| `ANALYSIS/FINDINGS-REGISTRY.md` | 不存在 | 存在 | 同上 `authority.findings` 悬空 |
| `ANALYSIS/CURRENT-EXPERIMENT-READINESS.md` | 不存在 | 存在 | 同上 `authority.readiness` 悬空 |
| `PAPER/` | 不存在 | 存在 | 运行产物契约 `:10` 提到"论文 claim 经 `PAPER/README.md` 定义的合格证据门"在本仓不可达 |
| `EXPERIMENTS/RUNS.csv` | 不存在 | 存在 | `EXPERIMENTS/README.md:19` 关于"RUNS.csv 只有表头"的陈述在本仓无对应文件 |

**结论（须写入任何 E0 下游文件）**：本仓不存在 `AGENTS.md` 与文档治理注册表；任务书 v1.4 §0.1（第 125 行）要求先读的 `AGENTS.md`、`AGENT-START-HERE.md`、`ANALYSIS/DOCUMENT-STATUS.json` 在本仓一律不存在，因此"先运行仓库要求的 workspace/document governance 检查"在本仓**无法执行**，只能记为上游（leo-direct-sim）合同。

### 2.2 本仓真正生效的合同

| 合同 | 路径 | 关键条款 |
|---|---|---|
| 平台章程 | `CHARTER.md` | 五条不变式（可重放 / 可反溯 / 可干预 / 可归因 / 代价可量化）`:22-42`；边界"契约进，实例不进" `:46-58`；三条不做 `:62-66` |
| 工作方式 | `WORKING-MODEL.md` | 证据必须指回唯一代码身份 `:8-15`；判据封闭（8–20 条，写不出 check 的不进集）`:20-42`；`overall` 只能由 `criteria_results` 推导 `:60`；失效是判据级 `:62-63`；复核上限两轮 `:65-66`；失败 fail-loud、禁止静默融合 `:72-77`；完成标准 `:80-84` |
| 主线登记 | `lines/README.md` | 状态取值 `ACTIVE` / `DORMANT` / `RETIRED` / `SUPERSEDED` `:9-14`；当前唯一 ACTIVE 主线为 `lines/TIME-SEMANTICS.md`（`:5`） |
| 运行产物契约 | `EXPERIMENTS/contracts/run-artifact-contract.md` | 见 §1.5 |
| 工作包复核 | `CODE/work/README.md` | 单写入者；`rNN/` 不可改写旧产物与回执 `:25-26`；实验设计工作包固定三角色 `cold_start` / `satellite_drl` / `adversarial` `:30`；`finalize_decision.py` 机器重算 `:34-45` |
| 分析器身份 | `CODE/experiment_platform/v2_analysis.py:77-89,112-137` | `ANALYZER_FILES` 共 11 个文件；`_analyzer_identity()` 要求 `git rev-parse HEAD` 为完整 40 位 SHA 且 `git status --porcelain -- <ANALYZER_FILES>` 为空，否则抛 `V2AnalysisError` |
| 状态登记 | `EXPERIMENTS/experiment-program.yaml`（598 行） | 见 §3.9 |
| CI | `.github/workflows/test.yml` | 单 job 跑四个测试目录；`cancel-in-progress: false`（`:9-15`）；依赖固定 python 3.11 / simpy 4.0.1 / numpy 1.24.3 / pyyaml / pillow 12.0.0（`:23-26`） |

### 2.3 并发写入者与冻结有效性（重要）

取证窗口内（2026-09-25T00:00:59 clean → 00:08:45 dirty）出现并发写入者，改动如下：

| 文件 | 状态 | mtime |
|---|---|---|
| `CODE/leo_sim/__main__.py` | 已修改 | 2026-09-25T00:04:11 |
| `CODE/leo_sim/receipt.py` | 已修改 | 2026-09-25T00:05:01 |
| `CODE/leo_sim/tests/test_pairing_trace_identity.py` | 已修改 | 2026-09-25T00:04:11 |
| `CODE/leo_sim/tests/test_review_residuals.py` | 已修改 | 2026-09-25T00:04:11 |
| `CODE/scripts/remote/pull-results-remote.sh` | 已修改 | 2026-09-25T00:03:25 |
| `CODE/scripts/remote/remote_job.py` | 已修改 | 2026-09-25T00:03:31 |
| `CODE/work/WP-LEO-V2-T1-TRUST-CHAIN-V6/round3/` | 新增未跟踪 | 2026-09-25T00:02:01 |

`git diff --stat` 合计 6 files changed, 58 insertions(+), 14 deletions(-)。改动内容对应在飞工作包 `WP-LEO-V2-T1-TRUST-CHAIN-V6` 的 `criteria-r3.json` 判据 T1/T2（`remote_job.formal_command` 为 `runtime_kind=leo_sim_v2` 同时附 `--decision-log` 与 `--timeline-log`，两条流位于 run 目录内）与 T6/T7/T8（提供流路径时重算 V6 流摘要并捕获篡改、不提供时行为逐字不变、对 V5 回执提供流路径时明确拒绝）。

对冻结的影响：`code_sha256` 由 `2a114890…` 变为 `57e86d4471af7a94b584202cb8b2418a92f6d2a7850a46b7e6c4e0696e489b7c`。任何下游文件选择冻结身份时必须二选一并写明：

1. 按 commit 冻结（本文件采用）：`98c858f` + `code_sha256 = 2a114890…`，并声明"工作树在该时刻之后被并发写入者改动，该 SHA 只对 clean checkout 成立"；
2. 按工作树冻结：必须附 `git diff` 全文与上述 6 个文件的 mtime，并标明这是未提交的在飞改动，不得用于任何正式授权。

附带影响：`receipt.py` 属于 `ANALYZER_FILES`，其处于 dirty 状态时运行 `v2_analysis` 会被 `_analyzer_identity` 直接拒绝（`v2_analysis.py:127-130`）。

### 2.4 E0 交付物的落盘位置与登记要求

| 问题 | 结论 | 依据 |
|---|---|---|
| 落盘目录 | `ANALYSIS/ARRIVAL-TIME-T1-20260923/` | 任务书 v1.4 §8 第 442 行建议目录 |
| 是否需要登记 | 不需要 | 本仓无 `DOCUMENT-STATUS.json` 与 `check_document_governance.py`；`README.md:37-39` 只约束 `ANALYSIS/` 内"模块名不可改" |
| 是否触发分析器身份 | 不触发 | 纯 md / csv / json 不进 `v2_analysis.ANALYZER_FILES`（`v2_analysis.py:77-89`） |
| 若新增 Python 脚本 | 不要放进 `ANALYZER_FILES` 覆盖路径，且须显式声明未登记 | 登记先例 commit `0e3f583`；未跟踪状态会使 `_analyzer_identity` 抛错 |
| `ANALYSIS/claims/` 用途 | 仅放 claim schema（`claim.schema.json`）；claim 实例 `RESEARCH_CLAIMS.yaml` 按 `README.md:72` 不入库 | `README.md:37-39,72`；契约测试 `CODE/tests/test_claim_schema_contract.py` |

---

## 3. 主链路盘点（路径 + 行号）

### 3.1 主入口 `CODE/leo_sim/__main__.py`（774 行）

| 功能 | 位置 | 要点 |
|---|---|---|
| 子命令表 | `:675-756` | `config validate` / `trace compile` / `experiment compile` / `run` / `receipt verify` / `acceptance run` / `compare run` / `platform check` |
| 正式 run 参数校验 | `:295-320` `_verify_formal_args` | `--authorization`、`--launch-nonce`（32 位小写 hex）、`--expect-run-id` 必须同时给出；三者全缺返回 None（非正式 run）；随后调用 `authorize_experiment.verify_authorization_for_leo_sim_v2_config` |
| 项目根识别 | `:288-292` | 必须同时存在 `CODE/leo_sim` 与 `EXPERIMENTS` |
| `run` 主流程 | `:413-628` | 正式参数 `:421`；dry-run 禁流 `:431-436`；F2 入口级拒绝 `:437-446`；正式输出必须是新空目录 `:448-456`；流写入器预检与行契约 `:457-489`；trace 取用 `:502-517`；`--expect-trace-sha256` 比对 `:512-517`；`run_simulation` `:539-551`；关闭 writer 取 SHA `:563-578`；空 decision 流拒绝 `:579-587`；`receipt.write_run` `:588-591`；sidecar manifest `:592-609`；formal witness `:610-616` |
| formal witness 写入 | `:323-356` | 输出必须等于 `CODE/Results/<expect_run_id>` `:326-329`；先自校验 receipt `:330-333`；写 `formal_run.json`（schema `leo-sim-formal-run/v1`）与 `_run_receipts/<launch_nonce>.txt` 指针 |
| 预编译 trace 消费 | `:359-410` | 强制 `manifest/v2` 与 `provenance/v2`；校验 `trace_identity_sha256`、`trace_sha256`、`simulation_horizon_s`、`emission_end_s`、`drain_s` 与 resolved config 一致，任一不符即 fail-loud |
| 决策流写入器 | `:65-113` `_DecisionLogWriter` | 临时文件加 `os.link` 原子发布；逐行调用 `validate_decision_row` `:87-93` |
| timeline 写入器 | `:116-129` `_TimelineLogWriter` | 注释说明 timeline 是 F2 的唯一通道，故不作 diagnostic-only 拒绝 |
| 退出码 | `:628` | `0` 自然结束；`5` interrupted；`3` 拒绝（授权 / F2 / 流）；`2` 配置或 trace；`4` CapExceeded；`6` 流或 witness 失败 |
| 回执验证 | `:631-639` | `receipt verify [--decision-log P] [--timeline-log Q] <dir>` |

### 3.2 事件内核 `CODE/leo_sim/kernel.py`（4615 行）

时间戳唯一来源为 `self.env.now`（SimPy）。全链（`ANALYSIS/CURRENT-EVENT-TIMELINE.md:16-49`）：

```text
emission(_emitter 2677-2724) -> queue_enter(uplink) -> service_start/finish(uplink 503/2592/2596)
-> propagation_start(547/1604) -> propagation_arrival(_ingress_after_prop 4304-4307)
-> satellite_ingress(4308/1637) -> [F2] _node_process(4261-4302) -> decision(decide_deferred 3846 / _decide 4036)
-> decision commit(_record_decision 3537/3571) -> service_start(ISL) -> ... 每跳重复 ...
-> _deliver_after_prop(4335-4353) -> deliveries[pid].delivered_at(4352)
```

该链不是流水线，而是每跳重复的循环；唯一例外是目的地下行 `_deliver_after_prop`（`:4335`）不做 `_node_process`，这是 F2 的显式作用域边界。

| 环节 | 位置 | 语义 |
|---|---|---|
| 初始化与 fail-loud | `:1089-1152` | sink 挂载；`obs_mode` 校验 `:1120-1123`；`frozen` 与 learner / forced_actions 互斥 `:1124-1129`；`node_process_delay_s > 0` 必须有 timeline_sink `:1145-1152` |
| F2 与计算时延不重叠 | `:1138` | 注释原文：where the decision stage begins: F2 and compute_delay_s are disjoint |
| decision_id 分配 | `:1497-1508` | 仅当存在任一 sink 时分配；输出专用，不影响路由 |
| 里程碑写入 | `:1510-1529` | 行结构 `{milestone, at=env.now, pid, decision_id}` 加 `extra` |
| 决策主体 | `:4036-4230` `_decide` | `now=env.now` `:4043`；重决策里程碑 `:4045-4052`；deadline 与跳数检查 `:4053-4058`；deliver 分支 `:4059-4116`；候选集 `routing.choose_next_hop` `:4126-4144`；合法性过滤 `:4163-4192`；learner 掩码 `:4194-4204`；确定性取 `legal[0]` `:4206`；强制动作 `:4207-4208`；提交并写决策行 `:4209-4214`；hold `:4216-4225`；fail `:4226-4230` |
| 计算时延 refresh | `:3831-3853` `decide_deferred` | `started=env.now` `:3846`；frozen 时在 timeout 之前取观测 `:3849-3850`；`yield timeout(compute_delay_s)` `:3851`；再调 `_decide` `:3852-3853`；延迟为 0 时该生成器不创建（调用点 `:4237-4240`、`:4315-4318`、`:4330-4333`） |
| 决策行写入 | `:3491-3599` `_record_decision` | `committed_at = env.now` `:3537`；`info_audit` `:3536`；refresh 用 commit 时刻观测 `:3540-3551`；frozen 用传入观测 `:3552-3556`；`truth_at_commit` `:3560-3570`；19 键行字面量 `:3571-3599` |
| 观测记录 | `:3325-3384` `_observation_at_start` | 每邻居各自带 `generated_at` / `received_at` / `age_s` / `hops` / `advertised_isl_queue_bits`；schema `leo-sim-observation-at-start/v1` |
| 合法预测 | `:3386-3489` `_estimate_at_start` | 只用 obs 时刻真正到达的信息；显式 `truth_used: False` `:3464`；schema `leo-sim-estimate-at-start/v1`；docstring 声明故意不读 peer 真实队列与 peer 自己的 cache `:3391-3398` |
| 真值审计 | `:3711-3802` `_decision_info_audit` | schema `leo-sim-decision-info/v1`；`mapping_status = truth_audit_not_learner_tensor` `:3799`；`candidate_truth` 字段 `:3746-3768` |
| 候选级下游真值 | `:3653-3709` `_peer_downstream_truth` | 含 `peer_egress_data_bits` / `ctrl_bits` / `in_service_remaining_bits` / `method`；仅决策 sink 路径调用 `:3666-3667` |
| 到达快照 | `:3627-3651` `_egress_snapshot` | 包到达邻居那一刻的每出口真值；变速下剩余量返回 unavailable 而非估计值 |
| F2 节点处理占用 | `:4261-4302` `_node_process` | `delay <= 0` 直接 return `:4294-4295`；写 `node_process_start` / `node_process_end` `:4297,4300`；仅数据包，上行 ingress 与每次 ISL 到达 `:4314,4329`；目的地下行不占用 `:4283-4287` |
| 运行入口 | `:4608-4615` | `run_simulation(resolved, rows, geometry, learning_out_dir, decision_sink, timeline_sink, forced_actions)` |
### 3.3 控制面与 AoI `CODE/leo_sim/control.py`（109 行）

| 项 | 位置 | 语义 |
|---|---|---|
| 时间契约 | `:14-22,44-51` | 有效当且仅当 `generated_at <= received_at <= now <= generated_at + ttl_s`；未来到达不算信息 |
| AoI | `:53-54` | `aoi(now) = now - generated_at`，从生成时刻计 |
| cache 写入 | `:64-68` | 同源仅保留 `generated_at` 更新者，陈旧或重复到达丢弃 |
| 快照 | `:86-108` `build_snapshot` | 只含直接可观测本地状态：位置、可见 cell、ISL 队列、ISL 传播时延、access 槽位；注释明确远端状态只能在该包真正到达后使用 |

模块级信息边界声明见 `control.py:1-8`：cache 恰好只含真正到达且未过期的内容，从不是未来几何，从不是隐藏的全局队列。

### 3.4 链路预算 `CODE/leo_sim/link_budget.py`（181 行）

| 项 | 位置 | 要点 |
|---|---|---|
| 常量 | `:29-31` | `VC = 299792458.0`、`K_BOLTZ = 1.38e-23`、`EFF = 0.55` |
| legacy MCS 表 | `:35-53` | `LEGACY_DVBS2X_SPEFF` 与 `LEGACY_DVBS2X_LIN`，源自 `SimulationRL.py:8298-8311 / 2889-2907 / 2363-2381` |
| 三套 RF 参数 | `:86-100` | ISL 26 GHz / 500 MHz / 10 W / 0.26 m；上行 30 GHz / 20 W / 0.33 m；下行 20 GHz / 10 W / 0.26 m |
| 速率公式 | `:166-181` `mcs_rate_bps` | 自由空间路损推 SNR，取最高可行谱效率乘带宽；低于 `min_rate_bps` 返回 0.0 |
| 可达范围 | `:117-141` `max_rate_range_km` | 用第一线性阈值反解距离，供内核做认证式唤醒 |
| 边界 | `:1-21` | 公式为重写而非复制，由 `tests/test_link_budget_integration.py` 与 `tests/test_link_budget_characterization.py` 用 golden 值钉死 |

与任务书 R5.1 对照：本模块只有 RF/MCS 一套参数，平台不存在 `published-optical-ISL` 或 100 Gbps 光学 profile。

### 3.5 指标 `CODE/leo_sim/metrics.py`（405 行）

- 事件白名单与严格校验 `:90-218`（7 种 kind）。
- 交叉一致性：`service_start` 必须晚于 `queue_enter` `:160-161`；`propagation_arrival` 实测时长必须等于 `delay_s` `:190-192`；`delivered` 不得早于 `satellite_ingress` `:215-216`。
- 未匹配 propagation 检查 `:220-225`；access boundary 下的上行到达配对 `:226-234`。
- 独立第二实现 `CODE/leo_sim/metrics_independent.py`（971 行）；但生产 receipt 仍以 `metrics.summarize` 自比较（`ANALYSIS/PLATFORM-AUDIT-REPORT.md:97`，L11）。

### 3.6 决策审计 `CODE/leo_sim/decision_ledger.py`（663 行）

| 项 | 位置 | 语义 |
|---|---|---|
| `TIMELINE_FIELDS` | `:45-57` | 11 个时刻，生命周期序；字段清单见 00e 表 C1 |
| `t_measure` | `:159-168` `measure_instant()` | frozen 取 `t_decision_start`，refresh 取 `t_decision_commit`；零计算延迟时二者相同；它是每决策标量，不表示任何单邻居测量时刻 `:35-38` |
| 四个分离对象 | `:15-33` 与 `:272-300` | `observation_at_start` / `estimate_at_start` / `truth_at_commit` / `truth_at_target` |
| `truth_at_target` | `:181-225` | schema `leo-sim-truth-at-target/v1`；来源 `peer_arrival_egress_snapshot`；未发生即 MISSING 并给 reason（五种：`no_arrival_recorded`、`arrival_carries_no_egress_snapshot`、`contended_direction_unresolved`、`peer_delivered_the_packet`、`contended_direction_absent_from_snapshot`）；不默认 0、不从 commit 真值复制 |
| 折叠时序 | `:305-314` | 用后继决策的 `chosen` 作为 `contended_direction`；`t_peer_redecision` 与两个 `t_peer_target_egress_*` 仅在存在后继重决策时填入 `:290-311` |
| 尝试枚举 | `:333-437` `build_attempts` | hold / fail / rejected 也计入，避免成功者账本 |
| 事后评分 | `:440-550` `score_downstream_predictions` | 使用 commit 时刻真值，docstring 明确不得当作 t0 信念质量 |
| 可部署评分 | `:553-652` `score_start_estimates` | 使用 `estimate_at_start`，`truth_used: False` |
| MISSING 审计 | `:655-663` `audit_ledger` | 列出每个决策仍缺失的字段 |
| MISSING 与 None 的区分 | `:42,171-178` | `MISSING` 表示记录早于该字段，`None` 表示已记录但无合法预测 |

### 3.7 回执 `CODE/leo_sim/receipt.py`（1713 行）V5 与 V6

| 项 | 位置 | 事实 |
|---|---|---|
| 键集常量 | `:45-64` | V3 = 25、V4 = 26、V5 = 28、V6 = 31 键 |
| 家族判定 | `:67-73` | `RECEIPT_SCHEMAS_V5_FAMILY = {v5, v6}` |
| 签发条件 | `:586-613` | 同时给出 decision 与 timeline 两条流时升为 v6 并附 `decision_stream_contract`、`decision_log_sha256`、`timeline_log_sha256`；只给一条或都不给保持 v5 |
| 写入 | `:655-680` `write_run` | 发布 run 目录并落 `receipt.json` |
| 验证入口 | `:1131-1150` | `verify_receipt_dir(dir, decision_log=None, timeline_log=None)`；严格模式要求本地 code 与 deps 等于运行期身份 |
| 流摘要重算 | `:1153-1191` | 仅当调用方提供流路径时才重算；对 V5 回执提供流路径时明确报 `carries no stream binding` `:1173-1177` |
| 本地科研资格 | `:224-232` | 恒为 False |
| 依赖绑定 | `:162-177` | python / simpy / numpy / pyyaml；DDQN 额外绑 tensorflow |

未闭合点（截至本文件取证）：不提供流路径时 `verify_receipt_dir` 只校验两个流摘要的形状而非数值（与 `ANALYSIS/T1-MEASUREMENT-PROTOCOL.md:51-53` 自述一致，代码 `:1161-1162`）。并发写入者正在修改 `receipt.py` 与 `__main__.py` 以闭合 T6/T7/T8（见 §2.3），引用该结论前须以当时 HEAD 重核。

### 3.8 正式运行协议

| 环节 | 入口 | 要点 |
|---|---|---|
| 编译 | `python3 -m CODE.leo_sim experiment compile --request <r.json> --out <dir>`（`__main__.py:277-285` → `governance.py:238-382`） | 产出 `resolved/<run_id>.leo-sim.yaml`、`request.json`、`run-manifest.json`、`analysis-request.json`、`RUNBOOK.md`、`compile-report.json`；`execution_authorized=false` `:327` |
| 请求契约 | `governance.py:253-283` | 顶层键固定 6 个：`schema`、`experiment_id`、`runtime_kind`、`work_finalization`、`acceptance`、`config`；`acceptance` 固定 4 键 |
| 身份绑定 | `governance.py:226-235`、`:310-321`、`:29-52` | `config_sha256`、`input_sha256`、`trace_identity_sha256`、`code_sha256`、`execution_chain_sha256`（覆盖 6 个非 leo_sim 文件） |
| 授权 | `CODE/experiment_platform/authorize_experiment.py`（571 行） | 依据 ACCEPTED 工作包 finalization 生成 `authorization.json`，不可手写 |
| 部署与运行 | `CODE/scripts/remote/{push-remote.sh, run-remote.sh, remote_job.py, deployment_guard.py}` | 正式产物位于 `CODE/Results/<run_id>/` |
| 远端流缺口 | `remote_job.py:304-309`、`run-remote.sh:44`（引自 `ANALYSIS/PLATFORM-AUDIT-REPORT.md:95,120`，本文件未独立复核原文） | 截至取证时刻，F2 依赖的 timeline 流在正式 VM 链路上不可达；`criteria-r3.json` T1/T2 正在修，须以当时 HEAD 重核 |

### 3.9 状态登记 `EXPERIMENTS/experiment-program.yaml`（598 行）

三层结构 `:4-10`：`current`（此刻已验证）、`historical`（过去部署，不代表当前状态）、`evidence`（约束 claim 的产物级事实）。

| 门或实验 | 状态 | 位置 |
|---|---|---|
| `gates.platform_frozen` | blocked | `:175-176` |
| `gates.formal_analysis_available` | completed_verified（含 raw event 缺失 caveat） | `:184-187` |
| `gates.t1_platform_ready` | completed_verified，但 `verification_provenance` 自述为 SELF-VERIFIED 且记录 AGENTS.md rule 20 未满足 | `:188-245` |
| `T1-TIME-LEDGER-PASS` | completed_verified | `:246-254` |
| `T1-DOWNSTREAM-RESOURCE-PASS` | completed_verified | `:255-264` |
| `T1-COUNTERFACTUAL-REPLAY-PASS` | completed_verified | `:265-274` |
| `T1-COMPUTE-DELAY-PASS` | completed_verified，附 R8-A10 限制 | `:275-289` |
| `T1-PRESSURE-WINDOW-PASS` | completed_verified，附 R8-A9 PARTIAL 限制 | `:290-310` |
| `gates.real_diagnostic_ready` | blocked | `:311-321` |
| `gates.current_load_levels_frozen` | blocked | `:319-321` |
| `gates.theory_ready` | blocked | `:322-328` |
| 实验 `E0-REAL` | rerun_required | `:410-418` |
| 实验 `E0-LOAD-CALIBRATION` | rerun_required（历史 50/100/200 仅作 historical_only） | `:448-462` |
| 实验 `ACCESS-BOUNDARY` | in_progress | `:420-446` |
| 实验 `E0-VIS-K17-DIAGNOSTIC` | preregistered | `:464-476` |
| 实验 `TRAFFIC-VALIDATE` / `METRIC-VALIDATE` | blocked | `:347-354`、`:379-390` |
| `requirements.exact_main_deployed_to_vm` | completed_verified（`main_7f29ea3`，历史记录） | `:158` |

E0 工程基线冻结条目 `:429-446`：`provisional_vm_candidate.status = frozen_e0_engineering_baseline`，`constellation = 280 星 / 14 面 / 20 星每面 / min_elevation 25°`，`trace_sha256 = f6981c32…`，但 `global_freeze: false`、`capacity_proof: false`、`paper_ready: false`，`freeze_condition` 要求先补 10/25 同 trace VM cell。

R02 原始数据永久缺失 `:61-69`：`r02_raw_results_present: false`，raw-event 重算 PERMANENTLY UNAVAILABLE；`:100` 记录 R02 = 24 runs / 12 unique configs + 12 exact reexecutions，故独立条件数为 12 而非 24。
---

## 4. 现有可复用入口

### 4.1 只读类入口（E0 阶段允许运行）

| 入口 | 命令 | 实测结果 | 是否写盘 |
|---|---|---|---|
| 配置校验 | `python3 -m CODE.leo_sim config validate CODE/leo_sim/profiles/smoke.yaml` | 输出 `{"status":"ok","version":"leo-sim-config/v1","sha256":"934315391f9572ca…"}` | 否 |
| dry-run | `python3 -m CODE.leo_sim run --config CODE/leo_sim/profiles/smoke.yaml --dry-run` | 输出 DRY RUN 与 `config_sha256`、`trace_sha256`、`code_sha256`、`offered_packets=1`、`offered_bits=8000000`、`horizon_s=60`；未指定 `--out` 时使用系统临时目录（`__main__.py:497-501`） | 仅系统临时目录 |
| 回执验证 | `python3 -m CODE.leo_sim receipt verify out/smoke` | 返回 FAILED，错误为 `leo_sim code sha mismatch (sources changed since the run)`，可用于证明身份绑定有效 | 否 |
| 测试收集 | `python3 -m pytest CODE/leo_sim/tests CODE/experiment_platform/tests CODE/tests ANALYSIS/tests -q --collect-only -p no:cacheprovider` | 941 tests collected；1 条警告 `Unknown pytest.mark.scene_smoke`（`CODE/leo_sim/tests/test_platform_check.py:190`） | 否，但必须带 `-p no:cacheprovider` |
| 定向测试（E1 相关回归底） | `python3 -m pytest <file> -q --collect-only -p no:cacheprovider` | `test_compute_delay.py` 8、`test_frozen_observation.py` 11、`test_decision_ledger.py` 17、`test_decision_snapshot.py` 7、`test_downstream_truth.py` 12、`test_counterfactual.py` 10、`test_f2_node_cost.py` 21、`test_receipt_v6_streams.py` 10、`test_access_boundary.py` 11 | 否 |

注意：运行全量 pytest 会写仓库内 `.pytest_cache/` 与 `__pycache__/`。在只读冻结阶段必须同时使用 `-p no:cacheprovider` 与 `PYTHONDONTWRITEBYTECODE=1`，或显式声明该写入可接受。本文件只做了 collect-only，因此"当前测试是否全绿"为 UNVERIFIED；`README.md:82-84` 记载的历史计数 `917 passed, 1 skipped` 与当前 941 条收集数不一致，属过期快照。

### 4.2 写盘类入口（E0 阶段禁止运行，仅登记）

| 入口 | 命令 | 产物与副作用 |
|---|---|---|
| trace 编译 | `python3 -m CODE.leo_sim trace compile --config <yaml> --out <dir>` | 写 `trace.csv` 与 `manifest.json` |
| run | `python3 -m CODE.leo_sim run --config <yaml> --out <dir> [--decision-log P --timeline-log Q]` | 写 5 至 7 个产物；加 `--authorization`、`--launch-nonce`、`--expect-run-id` 即为本地 formal-flag run，但仍不等于 VM 正式 run |
| 验收 | `python3 -m CODE.leo_sim acceptance run --out <dir>` | 5 个真实几何场景（`acceptance.py:17-19`） |
| 平台自检 | `python3 -m CODE.leo_sim platform check --out <dir>` | 退出码 9 表示 `PlatformCheckError` |
| 比较 | `python3 -m CODE.leo_sim compare run --out <dir>` | 退出码 8 表示失败 |
| 实验编译 | `python3 -m CODE.leo_sim experiment compile --request <json> --out <dir>` | 要求 out 为新建空目录 |
| 决策折叠 | `python3 -m CODE.experiment_platform.fold_decision_ledger --decision-log A --timeline-log B --out C` | 要求 `--out` 指向不存在的路径（`README.md:112-116`） |
| 反事实重放 | `python3 -m CODE.experiment_platform.replay_counterfactual --config <yaml> --decision-id N --forced-action S --out P` | 要求 `learning.algorithm=none` 且 `--out` 不存在；会在当前工作目录建临时目录（取证期间曾观察到仓库根下短暂的 `counterfactual-<rand>/`） |
| 工作包终结 | `python3 CODE/work/finalize_decision.py --brief … --decision … --out …` | 重算产物与回执 hash（`CODE/work/README.md:34-45`） |

### 4.3 依赖与版本漂移

| 项 | 本机实测 | CI 与 VM 固定值 | 影响 |
|---|---|---|---|
| python | 3.14.2 | 3.11 | 回执 `deps` 绑定运行时版本，本地 run 无法在 CI 版本上通过 `receipt verify` |
| simpy | 4.1.1 | 4.0.1 | 同上 |
| numpy | 2.3.5 | 1.24.3 | 同上 |
| pyyaml | 6.0.3 | 未固定具体版本 | 同上 |
| pytest | 9.1.1 | 未固定 | 测试收集与告警行为可能不同 |
| pillow | 未验证 | 12.0.0（`test.yml:26`） | 可能为 CI 专用依赖 |

实测证据：`out/e0-smoke-20260925/receipt.json` 的 `deps` 为 `{python: 3.14.2, simpy: 4.1.1, numpy: 2.3.5, pyyaml: 6.0.3}`。

### 4.4 E1（物理时间与 ETA 验证）脚本现状

**结论：E1 所需的 ETA / 物理时间最小脚本在本仓不存在，必须新写。**

| 检查项 | 结果 |
|---|---|
| 仓内是否存在 ETA 或到达时刻预测实现 | 不存在。以 `eta` 检索 `CODE/leo_sim` 仅命中 `metadata`、`reward_beta` 等无关符号；`ANALYSIS/` 下无 ETA 脚本 |
| 是否存在状态哈希 | 不存在（`grep -rn state_hash CODE/` 为 0 命中，引自 `ANALYSIS/PLATFORM-AUDIT-REPORT.md:91`） |
| 可复用的底层物理函数 | `model.propagation_delay_s(d) = d / C_KM_S`（`model.py:20-21`，`C_KM_S = 299792.458` 在 `:14`）、`model.isl_range_km`（`:259`、`:548`）、`model.gsl_available`（`:235`、`:617`）、`model.isl_available`（`:273`、`:620`）、`link_budget.mcs_rate_bps`（`link_budget.py:166-181`） |
| 可复用的回归底 | `test_compute_delay.py`、`test_frozen_observation.py`（含 2×2 反例与逐位等价）、`test_decision_ledger.py`、`test_f2_node_cost.py`、`test_downstream_truth.py`、`test_counterfactual.py` |
| 仓库外的历史探针 | `/Users/lge/Desktop/topic/_archive/root-scratch/.t1_*.py`（21 个以上只读探针）与 `/Users/lge/Desktop/topic/_archive/leo-runs/probe_*.py`。以 `grep -l leo-experiment-platform` 检索为 0 命中，全部硬编码指向 `leo-direct-sim` 或 `leo-direct-sim-wt-frozen`，不能直接对本仓使用 |
| 脚本约束 | 新脚本必须纯函数优先、只读、opt-in、默认语义不变；不得放进 `ANALYZER_FILES` 覆盖路径，也不得改写 `CODE/leo_sim/*.py`（会再次改变 `code_sha256` 并作废全部回执与授权） |

---

## 5. 当前阻塞（K1 至 K17）

| 编号 | 阻塞 | 证据 | 影响 |
|---|---|---|---|
| K1 | `flow_id` 与 `traffic_class` 在代码中完全不存在 | 实测对 `flow_id`、`traffic_class`、`flow_class` 三个标识符检索 `CODE/` 为 0 命中；旁证 `ANALYSIS/PLATFORM-AUDIT-REPORT.md:88`（L2）、`ANALYSIS/TRAFFIC-MODEL-SPEC.md:125,164`；`DataPacket` 全字段 per-packet（`kernel.py:67-97`），ledger 按 `pid` | 任何 flow-level、每流或按 traffic class 的结果不可产出 |
| K2 | F2 节点处理时延默认 0，E0 基线未启用 | `config.py:416` `node_process_delay_s = 0.0`；E0 基线 profile 未声明该键；`experiment-program.yaml:149-154` 记录四个正式 run 实测 0.0，并注明 F2 code present but DISABLED | 代价可量化只有能力、无数据；F2 不能作为实验因子 |
| K3 | F2 在正式 VM 链路上不可达 | `remote_job.py:304-309` 不含 timeline 参数、`run-remote.sh:44` 拒绝 `--`（引自 `ANALYSIS/PLATFORM-AUDIT-REPORT.md:95,120`，未独立复核原文） | 正式链路上无法采集 F2；但 `criteria-r3.json` T1/T2 正在修，且并发写入者已改 `remote_job.py`（见 §2.3） |
| K4 | 本地执行无法进入 canonical 分析 | `v2_analysis.py:410-415` 要求 `governance_receipt.json`，该文件只由 `remote_job.py` 生成（引自 `ANALYSIS/PLATFORM-AUDIT-REPORT.md:123`，B10）；实测本地 `out/*` 目录只有 5 个文件 | 本地 smoke 只能是工程证据 |
| K5 | 默认配置下 `utilization` 恒为 1.0，且分析期硬中止 | `metrics.py:334-340` 分母取服务窗口容量；`available_capacity_interval_s` 默认 `None`（`config.py:409`）；`v2_analysis` 在该读数上抛 `V2AnalysisError`（引自 `ANALYSIS/PLATFORM-AUDIT-REPORT.md:92,118`） | 未设 `available_capacity_interval_s` 的实验会在跑完全部 run 后才在分析期失败；E0 基线 profile 已显式设为 1 |
| K6 | 包粒度与量纲未校准（P0） | `config.py:281` `demand.packet_bits` 默认 8_000_000；E0 基线 profile 用 1_000_000；控制面 8000；任务书 v1.4 §R5.2（第 68 行）给出 1 Mbit 单元 25 次每秒与 1500 B 约 2083 包每秒、相差约 83.3 倍，并判定为 P0 blocker | `decisions_per_sat_second`、`compute_wait p50/p95/p99` 等新指标在现有量纲下不可信 |
| K7 | 链路代际未分离 | `config.py:345` `isl_rate_mbps = 1000.0`；`link_budget.py` 只有 RF/MCS 一套；任务书 R5.1（第 67 行）要求区分 RF 1 Gbps generic 与运营商 100 Gbps optical | 高带宽下无压力的阴性结果不可外推到 100 Gbps 光学链路 |
| K8 | 无状态哈希，反事实配对只有决策行指纹 | `grep -rn state_hash CODE/` 为 0 命中；配对靠 `counterfactual.py:34-72`（引自 `ANALYSIS/PLATFORM-AUDIT-REPORT.md:91`，L5） | 只能声明经决策日志见证到达同一分支点 |
| K9 | `code_sha256` 漂移作废全部历史授权 | 入库 20 份 `authorization.json` 严格门下 0 通过（引自 `ANALYSIS/PLATFORM-AUDIT-REPORT.md:115`，B2）；本文件独立实测 `receipt verify out/smoke` 因 code sha 失配 FAILED | 不得复用任何历史授权；改动 `CODE/leo_sim/*.py` 会再次作废全部回执 |
| K10 | 串行前置门对多数实验是空操作 | `v2_serial_gate.py:52-55` 在授权校验前 `return []`；33 份入库实验中仅 4 份声明 `execution_policy`（引自审计报告 `:116`，B3） | 逐 cell 串行 fail-closed 不是机械保证 |
| K11 | 编译期与分析期指标词表不相交 | `metric-catalog.json` 列 23 个指标，`v2_analysis._metric_from_result` 只接受 `delivery_rate`、`access_admission_rate`、`network_delivery_rate_by_horizon` 三个（引自审计报告 `:117`，B4） | 预注册主指标只能从这三个中选，或先修该缺口 |
| K12 | `scene_check` 硬绑定业务模型 | `scene_check.py:278-291` 要求 `provenance == population_proxy` 与 `temporal_model == local_diurnal_cosine`；`scope` 硬编码于 `:250-251,919`（引自审计报告 `:121`，B8；`ANALYSIS/TRAFFIC-MODEL-SPEC.md:132-140`） | M-Lab 场景无法通过场景合法性分类 |
| K13 | RNG 子流声明过度 | `rng.py:15-29` 声明 8 条流，实际只消耗 `demand`（`trace.py:694`）与 `nested_filter`（`trace.py:703`），GE 走 `link_stream`（`kernel.py:789`、`:2223`）（引自审计报告 `:90`，L4；`ANALYSIS/TRAFFIC-MODEL-SPEC.md:39-60`） | 不得声明每机制独立流、开关互不扰动 |
| K14 | burst 旋钮可静默失效且窗可越界 | `trace.py:303` 只在 `mode` 为 `burst` 或 `mlab` 时生效；窗可落在 `emission_end_s` 之外而 manifest 仍记录 burst；126 份 resolved config 中 55 份携带永不生效的 `burst_multiplier`（引自审计报告 `:103-104`，L17/L18） | 不得用 resolved config 是否含 `burst_multiplier` 判断该实验是否为 burst 实验 |
| K15 | F2 的端到端可加性在有竞争时失效 | 8 星单面实测端到端增量 0.850000245 s，不等于 6 × 3 × 0.05 = 0.900 s（引自审计报告 `:124`，B11；`ANALYSIS/CURRENT-EVENT-TIMELINE.md:171-180`） | 不得声明 F2 只移动节点阶段或端到端增量等于包数乘访问数乘时延 |
| K16 | 无授权也能运行，真实门在分析侧 | `__main__.py:295-298` 三参数全缺即 `return None`，普通 `run` 直接执行；`--authorization` 默认 None（引自审计报告 `:114`，B1） | `execution_authorized=false` 不是运行门禁；真实不变量是无授权不能被分析 |
| K17 | 缺少 Starlink 星载路由芯片参数 | 任务书 v1.4 R2（第 37 行）明写 `UNKNOWN_PUBLIC_ROUTER_HARDWARE`；R7 的 `REAL-HARDWARE-PROXY-PASS` 要求缺参数明写 UNKNOWN | 硬件相关字段必须留 UNKNOWN 占位，不得用 Jetson、Myriad 或 TOPS 代填 |

证据分级说明：标注"引自审计报告"或"引自审计报告某行"的条目，其行号来自 `ANALYSIS/PLATFORM-AUDIT-REPORT.md` 或 `ANALYSIS/TRAFFIC-MODEL-SPEC.md` 的原文，本文件未逐条独立复核该行；标注"实测"、"本文件独立实测"的条目由本次只读取证直接产出。
---

## 6. 功能与证据对照（任务书 v1.4 §8 要求）

| 任务书条目 | 本仓现状 | 结论 |
|---|---|---|
| §R7 的五个先决门（第 86-92 行） | 五个门为 `REAL-SOURCE-PROVENANCE-PASS`、`REAL-PACKETIZATION-PASS`、`REAL-HARDWARE-PROXY-PASS`、`REAL-LINK-GENERATION-PASS`、`NO-ISL-TELEMETRY-CLAIM`（第 5 个不是 `REAL-` 前缀） | 本仓代码中 `REAL-` 零命中，五门无任何代码或文档落点，须由 `00d-realism-gates.md` 新建；命名应如实写成"四个 REAL- 前缀门加一个 NO-ISL-TELEMETRY-CLAIM" |
| 要求先运行 workspace / document governance 检查（第 264 行，第 125 行） | 本仓无 `AGENTS.md`、无 `AGENT-START-HERE.md`、无 `ANALYSIS/DOCUMENT-STATUS.json`、无 `scripts/check_document_governance.py` | 该检查在本仓不可执行；只能记为上游 `leo-direct-sim` 合同 |
| E0 产出文件名（第 268 行写 `00_source_contract_audit.md`） | §8 第 444 行命名为 `00-repository-and-source-audit.md` | 以 §8 命名为准 |
| §8 交付物清单（第 444-453 行） | `00-` 至 `06-`、`screenshot-review-matrix.md`、`manifest.json`、`00b-…csv`、`00c-…md`、`00d-…md` | E0 需 `00`、`00b`、`00c`、`00d`、`manifest.json` 五件；本文件为其中的 `00` |
| E0 只读、不授权正式运行（第 126-128 行） | 与本次取证一致 | 满足 |
| 冻结 `code SHA + config SHA + trace SHA + 事件定义 + 真实信息边界`（第 264 行） | 见 §1.2、§1.3、§1.4、§1.5 与 00e | 已给出 |
| 复核 2026-08-29 全网压力报告（第 266 行） | `experiment-program.yaml:290-310` 与 `ANALYSIS/T1-PRESSURE-WINDOW.md`（50 行）在位；R02 原始结果本地不存在（`experiment-program.yaml:61-69`：`r02_raw_results_present: false`，raw-event 重算永久不可用） | 只能引派生分析，须写 `RAW_DATA_NOT_AVAILABLE` |
| 同 trace 的 a/b 精确重复不算独立 seed（第 266 行） | `experiment-program.yaml:100` 记录 R02 = 24 runs / 12 unique configs + 12 exact reexecutions | 独立条件数为 12 |
| E1 物理时间与 ETA 验证（第 270-274 行） | 无 ETA 脚本（§4.4） | E1 脚本须新写 |
| 文献对照与原创性边界核对（第 266 行） | 本文件范围限于仓库与来源取证，文献对照不在本文件内 | 由 `T1_真实流量与路线相关文献报告_20260924.md` 等承担 |

---

## 7. UNVERIFIED 清单

| 项 | 状态 | 说明 |
|---|---|---|
| 全量测试是否全绿（941 条全部通过） | UNVERIFIED | 只做 collect-only，未运行全量以避免写入 `.pytest_cache/`；`README.md:82-84` 的 917 passed 为历史值 |
| `remote_job.py:304-309` 与 `run-remote.sh:44` 的原文 | 引述未复核 | 来自 `ANALYSIS/PLATFORM-AUDIT-REPORT.md:95,120`；且该处正被并发写入者修改 |
| `kernel.py:1738/1747` 关于 GE 停机未计入 available capacity（L10） | 引述未复核 | 来自审计报告 `:96`；审计报告自身记录该定义未作出且无测试固定 |
| `metrics.py:334-340` 默认分母退化（L6 / B5） | 引述未复核 | 来自审计报告 `:92,118` |
| `matrix.py:961-962` 为授权失败主因（B2） | 引述未复核 | 来自审计报告 `:115`；但 `code_sha256` 漂移本身已由实测的 `receipt verify` FAILED 独立佐证 |
| `v2_analysis.py:410-415`、`:302-306`、`isl_pressure.py:345`（K4 / K5） | 引述未复核 | 来自审计报告 `:118,123` |
| 是否存在光学链路 profile | UNVERIFIED | 未执行 `optical` 关键词检索 |
| `pillow` 是否为本仓必需依赖 | UNVERIFIED | 仅见于 CI 安装行 |
| `exact_main_deployed_to_vm = main_7f29ea3` 与 `external_state_recheck.current.origin_main = c0cc4ea` 的先后关系 | 矛盾未决 | 前者是 `requirements` 的历史记录，后者是 `as_of 2026-09-23T22:22` 的 live 核实；须并列报告，不得静默融合（`WORKING-MODEL.md:74-76`） |
| 66 星旧配置的倾角与参数 | UNVERIFIED | 任务书第 272 行要求核对，本仓 profile 中未见 66 星配置 |
| `out/e2-corridor-300s*` 等并发产物 | 未追责 | 由并发写入者产生，与冻结身份无关 |

---

## 8. 复算命令（全部只读）

```bash
cd /Users/lge/Desktop/topic/leo-experiment-platform
export PYTHONDONTWRITEBYTECODE=1

# 冻结身份
git rev-parse --abbrev-ref HEAD; git rev-parse HEAD; git status --porcelain
git log -1 --format='%H %cI %s'
git merge-base --is-ancestor 3aa2890 HEAD && echo ANCESTOR
git rev-list --left-right --count @{u}...HEAD
git diff --stat 3aa2890..HEAD

# 三个 SHA
shasum -a 256 CODE/data/traffic/mlab_2026-05-27.csv
python3 -B -c "from CODE.leo_sim import receipt; print(receipt.code_sha256())"
python3 -B -c "from CODE.leo_sim import config; print(config.load_config_file('CODE/leo_sim/profiles/mlab_multiod_burst_t0_queue_280x14_e25.yaml')['sha256'])"

# 契约常量
python3 -B - <<'PY'
from CODE.leo_sim import receipt as R, decision_ledger as D, trace as T, config as C
print(len(R.RECEIPT_KEYS_V5), len(R.RECEIPT_KEYS_V6), len(D.TIMELINE_FIELDS), len(D.DECISION_ROW_KEYS))
print(T.TRACE_MANIFEST_SCHEMA, T.TRACE_PROVENANCE_SCHEMA, C.TRACE_IDENTITY_VERSION, C.CONFIG_SCHEMA_VERSION)
PY

# 只读入口自检
python3 -B -m CODE.leo_sim config validate CODE/leo_sim/profiles/smoke.yaml
python3 -B -m CODE.leo_sim run --config CODE/leo_sim/profiles/smoke.yaml --dry-run
python3 -B -m CODE.leo_sim receipt verify out/smoke
python3 -B -m pytest CODE/leo_sim/tests CODE/experiment_platform/tests CODE/tests ANALYSIS/tests -q --collect-only -p no:cacheprovider

# 缺失项取证
grep -rn "flow_id\|traffic_class" CODE/ | wc -l          # -> 0
for f in AGENTS.md CLAUDE.md AGENT-START-HERE.md ANALYSIS/DOCUMENT-STATUS.json; do [ -e "$f" ] && echo PRESENT $f || echo ABSENT $f; done
ls CODE/Results 2>&1                                      # -> No such file or directory
```

---

## 9. 冻结摘要（供 manifest.json 直接引用）

| 键 | 值 |
|---|---|
| `head_commit` | `98c858f642aeca8056d215224b20783b00354026` |
| `head_commit_time` | `2026-09-24T23:48:19+08:00` |
| `branch` | `chore/retire-dead-lines-q0-tiny` |
| `worktree_clean_at_freeze` | true（采样 2026-09-25T00:00:59） |
| `code_sha256` | `2a114890e8b11e429fec45a6b1efcb81c1d80a8e195a9c9407a250d218f16dbe` |
| `config_sha256_e0_baseline` | `c2c59497e2444c981cb37449a60cdb5a75f4c96a46f35827b416c15e240891a4` |
| `trace_source_sha256` | `f15cf8b9845c195046a4566d31ab9eb0137e16270ea98ee6a06b871a6f578437` |
| `local_evidence_scope` | engineering_only（`receipt.research_eligible` 恒为 false；本地无 `governance_receipt.json`） |
| `concurrency_warning` | 取证期间存在并发写入者，2026-09-25T00:08:45 起 `code_sha256` 为 `57e86d4471af7a94b584202cb8b2418a92f6d2a7850a46b7e6c4e0696e489b7c` |