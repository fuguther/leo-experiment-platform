# 冻结当前代码的准确文件范围（由生成器产出）

> 本文件由 `round4/generate_freeze_scope.py` 生成。
> **§1–§3 的路径、数量、行号与哈希全部由生成器从实际函数与 `git status` 产出**，不做手写；§4 的排除清单与 §5 的操作顺序是人工维护的策略文本（生成器中的常量），生成器只保证这两节与代码/仓库现状不矛盾。
> 任何与代码的不一致都是生成器的缺陷，而不是文档过期。

| 量 | 值 |
|---|---|
| 开发工作树 HEAD（本文件生成处，**不是**冻结提交） | `98c858f642aeca8056d215224b20783b00354026` |
| **冻结提交** | `eec0db985668c038c3d32cb1d1599019b82dae01` |
| 本文件所在提交 | `PENDING` |
| code_sha256()（当前树） | `e77472175e3a652f9b685808ed91d4626118ada305428a18f06f5749857365ae` |
| execution_chain 汇总 | `121d4c1ac2cf124feb912af1fe1487f0ddae6223d748a987629c2565c62f87aa` |
| 冻结集合文件数 | **22**（已跟踪修改 12 + 新增 10） |
| 其中：运行代码哈希覆盖 | 8 |
| 其中：执行链哈希覆盖 | 2 |
| 其中：测试 | 10 |
| 冻结集合汇总哈希 | `ca5e44ee8ff8fb3a14242354875440397737ef0042cd537327744882644ede34` |

---

## 1. 运行代码哈希覆盖：`receipt.code_sha256()`

实现（`CODE/leo_sim/receipt.py:160-167`，行号由生成器从源码读出）：对 `CODE/leo_sim/` 下 **非递归**的 `*.py`，按文件名排序，
依次 `h.update(name)` 与 `h.update(sha256(bytes).digest())`。

- **覆盖集合**：`CODE/leo_sim/*.py`（31 个文件）
- **不覆盖**：`CODE/leo_sim/tests/`（子目录，非递归）、`CODE/leo_sim/profiles/*.yaml`、`CODE/experiment_platform/`、`CODE/scripts/`。
  测试因此**需要冻结但不属于本哈希的覆盖范围**；不为迎合文档修改哈希算法。
- **汇总哈希**：`e77472175e3a652f9b685808ed91d4626118ada305428a18f06f5749857365ae`（即该函数的返回值本身）

| 文件 | sha256 |
|---|---|
| `CODE/leo_sim/__init__.py` | `ffa389fad40f7de4a3e5f9d710dc897c58328c97a8ad1c4f2b945e776b713225` |
| `CODE/leo_sim/__main__.py` | `18a5c82f623b92e4efb2beb9b4025c28b698ee9dbd9d3d6c7681f662ebfb4643` |
| `CODE/leo_sim/acceptance.py` | `ceb07ba1e797c005a3523bc056c1ac9a82f566ec1c1624e1ecc526663d724802` |
| `CODE/leo_sim/comparison.py` | `7a4e60a14a4d5773e965efa0156f87ce6809cd7b17829371a92864ee1f02fcea` |
| `CODE/leo_sim/config.py` | `f904eeebeb29ab8abd6da64381ab5b0f1912cce7324fd617b521bd91f533aec7` |
| `CODE/leo_sim/control.py` | `1ae5c16e0ae4fe1fd30386d903e07fa5748b0968cc29471909d07fab61f51488` |
| `CODE/leo_sim/counterfactual.py` | `58b0ee2827a791dc6e62a2bb3a680f20b597f09593b320b6327104b75740f8bf` |
| `CODE/leo_sim/coverage.py` | `7f7b5873d3e9064525fac59b5f8e6562653586f01cda41d774c578010652eef1` |
| `CODE/leo_sim/decision_ledger.py` | `e7ecdeb0fa0ada639056df3c1c06bbb81bd572c9acce0eada1cfb7ff2a2ed95f` |
| `CODE/leo_sim/fates.py` | `7fead037dba42c13060d8f526472c33a071368fda32cb13db743761d3c59ecfb` |
| `CODE/leo_sim/governance.py` | `312e9868961e2472f74590912ac308af61749d9c26a19c11ca05d99633e248d8` |
| `CODE/leo_sim/grid.py` | `af79e91e116f430410a02d178ecdc53dedd244d2d70da1b66b15c300bf348aa4` |
| `CODE/leo_sim/kernel.py` | `246ecc01ec42928d0bde2b60132ad4062d7466961d378f559da0bb30db39be8a` |
| `CODE/leo_sim/learning.py` | `b2b0ba272645e4c799ac39ee11d95884659803de482368f73ab9af18b0f0feaa` |
| `CODE/leo_sim/link_budget.py` | `9755c4222aa9ee268b1c1b62ee71e075d4571f50ab21920f7fea1efc02a9db88` |
| `CODE/leo_sim/matrix.py` | `c6b1203fbacdadb8b549bd47f42c7cc9f02210b78809e6206500d9fa2a55edbe` |
| `CODE/leo_sim/metrics.py` | `b22674f960172d0acb2e37f4057519f44ba6aaf4e2b0fea01ced054f017140b6` |
| `CODE/leo_sim/metrics_independent.py` | `778726b7121637af50f91ad08b913e0a2e5c464e92fb65a0ed9d961bdb03f2e7` |
| `CODE/leo_sim/model.py` | `b49485ef86fa7ab4649b905a0d20ebd4ba9943c56a112fb60cde0ab8316eb38c` |
| `CODE/leo_sim/outage.py` | `9d4ce37a995bd10e7a56ea7f1f165be7fb74b7c8cb517cbba4c7e59e44856f60` |
| `CODE/leo_sim/platform_check.py` | `a865f85fd92eacea13eb558b25a0f856939658d99991784fee5dad3f569359be` |
| `CODE/leo_sim/population.py` | `507c0c72f978f0a2afeb2989f6e53ac33aa30c811eab11025b5635393121ed97` |
| `CODE/leo_sim/pullback.py` | `b6e395977bc01dab2958a7e4177229af56feb7524359dbbd51bfb8b654b8d869` |
| `CODE/leo_sim/q0.py` | `9fbaa4945e4f314e8395de775396c0b6fc72e57e58698e5717b72881cde5af69` |
| `CODE/leo_sim/receipt.py` | `759c7f76db30673a010ad0c8cbe073289860c51da48c7a67ff2616081f6446de` |
| `CODE/leo_sim/recompute.py` | `9d141996afbd7d2718542044974c50350d204b7b1e4ab05beca88a82dd2d7d2b` |
| `CODE/leo_sim/rng.py` | `10435d9415b1d3d11a091a2831572461e17845838616f2260d185f36bc35642d` |
| `CODE/leo_sim/routing.py` | `0d37c2cece590af79b21caa8ba86b026dac4a3396142e2309bdbe09fe4981046` |
| `CODE/leo_sim/scene_check.py` | `2a631a77d37f33e8b15a8574f582c27d9deaba0036724d29d90ff30f88ac57a7` |
| `CODE/leo_sim/trace.py` | `6a29565cd8faa9498bc608b3b9a828ac8527e44ef98e787518165f8cff26291d` |
| `CODE/leo_sim/trace_family.py` | `e9eb8f91f5254519b0352094342f4675d87dfc3e6522632db5c60ee1ea580278` |

> 本轮改动落在该覆盖内的文件：`CODE/leo_sim/__main__.py`, `CODE/leo_sim/config.py`, `CODE/leo_sim/matrix.py`, `CODE/leo_sim/metrics_independent.py`, `CODE/leo_sim/pullback.py`, `CODE/leo_sim/receipt.py`, `CODE/leo_sim/recompute.py`, `CODE/leo_sim/trace.py`

---

## 2. 执行链哈希覆盖：`governance.execution_chain_sha256()`

实现（`CODE/leo_sim/governance.py:43-52`，行号由生成器从源码读出）：对 `EXECUTION_CHAIN_PATHS` 逐个取文件字节的 sha256，返回 `{path: sha256}` 映射。

- **覆盖集合**：6 个显式路径（不随目录扫描变化）
- **汇总哈希**（对映射做 `json.dumps(mapping, sort_keys=True, separators=(",", ":"))` 后取 sha256；默认分隔符会得到不同的值，所以分隔符是规则的一部分）：`121d4c1ac2cf124feb912af1fe1487f0ddae6223d748a987629c2565c62f87aa`

| 文件 | sha256 |
|---|---|
| `CODE/experiment_platform/authorize_experiment.py` | `14f6d5ebc8f913764045c112b0b10ba3c3871f8820b7e2d3c1fa47ac9a72aebb` |
| `CODE/experiment_platform/v2_analysis.py` | `a63b2cdef6c77ea33e399f4d9d87c993b16edb4f71e2addfbff827089531eef2` |
| `CODE/experiment_platform/v2_serial_gate.py` | `a139f232236acf921874e177817361288f0799f58f78e12a00d4ea3fce7953a3` |
| `CODE/scripts/remote/deployment_guard.py` | `c1580fdc42c3fdd5c650304998688d8ccdf69825c0070c65ba5b8c59c063973b` |
| `CODE/scripts/remote/remote_job.py` | `c468c2bcb77832f31b0b0bbaf590c04be276a678019738f73640e369bdb1d13e` |
| `CODE/scripts/remote/run-remote.sh` | `9b78a509fb5deb63c270247588b6390c3ec82f1c67ee983272174fedc5ad24e3` |

> 本轮改动落在该覆盖内的文件：`CODE/experiment_platform/v2_analysis.py`, `CODE/scripts/remote/remote_job.py`

---

## 3. 版本复现所需的测试与其他依赖

**测试文件（10 个；必须在冻结集合内，但不在第 1 节的哈希覆盖内）**

| 文件 | sha256 |
|---|---|
| `CODE/experiment_platform/tests/test_design_accounting_extension.py` | `49f53c9d9df77aea40d35962d973f64c1c5ad005557300adde6feadd98dd58a4` |
| `CODE/experiment_platform/tests/test_stale_authorization_guard.py` | `988a3ece83d0b7345a04325974b6b8f28501a53daf056a25d4724f8357a8834a` |
| `CODE/leo_sim/tests/test_burst_transform.py` | `326d8fecb2daaf0e1f3e18a5490b2bd8c3d34b8c5b269c0e16319248389e7527` |
| `CODE/leo_sim/tests/test_config.py` | `e85c9063e7ab16e693f41c6ccfc7dad5b825f24c43e3cbd8de6ac7d6c696f7b7` |
| `CODE/leo_sim/tests/test_formal_stream_gate.py` | `2cbee00c5ff9fe96629b71c2a202f05e3d6d01e559b87958813af237cc5be370` |
| `CODE/leo_sim/tests/test_pairing_trace_identity.py` | `cbc1a1a9cd7b1ff5669c03fee0a9101ad2e772bb998164d38f938b74ce6a757c` |
| `CODE/leo_sim/tests/test_pre_experiment_trust.py` | `ee2d1ea6b838fe52351788da35ea5e54badb490bd2d468444ceb8a1384960bf4` |
| `CODE/leo_sim/tests/test_pre_experiment_trust_round2.py` | `bd466cdaf984653c2d3ff46dde28b5cfdab355692dac25195b7e5ca5a1c112c9` |
| `CODE/leo_sim/tests/test_pullback_authority.py` | `fcbc1a7af484a9364362e0054d2badb68d021f39aedb2a1270a1061fc1392605` |
| `CODE/leo_sim/tests/test_review_residuals.py` | `f137bac8fdb701646fd66851a969cb4d137c0f40a1886b425bc34202064f85ff` |

**其他依赖（既不在运行代码哈希、也不在执行链哈希覆盖内，但属于本次改动集）**

| 文件 | sha256 |
|---|---|
| `CODE/experiment_platform/primary_metrics.py` | `9e5947b551d4c7365e416057bafd5d7d9f09e6182abed2e440237a4ba0d56260` |
| `CODE/scripts/remote/pull-results-remote.sh` | `15a7677097265dd93fc24999235cf96c692e653056035a74b385930e3be1b03b` |

**冻结集合全量（22 个文件，逐文件 sha256）**

| 文件 | sha256 |
|---|---|
| `CODE/experiment_platform/primary_metrics.py` | `9e5947b551d4c7365e416057bafd5d7d9f09e6182abed2e440237a4ba0d56260` |
| `CODE/experiment_platform/tests/test_design_accounting_extension.py` | `49f53c9d9df77aea40d35962d973f64c1c5ad005557300adde6feadd98dd58a4` |
| `CODE/experiment_platform/tests/test_stale_authorization_guard.py` | `988a3ece83d0b7345a04325974b6b8f28501a53daf056a25d4724f8357a8834a` |
| `CODE/experiment_platform/v2_analysis.py` | `a63b2cdef6c77ea33e399f4d9d87c993b16edb4f71e2addfbff827089531eef2` |
| `CODE/leo_sim/__main__.py` | `18a5c82f623b92e4efb2beb9b4025c28b698ee9dbd9d3d6c7681f662ebfb4643` |
| `CODE/leo_sim/config.py` | `f904eeebeb29ab8abd6da64381ab5b0f1912cce7324fd617b521bd91f533aec7` |
| `CODE/leo_sim/matrix.py` | `c6b1203fbacdadb8b549bd47f42c7cc9f02210b78809e6206500d9fa2a55edbe` |
| `CODE/leo_sim/metrics_independent.py` | `778726b7121637af50f91ad08b913e0a2e5c464e92fb65a0ed9d961bdb03f2e7` |
| `CODE/leo_sim/pullback.py` | `b6e395977bc01dab2958a7e4177229af56feb7524359dbbd51bfb8b654b8d869` |
| `CODE/leo_sim/receipt.py` | `759c7f76db30673a010ad0c8cbe073289860c51da48c7a67ff2616081f6446de` |
| `CODE/leo_sim/recompute.py` | `9d141996afbd7d2718542044974c50350d204b7b1e4ab05beca88a82dd2d7d2b` |
| `CODE/leo_sim/tests/test_burst_transform.py` | `326d8fecb2daaf0e1f3e18a5490b2bd8c3d34b8c5b269c0e16319248389e7527` |
| `CODE/leo_sim/tests/test_config.py` | `e85c9063e7ab16e693f41c6ccfc7dad5b825f24c43e3cbd8de6ac7d6c696f7b7` |
| `CODE/leo_sim/tests/test_formal_stream_gate.py` | `2cbee00c5ff9fe96629b71c2a202f05e3d6d01e559b87958813af237cc5be370` |
| `CODE/leo_sim/tests/test_pairing_trace_identity.py` | `cbc1a1a9cd7b1ff5669c03fee0a9101ad2e772bb998164d38f938b74ce6a757c` |
| `CODE/leo_sim/tests/test_pre_experiment_trust.py` | `ee2d1ea6b838fe52351788da35ea5e54badb490bd2d468444ceb8a1384960bf4` |
| `CODE/leo_sim/tests/test_pre_experiment_trust_round2.py` | `bd466cdaf984653c2d3ff46dde28b5cfdab355692dac25195b7e5ca5a1c112c9` |
| `CODE/leo_sim/tests/test_pullback_authority.py` | `fcbc1a7af484a9364362e0054d2badb68d021f39aedb2a1270a1061fc1392605` |
| `CODE/leo_sim/tests/test_review_residuals.py` | `f137bac8fdb701646fd66851a969cb4d137c0f40a1886b425bc34202064f85ff` |
| `CODE/leo_sim/trace.py` | `6a29565cd8faa9498bc608b3b9a828ac8527e44ef98e787518165f8cff26291d` |
| `CODE/scripts/remote/pull-results-remote.sh` | `15a7677097265dd93fc24999235cf96c692e653056035a74b385930e3be1b03b` |
| `CODE/scripts/remote/remote_job.py` | `c468c2bcb77832f31b0b0bbaf590c04be276a678019738f73640e369bdb1d13e` |

---

## 4. 明确不得进入冻结集合的东西

| 路径 | 为什么 |
|---|---|
| `ANALYSIS/ARRIVAL-TIME-T1-20260923/` | 他人的唯一研究材料（未跟踪） |
| `CODE/leo_sim/profiles/linkgen_generic_rf_mcs_1g.yaml` | 研究材料；位于 CODE/leo_sim/ 下但不在 *.py 的非递归 glob 内 |
| `CODE/leo_sim/profiles/linkgen_published_optical_100g.yaml` | 同上 |
| `CODE/leo_sim/profiles/t1_pressure_corridor_v2.yaml` | 同上 |
| `CODE/work/WP-LEO-V2-T1-TRUST-CHAIN-V6/round3/` | 既有复核回执（只读证据） |
| `CODE/work/WP-MATRIX/` | 先前遗留的未跟踪目录 |
| `CODE/work/WP-LEO-V2-PRE-EXPERIMENT-TRUST/` | 本任务的证据/报告；可作附件，非代码身份 |
| `CODE/work/WP-LEO-V2-PRE-TRUST-REPRO/` | 复现脚本生成物（可重建） |
| `EXPERIMENTS/EXP-LEO-V2-PRE-TRUST-REPRO/` | 同上 |
| `CODE/Results/` | 运行结果目录（gitignored） |
| `CODE/scripts/remote/remote.env` | 本机 VM 连接配置（gitignored，含主机信息） |

---

## 5. 建立可复核版本的操作顺序

```text
1. 在独立工作区只 add §3 列出的全部路径建一次提交（禁止 git add . / -A）；
   路径条数以生成器当次读到的为准，不要抄写数字
2. 提交后回填 criteria.json 的 frozen_at_sha，并记录 code_sha256、
   execution_chain 汇总与冻结集合汇总哈希
3. 在该提交的全新检出目录做独立冷启动复核（作者自检不算复核）
4. 只在复核 PASS 之后：重编矩阵 -> 重审 -> 重签授权 -> 部署 -> 正式运行
```

**为什么必须按这个顺序**：`CODE/leo_sim/*.py` 的任何变化都会作废该 checkout 上的
全部已编译矩阵与已发授权（平台审计 finding B2）。

---

## 生成命令

```bash
python3 CODE/work/WP-LEO-V2-PRE-EXPERIMENT-TRUST/round4/generate_freeze_scope.py \
    --out CODE/work/WP-LEO-V2-PRE-EXPERIMENT-TRUST/round3/FREEZE-SCOPE.md
```
