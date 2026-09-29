# T1 仓库当前状态

本文是仓库级当前状态入口；工作包内 STATUS 只记录各自范围。具体版本以完整 Git commit、release-id 和 run-id 为准。

## 当前整合与运行状态

- PR #7 已通过独立审查并合入 `main`，代码合并 SHA：`857a9de4fa5c4eb07a3b466788b3a9ccba69c847`。精确 PR head `e44f0d5369256e5445443a63df2acad043418976` 的 GitHub Actions `test` run `36619082541` 成功；维护检查和 remote 协议测试已进入 CI。
- PR #8 状态/索引跟进已合入 `main`，merge SHA `c6fd2898002e5898de1c353c110673ee5d84e2af`；其 PR CI run `36625374273` 和合并后该精确 `main` SHA 的 push CI run `36627212189` 均成功，pytest 为 1,059 passed、2 skipped、2 warnings。PR #8 不改变 T1 运行代码。
- PR #9 已通过独立审查并合入 `main`，merge SHA `d8384aa9dde425ecfcc87cff42d4b8786cf2d043`；PR head `643ee41af219bb26c3f253e20cd06c6a1af41f07` 的 CI run `36629702140` 和该精确合并 SHA 的 push CI run `36631973728` 均成功，pytest 为 1,059 passed、2 skipped、2 warnings。PR #9 只加入脱敏索引和状态说明，不改变运行代码。
- 该 `main` SHA 已发布到隔离 T1 的不可变 release 目录。release-id：`d8384aa9dde425ecfcc87cff42d4b8786cf2d043-435da056ae241a23b26af76de06beef2f5f0f889512d22f7ac985e64dd3f6471`；source tree SHA-256 `435da056ae241a23b26af76de06beef2f5f0f889512d22f7ac985e64dd3f6471`，artifact SHA-256 `c76dc758575c51a28ce61eef6acece6ee35846dd0478fa0d26b04a5e60b988af`。发布器验证远端 `main` 备份身份并确认 release 安装成功；它不代表工作区资料已备份。
- 新 run `syncdiag-20260930-04` 的配置校验命令遗漏子命令必需的位置文件参数，故执行状态为 failed / exit 2。失败 receipt SHA-256 `d5a9868a80c7680433e79383e8327de4f1c4e880ca1a4535adebd0d8f9cf0af5`；回传器验证 receipt 与文件清单成功，索引 `pullback_status=VERIFIED` 只表示证据回传核验通过，不表示命令成功。失败材料保留，没有复用 run-id。
- 新 run `syncdiag-20260930-05` 在上述 release 上以 `diagnostic` 模式完成 smoke 配置校验（status `completed` / exit 0）；receipt SHA-256 `4fe28fd41c9a62ea3f28455b41ab8244849c6cde6153b06343bac3d59b68beb4`，run-manifest SHA-256 `f48b4d1d06aa9db8b7e8d7a4acf63c6320ed4735779f1a75e689d12ca15c44ce`，配置 SHA-256 `ffe9cee60d6c34e8222747a2d0fdbd7a835d79779dd170ced9ac5656f12637c3`，校验结果 SHA-256 `934315391f9572ca2f03a641a3b2a800889330e8f0579fccbb7ee29379c68816`。VM 再验、回传和索引 URI `evidence://t1/syncdiag-20260930-05` 均为 `verified`。
- PR #10 已通过独立复审并 squash 合入，head `8b197f465fbe1083431d77a481b1fef2b5a6515a`，merge SHA `d29e27ea4a5ae36bdc90dd0e186daa0ef927a8ed`。PR merge-ref CI run `36636615895` 测试了该 head 合入 base `d8384aa9dde425ecfcc87cff42d4b8786cf2d043` 的集成对象 `066fc256c982421e59db1580c3facd7d7c2b3c91`，结果 1,060 passed、2 skipped、2 warnings。
- 接受版本已发布到隔离 T1，不可变 release-id `d29e27ea4a5ae36bdc90dd0e186daa0ef927a8ed-5ece4b536728a28cdcb3c37331a5be35ce4a63d8358965181b9474e4cf360171`；source tree SHA-256 `5ece4b536728a28cdcb3c37331a5be35ce4a63d8358965181b9474e4cf360171`，artifact SHA-256 `8db3aa2bb750e2951f4b4d17253d38e1dac053387cf9d069624dd08e20872e24`。发布器的远端 main 身份、安装与 incoming 清理状态均 verified/published/cleaned。
- 新 run `syncdiag-20260930-06` 在该 release 上以 diagnostic、300 秒上限完成 smoke 配置校验（completed / exit 0）。receipt SHA-256 `904c645984b67a3c48a53aad27051747343260ea30ea6862aab4b808e9d3b973`，run-manifest SHA-256 `fd36666103569df0d785b6733f043153d8fa53b4f873e59485ab724c93139f4c`，配置 SHA-256 `ffe9cee60d6c34e8222747a2d0fdbd7a835d79779dd170ced9ac5656f12637c3`，校验输出 SHA-256 `934315391f9572ca2f03a641a3b2a800889330e8f0579fccbb7ee29379c68816`。VM receipt、manifest/file-set 再验成功，本机 pullback 和部署索引 URI `evidence://t1/syncdiag-20260930-06` 为 verified。
- 在较早 PR #7 release `857a9de4fa5c4eb07a3b466788b3a9ccba69c847-27161ebf8e8db22b6ab128d7458d038d69bd63031b361f3a8a36fcae7f925a38` 的隔离 Python 前缀上运行 `CODE/scripts/remote/tests` 和维护检查测试，带有 host、release、commit、解释器和命令身份的日志记录 `64 passed in 7.86 s`；本机相同测试选择 `64 passed`。这是旧 release 的工程测试证据，不表示新 release `d8384aa9…-435da056…` 已运行同一套 64 项测试，也不是仿真、训练或科研结果。
- PR #8 对 `syncdiag-20260930-03` 添加了经核验的小索引行并更新状态；PR #9 整合了并发与 release 选择的四条索引记录；PR #10 同时整合 #04/#05 索引、runner 参数规则及 release identity 修复。PR #10 后的 #06 新诊断索引仍待 PR #11 更新状态/索引；原始 receipt、manifest 与运行日志留在本机私有证据目录。

## 日常同步与发布

1. 进入工作树先读本文件和 `AGENTS.md`，确认仓库、owner、branch、完整 HEAD、dirty/untracked/ignored 路径及其他活动任务；执行 `python3 -B CODE/scripts/maintenance_check.py --repo .`。
2. 只从已提交、已推送、干净的完整 commit 运行 `CODE/scripts/remote/publish-release-remote.sh --commit <FULL_SHA>`。新发布写入不可变 T1 release，不再覆盖共享 `CODE/`；不得把旧 `t1-vm.sh sync` 当作新发布入口。
3. 诊断/开发使用固定 release-id 和从未用过的 run-id，经 `CODE/scripts/remote/run-release-remote.sh` 执行。`--config` 只固化输入；子命令仍须在 `--` 后取得必需文件参数，例如 `--config CODE/leo_sim/profiles/smoke.yaml -- python3 -m CODE.leo_sim config validate CODE/leo_sim/profiles/smoke.yaml`。新 runner 禁止 formal 模式；formal 继续走原有编译、独立审阅、授权和 receipt 路径。
4. 失败发布先确认没有同 release 活动上传，再检查 incoming/bootstrap。用已安装 release helper 的 quarantine 恢复器保留残留后重试；不得删失败回执、复用 run-id 或覆盖已发布 release。
5. 回传只能用 `CODE/scripts/remote/pull-release-results-remote.sh`。它先验证 VM receipt、run/release 身份和文件清单，再原子落盘并追加 `ANALYSIS/DEPLOYMENT-INDEX.jsonl`。验收本机证据时运行 `python3 -B CODE/scripts/maintenance_check.py --repo . --verify-evidence --evidence-root <实际本机证据根>`。

## 依赖与平台边界

- `CODE/dependencies/t1-vm-linux-aarch64/` 包含直接依赖说明、88 个固定版本 pip 解析结果和 27 个 Conda explicit URL；解释器、Linux/aarch64 与系统条件单独记录。
- 新版本化 Conda 前缀已从上述锁在 T1 重建；88 个 pip 固定版本与 27 个 Conda URL 全部对账匹配，`pip check`、核心导入和 64 项目标测试通过。重建证据保存在本任务本机私有证据目录。
- pip 锁没有 wheel SHA-256，所以结论是平台限定的版本集合可重建，不是制品字节级可复现。CUDA toolkit 未确认；本次诊断和维护/协议测试没有调用 GPU。
- release 按精确 Git 树和路径/后缀规则打包；CSV/JSON 不会仅因扩展名自动排除，数据来源和许可仍须在 `资产登记.csv` 中逐集合核对。未核实材料不得新上传到公开 GitHub。

## P2 验收边界

- 本地协议 fixtures 覆盖声明输入/配置快照、重复 run-id 拒绝、回传截断/篡改、索引并发和旧/新 release 并存；formal fixtures 覆盖配置/源码绑定与过期 authorization 拒绝。
- T1 真实 SSH 故障注入覆盖 4 KiB 上传中断和 release 安装后的 cleanup 中断；两类残留都经 quarantine 保留，已安装 release 哈希不变；同 release 重试返回 `already_present`。失败材料与哈希见本机私有治理证据。
- release 目录 basename、envelope 身份与请求 release-id 的错配在 PR #10 中加入拒绝检查和回归测试；测试先在旧实现失败，修复后 `CODE/scripts/remote/tests` 本机 53 passed，PR merge-ref CI 1,060 passed，接受版本 `d29e27e…` 已部署并完成新 T1 配置诊断。目录错配负例在本机 fixture/CI 验证，未对线上不可变 release 做重命名故障注入。完整语义身份错配矩阵仍未通过：T1 runner 对 runtime 和可选 authorization 记录身份/哈希，但不独立判断期望 runtime，也不执行 formal authorization 语义；模型错配没有单独负例。formal 的数据/模型/授权组合未完整逐项验收。
- 本次在隔离 T1 同时运行 `syncdiag-20260930-concurrent-a/b`：二者使用同一当前 release 与相同配置快照，各自有独立 run 目录、manifest、stdout 与 receipt；并发 pullback 均验证成功，部署索引保留两条唯一 run-id 记录。随后用显式 release-id 选择旧 release `b8d8263…-401d84ab…` 做静态配置校验，再选回当前 release `857a9de4…-27161ebf…` 成功运行；没有全局可变活动指针，故此验收证明旧/新版本可独立选择且不覆盖彼此，不声称验证了全局指针切换。两 run 并发与旧/新 release 并存的本地 fixtures 仍保留。

## 文件、备份与历史限制

- topic 根 `AGENTS.md` 管跨项目边界；topic 根主方案和 `资产登记.csv` 是三端治理的唯一权威工作区文档/清单；本文件只报告仓库状态，不复制全套本机资料。它们目前只有已确认的本机权威副本。
- 尚无获准且已核实的设备外独立备份目标；GitHub 只保存本仓库已提交的公开文件，T1 release 也不代表本机资料已备份。需要目标容量、权限、用途确认后再复制、重算源/目标 SHA 并恢复样本。
- `/data/论文/leo-direct-sim` 旧 formal 根保持只读。4 个清单外 JSON 的历史 source snapshot 可关联到已知代码快照，但没有精确 run-id/receipt 绑定；不删除、不重写 receipt、不重新盖章。该旧账与新的隔离 T1 release 流程分开记录。
- 当前复核到 7 个 Git worktree。其他工作树 owner/活动进程无法全部确认；dirty、untracked 和 ignored 研究材料原样保留，没有批量归档或删除，也未声称其他工作树已统一升级。
- 状态入口持续维护；每次规则或工作状态变化更新本文件。检查器只读，不联网、不做实验、不删文件；`0 errors` 仅表示通过其列明的维护范围。
