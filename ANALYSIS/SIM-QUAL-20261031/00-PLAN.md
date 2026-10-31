# SIM-QUAL-20261031 — 仿真资格考试 + 四策略 stale-state 敏感性（本轮计划）

> 状态：进行中。本文件只记录本轮**任务划分、写作用域、验收口径**，不记录结果。
> 代码身份：worktree `.wt-qual-20261031`，branch `qual/sim-qual-20261031`，base commit `a90c1f9`
> （= `night/time-exp-20261031` HEAD，含 time_alignment / queue_ledger / target_probe / ta-pilot）。
> 仓库规则以 `platform/AGENTS.md` 与工作区 `AGENTS.md` 为准；本目录是**唯一**新增报告目录。

## 0. 本轮目标（用户 2026-10-31 任务书）

先证明仿真器能正确产生"逐包转发—排队—链路占用—传播—状态同步—路由决策"机制，
再研究不同路由策略对 stale state 的敏感度。不得先跑历史实验、不得先训练神经网络。

顺序（不可跳过）：

1. 仓库/分支/HEAD/dirty 恢复与核验（Lead 已完成，见 §3）。
2. **资格考试 A–F**：tiny deterministic 场景，事件级断言，不是平均时延。
3. 若 A–F 有失败：先修仿真器 + 加回归测试，不进入算法实验。
4. 四种干净策略（固定定义见 `POLICY-SPEC.md`）+ 单测。
5. 冻结合同（traffic/topology/control/link/seed/policy 参数/metrics/stop）。
6. tiny/pilot：先看有没有真实 action flip；再扩负载/seed。
7. 结果链：AoI / margin m / L / Γ=Lτ/m / P(a_stale≠a_fresh) / regret R / W。
8. 保存：本目录报告 + `ANALYSIS/DEPLOYMENT-INDEX.jsonl` 运行回执 + reasoning 账本更新。

## 1. 写作用域（一个文件只有一个写者）

| 谁 | 允许写 | 禁止写 |
|---|---|---|
| Lead | `ANALYSIS/SIM-QUAL-20261031/**`、git commit、冻结合同、运行/分析脚本 | 核心仿真代码（除非协调后） |
| `mech-auditor` | `ANALYSIS/SIM-QUAL-20261031/AUDIT-MECHANISMS.md` | 任何代码/测试 |
| `test-auditor` | `ANALYSIS/SIM-QUAL-20261031/AUDIT-EXISTING-TESTS.md` | 任何代码/测试 |
| `harness-auditor` | `ANALYSIS/SIM-QUAL-20261031/AUDIT-HARNESS.md` | 任何代码/测试 |
| `qual-author` | `CODE/leo_sim/tests/test_qualification_20261031.py`、`ANALYSIS/SIM-QUAL-20261031/qualification-evidence/*.json` | 核心仿真代码、其它测试 |
| `policy-builder` | `CODE/leo_sim/state_policy.py`、`CODE/leo_sim/tests/test_state_policy.py`、`CODE/leo_sim/config.py`、`CODE/leo_sim/kernel.py`、`CODE/leo_sim/time_alignment.py` | 其它报告、qualification 测试 |

所有改动只发生在本 worktree；不得改动 `platform/` 主 checkout 或其它 `.wt-*` 工作树。

## 2. 验收口径

- A–F 每条必须给出：理论预期 → 实际事件（原始时间戳/顺序）→ 断言 → PASS/FAIL → 失败原因。
- 禁止用"平均时延变好/变差"代替事件级验证。
- 四策略必须共享同一物理网络/trace/pid/seed/topology/link/control dissemination，仅决策规则不同。
- 结果必须同时报告负例/零效应；不得只筛正例。
- 本机只跑单元/回归测试与只读复核；产生科研数值的仿真在 VM 按既有 release→run→pull 流程执行。

## 3. 起始 Git 实查（Lead，2026-10-31 21:0x）

- `platform/`：`research/scene-v3v4-20261020 @ 65acf0c`，clean；**不含** `time_alignment.py`。
- `.wt-night-20261031/`：`night/time-exp-20261031 @ a90c1f9`，clean、已推（origin 有同名 ref），为当前最新实验代码线。
- `.wt-instr-20261030/`：`instr/ledger-export-20261030 @ f2f018a`，是 `a90c1f9` 的祖先（night 线包含它）。
- 本轮从 `a90c1f9` 开新 branch/worktree，不覆盖上述任何一条线。

## 4. 交付文件（不建第二套）

- `00-PLAN.md`（本文件）
- `AUDIT-MECHANISMS.md`、`AUDIT-EXISTING-TESTS.md`、`AUDIT-HARNESS.md`
- `QUALIFICATION-20261031.md`（A–F 结果 + 事件证据索引）
- `qualification-evidence/*.json`
- `POLICY-SPEC.md`（四策略冻结定义）
- `PROTOCOL-20261031.md`（冻结合同）
- `RESULTS-20261031.md`
- `CONCLUSION-20261031.md`（Strict / Defeasible / Unknown 三分）
