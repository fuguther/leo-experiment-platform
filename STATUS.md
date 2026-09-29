# T1 仓库当前状态

本文是仓库级状态入口；工作包内的 `STATUS.md` 仍保留各自证据，不代表全仓状态。版本身份以本文所在的 Git commit 为准。

## 同步与发布

- T1 仓库身份：`fuguther/leo-experiment-platform`；精确发布只接受干净任务 worktree 中的完整 commit SHA。
- `CODE/scripts/remote/publish-release-remote.sh` 将 Git 对象打成确定性包，校验文件清单后发布到 VM `/data/论文/leo-t1-wt/releases/<release-id>`。新 release 并存、只读；不写旧正式目录 `/data/论文/leo-direct-sim`。
- `CODE/scripts/remote/run-release-remote.sh` 只允许 `diagnostic` 或 `development`，每次使用新 `run-id`。正式实验仍走现有 compile/review/authorization/receipt runner。
- `CODE/scripts/remote/pull-release-results-remote.sh` 只拉回通过 receipt 与文件 hash 校验的单个 run，先写 `.incoming/<run-id>.partial`，验证后原子落盘，并追加 `ANALYSIS/DEPLOYMENT-INDEX.jsonl`。
- 发布意外中断后，先确认没有同一 release 的活动上传；恢复必须同时检查 incoming 与 bootstrap。已发布旧版的 quarantine 只移动 incoming，不能据此声称已可重试；本地修订与验证状态见下节维护记录。
- GitHub 精确 commit 未被 live ref 确认时，release 和 run 显示 `remote_backup_pending`；这不构成 formal 资格。

## 数据、依赖与证据边界

- release 使用 T1 专属数据/凭据排除规则，不改变旧 formal deploy 的 `deployment_guard.excluded()` 策略。
- 原始 PDF、Office 文档、图像、归档、地理栅格、训练数据和 checkpoint 不进入 T1 source release。需要这些输入的运行须由运行人从获准来源单独提供，并把实际 VM 路径及 hash 纳入 run receipt。
- `CODE/dependencies/t1-vm-linux-aarch64/` 现在记录 T1 的显式 Conda URL 集、88 个 PyPI 包的精确版本、核心直接依赖和测试依赖。来源是 2026-09-30 对既有 T1 Python 3.11.15 环境的只读导出；lock 身份会随 release manifest 记录。版本清单不含 PyPI wheel 哈希，只有隔离重建、`pip check`、导入和测试通过后，才标为“可重建”。
- 新 runner 默认使用 T1 专属根内版本化路径 `envs/t1-linux-aarch64-py311-pkgset-v1/bin/python`；可用 `T1_REMOTE_RUN_PYTHON` 显式覆盖。现有正式/研究环境不会被安装或升级。
- 原始文献/数据的来源、许可、唯一权威路径及独立设备备份仍须在项目资料盘点中核验。主机上的另一目录不能作为设备级备份证明。
- 本仓库的本机测试和诊断只验证同步/回执机制，不证明路由机制、实验结果或论文 claim。
- 工作区级权威文档在本仓库上一级目录的 `AGENTS.md`、`三端文件与同步治理/三端管理方案.md` 和 `资产登记.csv`；完整主方案及原始资料/回传证据不进入公共仓库。目前未确认设备外独立备份。需等用户指定存储后，按清单复制、校验全部 SHA-256 并做样本恢复；在此之前保持源文件原位。

## 可复核入口

```bash
python3 -m pytest CODE/scripts/remote/tests/test_release_protocol.py -q
bash CODE/scripts/remote/publish-release-remote.sh --help
bash CODE/scripts/remote/run-release-remote.sh --help
bash CODE/scripts/remote/pull-release-results-remote.sh --help
```

以上命令是接口说明。是否已成功部署或回传，以 `ANALYSIS/DEPLOYMENT-INDEX.jsonl` 中的 receipt hash 和外置证据目录为准；无索引记录时不得声称闭环已完成。

## 2026-09-29 已核验闭环

- Public GitHub 分支 `codex/20260929-three-end-sync` 本次已发布代码提交 `b8d8263ebe6bc7f9a2e7e5e7b91e7a5afa984399`；未合并 main。
- T1 已安装 release `b8d8263ebe6bc7f9a2e7e5e7b91e7a5afa984399-401d84ab28d6b89b1bc404be49335bcf7ea6711b1dbdaec2adcc9d79fee90470`，远端回报 `published`，incoming 已在验证后清理；GitHub ref 校验为 `verified`。
- `syncdiag-20260929-02` 在该 release 上以 `diagnostic` 模式通过 smoke 配置校验（exit 0，配置 SHA-256 `ffe9cee60d6c34e8222747a2d0fdbd7a835d79779dd170ced9ac5656f12637c3`）；run receipt SHA-256 为 `1f9e5f72e6ff230d7cba8c9c0cafa2327176ce7b811bf23bfc6d15e1cb3da915`，回传状态 `VERIFIED`。这是配置验证，不是仿真或科研结果。
- 首次 `syncdiag-20260929-01` 因误用系统 Python 3.12（缺 PyYAML）失败；receipt 与日志已独立拉回并保留，换用既有 `leo-i39` 环境后以新 run-id 重跑成功。回传调试留下的 partial 位于本机 evidence `.quarantine/`，没有升级成 verified run。
- T1 新协议只写 `releases/`、`runs/`；不迁移或写旧 T1 `Results/`。旧 formal 根 `/data/论文/leo-direct-sim` 的部署源码 hash 前后均为 `581370c53e58293dabf4370df9d63ff79247f1ada92db265e8149f37a815a8a4`（1,014 文件）。旧 formal receipt 仍无法完整验证：现有 4 个额外 `ANALYSIS/DIAG-T1-FROZEN-BRANCH-20260926/*` 文件与 receipt 清单不符；本次未改它们。
- 本机证据位于相对路径 `../三端文件与同步治理/evidence/t1/`，在此设备之外的备份尚未核实；依赖 lockfile 与原始研究材料的来源/许可登记仍未完成。

## 2026-09-30 维护复审收尾（本地修订，尚未发布）

- 规则入口已纠正：AGENTS 不再指向旧共享 CODE 的 rsync 覆盖流程，README 不再用旧分支名和旧证据 SHA 代表当前状态。代码发布仍以固定身份为准。
- 增加只读检查器 `CODE/scripts/maintenance_check.py`，检查入库路径、工作树登记、文档入口与回传索引；`--verify-evidence` 进一步重算可访问的本机证据。检查范围之外的原始材料来源、许可、独立备份与环境重建不因通过而完成。
- CI 配置加入维护检查，并补收 `CODE/scripts/remote/tests`。这些改动尚未推送/合并，不能声称 GitHub 已执行新门禁。
- 已核实并清理两条失效 Git worktree 登记：原路径不存在、无进程以其为 cwd、无对应活动 Codex 任务；各自 HEAD 已被远端分支包含。没有删除工作树内容或提交。其他脏/含 ignored 输出、活动任务或唯一本地提交的 worktree 均保留。
- 其他工作树、旧结果与研究资料未搬移或清空。资产登记给出路径与哈希快照；来源、许可和设备外备份仍按未核实处理。

上传中断恢复已在本地修复：quarantine 同时保留 incoming/bootstrap，支持只有 bootstrap 的中断；不会触碰已发布 release。协议测试独立复跑 44 passed，既有正式授权相关测试 10 passed。新恢复行为尚未在 VM 部署验证；旧 helper 不会自动获得修复。完整维护记录见主方案 §11，旧 VM 诊断只覆盖原发布身份。


收尾验证：维护检查器 7 项、发布协议 44 项、既有正式授权相关 10 项，合计 **61 passed（6.82 s）**；`git diff --check` 通过，CI YAML 可解析并包含维护检查与协议测试。实际只读检查覆盖 561 个已跟踪文件、9 个工作树登记、2 条索引/本机回执，结果 0 errors / 5 warnings。五条警告为两个失效工作树各两条提示，以及备份/依赖锁未核实的范围提示；不是五个新缺陷。维护检查器不扫描凭据正文，也不能自动判定原始资料重复或可删除。

本轮未提交、未推送、未改 VM；治理分支的本地修改和 topic 根规则已保存。新 CI 门禁与恢复修复尚未远端生效，旧工作树规则不会自动更新。
