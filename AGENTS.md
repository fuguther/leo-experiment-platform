# leo-experiment-platform 工作与维护规则

本文件保存稳定规则，适用于包含本文件的仓库及其工作树，不记录滚动 SHA、磁盘余量或当前实验进度。先读根目录 `STATUS.md`；工作包 STATUS 仅说明该工作包。

## 执行位置与边界

- 所有仿真、压力测试、决策计时及产生科研数值的实验在 VM 执行。本机只做代码修改、单元/回归测试、静态检查、文档和已回传工件的只读复核。
- 本机旧实验产物是历史材料，不作为新 VM 实验结果。
- 旧正式部署 `/data/论文/leo-direct-sim` 在当前 VM（`cuda-liguang13`）上已不存在/退役；不得把 T1 发布、配置或清理指向它，当前 VM 唯一活跃运行与证据根为 `/data/论文/leo-t1-wt/`。
- T1 根 `/data/论文/leo-t1-wt` 的旧 CODE/Results/launch.json 是历史流程。新发布只使用 `releases/<release-id>`，新运行只使用 `runs/<run-id>`；不得回到向共享 CODE 目录 rsync 覆盖的流程。
- 主机连接、Python 路径和环境事实以当前配置及实查为准；不用历史磁盘余量或系统 Python 版本代替运行环境验证。

## 每次开发、发布和回传

1. 确认工作树归属、分支、HEAD 和修改状态；保护别的任务的 dirty/untracked 文件。同一文件指定一个写入负责人。
2. 按用户已授权任务修改必要文件并验证，更新既有状态入口。禁止删测试、放宽断言或静默回退制造通过。
3. canonical 发布必须来自干净 `main` 工作树的完整 commit SHA，使用 `CODE/scripts/remote/publish-release-remote.sh`；构建器同时要求该 commit 精确等于远端 `refs/heads/main`。Mac 在 promotion 前生成带时间、目标仓库、commit/ref 和自哈希的 live-main proof；VM 校验 proof 并把它嵌入 `CURRENT.json`，不要求 VM 能直连 GitHub。未确认远端备份的包只能走显式 `--development-release`，不得成为 current。GitHub 备份、合并 main 与发布仍是不同动作，不能用本地 dirty 文件代替任一动作。
4. 有界诊断/开发运行使用 `CODE/scripts/remote/run-release-remote.sh`，指定已核验 release 和新 run-id；默认只接受与 VM `CURRENT.json` 完全一致的 release，旧的/来源未知的 release 只有显式 `--allow-historical-release` 才能复现。开始创建 run 目录前，runner 强制比较当前 Python、Linux 平台、机器架构和已安装 Python 包清单摘要与 release 内 `CODE/dependencies/t1-vm-linux-aarch64/runtime-identity.json`，并重算核对其中声明的 pip/Conda 锁哈希；不匹配时拒绝运行。成功的 run manifest 记录实际环境并绑定契约哈希，receipt 绑定 manifest 哈希。声明的数据、模型和配置会快照到该 run。`--config` 只登记并固化输入快照，不会自动补齐子命令的位置参数；例如 `config validate` 仍须在 `--` 后显式传入 release 相对路径，runner 会将其替换为 run 隔离快照。失败运行保留回执，不复用 run-id 掩盖失败。
5. 回传使用 `CODE/scripts/remote/pull-release-results-remote.sh`：partial 暂存、校验、原子落盘、追加 `ANALYSIS/DEPLOYMENT-INDEX.jsonl`。不得手改 VERIFIED 或 receipt hash。
6. 证据对照本次固定 release、配置和输入身份核验，不要求历史结果等于后来工作树的 HEAD。之后提交不会自动使旧证据失效，也不会使旧证据自动覆盖新代码。
7. 发布中断时先确认该 release 没有活动上传，再检查 partial/bootstrap/staging；隔离残留并保留原因。不得盲目删目录或覆盖重试；已测试的恢复入口及限制见 STATUS。

正式实验（`formal`）在原则上仍须走编译 → 独立审阅 → 明确授权 → 自然结束回执 → 分析重算；但当前绑定旧 `/data/论文/leo-direct-sim` 的 `CODE/scripts/remote/run-remote.sh` 尚未迁移到 `/data/论文/leo-t1-wt/releases`（参见 `ANALYSIS/CURRENT-EVENT-TIMELINE.md` B7 与 `run-release-remote.sh` 对 `formal` 的拒绝），因此现阶段 canonical `formal` 入口处于 **BLOCKED / 未迁移** 状态。T1 新发布与 `diagnostic`/`development` 验证不授予 `FORMAL_RUN`，不证明科研收益。

## 文件与维护规则

- 根 STATUS 是当前状态入口；README 负责导航；本文件存稳定规则；工作包和验收报告保留日期、代码身份与证据范围。不要重复建立第二套当前状态文档。
- 平台只维护一条公共主干：world/demand → 合法信息 → 动作 → 共享服务与响应 → 完整 cohort outcome → 不可变证据。T1、time-alignment、population/scene 和其它研究驱动是实验叶子，必须复用主干接口，不得另造一套仿真、指标或回执体系。
- 算法候选经外部 policy plugin/module 接入，不把模型结构、训练循环或实验专用语义写入仿真核心。新增研究问题优先由配置、合同或小 adapter 表达；只有无法复用时才新增代码。
- 清理先区分“活动主干”“当前实验叶子”“独立核验”“只读兼容”和“可从固定 Git/run 证据恢复的历史”。删除前核对消费者、契约、测试和证据；历史轮次说明、一次性实例和重复导航可以删除，测试中的反例、旧 schema 读取能力和原始运行证据不得按年龄清理。
- 源码、测试、配置模板进 Git。凭据、真实环境配置、模型、外部原始数据和运行输出不因同步方便而入库；Results/out/leo_sim_out 等保持隔离。T1 release 按精确 Git 树及路径/后缀清单打包，不通用地排除 CSV/JSON 或验证许可；发布前核对 manifest 中的数据文件与本机资产登记，未核实来源/许可的材料不得作为新内容推送至公开 GitHub。
- 开始和收尾运行 `python3 -B CODE/scripts/maintenance_check.py --repo .`。回传后或验收前加 `--verify-evidence --evidence-root /path/to/local-evidence` 并替换为本机实际证据根；索引只保存不含本机路径的 `evidence://t1/<run-id>`，检查器按显式证据根读取对应子目录。此检查只读，不联网、不实验、不删除。
- 检查错误必须修复或明确阻塞；警告逐项说明。dirty 是工作状态，不得自动清理；缺少外置证据的 checkout 不能冒称回传已核验。
- 修改维护检查或发布协议时运行相应测试。CI 收集 `CODE/tests` 与 `CODE/scripts/remote/tests`；本机通过不等于远端当前 SHA 已通过。
- 原始资料、备份、许可和依赖锁的未完成项保留在 STATUS。运行环境清单不替代依赖锁，同盘副本不替代独立备份。
- 回收前核对所有者、活动进程、未推送提交、未跟踪/ignored 资产、结果引用和备份。没有明确授权不删除历史材料或工作树；Codex 托管工作树使用归档工具。禁止自动 git clean/reset/stash、force-push 或定时垃圾清理。

## 独立复核合同

- 开审前冻结一份 `criteria.json`：8–20 条，逐条编号，并写明可判定的主张、可执行的 `check` 与 `touches` 路径；写不出检查或明确谓词的条目不进入判据集。
- 判据在轮内不可增补。审阅者逐条给出 `PASS` 或 `FAIL`；`FAIL`/阻断必须附复现命令和实际输出，缺少证据时只能记为 UNKNOWN 并交任务所有者判断，不能伪装成阻断或通过。
- review receipt 必须绑定 `reviewed_sha`、`criteria_sha256`、reviewer、round 和逐条结果；`overall` 只由判据结果推导：任一 `FAIL` 即 `FAIL`，否则 `PASS`。
- 被判据 `touches` 覆盖的路径在修复后发生变化时，只重新审核仍与 diff 相交的判据；新发现的真实问题仍按影响处理，不因它未出现在原判据集中而静默放行。
- 同一冻结判据合同最多两轮。第二轮仍不一致时，按判据歧义处理并交人裁决；建立新判据属于新一轮任务合同，不能借“再加一条”无限延长复核。
- 多个审阅者在同一 SHA、同一判据集上并行独立审核，结果取并集。旧 `WORKING-MODEL.md` 的固定 3 天分支寿命与“本地最多领先远端 1 个提交过夜”规则退出；分支寿命由任务 Issue/所有者和本文件的工作树规则管理，以避免用日期或领先量替代真实完成条件。

## 工作区级治理文档

- 工作区级 `AGENTS.md` 与唯一治理主方案、资产登记表保存在本公共仓库以外的权威工作区文档目录；其中含本机路径和本地证据。本仓库的 `AGENTS.md`、`STATUS.md` 只约束和报告仓库内工作，不复制整份主方案，也不把本机目录路径写入公开索引。
- 工作区主方案、资产清单和本机回传证据不进入公共仓库；当前没有已核验的设备外独立备份。GitHub 上本仓库的公开内容或 T1 发布目录不代表这些资料已备份。备份目标确定后，按主方案记录的清单复制、逐文件验 SHA-256，并从目标恢复样本验证。

报告必须区分本机代码检查、VM 诊断、正式实验与论文结论；维护检查通过只覆盖它实际检查的规则。
