# leo-experiment-platform 工作与维护规则

本文件保存稳定规则，适用于包含本文件的仓库及其工作树，不记录滚动 SHA、磁盘余量或当前实验进度。先读根目录 `STATUS.md`；工作包 STATUS 仅说明该工作包。

## 执行位置与边界

- 所有仿真、压力测试、决策计时及产生科研数值的实验在 VM 执行。本机只做代码修改、单元/回归测试、静态检查、文档和已回传工件的只读复核。
- 本机旧实验产物是历史材料，不作为新 VM 实验结果。
- 旧正式部署 `/data/论文/leo-direct-sim` 保持只读；不得把 T1 发布或清理指向它。
- T1 根 `/data/论文/leo-t1-wt` 的旧 CODE/Results/launch.json 是历史流程。新发布只使用 `releases/<release-id>`，新运行只使用 `runs/<run-id>`；不得回到向共享 CODE 目录 rsync 覆盖的流程。
- 主机连接、Python 路径和环境事实以当前配置及实查为准；不用历史磁盘余量或系统 Python 版本代替运行环境验证。

## 每次开发、发布和回传

1. 确认工作树归属、分支、HEAD 和修改状态；保护别的任务的 dirty/untracked 文件。同一文件指定一个写入负责人。
2. 按用户已授权任务修改必要文件并验证，更新既有状态入口。禁止删测试、放宽断言或静默回退制造通过。
3. 发布必须来自干净工作树的完整 commit SHA，使用 `CODE/scripts/remote/publish-release-remote.sh`。GitHub 备份、合并 main 与发布是不同动作；未核实远端 ref 时保留 `remote_backup_pending`。
4. 有界诊断/开发运行使用 `CODE/scripts/remote/run-release-remote.sh`，指定已核验 release 和新 run-id；记录实际输入、模型、配置及环境身份。失败运行保留回执，不复用 run-id 掩盖失败。
5. 回传使用 `CODE/scripts/remote/pull-release-results-remote.sh`：partial 暂存、校验、原子落盘、追加 `ANALYSIS/DEPLOYMENT-INDEX.jsonl`。不得手改 VERIFIED 或 receipt hash。
6. 证据对照本次固定 release、配置和输入身份核验，不要求历史结果等于后来工作树的 HEAD。之后提交不会自动使旧证据失效，也不会使旧证据自动覆盖新代码。
7. 发布中断时先确认该 release 没有活动上传，再检查 partial/bootstrap/staging；隔离残留并保留原因。不得盲目删目录或覆盖重试；已测试的恢复入口及限制见 STATUS。

正式实验仍走编译 → 独立审阅 → 明确授权 → 既有 `CODE/scripts/remote/run-remote.sh` → 自然结束回执 → 分析重算。T1 新发布/诊断验证不授予 FORMAL_RUN，不证明科研收益。

## 文件与维护规则

- 根 STATUS 是当前状态入口；README 负责导航；本文件存稳定规则；工作包和验收报告保留日期、代码身份与证据范围。不要重复建立第二套当前状态文档。
- 源码、测试、配置模板进 Git。凭据、真实环境配置、模型、外部原始数据和运行输出不因同步方便而入库；Results/out/leo_sim_out 等保持隔离。需要的外置资产记录来源与 hash。
- 开始和收尾运行 `python3 -B CODE/scripts/maintenance_check.py --repo .`。回传后或验收前再加 `--verify-evidence`；此检查只读，不联网、不实验、不删除。
- 检查错误必须修复或明确阻塞；警告逐项说明。dirty 是工作状态，不得自动清理；缺少外置证据的 checkout 不能冒称回传已核验。
- 修改维护检查或发布协议时运行相应测试。CI 收集 `CODE/tests` 与 `CODE/scripts/remote/tests`；本机通过不等于远端当前 SHA 已通过。
- 原始资料、备份、许可和依赖锁的未完成项保留在 STATUS。运行环境清单不替代依赖锁，同盘副本不替代独立备份。
- 回收前核对所有者、活动进程、未推送提交、未跟踪/ignored 资产、结果引用和备份。没有明确授权不删除历史材料或工作树；Codex 托管工作树使用归档工具。禁止自动 git clean/reset/stash、force-push 或定时垃圾清理。

## 工作区级治理文档

- 跨项目规则的权威副本在 `../AGENTS.md`；三端治理范围、证据边界和历史问题的唯一主方案在 `../三端文件与同步治理/三端管理方案.md`；该目录的 `资产登记.csv` 是原始资料与证据的路径/哈希清单。本仓库的 `AGENTS.md`、`STATUS.md` 只约束和报告仓库内工作，不复制整份主方案。
- 工作区主方案、资产清单和本机回传证据不进入公共仓库；当前没有已核验的设备外独立备份。GitHub 上本仓库的公开内容或 T1 发布目录不代表这些资料已备份。备份目标确定后，按主方案记录的清单复制、逐文件验 SHA-256，并从目标恢复样本验证。

报告必须区分本机代码检查、VM 诊断、正式实验与论文结论；维护检查通过只覆盖它实际检查的规则。
