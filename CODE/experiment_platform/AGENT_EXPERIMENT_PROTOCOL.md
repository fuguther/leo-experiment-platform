# Agent 实验协议

本平台的一等用户是 Agent。网页只用于人类查看同一份参数目录，不拥有独立默认值或规则。

## Agent 必须提交什么

复制 `EXPERIMENTS/templates/experiment-request.example.json`，只填写：

1. 研究问题、假设和可证伪条件；
2. 实验角色：探索、诊断、确认或全局信息上界；
3. 基础 profile；
4. 本次允许变化的参数路径；
5. 各 arm 的角色、方法族、信息条件、预算、checkpoint lineage 和少量差异；
6. seed、主指标、必要产物与完整的分析预注册。

Agent 不应复制整份配置，也不应从旧 run 名称猜参数。V2 实验使用紧凑编译请求
（字段集合由 `CODE/leo_sim/governance.py` 的编译入口校验），与上面的 legacy
模板格式不同。

## 编译

编译分两族。通用/legacy 编译是**外部旧平台兼容合同**：本仓库不存在可直接
运行的 `legacy_gateway` 运行时（旧平台运行器不在本库），legacy 执行需要外部
旧平台，不能从本仓库独立完成；本仓库当前唯一可正式执行的路线是 `leo_sim_v2`。
产物后缀与运行入口的对应关系由 `CODE/scripts/remote/remote_job.py` 的
`validate_formal_paths` 强制（`leo_sim_v2` 只接受编译出的 `*.leo-sim.yaml`，
通用/legacy 只接受 `*.config.json`，不匹配即 fail-loud）。

**通用/legacy 编译（外部旧平台兼容合同）**（生成 `resolved/<arm>.s<seed>.config.json`）：

```bash
python3 CODE/experiment_platform/compile_experiment.py \
  EXPERIMENTS/templates/experiment-request.example.json \
  --out EXPERIMENTS/EXP-YYYYMMDD-NNN
```

编译器读取 `parameter-catalog.json`、`profiles.json` 和 `metric-catalog.json`，生成：

- `request.json`：进入编译的原始请求快照；
- `resolved/<arm>.s<seed>.config.json`：每个 arm×seed 唯一的不可变机器配置，供外部旧平台的 `legacy_gateway` 运行入口使用（本仓库不提供 legacy 正式执行）；
- `run-manifest.json`：计划运行、seed、信息条件、完成契约和产物要求；
- `analysis-request.json`：分析问题、主指标、排除规则和不能推出什么；
- `compile-report.json`：阻塞错误、警告、使用的 profile 和 hash。

只要 `compile-report.json` 有 error，Agent 就不得生成启动命令。

**leo_sim_v2 编译**（生成 `resolved/<run_id>.leo-sim.yaml` 与 `RUNBOOK.md`，
配合 `--runtime-kind leo_sim_v2` 运行）：

```bash
python3 -m CODE.leo_sim experiment compile \
  --request <compact-v2-request.json> \
  --out EXPERIMENTS/EXP-...
```

生成：

- `resolved/<run_id>.leo-sim.yaml`：每个 run 唯一的不可变机器配置，只交给正式运行入口以 `leo_sim_v2` 执行；
- `request.json`、`run-manifest.json`、`analysis-request.json`：与 legacy 侧同义的编译/计划/分析产物（schema 以 `CODE/leo_sim/governance.py` 的编译入口为准）；
- `RUNBOOK.md`：含该实验的正式启动命令与审阅→最终化→授权→部署→运行顺序。

## 设计规则

- strict 设计必须恰有一个无修改的 `control` arm、恰有一个变化因素，且不得使用 `coupled_parameters`。
- 技术耦合或多因素设计只能使用 `exploratory_multi_factor`，不得声称单因素因果。
- 编译器比较实际展开后的配置；未生效、未接线或控制因素外的差异会直接 BLOCK。
- 探索性多因素组合允许运行，但必须写 `one_change_policy=exploratory_multi_factor`，且不能声称单因素因果。
- 每个 arm 分别声明训练、评估、部署的信息集合；编译器根据实际配置推导并逐项核对。
- `oracle_global_dijkstra` 只能用于 `upper_bound` 角色。
- 当前训练恢复链未证明能可靠恢复完整状态；`warm_start` 与 `exact_resume` 都会被阻断。
- `code-compatibility-v1` 只是兼容性参考，不得用于 confirmatory 实验。
- 编译只产生 `execution_authorized=false` 的设计产物；独立设计复核通过前不能启动。
- 旧实验结论和旧 profile 不自动成为默认值。发现默认冲突时必须显式选择并记录。

## Agent 交接

设计者输出后，所有实验固定由三个独立角色审阅：`cold_start` 检查可理解性和合同完整性，`satellite_drl` 检查领域有效性、公平性与信息条件，`adversarial` 主动寻找反例和绕过。三者都绑定当前编译产物并 PASS 后才可授权；不能在 brief 中少声明角色来降低门槛。

## 从审阅通过到执行

1. 生产者把完整编译目录作为工作包产物，所有审阅回执绑定其 hash。
2. 决策者写入 ACCEPT 后，用 `CODE/work/finalize_decision.py` 生成可重算的
   `agent-work-finalization/v1` 回执。
3. 运行授权器：

```bash
python3 CODE/experiment_platform/authorize_experiment.py \
  --experiment EXPERIMENTS/EXP-... \
  --finalization CODE/work/WP-.../R01/finalization.json \
  --out EXPERIMENTS/EXP-.../authorization.json
```

4. 逐个运行传入同一授权（正式执行只经 canonical 远程运行器；`--config`
   后缀必须与 `--runtime-kind` 匹配，见 `remote_job.py` 的
   `validate_formal_paths`）：

**leo_sim_v2**（本仓库唯一可执行的正式路线；编译产物为 `resolved/*.leo-sim.yaml`）：

```bash
CODE/scripts/remote/run-remote.sh \
  --runtime-kind leo_sim_v2 \
  --config EXPERIMENTS/EXP-.../resolved/<run_id>.leo-sim.yaml \
  --authorization EXPERIMENTS/EXP-.../authorization.json
```

遗留的 `legacy_gateway`（`*.config.json`）路线是外部旧平台兼容合同：执行需要
外部旧平台，不能从本仓库独立完成；这里不提供可复制执行的 legacy 启动示例。

V2 正式运行由远端 `CODE/scripts/remote/remote_job.py` 以
`python3 -m CODE.leo_sim run`（绑定 authorization、launch nonce、expect run id）
执行。`formal_run.json` 只由 leo_sim 自身在自然结束（`natural_end=true`）时写入；
`governance_receipt.json` 由 remote_job 在结果目录可用后生成，即使存在
verification errors、子进程失败或非自然结束也可能存在，此时其 `research_eligible`
必须为 `false`——文件存在不等于正式准入。正式准入另要求 `natural_end=true`、
conservation、无 verification errors、外部启动证人与全部身份绑定，并经
`v2_analysis` 后验确认。本地 CLI/冒烟命令（如 `python3 -m CODE.leo_sim run`
不带正式参数）只用于编译、预览与本地检查，不属于正式执行，其输出不能作为
实验证据。

授权器（`CODE/experiment_platform/authorize_experiment.py`）验证的是前置证据
（finalization、审阅回执、编译产物与授权矩阵），不生成运行后的
`governance_receipt.json`；该回执由正式运行链在结果目录可用后生成（见上），
正式准入由 `v2_analysis` 后验确认。正式运行链（`CODE/scripts/remote/run-remote.sh`，
含本地 `CODE/experiment_platform/v2_serial_gate.py` 前置门与远端
`remote_job.py`）会重算 brief、decision、审阅回执、编译产物与逐 run
canonical config hash。只有 `status=AUTHORIZED` 而无法重建证据链的文件无效。
任何审阅后修改都使旧授权失效，需要新 revision 复审。`--dry-run` 可免授权，
但只表示配置预览；无 platform provenance 的真实运行会被明确拒绝。

运行产物按 `EXPERIMENTS/contracts/run-artifact-contract.md` 分为通用/legacy 族、
`leo_sim_v2` 核心结果文件与 `leo_sim_v2` 正式治理增量三部分；通用产物不能
替代 V2 治理产物，任一非正式产物单独存在都不能建立论文 claim。
