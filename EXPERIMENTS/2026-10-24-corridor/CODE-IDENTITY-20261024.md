# 代码身份记录（2026-10-24 18:00）

- 仓库：`reports/leo-experiment-platform`（worktree `.wt-arrival-20261024`）
- 分支：`research/arrival-rate-20261024`，**HEAD `407fb3b`**（17:42）
- VM 代码树：`/data/论文/leo-t1-wt/scratch/corridor-20261024/CODE`
- 三个关键文件的 sha256 前缀（VM 与 HEAD **逐位一致**）：
  - `kernel.py` **1f3f7ed7aeaec4bf**
  - `time_alignment.py` **e47e471112781d7d**
  - `config.py` **10b7e5d70dba6229**
- 相对基线 `5e52ba3` 的改动（本线）：
  1. `kernel.py`：ISLLink 到达率 EWMA + 广告 `arrival_rate_bps` + 接收端提取 + `_split_zero_rate_directions` 零速率出口守卫（部分剔除也记审计）+ `rate_lookup_errors`。
  2. `time_alignment.py`：`StateSample.arrival_rate_bps`；`predictors += drift`；`drift_alpha`；`arrival_ewma/arrival_rate_at`。
  3. `config.py`：`drift`/`arrival_window_s`/`drift_alpha` 注册与校验。
- **注意**：该分支 17:00–17:42 还有另一位写者（DSH agent）提交的 `rate_aware` 预测器与相关 docs（`8b77779`、`0f8e0d3`、`5f82d3c` 等）。
  它只影响 `predictor=rate_aware` 的新配置；本线所有 MCS/20 城市实验用 `bounded_linear`，行为不受影响。
- 运行不含 git 元数据（`driver_identity.git.available=false`），**实验身份以上面的文件 sha256 + HEAD 为准**；每个 run 的
  `driver_identity.config_sha256` 记录其配置哈希。
- 本批（17:59 发射）的 profiles/脚本已入库：`EXPERIMENTS/2026-10-24-corridor/profiles/`。
