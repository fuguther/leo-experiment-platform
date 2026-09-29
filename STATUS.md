# T1 仓库当前状态

本文是仓库级状态入口；工作包内的 `STATUS.md` 仍保留各自证据，不代表全仓状态。版本身份以本文所在的 Git commit 为准。

## 同步与发布

- T1 仓库身份：`fuguther/leo-experiment-platform`；精确发布只接受干净任务 worktree 中的完整 commit SHA。
- `CODE/scripts/remote/publish-release-remote.sh` 将 Git 对象打成确定性包，校验文件清单后发布到 VM `/data/论文/leo-t1-wt/releases/<release-id>`。新 release 并存、只读；不写旧正式目录 `/data/论文/leo-direct-sim`。
- `CODE/scripts/remote/run-release-remote.sh` 只允许 `diagnostic` 或 `development`，每次使用新 `run-id`。正式实验仍走现有 compile/review/authorization/receipt runner。
- `CODE/scripts/remote/pull-release-results-remote.sh` 只拉回通过 receipt 与文件 hash 校验的单个 run，先写 `.incoming/<run-id>.partial`，验证后原子落盘，并追加 `ANALYSIS/DEPLOYMENT-INDEX.jsonl`。
- 发布意外中断后，先确认没有同一 release 的活动上传，再用保留在 T1 根下的 `release_protocol.py quarantine-incoming --release-id <release-id>` 把旧 partial 原子移入 `.quarantine/`；它不会删除失败包。
- GitHub 精确 commit 未被 live ref 确认时，release 和 run 显示 `remote_backup_pending`；这不构成 formal 资格。

## 数据、依赖与证据边界

- release 使用 T1 专属数据/凭据排除规则，不改变旧 formal deploy 的 `deployment_guard.excluded()` 策略。
- 原始 PDF、Office 文档、图像、归档、地理栅格、训练数据和 checkpoint 不进入 T1 source release。需要这些输入的运行须由运行人从获准来源单独提供，并把实际 VM 路径及 hash 纳入 run receipt。
- 当前源码 commit 没有识别到可用的依赖 lockfile；release 标记 `not_pinned`，run receipt 记录 VM Python、平台及已安装包清单 hash。该信息便于追查，不等于依赖可精确重建。
- T1 VM 已只读核验的现有运行环境为 `/data/liguang13/conda-envs/leo-i39/bin/python`（Python 3.11.15、PyYAML 6.0.2）；新 runner 默认使用它，可用 `T1_REMOTE_RUN_PYTHON` 显式覆盖。该环境没有 lockfile，仍不构成精确依赖冻结。
- 原始文献/数据的来源、许可、唯一权威路径及独立设备备份仍须在项目资料盘点中核验。主机上的另一目录不能作为设备级备份证明。
- 本仓库的本机测试和诊断只验证同步/回执机制，不证明路由机制、实验结果或论文 claim。

## 可复核入口

```bash
python3 -m pytest CODE/scripts/remote/tests/test_release_protocol.py -q
bash CODE/scripts/remote/publish-release-remote.sh --help
bash CODE/scripts/remote/run-release-remote.sh --help
bash CODE/scripts/remote/pull-release-results-remote.sh --help
```

以上命令是接口说明。是否已成功部署或回传，以 `ANALYSIS/DEPLOYMENT-INDEX.jsonl` 中的 receipt hash 和外置证据目录为准；无索引记录时不得声称闭环已完成。

## 2026-09-29 已核验闭环

- Public GitHub 分支 `codex/20260929-three-end-sync` 当前代码提交 `b8d8263ebe6bc7f9a2e7e5e7b91e7a5afa984399`；未合并 main。
- T1 已安装 release `b8d8263ebe6bc7f9a2e7e5e7b91e7a5afa984399-401d84ab28d6b89b1bc404be49335bcf7ea6711b1dbdaec2adcc9d79fee90470`，远端回报 `published`，incoming 已在验证后清理；GitHub ref 校验为 `verified`。
- `syncdiag-20260929-02` 在该 release 上以 `diagnostic` 模式通过 smoke 配置校验（exit 0，配置 SHA-256 `ffe9cee60d6c34e8222747a2d0fdbd7a835d79779dd170ced9ac5656f12637c3`）；run receipt SHA-256 为 `1f9e5f72e6ff230d7cba8c9c0cafa2327176ce7b811bf23bfc6d15e1cb3da915`，回传状态 `VERIFIED`。这是配置验证，不是仿真或科研结果。
- 首次 `syncdiag-20260929-01` 因误用系统 Python 3.12（缺 PyYAML）失败；receipt 与日志已独立拉回并保留，换用既有 `leo-i39` 环境后以新 run-id 重跑成功。回传调试留下的 partial 位于本机 evidence `.quarantine/`，没有升级成 verified run。
- T1 新协议只写 `releases/`、`runs/`；不迁移或写旧 T1 `Results/`。旧 formal 根 `/data/论文/leo-direct-sim` 的部署源码 hash 前后均为 `581370c53e58293dabf4370df9d63ff79247f1ada92db265e8149f37a815a8a4`（1,014 文件）。旧 formal receipt 仍无法完整验证：现有 4 个额外 `ANALYSIS/DIAG-T1-FROZEN-BRANCH-20260926/*` 文件与 receipt 清单不符；本次未改它们。
- 本机证据位于相对路径 `../三端文件与同步治理/evidence/t1/`，在此设备之外的备份尚未核实；依赖 lockfile 与原始研究材料的来源/许可登记仍未完成。
