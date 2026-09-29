# T1 仓库当前状态

本文是仓库级当前状态入口；工作包内的 STATUS 只记录各自范围。版本身份以本文件所在的完整 Git commit 为准。

## 日常同步与发布

- 只用 `CODE/scripts/remote/publish-release-remote.sh` 从干净、已推送的完整 commit 构建不可变 T1 release；不再把共享 `CODE/` 覆盖同步到 VM。
- 每个诊断或开发运行使用已核验的 release 和新 `run-id`。新入口拒绝 formal 模式；正式实验继续走独立编译、审阅、授权和回执流程。
- 回传用 `CODE/scripts/remote/pull-release-results-remote.sh`：校验 run receipt、文件集合和逐文件哈希后原子落盘，再追加 `ANALYSIS/DEPLOYMENT-INDEX.jsonl`。失败 partial 保留在隔离目录，不改写成 `VERIFIED`。
- 上传中断后先确认该 release 没有活动上传；同时检查 incoming 与 bootstrap。恢复会保留失败材料，不动已发布 release。

## 运行环境与数据边界

- `CODE/dependencies/t1-vm-linux-aarch64/` 记录从 T1 Python 3.11.15 环境只读导出的 Conda 显式包 URL 和 88 个 PyPI 包的精确版本。核心直接依赖和测试依赖另列；CUDA 驱动、系统库与工具链条件单独记录。
- 这些版本锁不含 PyPI wheel 哈希。需在独立前缀完成重建、`pip check`、导入和测试后，才能称环境可按记录重建；现有活动环境没有被修改。
- runner 默认解释器路径对应新版本化的 T1 隔离环境；该路径在本次 PR 整合和环境重建前尚未生效。运行时仍写入 receipt，不能仅凭锁文件称复现已验证。
- release 由精确 Git 树构建，只包含 `ANALYSIS/`、`CODE/`、`EXPERIMENTS/`、`lines/`、可选 `docs/` 和列明的根文件，并执行路径/后缀排除。该过滤器不通用地排除 CSV/JSON，也不验证数据许可；例如已跟踪的 M-Lab 派生 CSV、对应派生 JSON、测试 CSV 和手工 micro-trace 当前会进入 release。它们不是本次配置诊断的输入，M-Lab 来源许可仍须按本机资产登记状态核实；不得把未核实资产作为新内容推送到公开 GitHub。声明的运行数据/模型/配置会另行快照到 run 目录，子进程参数和 `T1_INPUT_<NAME>` 指向快照，receipt 绑定相对路径、长度和 SHA-256。

## 已核验的历史记录与限制

- 历史 release `b8d8263…` 与 `syncdiag-20260929-02` 在旧代码上完成了配置校验和回传；它不证明本次恢复修复、环境锁或新 release 已在 VM 生效。
- 四个旧 formal 目录的清单外 JSON 已复核大小、哈希、格式和部分源码身份。其源码哈希能对应到已知代码快照，但没有精确 run-id/receipt 绑定；保留为历史来源未完全核实的问题，不改旧 formal 部署或回执。
- 新 T1 release 和 runs 使用隔离目录；旧 formal 部署和历史结果保持只读。旧账问题与新 T1 流程缺陷分开记录。

## 规则、检查与备份

- 仓库稳定规则见 `AGENTS.md`；工作区级规则和唯一治理主方案/资产登记表保存在本公共仓库以外的权威工作区文档目录，保留本机证据与路径，不复制整份到公共仓库。
- `CODE/scripts/maintenance_check.py --repo .` 是只读检查；验证回执时使用 `--verify-evidence --evidence-root /path/to/local-evidence` 并替换成实际证据根。公开 JSONL 索引只保存 `evidence://t1/<run-id>`，不保存本机路径。检查器不扫描文件正文中的凭据，不检查资料许可，不创建备份，也不运行实验。
- CI 会执行维护检查，并收集 `CODE/scripts/remote/tests`。本机结果不代替对 PR 精确 head SHA 的 GitHub CI。
- 工作区主方案、资产登记和本机回传证据尚无已核验的设备外独立备份。公共 GitHub 内容和 T1 发布目录不算这些资料的备份。备份目标、容量和权限确认后，按资产清单复制、核验哈希并恢复样本。

## 本次整合状态

- 治理变更已从原任务分支拆为以最新 `main` 为基线的窄范围 PR 分支；本状态文件随 PR 一并审查。
- 只有 PR 精确 head 的独立复核、必需 pytest CI 和最新 main 对账全部通过后才可合并。合并后再发布精确接受的 clean commit，用新的 run-id 做配置/工程诊断、回传并核验部署索引。
- 在上述步骤完成前，new release/run-id、VM 回执及本机索引仍待生成；不得把历史成功记录写成新代码部署证据。
