# 冻结当前代码的准确文件范围（供下一步建立可复核版本）

**目的**：本轮的改动全部在工作树里、没有 commit SHA，因此无法给独立复核者一个可冻结的身份。
本文件列出**建立这样一个身份所需的准确文件集合**，以及**不得进入该集合的东西**。

**冻结时刻的身份实测**（2026-09-25，HEAD 仍为 `98c858f642aeca8056d215224b20783b00354026`）：

```text
code_sha256              = 61d48a7dbab79cfae9c8aa49853ccd223cf616d189675dcde9459334297401e4
execution_chain_sha256   = {"CODE/experiment_platform/authorize_experiment.py": "14f6d5ebc8f913764045c112b0b10ba3c3871f8820b7e2d3c1fa47ac9a72aebb",
                            "CODE/experiment_platform/v2_analysis.py": "9a74bb00ca6d2cee1935d5a62305c50e9450649106079b2db57b81fb0f31b2b2",
                            "CODE/experiment_platform/v2_serial_gate.py": "a139f232236acf921874e177817361288f0799f58f78e12a00d4ea3fce7953a3",
                            "CODE/scripts/remote/deployment_guard.py": "c1580fdc42c3fdd5c650304998688d8ccdf69825c0070c65ba5b8c59c063973b",
                            "CODE/scripts/remote/remote_job.py": "c468c2bcb77832f31b0b0bbaf590c04be276a678019738f73640e369bdb1d13e",
                            "CODE/scripts/remote/run-remote.sh": "9b78a509fb5deb63c270247588b6390c3ec82f1c67ee983272174fedc5ad24e3"}
config_sha256(smoke.yaml) = 934315391f9572ca2f03a641a3b2a800889330e8f0579fccbb7ee29379c68816   （未变：本轮未增删任何 config key）
```

---

## 1. 必须冻结的文件（22 个，按路径枚举 —— 不以 git 的 tracked/untracked 划分）

**A. 决定 `code_sha256` 的 `CODE/leo_sim/*.py`（11 个）** —— 任何一个字节变了，全部已编译矩阵与已发授权作废：

| 文件 | 状态 |
|---|---|
| `CODE/leo_sim/__main__.py` | 修改（第一轮 + 本轮） |
| `CODE/leo_sim/config.py` | 修改（第一轮） |
| `CODE/leo_sim/matrix.py` | 修改（第一轮） |
| `CODE/leo_sim/metrics_independent.py` | 修改（第一轮） |
| `CODE/leo_sim/receipt.py` | 修改（开工前未提交 + 第一轮 + 本轮） |
| `CODE/leo_sim/trace.py` | 修改（第一轮 + 本轮） |
| `CODE/leo_sim/recompute.py` | **新增**（第一轮） |
| `CODE/leo_sim/pullback.py` | **新增**（第一轮；本轮重写） |
| `CODE/leo_sim/tests/test_config.py` | 修改（第一轮） |
| `CODE/leo_sim/tests/test_pre_experiment_trust.py` | **新增**（第一轮）+ 本轮修改 |
| `CODE/leo_sim/tests/test_pre_experiment_trust_round2.py` | **新增**（第一轮） |
| `CODE/leo_sim/tests/test_formal_stream_gate.py` | **新增**（第一轮） |
| `CODE/leo_sim/tests/test_pullback_authority.py` | **新增**（本轮） |
| `CODE/leo_sim/tests/test_burst_transform.py` | **新增**（本轮） |

> 注：`test_*.py` 也在 `CODE/leo_sim/` 下，因此**测试文件的改动同样会改变 `code_sha256`**。冻结时不能只冻结非测试文件。

**B. 决定 `execution_chain_sha256` 的 6 个文件** —— 本轮**未改动**其中任何一个（值与第二轮相同），但冻结时必须一并固定：

`CODE/experiment_platform/{authorize_experiment,v2_analysis,v2_serial_gate}.py`、`CODE/scripts/remote/{deployment_guard.py,remote_job.py,run-remote.sh}`

**C. 既不属于 A 也不属于 B、但属于本次改动集的 2 个文件**：

| 文件 | 状态 |
|---|---|
| `CODE/experiment_platform/primary_metrics.py` | **新增**（第一轮） |
| `CODE/leo_sim/tests/test_pairing_trace_identity.py`、`test_review_residuals.py` | 修改（**开工前**未提交；不是本轮作者所改） |
| `CODE/scripts/remote/pull-results-remote.sh` | 修改（**开工前**未提交；不是本轮作者所改） |

---

## 2. 明确不得进入冻结集合的东西

| 路径 | 为什么 |
|---|---|
| `ANALYSIS/ARRIVAL-TIME-T1-20260923/` | 他人唯一研究材料（未跟踪）；与平台代码身份无关 |
| `CODE/leo_sim/profiles/linkgen_generic_rf_mcs_1g.yaml`、`linkgen_published_optical_100g.yaml`、`t1_pressure_corridor_v2.yaml` | 同上：虽然位于 `CODE/leo_sim/` 下，但**不在 `code_sha256` 的 glob 里**（该函数只取 `*.py`），且属研究材料 |
| `CODE/work/WP-LEO-V2-T1-TRUST-CHAIN-V6/round3/` | 既有复核回执（只读证据） |
| `CODE/work/WP-MATRIX/` | 先前遗留的未跟踪目录（本任务始终未触碰） |
| `CODE/work/WP-LEO-V2-PRE-EXPERIMENT-TRUST/` | 本任务的证据/报告产物；可作为附件，但不是代码身份的一部分 |
| `CODE/work/WP-LEO-V2-PRE-TRUST-REPRO/`、`EXPERIMENTS/EXP-LEO-V2-PRE-TRUST-REPRO/`、`CODE/Results/EXP-LEO-V2-PRE-TRUST-REPRO-*` | 复现脚本的生成物（可重建） |

---

## 3. 建立可复核版本的操作顺序（不得跳步）

```text
1. 冻结：把 §1 的 22 个文件按当前字节提交到一个分支（本轮任务书禁止提交，故由下一步执行者完成）
2. 记录：提交后回填 criteria.json 的 frozen_at_sha，并写入 code_sha256 / execution_chain_sha256
3. 复核：用 round2/criteria.json（上一轮 18 条）与 round3/ 的新增条款做独立冷启动复核
4. 只在复核 PASS 之后：重编矩阵 → 重审 → 重签授权 → 部署 → 正式运行
```

**为什么必须按这个顺序**：`CODE/leo_sim/*.py` 的任何变化都会作废该 checkout 上的全部已编译矩阵与已发授权
（平台审计 finding B2）。"改了再补审"与"审了再改"在哈希绑定下互斥。
