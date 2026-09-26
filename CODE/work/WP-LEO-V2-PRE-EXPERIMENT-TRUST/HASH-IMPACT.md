# 代码哈希变化对旧矩阵、授权与回执的影响（R11 收口轮）

**日期**：2026-09-25  ·  **HEAD**：`98c858f642aeca8056d215224b20783b00354026`（未提交改动都在工作树里）

本文件回答一个问题：**这一轮改动之后，哪些既有产物不能再用了，为什么，以及正确的重启顺序是什么。**

---

## 1. 三个代码身份（实测）

| 树 | code_sha256（覆盖 `CODE/leo_sim/*.py`） | execution_chain_sha256（6 个授权/部署文件） |
|---|---|---|
| 提交态 `98c858f` 干净树 | `2a114890e8b11e429fec45a6b1efcb81c1d80a8e195a9c9407a250d218f16dbe` | `2b6096fc281d4f1ec5adbb2850bce0065c272aed21db4fa8b250b4c6d542d1de` |
| 同上 + **开工前**未提交改动 | `50f4c17b41850f254ea915356392e48cd25ae97c10558d8d510eca8f7a938a0a` | 见 §3 注 |
| 同上 + 本轮（R11 收口）改动 | `681a10b1c71f14f3b266ddd1fa9d841f4bf2096d379f7aa6ed78bb27a1bcc02b` | `d3f283d49dad4ef716a7ccf72342a572243133901d77b46cce616bda186f1e15` |

`receipt.code_sha256()` 的实现：对 `CODE/leo_sim/*.py`（非递归）的**文件名 + 字节**做 SHA256（`receipt.py:160-167`）。因此**只要 `CODE/leo_sim` 下任何一个 .py 字节变了，这个身份就变了** —— 与改动是否"安全"无关。

`governance.execution_chain_sha256()`（`governance.py:43-52`）覆盖：

| 文件 | 本轮是否变化 |
|---|---|
| `CODE/experiment_platform/authorize_experiment.py` | 未变 |
| `CODE/experiment_platform/v2_analysis.py` | **变了**（第一轮：主指标词表前置判定） |
| `CODE/experiment_platform/v2_serial_gate.py` | 未变 |
| `CODE/scripts/remote/deployment_guard.py` | 未变 |
| `CODE/scripts/remote/remote_job.py` | **变了**（本轮：流契约改为按 run 目录解析） |
| `CODE/scripts/remote/run-remote.sh` | 未变 |

> 注：开工前的未提交改动已经把 `__main__.py` / `receipt.py` / `remote_job.py` 改过一轮，所以"开工前"那一行的 execution chain 也已经不等于干净树的值；此处不重复列出，因为无法从 git 对象重建（改动从未提交）。可复算的量是干净树与本轮两行。

---

## 2. 被作废的产物（精确到字段与重算点）

| 产物 | 绑定字段 | 重算点 | 现状 |
|---|---|---|---|
| `EXPERIMENTS/<id>/resolved/*.leo-sim.yaml` | 由 resolved config 派生 | —— | **不受影响**（本轮未新增/修改任何 config key；实测 `smoke.yaml` 的 `config_sha256` 仍为 `934315391f9572ca2f03a641a3b2a800889330e8f0579fccbb7ee29379c68816`） |
| `EXPERIMENTS/<id>/run-manifest.json` | 每个 cell 的 `config_sha256 / code_sha256 / execution_chain_sha256 / controlled_signature` | `matrix._resolve_cells` | **作废**：code_sha256 与 execution_chain_sha256 都变了 |
| `EXPERIMENTS/<id>/authorization.json` | `authorized_runs[*].{config_sha256, code_sha256, execution_chain_sha256}` + `experiment_artifact_hashes` | `authorize_experiment.build_authorization` | **作废**：任何严格重算都会失败 |
| `EXPERIMENTS/<id>/analysis-request.json / RUNBOOK.md` | 由 manifest/accounting 派生 | `matrix._analysis_document` / `_render_runbook` | 不带 `design` 的请求**逐字节不变**（第一轮已验证），但绑定在 manifest 上，随矩阵一起重编 |
| `CODE/work/**/finalization.json` | `artifact_hashes`（含上述全部） | `finalize_decision.evaluate_decision` | **作废**：绑定的是重编前的字节 |
| 运行回执 `receipt.json` | `code_sha256` | `receipt.build_receipt` | **历史回执仍可"读"**：其内部一致性（trace/manifest/ledgers/config）可由 `receipt.verify_run_artifacts` 重算；但**严格** `verify_receipt_dir` 会因 `local code_sha256 != recorded` 失败。这不是损坏，是设计 |
| `formal_run.json / governance_receipt.json` | `code_sha256`（经 formal dict）/ `execution_chain_sha256` | `_write_formal_witness` / `build_v2_governance_receipt` | **作废**：execution chain 变了 |

**一句话**：`CODE/leo_sim` 或 execution chain 的任何一个字节变化，都会让**该 checkout 上全部已编译矩阵与已发授权失效**（平台审计 finding B2）。这不是本轮引入的：**开工前的未提交改动已经造成过一次**，本轮又推进一次。

---

## 3. 不许做的事

1. **不许复用旧授权。** 旧 `authorization.json` 的 `code_sha256` 指向一个不再是当前代码的身份；用它启动正式运行等于声称"跑的是被审的那份代码"。
2. **不许把旧回执的数值搬进新回执。** 历史 `receipt.json` 里的 @utilization` 等数字属于旧身份，只能作为历史记录引用，不能被新一轮分析当作当前读数。
3. **不许为了让 gate 变绿而放宽 `verify_v2_formal_result` / `verify_receipt_streams` / pull-back 的任何一条。** 本轮的三道门都是"缺失即失败"，放宽它们等于把 R11 重新打开。

---

## 4. 正确的重启顺序（唯一可行）

`
冻结 CODE/leo_sim + execution chain
  → 重新编译矩阵（matrix.compile_matrix_experiment）
  → 独立审阅（三份回执绑定新字节）
  → finalization + authorization（绑定新 code_sha256 / execution_chain_sha256）
  → 部署（deployment_guard 校验 execution chain）
  → 正式运行（写 V5 或 V6 回执 + formal_run.json）
  → VM 门：verify_receipt_dir + verify_receipt_streams(require_streams=True)
  → 拉回（pull-results-remote.sh，含两条流与其侧车）
  → 本地门：python3 -m CODE.leo_sim receipt verify-pulled --run-dir <dir>
`

**"改了再补审"与"审了再改"在哈希绑定下互斥。** 审阅后重新编译会使回执全部失效。

---

## 5. 本轮新增的身份说明

本轮**没有**引入任何新的 config key，因此：

- `config_sha256` 不变（对同一份 config 文本）；
- 但 `code_sha256` 与 `execution_chain_sha256` **都变了**；
- 所以**必须重编、重审、重签授权**才能跑正式实验。**当前 checkout 上不存在任何有效的正式授权。**

---

## 6. 仍需 VM 的部分（不得由本地替代）

| 项 | 为什么本地做不了 |
|---|---|
| 部署身份（`deployment_receipt` / `source_tree_sha256`） | 需要真实 VM 上的部署动作 |
| 外部启动证人 `.remote_runtime/launches/<nonce>.json` | 由 VM 侧 run-remote.sh 写，结果目录无法生成 |
| `governance_receipt.json` | 只由 `remote_job.build_v2_governance_receipt` 在 VM 上生成 |
| 端到端：部署 → 授权 → 运行 → 回执 → 拉回 → 本地重验 | 需要 VM |

**因此 R11 的 VM 门保持"未通过"**（见 `release-table.json`）。本轮的收口只到"入口层与本地门"，且这一点在报告里逐项写明。
