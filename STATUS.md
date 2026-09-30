# T1 仓库当前状态

本文是仓库级当前状态入口；工作包内 STATUS 只记录各自范围。具体版本以完整 Git commit、release-id 和 run-id 为准。

## 当前研究工作包 B（2026-10-01；发布前）

- 执行树为 工作树 `.t1-dev-experiment-a`，分支 `codex/20260930-t1-experiment-a`，当前修改以新 commit 为待发布身份；仅此工作树有本包改动。
- 当前合同 `CODE/work/WP-T1-COMPLETE/contract_dev_b.yaml` 已编译并校验：容器打包 42 格；实际 B tier 9 格、57 次模拟调用上界，其中 pilot 5 格/39 次、同一 bundle 与 run 内续跑 4 格/18 次。容器编译 cap 60 格，B 执行门禁仍为 ≤40 格、≤60 次调用、≤3600 s 模拟墙钟、≤120 s/格、单并发。
- 场景保留原 24 星/3 轨道面平台与完整 N/E/S/W 邻居候选，只把六条有向 OD 端点限定在区域内；包含六 OD 稳态阴性对照和六 OD 低—增长—热点方向转移—消退竞争轨迹。四信息臂、五模式（含 `precomputed`）、D=30 s、人口窗 [5,20] s 均按新合同执行。
- 本机关键门禁/回放/OD/调用账本/四方向测试 25 passed，修改 Python 文件 `py_compile` 通过；完整合同编译 42 格、B tier 9 格/57 调用，`validate` 为 valid。以上是本机软件/静态证据，不是 VM 运行结果；B VM 尚未启动，实际模拟调用和 B 模拟墙钟均为 0。
- WP-A run02 的实际 VM elapsed 1061 s。四邻居调用估算修正后，A 原冻结的 78 上界可追溯为历史三出口假设，四出口结构上界为 83；A 内部实际调用数没有单独记录，不能把任一估算写成观察值。保守累计墙钟记账把 A 全部 1061 s 计入；B 仍受 3600 s 上限，总计上界 4661 s，低于 7200 s。A 结果与身份保持历史且不重跑。
- 目标新 run-id 为 `wp-b-dev-20261001-01`（部署索引未发现已用记录）；release 尚待提交、推送和发布。FORMAL_RUN、确认种子与训练均未启动。
- WP-A 的 `REWORK`、已回传结果和历史缺陷仍只代表其原运行身份；不得把 B 的本机测试/静态通过当作科研结果。

## 已完成的治理发布与同步记录（保留原身份）

- PR #7 已通过独立审查并合入 `main`，代码合并 SHA：`857a9de4fa5c4eb07a3b466788b3a9ccba69c847`。精确 PR head `e44f0d5369256e5445443a63df2acad043418976` 的 GitHub Actions `test` run `36619082541` 成功；维护检查和 remote 协议测试已进入 CI。
- PR #8 状态/索引跟进已合入 `main`，merge SHA `c6fd2898002e5898de1c353c110673ee5d84e2af`；其 PR CI run `36625374273` 和合并后该精确 `main` SHA 的 push CI run `36627212189` 均成功，pytest 为 1,059 passed、2 skipped、2 warnings。PR #8 不改变 T1 运行代码。
- PR #9 已通过独立审查并合入 `main`，merge SHA `d8384aa9dde425ecfcc87cff42d4b8786cf2d043`；PR head `643ee41af219bb26c3f253e20cd06c6a1af41f07` 的 CI run `36629702140` 和该精确合并 SHA 的 push CI run `36631973728` 均成功，pytest 为 1,059 passed、2 skipped、2 warnings。PR #9 只加入脱敏索引和状态说明，不改变运行代码。
- 该 `main` SHA 已发布到隔离 T1 的不可变 release 目录。release-id：`d8384aa9dde425ecfcc87cff42d4b8786cf2d043-435da056ae241a23b26af76de06beef2f5f0f889512d22f7ac985e64dd3f6471`；source tree SHA-256 `435da056ae241a23b26af76de06beef2f5f0f889512d22f7ac985e64dd3f6471`，artifact SHA-256 `c76dc758575c51a28ce61eef6acece6ee35846dd0478fa0d26b04a5e60b988af`。发布器验证远端 `main` 备份身份并确认 release 安装成功；它不代表工作区资料已备份。
- 新 run `syncdiag-20260930-04` 的配置校验命令遗漏子命令必需的位置文件参数，故执行状态为 failed / exit 2。失败 receipt SHA-256 `d5a9868a80c7680433e79383e8327de4f1c4e880ca1a4535adebd0d8f9cf0af5`；回传器验证 receipt 与文件清单成功，索引 `pullback_status=VERIFIED` 只表示证据回传核验通过，不表示命令成功。失败材料保留，没有复用 run-id。
- 新 run `syncdiag-20260930-05` 在上述 release 上以 `diagnostic` 模式完成 smoke 配置校验（status `completed` / exit 0）；receipt SHA-256 `4fe28fd41c9a62ea3f28455b41ab8244849c6cde6153b06343bac3d59b68beb4`，run-manifest SHA-256 `f48b4d1d06aa9db8b7e8d7a4acf63c6320ed4735779f1a75e689d12ca15c44ce`，配置 SHA-256 `ffe9cee60d6c34e8222747a2d0fdbd7a835d79779dd170ced9ac5656f12637c3`，校验结果 SHA-256 `934315391f9572ca2f03a641a3b2a800889330e8f0579fccbb7ee29379c68816`。VM 再验、回传和索引 URI `evidence://t1/syncdiag-20260930-05` 均为 `verified`。
- PR #10 已通过独立复审并 squash 合入，head `8b197f465fbe1083431d77a481b1fef2b5a6515a`，merge SHA `d29e27ea4a5ae36bdc90dd0e186daa0ef927a8ed`。PR merge-ref CI [run 36636615895](https://github.com/fuguther/leo-experiment-platform/actions/runs/36636615895) 测试了该 head 合入 base `d8384aa9dde425ecfcc87cff42d4b8786cf2d043` 的集成对象 `066fc256c982421e59db1580c3facd7d7c2b3c91`，结果 1,060 passed、2 skipped、2 warnings；该链接为 PR merge-ref 检查，不代表合并后的 main push CI。
- 接受版本已发布到隔离 T1，不可变 release-id `d29e27ea4a5ae36bdc90dd0e186daa0ef927a8ed-5ece4b536728a28cdcb3c37331a5be35ce4a63d8358965181b9474e4cf360171`；source tree SHA-256 `5ece4b536728a28cdcb3c37331a5be35ce4a63d8358965181b9474e4cf360171`，artifact SHA-256 `8db3aa2bb750e2951f4b4d17253d38e1dac053387cf9d069624dd08e20872e24`。发布器的远端 main 身份、安装与 incoming 清理状态均 verified/published/cleaned。
- 新 run `syncdiag-20260930-06` 在该 release 上以 diagnostic 模式完成 smoke 配置校验（completed / exit 0）。receipt 的 protocol canonical `receipt_sha256` 字段为 `904c645984b67a3c48a53aad27051747343260ea30ea6862aab4b808e9d3b973`（不是 receipt 文件原始字节哈希）；run-manifest SHA-256 `fd36666103569df0d785b6733f043153d8fa53b4f873e59485ab724c93139f4c`，配置 SHA-256 `ffe9cee60d6c34e8222747a2d0fdbd7a835d79779dd170ced9ac5656f12637c3`，校验输出 SHA-256 `934315391f9572ca2f03a641a3b2a800889330e8f0579fccbb7ee29379c68816`。VM receipt、manifest/file-set 再验成功，本机 pullback 和部署索引 URI `evidence://t1/syncdiag-20260930-06` 为 verified。
- PR #11 已 squash 合入 `main`：head `99158c5f64120fcb1695826a4406c53f15c23757`，merge SHA `803fafcf7b51033d8b32e609750e32d7dfe7e996`；其 merge-ref CI [run 36640391596](https://github.com/fuguther/leo-experiment-platform/actions/runs/36640391596) 成功，测试合并对象 `6a2bd8b`（1060 passed、2 skipped、2 warnings）。该 PR 只更新 #06 的脱敏回传索引和状态，没有发布运行代码。
- PR #12 已通过独立复审并 squash 合入 `main`：PR head `6b9a218f77eae91a63ad42b699c8523616d91567`，merge SHA `6b4bd6b406d34f97b8f1f4c85c1a717379366536`。精确 PR head 的 CI run `36645147051` 与该 merge SHA 的 main push CI run `36650978047` 均成功，pytest 均为 1,065 passed、2 skipped、2 warnings；CI 中维护检查 0 errors、1 条范围 warning。
- 从上述精确 main merge SHA 发布的隔离 T1 release-id 为 `6b4bd6b406d34f97b8f1f4c85c1a717379366536-06c89985256898a012b5796ac362cffe0096123a30c58e01fb0b20012009f3e0`；source tree SHA-256 `06c89985256898a012b5796ac362cffe0096123a30c58e01fb0b20012009f3e0`，artifact SHA-256 `e781d0817034f995201b3748155ee1967eb5efb137765dc04df0b910caf94b44`。发布器报告远端 main 备份身份 verified、release published、incoming 清理完成。
- 新 run `syncdiag-20260930-07` 在该 release 上完成 diagnostic smoke 配置校验（completed / exit 0），验证结果内容 SHA-256 `934315391f9572ca2f03a641a3b2a800889330e8f0579fccbb7ee29379c68816`。receipt canonical SHA-256 `9a257b922876bdd77cec75fca0cc9898df33894af028e67d460a751591c224a0`，receipt 原始文件 SHA-256 `b744f5cbb661d0ba24d414a3ebc6c85098db1238e71aef214afec92fb351ce6b`，manifest SHA-256 `6ad17f3d70a4af638d5737ab4783643cb928d2d4c34074d1c985a72ede7211f3`。manifest 绑定 runtime contract SHA-256 `b3b548fdba7f364d5f48ed0bcc5c77ca667f6bd4ad2c463844eca8a18923e5d0`；T1 实际环境为 Python 3.11.15/Linux/aarch64，包清单摘要 `3bc4c603b890231bfe8e27441c639d2bf0152617524b43e72dd4dfc34edbe152`。VM receipt、manifest/file-set 复验、本机 pullback 和 `evidence://t1/syncdiag-20260930-07` 均 verified。只运行配置校验，无仿真、训练、GPU 调用或正式实验。
- 在较早 PR #7 release `857a9de4fa5c4eb07a3b466788b3a9ccba69c847-27161ebf8e8db22b6ab128d7458d038d69bd63031b361f3a8a36fcae7f925a38` 的隔离 Python 前缀上运行 `CODE/scripts/remote/tests` 和维护检查测试，带有 host、release、commit、解释器和命令身份的日志记录 `64 passed in 7.86 s`；本机相同测试选择 `64 passed`。这是旧 release 的工程测试证据，不表示新 release `d8384aa9…-435da056…` 已运行同一套 64 项测试，也不是仿真、训练或科研结果。
- PR #8 对 `syncdiag-20260930-03` 添加了经核验的小索引行并更新状态；PR #9 整合了并发与 release 选择的四条索引记录；PR #10 同时整合 #04/#05 索引、runner 参数规则及 release identity 修复；PR #11 已整合 #06 的脱敏索引和状态。原始 receipt、manifest 与运行日志留在本机私有证据目录。

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
- PR #10 已修复并测试 release basename/envelope/request identity 错配；PR #12 加入无普通 API/CLI 绕过的 runtime contract，拒绝 Python/platform/machine/包集及 pip/Conda 锁身份不匹配，并在创建 run 目录前停止。V2 授权夹具覆盖错误 run-id、授权后数据 trace 和 checkpoint 文件字节变化拒绝；这些测试不加载模型、不启动仿真或训练。真实 T1 #07 与契约匹配，manifest/receipt 身份绑定成功。完整模型加载、未登记 CUDA/toolkit 和正式科研运行不在本次验收范围。
- 本次在隔离 T1 同时运行 `syncdiag-20260930-concurrent-a/b`：二者使用同一当前 release 与相同配置快照，各自有独立 run 目录、manifest、stdout 与 receipt；并发 pullback 均验证成功，部署索引保留两条唯一 run-id 记录。随后用显式 release-id 选择旧 release `b8d8263…-401d84ab…` 做静态配置校验，再选回当前 release `857a9de4…-27161ebf…` 成功运行；没有全局可变活动指针，故此验收证明旧/新版本可独立选择且不覆盖彼此，不声称验证了全局指针切换。两 run 并发与旧/新 release 并存的本地 fixtures 仍保留。

## 文件、备份与历史限制

- topic 根 `AGENTS.md` 管跨项目边界；topic 根主方案和 `资产登记.csv` 是三端治理的唯一权威工作区文档/清单；本文件只报告仓库状态，不复制全套本机资料。它们目前只有已确认的本机权威副本。
- 尚无获准且已核实的设备外独立备份目标；GitHub 只保存本仓库已提交的公开文件，T1 release 也不代表本机资料已备份。需要目标容量、权限、用途确认后再复制、重算源/目标 SHA 并恢复样本。
- `/data/论文/leo-direct-sim` 旧 formal 根保持只读。4 个清单外 JSON 的历史 source snapshot 可关联到已知代码快照，但没有精确 run-id/receipt 绑定；不删除、不重写 receipt、不重新盖章。该旧账与新的隔离 T1 release 流程分开记录。
- 当前复核到 7 个 Git worktree。其他工作树 owner/活动进程无法全部确认；dirty、untracked 和 ignored 研究材料原样保留，没有批量归档或删除，也未声称其他工作树已统一升级。
- 状态入口持续维护；每次规则或工作状态变化更新本文件。检查器只读，不联网、不做实验、不删文件；`0 errors` 仅表示通过其列明的维护范围。
