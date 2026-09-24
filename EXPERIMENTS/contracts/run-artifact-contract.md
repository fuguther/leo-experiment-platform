# Run 产物契约

本契约区分三部分运行产物：

- **通用/legacy 产物族**：`legacy_gateway` 运行时（外部旧平台兼容合同；本仓库不存在可直接运行的 legacy 运行时，旧平台运行器不在本库）产生的结果目录文件（`run_trace/run_meta.json`、`config_used.json`、`artifact_manifest.json`、`experiment_bundle/summary_metrics.csv` 等）。本地 `leo_sim` CLI 不产生该族。
- **`leo_sim_v2` 核心结果文件**：`leo_sim` 运行产生的结果目录文件（`receipt.json`、`resolved_config.json`、`manifest.json`、`trace.csv`、`ledgers.json`）。本地 CLI 与正式运行都会生成这些文件，但它们单独存在不能构成正式证据。
- **`leo_sim_v2` 正式治理增量**：只有正式运行链产生（`formal_run.json`、`governance_receipt.json`、外部启动证人、`_run_receipts/` 指针与授权/最终化绑定）。

> 通用产物不能替代 V2 治理产物；任一族的产物单独存在都不能建立论文 claim。
> 论文 claim 只经 `PAPER/README.md` 定义的合格证据门进入。

## 两层门：运行准入不等于科研充分

正式实验必须同时区分两类门，禁止用前一类替代后一类：

- **运行/证据准入门**：请求中的 `acceptance` 用于拒绝完全退化、与配置意图不符或证据链不完整的运行；`governance_receipt.json` 的 `research_eligible=true` 只表示该运行可以进入后验分析。它不表示样本量充分、场景达到目标工作区、干预可识别或论文 claim 成立。
- **科研充分性门**：由预注册 analysis/decision contract、场景分类、物理有效性、独立条件数、统计设计和逐 claim 审阅共同决定。即使所有运行均为 `research_eligible=true`，未通过这一层也只能作为工程或描述性证据。

`min_delivered_packets` 等低门槛可以在有记录的诊断实验中作为非退化门，但必须同时冻结更强的场景/物理判定；不得把低门槛通过表述为“实验质量充分”。完全相同的 resolved config 重执行只增加可重复性证据，不增加独立实验条件数。后验 V2 analyzer 必须从 matrix cells 重算并输出这组记账；旧授权中即使没有 compiler 预填字段，也不得省略。

## 通用/legacy 产物族

（运行时范围：`legacy_gateway`——外部旧平台兼容合同，执行需要外部旧平台，本仓库不能独立完成；本地 `leo_sim` CLI/冒烟不产生该族文件。）

正式比较的最小 L0 产物：

- `run_trace/run_meta.json`：schema、run/experiment/attempt ID、自然结束、中断、seed、实际方法、信息条件、配置 hash、代码 commit、created/received/lost/in-flight。
- `config_used.json`：实际执行的 canonical 配置，不从文件名还原。
- `artifact_manifest.json`：每个必要输入和输出的相对路径、字节数、SHA256、schema 和完整性。

标准 L1 产物：`experiment_bundle/summary_metrics.csv`、per-block latency、per-OD tail。诊断 L2/L3 产物按分析请求声明。缺字段必须标 `NOT_ELIGIBLE`，不得静默填 0。

## leo_sim_v2 核心结果文件

（产生范围：`leo_sim` 本身——本地 CLI `python -m CODE.leo_sim run` 与正式运行都会写出；正式证据还需下文治理增量。）

- `receipt.json`（`leo-sim-receipt/v5`）：运行回执，绑定 canonical config hash、trace manifest/identity hash、input hash、code hash 与 seed，并报告 `natural_end`、`conservation_ok`。
- `resolved_config.json`：实际执行的 canonical 配置字节，不从文件名还原。
- `manifest.json` 与 `trace.csv`：trace/manifest 身份，由 `trace_identity_sha256`、`trace_manifest_sha256` 绑定到回执。
- `ledgers.json`：按字段权威（recomputed / ledger_consistency / diagnostic）可校验的账本。

本地 CLI 生成这些文件时没有任何正式治理增量（授权绑定、外部启动证人、治理回执
均缺失，具体增量文件见下文）；任何本地 smoke 不得升级为正式证据。

## leo_sim_v2 正式治理增量

（产生范围：正式运行链 `CODE/scripts/remote/run-remote.sh` → 远端
`CODE/scripts/remote/remote_job.py` → `python -m CODE.leo_sim run`（绑定
authorization、launch nonce、expect run id）。正式运行产物位于
`CODE/Results/<run_id>/`，输出目录解析必须等于该路径，否则 fail-loud。）

- `formal_run.json`（`leo-sim-formal-run/v1`）：由 leo_sim 自身在自然结束后写入的正式证人，绑定 run_id、config/code/authorization hash、`receipt_sha256`、`natural_end`、`conservation_ok`；同时在兄弟目录 `_run_receipts/` 写 `<launch_nonce>.txt` 结果指针。
- `governance_receipt.json`：由 `CODE/scripts/remote/remote_job.py`（`build_v2_governance_receipt`）在结果目录可用后生成，包含 `payload_sha256`、`execution_chain_sha256`、`authorization_sha256`、deployment 身份、`run_receipt_sha256`、`resolved_config_sha256`、`trace_manifest_sha256` 与 `research_eligible`。即使存在 verification errors、子进程失败或非自然结束，该文件也可能存在，此时 `research_eligible` 必须为 `false`——文件存在不等于正式准入。授权与最终化（前置）只绑定和验证编译产物、审阅回执与授权矩阵，不生成运行后的 governance receipt；正式准入由 `v2_analysis` 后验验证（重算并核对全部绑定 hash）。
- **外部启动证人**：`CODE/Results/_external_launch_witness/<run_id>.json`，由正式拉回流程从 canonical VM `.remote_runtime/launches/<launch_nonce>.json` 只读取得；不可由结果目录生成或替代，缺失即 fail-loud。

正式准入（文件齐全且全部满足；任一不满足即 `NOT_ELIGIBLE`，不得降级为通用/legacy 产物或本地核心文件继续使用）：

- 自然结束与守恒：`natural_end=true` 且 `conservation_ok=true`。
- 无任何 verification errors：`governance_receipt.json` 的 `research_eligible=true` 且 `verification_errors=[]`。
- 身份一致：`receipt.json`、`formal_run.json`、`governance_receipt.json` 与授权的 config、code、authorization、trace identity hash 全部一致。
- 持久化分析验证：`CODE/experiment_platform/v2_analysis.py` 对授权 cohort 重算并持久化 `analysis-manifest.json`（schema `leo-sim-v2-analysis/v1`），绑定每个 run 的证据文件 hash；重新验证时与重算结果逐项对照，不一致即失败。只有验证通过的 manifest 才能作为 candidate evidence 进入 `PAPER/eligible_claims.py` 的合格门。
